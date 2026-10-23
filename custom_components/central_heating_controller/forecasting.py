"""Dependency-free learning and forecasting of indoor thermal behaviour."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from math import exp, isfinite, sqrt
from typing import Callable

_MAX_GAP = timedelta(minutes=30)
_WINDOW_DURATION = timedelta(minutes=15)
_MAX_ROWS = 1000
_LAGS = (0, 5, 10, 20, 40, 60)  # exponential response time constants, minutes
_LOSS_BOUNDS = (0.005, 0.5)
_GAIN_BOUNDS = (0.05, 8.0)
_PRIOR_LOSS = 0.08
_PRIOR_GAIN = 1.5


def _number(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        result = float(value)
    except OverflowError, TypeError, ValueError:
        return None
    return result if isfinite(result) else None


def _demand(value: object) -> float | None:
    if isinstance(value, bool):
        return float(value)
    number = _number(value)
    return number if number is not None and 0 <= number <= 1 else None


def _timestamp(value: object) -> datetime | None:
    if not isinstance(value, datetime) or value.tzinfo is None:
        return None
    try:
        if value.utcoffset() is None:
            return None
        return value.astimezone(timezone.utc)
    except Exception:
        return None


@dataclass(frozen=True)
class ForecastPoint:
    """One simulated thermostat state."""

    at: datetime
    indoor_temperature: float
    outdoor_temperature: float
    target_temperature: float | None
    heating: bool
    demand: float = 0.0


@dataclass(frozen=True)
class ForecastResult:
    """Forecast samples and a concise measure of model reliability."""

    points: tuple[ForecastPoint, ...]
    confidence: float
    residual_rate_c_per_hour: float
    learned_samples: int
    heat_gain_c_per_hour: float
    heat_loss_per_hour: float
    lag_minutes: int = 0
    lag_learned: bool = False
    demand_model_learned: bool = False


class ThermalForecaster:
    """Fit a bounded one-zone heat model from 15-minute temperature slopes.

    Each possible response time filters the observed heating demand. A response
    time is used only after it beats the immediate-response model on later,
    unseen windows. Gaps and implausible readings reset transient heat state.
    """

    def __init__(self) -> None:
        self._previous: tuple[datetime, float, float, float] | None = None
        self._previous_target: float | None = None
        self._fractional_mode = False
        self._response = [0.0] * len(_LAGS)
        self._window_started: datetime | None = None
        self._window_temperature = 0.0
        self._window_heat_hours = [0.0] * len(_LAGS)
        self._window_delta_hours = 0.0
        self._window_hours = 0.0
        self._window_policy_hours = 0.0
        self._window_deficit_hours = 0.0
        self._window_demand_hours = 0.0
        # Row: immediate demand, negative indoor/outdoor difference, slope,
        # then filtered demand at each non-zero candidate response time.
        self._rows: list[tuple[float, ...]] = []
        self._policy_rows: list[tuple[float, float]] = []
        self._gain = _PRIOR_GAIN
        self._loss = _PRIOR_LOSS
        self._lag_minutes = 0
        self._lag_learned = False
        self._demand_gain = 1.0
        self._maintenance_demand = 0.0
        self._demand_model_learned = False
        self._residual_rate = 1.0
        self._identifiable = False

    @property
    def sample_count(self) -> int:
        return len(self._rows)

    @property
    def heat_gain_c_per_hour(self) -> float:
        return self._gain

    @property
    def heat_loss_per_hour(self) -> float:
        return self._loss

    @property
    def lag_minutes(self) -> int:
        return self._lag_minutes

    @property
    def lag_learned(self) -> bool:
        return self._lag_learned

    @property
    def demand_model_learned(self) -> bool:
        return self._demand_model_learned

    @property
    def confidence(self) -> float:
        """Return a heuristic score, not a calibrated probability."""
        if not self._identifiable:
            return 0.0
        excitation = self._excitation_threshold()
        heated = sum(row[0] > excitation for row in self._rows)
        unheated = sum(row[0] < excitation * 0.25 for row in self._rows)
        coverage = min(1.0, heated / 12, unheated / 12)
        quality = 1.0 / (1.0 + self._residual_rate)
        return round(coverage * quality, 3)

    def observe(
        self,
        at: datetime,
        indoor_temperature: float | None,
        outdoor_temperature: float | None,
        heating: bool | float | None,
        *,
        target_temperature: float | None = None,
    ) -> bool:
        """Add an observation, returning true when it contributed a sample."""
        stamp = _timestamp(at)
        indoor = _number(indoor_temperature)
        outdoor = _number(outdoor_temperature)
        demand = _demand(heating)
        if stamp is None or indoor is None or outdoor is None or demand is None:
            self._previous = None
            self._previous_target = None
            self._clear_window()
            self._response = [0.0] * len(_LAGS)
            return False
        if not -20 <= indoor <= 60 or not -60 <= outdoor <= 60 or abs(indoor - outdoor) > 80:
            self._previous = None
            self._previous_target = None
            self._clear_window()
            self._response = [0.0] * len(_LAGS)
            return False
        target = _number(target_temperature)
        if target is not None and not -20 <= target <= 60:
            target = None
        current = (stamp, indoor, outdoor, demand)
        previous = self._previous
        self._previous = current
        self._previous_target = target
        self._fractional_mode = not isinstance(heating, bool)
        if previous is None:
            self._response = [demand] * len(_LAGS)
            self._start_window(current)
            return False
        old_at, old_indoor, old_outdoor, old_demand = previous
        elapsed = stamp - old_at
        if elapsed <= timedelta(0) or elapsed > _MAX_GAP or abs(indoor - old_indoor) > 2.0:
            self._response = [demand] * len(_LAGS)
            self._start_window(current)
            return False
        hours = elapsed.total_seconds() / 3600
        minutes = elapsed.total_seconds() / 60
        # A transition is only known to have happened between readings.
        average_demand = (old_demand + demand) / 2
        for index, lag in enumerate(_LAGS):
            if lag == 0:
                average_response = average_demand
                self._response[index] = demand
            else:
                decay = exp(-minutes / lag)
                initial = self._response[index]
                average_response = average_demand + (initial - average_demand) * (lag / minutes) * (
                    1 - decay
                )
                self._response[index] = average_demand + (initial - average_demand) * decay
            self._window_heat_hours[index] += hours * average_response
        self._window_delta_hours += hours * (((old_indoor + indoor) - (old_outdoor + outdoor)) / 2)
        self._window_hours += hours
        if target is not None:
            self._window_policy_hours += hours
            self._window_deficit_hours += hours * (target - indoor)
            self._window_demand_hours += hours * demand
        if stamp - (self._window_started or stamp) < _WINDOW_DURATION:
            return False
        duration = self._window_hours
        change = indoor - self._window_temperature
        if duration <= 0 or abs(change) / duration > 8.0:
            self._response = [demand] * len(_LAGS)
            self._start_window(current)
            return False
        slope = change / duration
        row = (
            self._window_heat_hours[0] / duration,
            -self._window_delta_hours / duration,
            slope,
            *(heat_hours / duration for heat_hours in self._window_heat_hours[1:]),
        )
        self._rows.append(row)
        if len(self._rows) > _MAX_ROWS:
            self._rows.pop(0)
        if self._window_policy_hours >= duration * 0.7:
            self._policy_rows.append(
                (
                    self._window_deficit_hours / self._window_policy_hours,
                    self._window_demand_hours / self._window_policy_hours,
                )
            )
            if len(self._policy_rows) > _MAX_ROWS:
                self._policy_rows.pop(0)
        self._fit()
        self._fit_demand_policy()
        self._start_window(current)
        return True

    def _clear_window(self) -> None:
        self._window_started = None
        self._window_hours = 0.0
        self._window_heat_hours = [0.0] * len(_LAGS)
        self._window_delta_hours = 0.0
        self._window_policy_hours = 0.0
        self._window_deficit_hours = 0.0
        self._window_demand_hours = 0.0

    def _start_window(self, observation: tuple[datetime, float, float, float]) -> None:
        self._clear_window()
        self._window_started = observation[0]
        self._window_temperature = observation[1]

    @staticmethod
    def _fit_rows(
        rows: list[tuple[float, ...]], candidate: int
    ) -> tuple[float, float, float] | None:
        x_index = 0 if candidate == 0 else candidate + 2
        weights = [1.0] * len(rows)
        for _ in range(5):
            xx = sum(w * row[x_index] ** 2 for w, row in zip(weights, rows))
            zz = sum(w * row[1] ** 2 for w, row in zip(weights, rows))
            xz = sum(w * row[x_index] * row[1] for w, row in zip(weights, rows))
            xy = sum(w * row[x_index] * row[2] for w, row in zip(weights, rows))
            zy = sum(w * row[1] * row[2] for w, row in zip(weights, rows))
            det = xx * zz - xz * xz
            if det <= 1e-8:
                return None
            gain = (xy * zz - zy * xz) / det
            loss = (xx * zy - xz * xy) / det
            if not isfinite(gain) or not isfinite(loss):
                return None
            gain = min(max(gain, _GAIN_BOUNDS[0]), _GAIN_BOUNDS[1])
            loss = min(max(loss, _LOSS_BOUNDS[0]), _LOSS_BOUNDS[1])
            residuals = [row[2] - (gain * row[x_index] + loss * row[1]) for row in rows]
            weights = [1.0 if abs(error) <= 0.5 else 0.5 / abs(error) for error in residuals]
        rms = sqrt(sum(error * error for error in residuals) / len(rows))
        return gain, loss, rms

    def _fit(self) -> None:
        excitation = self._excitation_threshold()
        heated = sum(row[0] > excitation for row in self._rows)
        unheated = sum(row[0] < excitation * 0.25 for row in self._rows)
        if heated < 3 or unheated < 3:
            self._identifiable = False
            return
        if max(row[0] for row in self._rows) - min(row[0] for row in self._rows) < 0.05:
            self._identifiable = False
            return
        selected = 0
        self._lag_learned = False
        if len(self._rows) >= 40:
            split = max(25, int(len(self._rows) * 0.7))
            training, validation = self._rows[:split], self._rows[split:]
            transitions = sum(
                abs(b[0] - a[0]) > excitation for a, b in zip(self._rows, self._rows[1:])
            )
            if transitions >= 8 and len(validation) >= 12:
                scores: list[tuple[float, int]] = []
                for candidate in range(len(_LAGS)):
                    fit = self._fit_rows(training, candidate)
                    if fit is None:
                        continue
                    gain, loss, _ = fit
                    x_index = 0 if candidate == 0 else candidate + 2
                    error = sqrt(
                        sum(
                            (row[2] - gain * row[x_index] - loss * row[1]) ** 2
                            for row in validation
                        )
                        / len(validation)
                    )
                    scores.append((error, candidate))
                if scores:
                    scores.sort()
                    baseline = next((score for score, candidate in scores if candidate == 0), None)
                    best_error, best_candidate = scores[0]
                    self._lag_learned = baseline is not None
                    if (
                        baseline is not None
                        and best_candidate != 0
                        and best_error < baseline * 0.9
                        and baseline - best_error > 0.015
                    ):
                        selected = best_candidate
        fit = self._fit_rows(self._rows, selected)
        if fit is None:
            self._identifiable = False
            self._lag_learned = False
            return
        self._gain, self._loss, self._residual_rate = fit
        self._lag_minutes = _LAGS[selected]
        self._identifiable = True

    def _excitation_threshold(self) -> float:
        if not self._rows:
            return 0.04
        span = max(row[0] for row in self._rows) - min(row[0] for row in self._rows)
        return max(0.04, min(0.25, span * 0.4))

    def _fit_demand_policy(self) -> None:
        active = [
            (deficit, demand)
            for deficit, demand in self._policy_rows
            if deficit >= 0.15 and demand >= 0.05
        ]
        near_target = [demand for deficit, demand in self._policy_rows if -0.15 <= deficit <= 0.3]
        inactive = sum(deficit < -0.15 and demand < 0.1 for deficit, demand in self._policy_rows)
        if inactive < 3 or (len(active) < 6 and len(near_target) < 6):
            self._demand_model_learned = False
            return
        self._maintenance_demand = (
            min(0.5, max(0.0, sum(near_target) / len(near_target)))
            if len(near_target) >= 3
            else 0.0
        )
        denominator = sum(deficit * deficit for deficit, _ in active)
        self._demand_gain = (
            min(
                5.0,
                max(
                    0.05,
                    sum(deficit * (demand - self._maintenance_demand) for deficit, demand in active)
                    / denominator,
                ),
            )
            if len(active) >= 6 and denominator >= 0.1
            else 0.35
        )
        self._demand_model_learned = True

    def forecast(
        self,
        start_at: datetime,
        indoor_temperature: float,
        outdoor_temperature: Callable[[datetime], float],
        target_temperature: Callable[[datetime], float | None],
        *,
        hours: float = 24,
        step_minutes: int = 5,
    ) -> ForecastResult:
        """Simulate fractional thermostat demand and its thermal response."""
        stamp = _timestamp(start_at)
        indoor = _number(indoor_temperature)
        duration = _number(hours)
        if stamp is None or indoor is None or duration is None or duration <= 0:
            raise ValueError("start time, temperature and positive duration are required")
        if (
            isinstance(step_minutes, bool)
            or not isinstance(step_minutes, int)
            or not 1 <= step_minutes <= 60
        ):
            raise ValueError("step_minutes must be an integer from 1 to 60")
        if duration > 49:
            raise ValueError("forecast duration cannot exceed 49 hours")
        minute = timedelta(minutes=1)
        total_minutes = int(duration * 60)
        heating = False
        response = 0.0
        previous = self._previous
        if previous is not None and timedelta(0) <= stamp - previous[0] <= _MAX_GAP:
            candidate = _LAGS.index(self._lag_minutes)
            response = self._response[candidate]
            if self._lag_minutes:
                elapsed = (stamp - previous[0]).total_seconds() / 60
                response = previous[3] + (response - previous[3]) * exp(
                    -elapsed / self._lag_minutes
                )
        points: list[ForecastPoint] = []
        for index in range(total_minutes + 1):
            at = stamp + index * minute
            target = _number(target_temperature(at))
            outside = _number(outdoor_temperature(at))
            if outside is None:
                raise ValueError("outdoor forecast must return finite temperatures")
            if target is None:
                demand = 0.0
                heating = False
            elif self._fractional_mode or self._demand_model_learned:
                deficit = target - indoor
                if deficit < -0.15:
                    demand = 0.0
                elif self._demand_model_learned:
                    demand = min(
                        1.0,
                        max(0.0, self._maintenance_demand + self._demand_gain * max(0.0, deficit)),
                    )
                else:
                    maintenance = min(0.25, self._loss * max(0.0, indoor - outside) / self._gain)
                    demand = min(1.0, maintenance + 0.35 * max(0.0, deficit))
                previous_target = self._previous_target
                if (
                    previous is not None
                    and self._fractional_mode
                    and previous_target is not None
                    and abs(target - previous_target) <= 0.05
                    and timedelta(0) <= stamp - previous[0] <= timedelta(minutes=15)
                ):
                    age_minutes = (stamp - previous[0]).total_seconds() / 60
                    weight = max(0.0, 1.0 - (age_minutes + index) / 15)
                    demand = weight * previous[3] + (1 - weight) * demand
                heating = demand > 0.05
            else:
                heating = indoor < target if heating else indoor < target - 0.15
                demand = float(heating)
            if index % step_minutes == 0 or index == total_minutes:
                points.append(ForecastPoint(at, indoor, outside, target, heating, demand))
            if index == total_minutes:
                break
            if self._lag_minutes:
                decay = exp(-1 / self._lag_minutes)
                average_response = demand + (response - demand) * self._lag_minutes * (1 - decay)
                response = demand + (response - demand) * decay
            else:
                average_response = demand
                response = demand
            indoor += (self._gain * average_response - self._loss * (indoor - outside)) / 60
        return ForecastResult(
            tuple(points),
            self.confidence,
            self._residual_rate,
            self.sample_count,
            self._gain,
            self._loss,
            self._lag_minutes,
            self._lag_learned,
            self._demand_model_learned,
        )

    def to_dict(self) -> dict[str, object]:
        """Serialise bounded learned data; transient observations are omitted."""
        return {
            "version": 2,
            "rows": [list(row) for row in self._rows],
            "policy_rows": [list(row) for row in self._policy_rows],
        }

    @classmethod
    def from_dict(cls, data: object) -> ThermalForecaster:
        """Restore validated samples, then derive parameters afresh."""
        model = cls()
        if not isinstance(data, dict) or data.get("version") != 2:
            return model
        rows = data.get("rows")
        policy_rows = data.get("policy_rows")
        if (
            not isinstance(rows, list)
            or not isinstance(policy_rows, list)
            or len(rows) > _MAX_ROWS
            or len(policy_rows) > _MAX_ROWS
        ):
            return model
        checked: list[tuple[float, ...]] = []
        for row in rows:
            if not isinstance(row, list) or len(row) != 3 + len(_LAGS) - 1:
                return cls()
            values = tuple(_number(value) for value in row)
            if any(value is None for value in values):
                return cls()
            x, z, y, *lagged = values
            if (
                not 0 <= x <= 1
                or abs(z) > 80
                or abs(y) > 8
                or any(not 0 <= value <= 1 for value in lagged)
            ):
                return cls()
            checked.append(values)
        checked_policy: list[tuple[float, float]] = []
        for row in policy_rows:
            if not isinstance(row, list) or len(row) != 2:
                return cls()
            deficit, demand = (_number(value) for value in row)
            if (
                deficit is None
                or demand is None
                or not -80 <= deficit <= 80
                or not 0 <= demand <= 1
            ):
                return cls()
            checked_policy.append((deficit, demand))
        model._rows = checked
        model._policy_rows = checked_policy
        model._fit()
        model._fit_demand_policy()
        return model
