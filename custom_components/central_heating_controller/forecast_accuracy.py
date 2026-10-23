"""Persistable scoring of issued temperature forecasts against later readings."""

from __future__ import annotations

from collections import deque
from datetime import datetime, timedelta, timezone
from math import isfinite, sqrt

from .forecasting import ForecastPoint

_HORIZONS = {"1h": timedelta(hours=1), "6h": timedelta(hours=6), "24h": timedelta(hours=24)}
_MATCH_TOLERANCE = timedelta(minutes=5)
_MAX_PENDING = 100
_SCORES_PER_HORIZON = 168


def _finite(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        number = float(value)
    except (OverflowError, TypeError, ValueError):
        return None
    return number if isfinite(number) else None


def _utc(value: object) -> datetime | None:
    if not isinstance(value, datetime) or value.tzinfo is None:
        return None
    try:
        if value.utcoffset() is None:
            return None
        return value.astimezone(timezone.utc)
    except Exception:
        return None


class ForecastAccuracyTracker:
    """Track bounded forecast cohorts and score them when fresh readings arrive.

    ``to_dict`` and ``from_dict`` expose a versioned JSON-compatible snapshot
    for the integration's persistent storage.
    """

    def __init__(self) -> None:
        self._pending: list[tuple[str, datetime, float]] = []
        # Store signed errors (actual minus predicted) with their observation time.
        self._scores: dict[str, deque[tuple[datetime, float]]] = {
            key: deque(maxlen=_SCORES_PER_HORIZON) for key in _HORIZONS
        }
        self._last_issue_hour: int | None = None
        self._last_observation: datetime | None = None

    @property
    def pending_count(self) -> int:
        """Number of forecasts currently awaiting a matching observation."""
        return len(self._pending)

    def issue(self, now: datetime, points: tuple[ForecastPoint, ...]) -> bool:
        """Record up to three horizons once per clock hour; return if accepted."""
        issued_at = _utc(now)
        if issued_at is None or not isinstance(points, tuple):
            return False
        hour = int(issued_at.timestamp() // 3600)
        if self._last_issue_hour is not None and hour <= self._last_issue_hour:
            return False
        candidates: list[tuple[str, datetime, float]] = []
        for name, horizon in _HORIZONS.items():
            desired = issued_at + horizon
            best: tuple[float, datetime, float] | None = None
            for point in points:
                point_at = _utc(point.at) if isinstance(point, ForecastPoint) else None
                temperature = _finite(point.indoor_temperature) if isinstance(point, ForecastPoint) else None
                if point_at is None or temperature is None:
                    continue
                difference = abs((point_at - desired).total_seconds())
                if difference <= _MATCH_TOLERANCE.total_seconds() and (best is None or difference < best[0]):
                    best = (difference, point_at, temperature)
            if best is not None:
                candidates.append((name, best[1], best[2]))
        self._last_issue_hour = hour
        self._pending.extend(candidates)
        if len(self._pending) > _MAX_PENDING:
            del self._pending[: len(self._pending) - _MAX_PENDING]
        return bool(candidates)

    def observe(self, now: datetime, current_c: float | None) -> bool:
        """Score only due forecasts using the timestamp of the actual reading."""
        observed_at = _utc(now)
        if observed_at is None:
            return False
        if self._last_observation is not None and observed_at <= self._last_observation:
            return False
        self._last_observation = observed_at
        actual = _finite(current_c)
        if actual is not None and not -100 <= actual <= 100:
            actual = None
        pending: list[tuple[str, datetime, float]] = []
        changed = False
        for horizon, target_at, predicted in self._pending:
            offset = observed_at - target_at
            if timedelta(0) <= offset <= _MATCH_TOLERANCE and actual is not None:
                self._scores[horizon].append((observed_at, actual - predicted))
                changed = True
            elif observed_at > target_at + _MATCH_TOLERANCE:
                changed = True
                continue
            else:
                pending.append((horizon, target_at, predicted))
        self._pending = pending
        return changed

    def metrics(self) -> dict[str, dict[str, float | int | None]]:
        """Return rolling MAE, signed bias, RMSE and sample count per horizon."""
        result: dict[str, dict[str, float | int | None]] = {}
        for horizon, samples in self._scores.items():
            errors = [error for _, error in samples]
            result[horizon] = {
                "mae_c": sum(abs(error) for error in errors) / len(errors) if errors else None,
                "bias_c": sum(errors) / len(errors) if errors else None,
                "rmse_c": sqrt(sum(error * error for error in errors) / len(errors)) if errors else None,
                "count": len(errors),
            }
        return result

    def readiness_metrics(self, now: datetime) -> dict[str, dict[str, float | int | str | None]]:
        """Return recent, non-future evidence used by the readiness gate.

        Only observations from the seven days ending at ``now`` are included.
        This method does not change the stored score cohorts, so callers can
        inspect a rolling window without affecting the existing lifetime-ish
        ``metrics()`` view or persisted data.
        """
        current = _utc(now)
        result: dict[str, dict[str, float | int | str | None]] = {}
        for horizon, samples in self._scores.items():
            recent = [
                (at, error)
                for at, error in samples
                if current is not None and current - timedelta(days=7) <= at <= current
            ]
            errors = [error for _, error in recent]
            times = [at for at, _ in recent]
            result[horizon] = {
                "mae_c": sum(abs(error) for error in errors) / len(errors) if errors else None,
                "bias_c": sum(errors) / len(errors) if errors else None,
                "rmse_c": sqrt(sum(error * error for error in errors) / len(errors)) if errors else None,
                "count": len(errors),
                "distinct_dates": len({at.date() for at in times}),
                "span_hours": (max(times) - min(times)).total_seconds() / 3600 if len(times) >= 2 else 0.0,
                "latest_at": max(times).isoformat() if times else None,
            }
        return result

    def to_dict(self) -> dict[str, object]:
        """Return a JSON-compatible snapshot with bounded pending and score data."""
        return {
            "version": 1,
            "pending": [
                {"horizon": horizon, "target_at": target_at.isoformat(), "predicted_c": predicted}
                for horizon, target_at, predicted in self._pending
            ],
            "scores": {
                horizon: [{"observed_at": at.isoformat(), "error_c": error} for at, error in samples]
                for horizon, samples in self._scores.items()
            },
            "last_issue_hour": self._last_issue_hour,
            "last_observation": self._last_observation.isoformat() if self._last_observation else None,
        }

    @classmethod
    def from_dict(cls, value: object, *, now: datetime | None = None) -> ForecastAccuracyTracker:
        """Restore a validated snapshot; optionally discard entries stale at ``now``.

        Unknown versions and malformed records are ignored safely. A supplied
        ``now`` also rejects future-dated records and pending forecasts whose
        scoring window has already passed.
        """
        tracker = cls()
        current = _utc(now) if now is not None else None
        if now is not None and current is None:
            return tracker
        if not isinstance(value, dict) or value.get("version") != 1:
            return tracker
        last_hour = value.get("last_issue_hour")
        if isinstance(last_hour, int) and not isinstance(last_hour, bool):
            if current is None or last_hour <= int(current.timestamp() // 3600):
                tracker._last_issue_hour = last_hour
        last_observation = _parse_datetime(value.get("last_observation"))
        if last_observation is not None and (current is None or last_observation <= current):
            tracker._last_observation = last_observation

        raw_pending = value.get("pending")
        if isinstance(raw_pending, list):
            for record in raw_pending[-_MAX_PENDING:]:
                if not isinstance(record, dict):
                    continue
                horizon, target, predicted = record.get("horizon"), _parse_datetime(record.get("target_at")), _finite(record.get("predicted_c"))
                if not isinstance(horizon, str) or horizon not in _HORIZONS or target is None or predicted is None:
                    continue
                if not -100 <= predicted <= 100:
                    continue
                if current is not None and (target > current + timedelta(hours=24) + _MATCH_TOLERANCE or target + _MATCH_TOLERANCE < current):
                    continue
                tracker._pending.append((horizon, target, predicted))

        raw_scores = value.get("scores")
        if isinstance(raw_scores, dict):
            for horizon in _HORIZONS:
                records = raw_scores.get(horizon)
                if not isinstance(records, list):
                    continue
                for record in records[-_SCORES_PER_HORIZON:]:
                    if not isinstance(record, dict):
                        continue
                    at, error = _parse_datetime(record.get("observed_at")), _finite(record.get("error_c"))
                    if at is None or error is None or abs(error) > 200 or (current is not None and at > current):
                        continue
                    tracker._scores[horizon].append((at, error))
        return tracker


def _parse_datetime(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        return _utc(datetime.fromisoformat(value))
    except (TypeError, ValueError, OverflowError):
        return None
