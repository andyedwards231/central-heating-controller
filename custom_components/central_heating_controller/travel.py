"""Pure journey matching and preheating calculations."""

from dataclasses import dataclass
from datetime import datetime, timedelta, tzinfo
import math
import re

from homeassistant.util import dt as dt_util

_DESTINATION_SEPARATORS = re.compile(r"[\s_-]+")
_INVALID_DESTINATIONS = frozenset({"", "unknown", "unavailable"})


def _normalize_destination(value: str | None) -> str | None:
    """Normalize a destination value for comparison."""
    if not isinstance(value, str):
        return None

    normalized = value.strip().casefold()
    if normalized.startswith("zone."):
        normalized = normalized.removeprefix("zone.")
    return _DESTINATION_SEPARATORS.sub("_", normalized)


def destination_is_home(
    destination: str | None,
    zone_entity_id: str,
    zone_friendly_name: str | None,
    exact_override: str | None,
) -> bool:
    """Return whether a destination identifies the configured home zone."""
    normalized_destination = _normalize_destination(destination)
    if normalized_destination is None or normalized_destination in _INVALID_DESTINATIONS:
        return False

    zone_object_id = zone_entity_id.split(".", 1)[-1]
    candidates = (
        zone_entity_id,
        zone_object_id,
        zone_friendly_name,
        exact_override,
    )
    return normalized_destination in {
        normalized
        for candidate in candidates
        if (normalized := _normalize_destination(candidate)) not in _INVALID_DESTINATIONS
        and normalized is not None
    }


def _valid_timezone(value: object) -> bool:
    """Return whether a value can be used as a datetime timezone."""
    if not isinstance(value, tzinfo):
        return False
    try:
        probe = datetime(2000, 1, 1, tzinfo=value)
        return probe.utcoffset() is not None
    except Exception:
        return False


def parse_arrival_time(raw: object, local_tz: tzinfo | None) -> datetime | None:
    """Parse a journey arrival value and return it in UTC."""
    parsed: datetime | None

    if isinstance(raw, bool):
        return None
    if isinstance(raw, (int, float)):
        if isinstance(raw, float) and not math.isfinite(raw):
            return None
        try:
            parsed = dt_util.utc_from_timestamp(raw)
        except OverflowError, OSError, ValueError:
            return None
    elif isinstance(raw, str):
        value = raw.strip()
        if not value or value.casefold() in {"unknown", "unavailable"}:
            return None
        if value.isdigit():
            try:
                parsed = dt_util.utc_from_timestamp(int(value))
            except OverflowError, OSError, ValueError:
                return None
        else:
            try:
                parsed = dt_util.parse_datetime(value)
            except ValueError:
                return None
            if parsed is None:
                return None
    else:
        return None

    if parsed.tzinfo is None or parsed.utcoffset() is None:
        if not _valid_timezone(local_tz):
            return None
        parsed = parsed.replace(tzinfo=local_tz)

    try:
        return dt_util.as_utc(parsed)
    except Exception:
        return None


@dataclass(frozen=True)
class PreheatTiming:
    """Result of evaluating an arrival against its preheat window."""

    ready: bool
    arrival: datetime | None
    start: datetime | None


def preheat_timing(
    raw_arrival: object,
    now: datetime,
    warmup_minutes: int,
    local_tz: tzinfo | None,
) -> PreheatTiming:
    """Calculate whether preheating should start for an arrival value."""
    if warmup_minutes < 0:
        raise ValueError("warmup_minutes must not be negative")
    if not _valid_timezone(local_tz):
        raise ValueError("local_tz must be a valid timezone")
    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware")

    try:
        now_offset = now.utcoffset()
    except Exception as err:
        raise ValueError("now must have a valid timezone") from err
    if now_offset is None:
        raise ValueError("now must have a valid timezone")
    try:
        now_utc = dt_util.as_utc(now)
    except Exception as err:
        raise ValueError("now must have a valid timezone") from err

    arrival = parse_arrival_time(raw_arrival, local_tz)
    if arrival is None or arrival <= now_utc:
        return PreheatTiming(True, None, None)

    start = arrival - timedelta(minutes=warmup_minutes)
    return PreheatTiming(now_utc >= start, arrival, start)
