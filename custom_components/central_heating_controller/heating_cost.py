"""Conservative heating-cost attribution from mixed-boiler gas intervals.

The fitted heating component is an estimate: interval gas also contains a
baseline term that may include domestic hot water and other boiler losses.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from itertools import combinations
from math import isfinite
from statistics import median

_MAX_GAP = timedelta(minutes=5)
_RETENTION = timedelta(days=14)
_MIN_INTERVALS = 30
_MIN_SPREAD = 0.05
_MAX_SAMPLES = 672
_MIN_DURATION = timedelta(minutes=25)
_MAX_DURATION = timedelta(minutes=35)


def _time(value: object) -> datetime | None:
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    if not isinstance(value, datetime) or value.tzinfo is None:
        return None
    try:
        if value.utcoffset() is None:
            return None
        return value.astimezone(timezone.utc)
    except (OverflowError, ValueError):
        return None


def _number(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return result if isfinite(result) else None


class HeatingCostModel:
    """Fit gas power against average heating demand over valid gas intervals.

    Demand is a normalized fraction (0..1). The fitted slope is kWh per
    full-demand hour; the intercept is a baseline gas power.
    The baseline may include hot water, so predictions are attribution estimates.
    """

    def __init__(self) -> None:
        self._observations: dict[datetime, float | None] = {}
        self._samples: dict[tuple[datetime, datetime], tuple[float, float, float]] = {}
        self._fit_cache: dict[str, float | int | str | None] | None = None

    def observe(self, at: datetime, demand: float | None) -> bool:
        timestamp = _time(at)
        if timestamp is None:
            return False
        value = _number(demand)
        if value is not None and not 0 <= value <= 1:
            value = None
        self._observations[timestamp] = value
        self._prune(timestamp)
        return True

    def _prune(self, now: datetime) -> None:
        cutoff = now - _RETENTION
        self._observations = {at: demand for at, demand in self._observations.items() if at >= cutoff}
        samples = {key: row for key, row in self._samples.items() if key[1] >= cutoff}
        if len(samples) != len(self._samples):
            self._fit_cache = None
        self._samples = samples

    def _bounded_points(self, start: datetime, end: datetime) -> list[tuple[datetime, float]] | None:
        raw = sorted(self._observations.items())
        if len(raw) < 2:
            return None
        points: list[tuple[datetime, float]] = []
        for boundary in (start, end):
            exact = next(((at, value) for at, value in raw if at == boundary), None)
            if exact is not None:
                if exact[1] is None:
                    return None
                points.append((boundary, exact[1]))
                continue
            left = [(at, value) for at, value in raw if at < boundary]
            right = [(at, value) for at, value in raw if at > boundary]
            if not left or not right or right[0][0] - left[-1][0] > _MAX_GAP:
                return None
            if left[-1][1] is None or right[0][1] is None:
                return None
            ratio = (boundary - left[-1][0]).total_seconds() / (right[0][0] - left[-1][0]).total_seconds()
            points.append((boundary, left[-1][1] + (right[0][1] - left[-1][1]) * ratio))
        points.extend((at, value) for at, value in raw if start < at < end and value is not None)
        points.sort()
        if any(b[0] - a[0] > _MAX_GAP for a, b in zip(points, points[1:])):
            return None
        if any(start < at < end and value is None for at, value in raw):
            return None
        return points

    def _integral(self, start: datetime, end: datetime) -> float | None:
        points = self._bounded_points(start, end)
        if points is None or end <= start:
            return None
        return sum((da + db) * 0.5 * (b - a).total_seconds() / 3600
                   for (a, da), (b, db) in zip(points, points[1:]))

    def ingest(self, intervals: list[dict[str, object]], now: datetime) -> int:
        """Ingest historical half-hour gas intervals with complete demand data.

        Dictionaries require ``start``, ``end`` and ``consumption_kwh``. The
        interval end must not be later than ``now``. Return newly stored rows.
        """
        current = _time(now)
        if current is None or not isinstance(intervals, list):
            return 0
        inserted = 0
        for interval in intervals:
            if not isinstance(interval, dict):
                continue
            start, end = _time(interval.get("start")), _time(interval.get("end"))
            gas = _number(interval.get("consumption_kwh"))
            if (start is None or end is None or gas is None or gas < 0 or end > current
                    or not _MIN_DURATION <= end - start <= _MAX_DURATION
                    or start < current - _RETENTION):
                continue
            key = (start, end)
            # Completed historical demand does not change; only recalculate when
            # the supplier corrects its gas reading. Avoid rescanning two weeks
            # of observations for every already-ingested interval each minute.
            previous = self._samples.get(key)
            if previous is not None and previous[2] == gas:
                continue
            demand_hours = self._integral(start, end)
            if demand_hours is None:
                continue
            duration = (end - start).total_seconds() / 3600
            average_demand = demand_hours / duration
            row = (average_demand, duration, gas)
            if self._samples.get(key) != row:
                self._samples[key] = row
                inserted += 1
        self._prune(current)
        if inserted:
            self._fit_cache = None
            self._samples = dict(sorted(self._samples.items())[-_MAX_SAMPLES:])
        return inserted

    def _fit(self) -> dict[str, float | int | str | None]:
        if self._fit_cache is not None:
            return self._fit_cache
        rows = list(self._samples.values())
        count = len(rows)
        x_values = [row[0] for row in rows]
        heating = sum(x >= 0.10 for x in x_values)
        nonheating = sum(x <= 0.05 for x in x_values)
        dates = {start.date() for start, _ in self._samples}
        empty: dict[str, float | int | str | None] = {
            "sample_count": count, "heating_samples": heating, "nonheating_samples": nonheating,
            "distinct_dates": len(dates), "heat_kw": None, "baseline_kw": None,
            "fit_median_absolute_error_kw": None, "heating_fit_median_absolute_error_kw": None, "slope_mad_kw": None,
            "reason": None,
        }
        if count < _MIN_INTERVALS or len(dates) < 3 or heating < 6 or nonheating < 6:
            empty["reason"] = "insufficient_samples"
            self._fit_cache = empty
            return empty
        if max(x_values) - min(x_values) < _MIN_SPREAD:
            empty["reason"] = "insufficient_demand_spread"
            self._fit_cache = empty
            return empty
        ys = [gas / duration for _, duration, gas in rows]
        slopes = [(ys[j] - ys[i]) / (x_values[j] - x_values[i])
                  for i, j in combinations(range(count), 2)
                  if abs(x_values[j] - x_values[i]) >= 0.05]
        if not slopes:
            empty["reason"] = "insufficient_demand_spread"
            self._fit_cache = empty
            return empty
        slope = median(slopes)
        intercept = median(y - slope * x for x, y in zip(x_values, ys))
        residuals = [abs(y - (intercept + slope * x)) for x, y in zip(x_values, ys)]
        heating_residuals = [error for error, x in zip(residuals, x_values) if x >= 0.10]
        mae = median(residuals)
        heating_mae = median(heating_residuals)
        slope_mad = median(abs(value - slope) for value in slopes)
        empty["fit_median_absolute_error_kw"] = mae
        empty["heating_fit_median_absolute_error_kw"] = heating_mae
        empty["slope_mad_kw"] = slope_mad
        if slope <= 0 or not 1 <= slope <= 200 or intercept < 0:
            empty["reason"] = "unstable_fit"
            self._fit_cache = empty
            return empty
        if (mae > max(1.0, slope * 0.35)
                or heating_mae > max(0.5, slope * 0.25)
                or slope_mad > max(1.0, slope * 0.5)):
            empty["reason"] = "unstable_fit"
            self._fit_cache = empty
            return empty
        empty.update({"heat_kw": slope, "baseline_kw": intercept, "reason": None})
        self._fit_cache = empty
        return empty

    @property
    def gas_kwh_per_demand_hour(self) -> float | None:
        fit = self._fit()
        value = fit["heat_kw"]
        return float(value) if isinstance(value, (int, float)) else None

    def calibration(self) -> dict[str, float | int | str | None]:
        """Return fit diagnostics and the mixed-boiler attribution assumption."""
        result = dict(self._fit())
        observations = sorted(self._observations)
        result.update({
            "observation_count": len(observations),
            "first_observation": observations[0].isoformat() if observations else None,
            "last_observation": observations[-1].isoformat() if observations else None,
            "latest_interval_end": max((end for _, end in self._samples), default=None).isoformat()
                if self._samples else None,
        })
        result["assumption"] = "Estimated heating attribution; baseline gas may include hot water and boiler losses."
        return result

    def predict_day(
        self,
        start: datetime,
        end: datetime,
        now: datetime,
        forecast_points: list[dict[str, object]],
        rate_gbp_per_kwh: float,
    ) -> dict[str, object]:
        a, b, current = _time(start), _time(end), _time(now)
        rate = _number(rate_gbp_per_kwh)
        if current is not None:
            self._prune(current)
        fit = self._fit()
        common = {"sample_count": fit["sample_count"], "fit_median_absolute_error_kw": fit["fit_median_absolute_error_kw"]}
        if a is None or b is None or current is None or b <= a:
            return {"cost_gbp": None, "reason": "invalid_period", **common}
        if rate is None or rate < 0:
            return {"cost_gbp": None, "reason": "invalid_rate", **common}
        coefficient = fit["heat_kw"]
        if not isinstance(coefficient, (int, float)):
            return {"cost_gbp": None, "reason": fit["reason"], **common}
        elapsed_end = min(b, current)
        demand_hours = 0.0
        if elapsed_end > a:
            observed = self._integral(a, elapsed_end)
            if observed is None:
                return {"cost_gbp": None, "reason": "incomplete_observations", **common}
            demand_hours += observed
        future_start = max(a, current)
        if b > future_start:
            forecast = self._forecast_integral(future_start, b, forecast_points)
            if forecast is None:
                return {"cost_gbp": None, "reason": "incomplete_forecast", **common}
            demand_hours += forecast
        kwh = demand_hours * float(coefficient)
        return {"cost_gbp": round(kwh * rate, 2), "reason": None,
                "estimated_kwh": kwh, "demand_hours": demand_hours, **common,
                "attribution": "estimated_heating"}

    @staticmethod
    def _forecast_integral(start: datetime, end: datetime, points: list[dict[str, object]]) -> float | None:
        parsed: list[tuple[datetime, float]] = []
        if not isinstance(points, list):
            return None
        for point in points:
            if not isinstance(point, dict):
                continue
            at = _time(point.get("datetime", point.get("at")))
            demand = _number(point.get("heating_demand_percent"))
            if at is not None and demand is not None and 0 <= demand <= 100:
                parsed.append((at, demand / 100))
        parsed.sort()
        clipped = [(at, d) for at, d in parsed if start <= at <= end]
        for boundary in (start, end):
            if any(at == boundary for at, _ in clipped):
                continue
            before = [(at, d) for at, d in parsed if at < boundary]
            after = [(at, d) for at, d in parsed if at > boundary]
            if not before or not after or after[0][0] - before[-1][0] > _MAX_GAP:
                return None
            left, right = before[-1], after[0]
            ratio = (boundary - left[0]).total_seconds() / (right[0] - left[0]).total_seconds()
            clipped.append((boundary, left[1] + (right[1] - left[1]) * ratio))
        clipped.sort()
        if len(clipped) < 2 or clipped[0][0] != start or clipped[-1][0] != end:
            return None
        if any(b[0] - a[0] > _MAX_GAP for a, b in zip(clipped, clipped[1:])):
            return None
        return sum((da + db) * 0.5 * (b - a).total_seconds() / 3600
                   for (a, da), (b, db) in zip(clipped, clipped[1:]))

    def export_state(self) -> dict[str, object]:
        return {
            "version": 1,
            "observations": [{"at": at.isoformat(), "demand": demand}
                             for at, demand in sorted(self._observations.items())],
            "samples": [{"start": start.isoformat(), "end": end.isoformat(),
                         "average_demand": row[0], "duration_hours": row[1], "consumption_kwh": row[2]}
                        for (start, end), row in sorted(self._samples.items())],
        }

    @classmethod
    def restore_state(cls, state: object, now: datetime | None = None) -> HeatingCostModel:
        model = cls()
        current = _time(now) if now is not None else None
        if now is not None and current is None:
            return model
        if not isinstance(state, dict) or state.get("version") != 1:
            return model
        observations = state.get("observations")
        if isinstance(observations, list):
            for item in observations:
                if not isinstance(item, dict):
                    continue
                at, demand = _time(item.get("at")), _number(item.get("demand"))
                if at is None or (current is not None and not current - _RETENTION <= at <= current):
                    continue
                if demand is not None and not 0 <= demand <= 1:
                    continue
                model._observations[at] = demand
        samples = state.get("samples")
        if isinstance(samples, list):
            for item in samples:
                if not isinstance(item, dict):
                    continue
                start, end = _time(item.get("start")), _time(item.get("end"))
                x, duration, gas = (_number(item.get("average_demand")),
                                   _number(item.get("duration_hours")),
                                   _number(item.get("consumption_kwh")))
                if (start is None or end is None or x is None or duration is None or gas is None
                        or not 0 <= x <= 1 or not 0 < duration <= 1 or gas < 0
                        or not _MIN_DURATION.total_seconds() / 3600 <= duration <= _MAX_DURATION.total_seconds() / 3600
                        or end <= start or abs((end - start).total_seconds() / 3600 - duration) > 1e-6
                        or (current is not None and not current - _RETENTION <= end <= current)):
                    continue
                model._samples[(start, end)] = (x, duration, gas)
        model._samples = dict(sorted(model._samples.items())[-_MAX_SAMPLES:])
        return model
