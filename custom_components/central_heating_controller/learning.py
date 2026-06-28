"""Adaptive heating-rate learning without Home Assistant dependencies."""

from datetime import datetime, timedelta, timezone
from math import ceil, isfinite

_MINIMUM_SAMPLE_DURATION = timedelta(minutes=15)
_MAXIMUM_RATE = 10.0
_TRUSTED_SAMPLE_COUNT = 3
_NEW_SAMPLE_WEIGHT = 0.3


def _finite_number(value: object) -> bool:
    """Return whether value is a finite real number, excluding booleans."""
    if isinstance(value, bool):
        return False
    if isinstance(value, int):
        return True
    if not isinstance(value, float):
        return False
    try:
        return isfinite(value)
    except OverflowError, TypeError, ValueError:
        return False


def _finite_float(value: object) -> float | None:
    """Return value as a finite float, or None when it cannot be represented."""
    if not _finite_number(value):
        return None
    try:
        converted = float(value)
    except OverflowError, TypeError, ValueError:
        return None
    return converted if isfinite(converted) else None


def _normalize_timestamp(value: object) -> datetime | None:
    """Return an aware timestamp in UTC, or None when it is unusable."""
    if not isinstance(value, datetime) or value.tzinfo is None:
        return None
    try:
        if value.utcoffset() is None:
            return None
        return value.astimezone(timezone.utc)
    except OverflowError, TypeError, ValueError:
        return None


class HeatingRateLearner:
    """Learn a smoothed temperature-rise rate from non-overlapping heating windows."""

    def __init__(self, rate: float | None = None, sample_count: int = 0) -> None:
        if self._valid_initial_state(rate, sample_count):
            self._rate = float(rate) if rate is not None else None
            self._sample_count = sample_count
        else:
            self._rate = None
            self._sample_count = 0
        self._window_started_at: datetime | None = None
        self._last_observed_at: datetime | None = None
        self._window_temperature: float | None = None
        self._last_observed_temperature: float | None = None

    @staticmethod
    def _valid_initial_state(rate: float | None, sample_count: int) -> bool:
        if isinstance(sample_count, bool) or not isinstance(sample_count, int) or sample_count < 0:
            return False
        if rate is None:
            return sample_count == 0
        return _finite_number(rate) and 0 < rate <= _MAXIMUM_RATE and sample_count > 0

    @property
    def rate(self) -> float | None:
        """Return the current learned heating rate."""
        return self._rate

    @property
    def sample_count(self) -> int:
        """Return the number of accepted heating samples."""
        return self._sample_count

    @property
    def trusted(self) -> bool:
        """Return whether enough valid samples exist to use the learned model."""
        return (
            self._sample_count >= _TRUSTED_SAMPLE_COUNT
            and _finite_number(self._rate)
            and self._rate > 0
        )

    def reset(self) -> None:
        """Clear the learned model and any in-progress observation window."""
        self._rate = None
        self._sample_count = 0
        self._reset_window()

    def _reset_window(self) -> None:
        self._window_started_at = None
        self._last_observed_at = None
        self._window_temperature = None
        self._last_observed_temperature = None

    def observe(
        self,
        now: datetime,
        current_temperature: float | None,
        target_temperature: float | None,
        heating: bool,
    ) -> float | None:
        """Observe active heating and return an updated rate when a sample is accepted."""
        observed_at = _normalize_timestamp(now)
        current = _finite_float(current_temperature)
        target = _finite_float(target_temperature)
        if (
            not heating
            or observed_at is None
            or current is None
            or target is None
            or current >= target
        ):
            self._reset_window()
            return None

        if self._window_started_at is None:
            self._window_started_at = observed_at
            self._last_observed_at = observed_at
            self._window_temperature = current
            self._last_observed_temperature = current
            return None

        if self._last_observed_at is None or observed_at <= self._last_observed_at:
            self._reset_window()
            return None
        if self._last_observed_temperature is None or current < self._last_observed_temperature:
            self._reset_window()
            return None

        elapsed = observed_at - self._window_started_at
        if elapsed < _MINIMUM_SAMPLE_DURATION:
            self._last_observed_at = observed_at
            self._last_observed_temperature = current
            return None

        baseline = self._window_temperature
        self._reset_window()
        if baseline is None:
            return None

        sample_rate = (current - baseline) / (elapsed.total_seconds() / 3600)
        if sample_rate <= 0 or sample_rate > _MAXIMUM_RATE:
            return None

        if self._rate is None:
            self._rate = sample_rate
        else:
            self._rate = _NEW_SAMPLE_WEIGHT * sample_rate + (1 - _NEW_SAMPLE_WEIGHT) * self._rate
        self._sample_count += 1
        return self._rate

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
        current = _finite_float(current_temperature)
        high = _finite_float(high_temperature)
        if current is None or high is None:
            return fallback
        if current >= high:
            return 0
        if not self.trusted:
            return fallback

        try:
            calculated_minutes = (high - current) / self._rate * 60
        except OverflowError:
            return maximum_minutes
        if not _finite_number(calculated_minutes):
            return maximum_minutes
        minutes = ceil(calculated_minutes)
        return min(max(minutes, 0), maximum_minutes)

    def to_dict(self) -> dict[str, float | int | None]:
        """Return only the state needed to persist the learned model."""
        return {"rate": self._rate, "sample_count": self._sample_count}
