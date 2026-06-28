"""Adaptive heating-rate learning without Home Assistant dependencies."""

from datetime import datetime, timedelta
from math import ceil, isfinite

_MINIMUM_SAMPLE_DURATION = timedelta(minutes=15)
_MAXIMUM_RATE = 10.0
_TRUSTED_SAMPLE_COUNT = 3
_NEW_SAMPLE_WEIGHT = 0.3


def _finite_number(value: object) -> bool:
    """Return whether value is a finite real number, excluding booleans."""
    return not isinstance(value, bool) and isinstance(value, int | float) and isfinite(value)


def _aware_datetime(value: object) -> bool:
    """Return whether value is a timezone-aware datetime."""
    if not isinstance(value, datetime) or value.tzinfo is None:
        return False
    try:
        return value.utcoffset() is not None
    except OverflowError, ValueError:
        return False


class HeatingRateLearner:
    """Learn a smoothed temperature-rise rate from non-overlapping heating windows."""

    def __init__(self, rate: float | None = None, sample_count: int = 0) -> None:
        if self._valid_initial_state(rate, sample_count):
            self.rate = float(rate) if rate is not None else None
            self.sample_count = sample_count
        else:
            self.rate = None
            self.sample_count = 0
        self._window_started_at: datetime | None = None
        self._window_temperature: float | None = None

    @staticmethod
    def _valid_initial_state(rate: float | None, sample_count: int) -> bool:
        if isinstance(sample_count, bool) or not isinstance(sample_count, int) or sample_count < 0:
            return False
        if rate is None:
            return sample_count == 0
        return _finite_number(rate) and rate > 0 and sample_count > 0

    @property
    def trusted(self) -> bool:
        """Return whether enough valid samples exist to use the learned model."""
        return (
            isinstance(self.sample_count, int)
            and not isinstance(self.sample_count, bool)
            and self.sample_count >= _TRUSTED_SAMPLE_COUNT
            and _finite_number(self.rate)
            and self.rate > 0
        )

    def reset(self) -> None:
        """Clear the learned model and any in-progress observation window."""
        self.rate = None
        self.sample_count = 0
        self._reset_window()

    def _reset_window(self) -> None:
        self._window_started_at = None
        self._window_temperature = None

    def observe(
        self,
        now: datetime,
        current_temperature: float | None,
        target_temperature: float | None,
        heating: bool,
    ) -> float | None:
        """Observe active heating and return an updated rate when a sample is accepted."""
        if (
            not heating
            or not _aware_datetime(now)
            or not _finite_number(current_temperature)
            or not _finite_number(target_temperature)
            or current_temperature >= target_temperature
        ):
            self._reset_window()
            return None

        current = float(current_temperature)
        if self._window_started_at is None:
            self._window_started_at = now
            self._window_temperature = current
            return None

        if now <= self._window_started_at:
            self._reset_window()
            return None

        elapsed = now - self._window_started_at
        if elapsed < _MINIMUM_SAMPLE_DURATION:
            return None

        baseline = self._window_temperature
        self._reset_window()
        if baseline is None:
            return None

        sample_rate = (current - baseline) / (elapsed.total_seconds() / 3600)
        if sample_rate <= 0 or sample_rate > _MAXIMUM_RATE:
            return None

        if self.rate is None or not _finite_number(self.rate) or self.rate <= 0:
            self.rate = sample_rate
        else:
            self.rate = _NEW_SAMPLE_WEIGHT * sample_rate + (1 - _NEW_SAMPLE_WEIGHT) * self.rate
        self.sample_count += 1
        return self.rate

    def warmup_minutes(
        self,
        current_temperature: float | None,
        high_temperature: float | None,
        fallback_minutes: float,
        maximum_minutes: float,
    ) -> float | int:
        """Calculate warmup time, using a bounded fallback until the model is trusted."""
        if (
            not _finite_number(fallback_minutes)
            or not _finite_number(maximum_minutes)
            or fallback_minutes < 0
            or maximum_minutes < 0
            or fallback_minutes > maximum_minutes
        ):
            raise ValueError("durations must be finite and nonnegative, with fallback <= maximum")

        fallback = min(fallback_minutes, maximum_minutes)
        if not _finite_number(current_temperature) or not _finite_number(high_temperature):
            return fallback
        if current_temperature >= high_temperature:
            return 0
        if not self.trusted:
            return fallback

        minutes = ceil((high_temperature - current_temperature) / self.rate * 60)
        return min(max(minutes, 0), maximum_minutes)

    def to_dict(self) -> dict[str, float | int | None]:
        """Return only the state needed to persist the learned model."""
        return {"rate": self.rate, "sample_count": self.sample_count}
