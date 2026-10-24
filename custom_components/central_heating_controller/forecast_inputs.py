"""Home Assistant adapters for weather and schedule forecast inputs."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, time, timedelta, timezone
from math import isfinite
from typing import Any
from zoneinfo import ZoneInfo

from homeassistant.util import dt as dt_util

_CACHE_SECONDS = 15 * 60
_RETRY_SECONDS = 5 * 60
_SERVICE_TIMEOUT = 10
_MIN_COVERAGE = timedelta(hours=24)
_MIN_ACCEPTED_COVERAGE = timedelta(hours=23)
_HOLD_LIMIT = timedelta(hours=1)
_CORRECTION_DECAY = timedelta(hours=3)
_WEEKDAYS = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")


def _number(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        return None
    try:
        number = float(value)
    except ValueError, OverflowError:
        return None
    return number if isfinite(number) else None


def _datetime(value: object) -> datetime | None:
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
    except ValueError, OverflowError:
        return None


def _celsius(value: object, unit: object) -> float | None:
    number = _number(value)
    if number is None:
        return None
    if unit in ("°C", "C", "celsius"):
        return number
    if unit in ("°F", "F", "fahrenheit"):
        return (number - 32) * 5 / 9
    return None


def _time(value: object) -> time | None:
    if isinstance(value, time):
        return value if value.tzinfo is None else None
    if isinstance(value, str):
        if value in ("24:00", "24:00:00"):
            return time.max
        try:
            parsed = time.fromisoformat(value)
            return parsed if parsed.tzinfo is None else None
        except ValueError:
            return None
    return None


def _schedule_response(value: object) -> dict | None:
    """Unwrap the response forms returned by Home Assistant Schedule."""
    if not isinstance(value, dict):
        return None
    if len(value) == 1 and isinstance(value.get("response"), dict):
        value = value["response"]
    return value if any(day in value for day in _WEEKDAYS) else None


def _display_time(value: object) -> str | None:
    """Format a validated schedule time, retaining the explicit 24:00 end."""
    if value in ("24:00", "24:00:00") or value == time.max:
        return "24:00"
    parsed = _time(value)
    return parsed.strftime("%H:%M") if parsed is not None else None


@dataclass(frozen=True)
class ForecastInputsResult:
    """Validated callable inputs, or a reason explaining why forecasting is unavailable."""

    outdoor_temperature: Any | None
    schedule_high: Any | None
    current_outdoor_temperature: float | None = None
    assumptions: tuple[str, ...] = ()
    reason: str | None = None

    @property
    def available(self) -> bool:
        return (
            self.reason is None
            and self.outdoor_temperature is not None
            and self.schedule_high is not None
        )


class ForecastInputs:
    """Fetch and cache weather and schedule service responses for forecasts."""

    def __init__(self, hass: Any) -> None:
        self.hass = hass
        self._cache: dict[tuple[str, str], tuple[float, object]] = {}
        self._failed_at: dict[tuple[str, str], float] = {}
        self._failure: dict[tuple[str, str], str] = {}
        self.revision = 0

    def invalidate_schedule(self, entity_id: str) -> None:
        """Refresh edited schedules immediately instead of waiting for the cache."""
        key = ("schedule", entity_id)
        self._cache.pop(key, None)
        self._failed_at.pop(key, None)
        self._failure.pop(key, None)
        self.revision += 1

    def cached_schedule(self, entity_id: str) -> dict[str, list[dict[str, str]]] | None:
        """Return a safe, start/end-only view of the last successful schedule response."""
        cached = self._cache.get(("schedule", entity_id))
        if cached is None:
            return None
        raw_response = cached[1]
        if isinstance(raw_response, dict) and isinstance(raw_response.get(entity_id), dict):
            raw_response = raw_response[entity_id]
        response = _schedule_response(raw_response)
        if response is None:
            return None
        result: dict[str, list[dict[str, str]]] = {day: [] for day in _WEEKDAYS}
        for day in _WEEKDAYS:
            rows = response.get(day, [])
            if not isinstance(rows, list):
                return None
            for row in rows:
                if not isinstance(row, dict):
                    return None
                start, end = row.get("from"), row.get("to")
                parsed_start, parsed_end = _time(start), _time(end)
                start_text, end_text = _display_time(start), _display_time(end)
                if (parsed_start is None or parsed_end is None or parsed_end <= parsed_start
                        or start_text is None or end_text is None):
                    return None
                result[day].append({"start": start_text, "end": end_text})
        return result

    async def _service(
        self, domain: str, service: str, entity_id: str, data: dict[str, object]
    ) -> object:
        key = (domain, entity_id)
        now = asyncio.get_running_loop().time()
        cached = self._cache.get(key)
        if cached and now - cached[0] < _CACHE_SECONDS:
            return cached[1]
        if key in self._failed_at and now - self._failed_at[key] < _RETRY_SECONDS:
            if cached and now - cached[0] < _CACHE_SECONDS + _RETRY_SECONDS:
                return cached[1]
            raise ValueError(self._failure[key])
        try:
            response = await asyncio.wait_for(
                self.hass.services.async_call(
                    domain,
                    service,
                    {"entity_id": entity_id, **data},
                    blocking=True,
                    return_response=True,
                ),
                timeout=_SERVICE_TIMEOUT,
            )
        except Exception as err:
            self._failed_at[key] = now
            self._failure[key] = f"{domain}.{service} failed: {err}"
            # Keep a previous successful response briefly if the refresh failed.
            if cached and now - cached[0] < _CACHE_SECONDS + _RETRY_SECONDS:
                return cached[1]
            raise ValueError(self._failure[key]) from err
        self._cache[key] = (now, response)
        self.revision += 1
        self._failed_at.pop(key, None)
        self._failure.pop(key, None)
        return response

    async def async_load(
        self, now: datetime, weather_entity: str, outdoor_entity: str, schedule_entity: str
    ) -> ForecastInputsResult:
        """Load forecast functions. Invalid inputs return a reason without raising."""
        stamp = _datetime(now)
        if stamp is None:
            return ForecastInputsResult(None, None, reason="Forecast start time is invalid")
        try:
            weather, schedule = await asyncio.gather(
                self._service("weather", "get_forecasts", weather_entity, {"type": "hourly"}),
                self._service("schedule", "get_schedule", schedule_entity, {}),
            )
            # Some HA versions wrap each entity's response in an entity-id key.
            if (
                isinstance(weather, dict)
                and "forecast" not in weather
                and isinstance(weather.get(weather_entity), dict)
            ):
                weather = weather[weather_entity]
            if isinstance(schedule, dict) and isinstance(schedule.get(schedule_entity), dict):
                schedule = schedule[schedule_entity]
            forecasts = weather.get("forecast") if isinstance(weather, dict) else None
            if not isinstance(forecasts, list):
                raise ValueError("Weather service returned no hourly forecast")
            state = self.hass.states.get(outdoor_entity)
            if state is None:
                raise ValueError("Outdoor temperature entity is unavailable")
            reported = getattr(state, "last_reported", None)
            if isinstance(reported, datetime) and stamp - reported > timedelta(hours=2):
                raise ValueError("Outdoor sensor has not reported for over two hours")
            current = _celsius(state.state, state.attributes.get("unit_of_measurement"))
            if current is None:
                raise ValueError("Outdoor temperature is not a finite Celsius or Fahrenheit value")
            weather_state = self.hass.states.get(weather_entity)
            if weather_state is None:
                raise ValueError("Weather entity is unavailable")
            forecast_unit = weather_state.attributes.get("temperature_unit")
            if forecast_unit is None:
                forecast_unit = self.hass.config.units.temperature_unit
            points: list[tuple[datetime, float]] = []
            for item in forecasts:
                if not isinstance(item, dict):
                    raise ValueError("Hourly weather forecast contains a malformed point")
                at = _datetime(item.get("datetime"))
                # HA's weather service converts forecasts to the configured
                # system unit; the outdoor sensor can use a different unit.
                temp = _celsius(item.get("temperature"), forecast_unit)
                if at is None or temp is None:
                    raise ValueError(
                        "Hourly weather forecast has an invalid timestamp or temperature"
                    )
                points.append((at, temp))
            points.sort(key=lambda point: point[0])
            if len(points) < 2 or any(b[0] <= a[0] for a, b in zip(points, points[1:])):
                raise ValueError(
                    "Hourly weather forecast needs at least two ordered, unique points"
                )
            if points[0][0] - stamp > _HOLD_LIMIT:
                raise ValueError("Hourly weather forecast starts more than one hour in the future")
            if any(b[0] - a[0] > timedelta(hours=2) for a, b in zip(points, points[1:])):
                raise ValueError("Hourly weather forecast has a gap longer than two hours")
            end = points[-1][0]
            if end < stamp + _MIN_ACCEPTED_COVERAGE:
                raise ValueError("Hourly weather forecast does not cover the next 24 hours")

            def baseline(at: datetime) -> float:
                if at <= points[0][0]:
                    return points[0][1]
                for (left_at, left_temp), (right_at, right_temp) in zip(points, points[1:]):
                    if left_at <= at <= right_at:
                        fraction = (at - left_at).total_seconds() / (
                            right_at - left_at
                        ).total_seconds()
                        return left_temp + fraction * (right_temp - left_temp)
                return points[-1][1]

            correction = current - baseline(stamp)

            def outdoor(at: datetime) -> float:
                instant = _datetime(at)
                if instant is None:
                    raise ValueError("Outdoor forecast requested at an invalid time")
                if instant <= points[0][0]:
                    base = points[0][1]
                elif instant >= end:
                    if instant - end > _HOLD_LIMIT:
                        raise ValueError("Outdoor forecast exceeds its coverage")
                    base = points[-1][1]
                else:
                    for (left_at, left_temp), (right_at, right_temp) in zip(points, points[1:]):
                        if left_at <= instant <= right_at:
                            fraction = (instant - left_at).total_seconds() / (
                                right_at - left_at
                            ).total_seconds()
                            base = left_temp + fraction * (right_temp - left_temp)
                            break
                age = max(0.0, (instant - stamp).total_seconds())
                decay = max(0.0, 1 - age / _CORRECTION_DECAY.total_seconds())
                return base + correction * decay

            target = self._schedule_callable(schedule)
            assumptions = (
                ("Held the final weather value for up to one hour beyond the final forecast point",)
                if end < stamp + _MIN_COVERAGE
                else ()
            )
            return ForecastInputsResult(outdoor, target, current, assumptions)
        except (ValueError, AttributeError, TypeError, KeyError) as err:
            return ForecastInputsResult(None, None, reason=str(err))

    def _schedule_callable(self, response: object):
        if not isinstance(response, dict):
            raise ValueError("Schedule service returned malformed data")
        response = _schedule_response(response)
        if response is None:
            raise ValueError("Schedule service returned no weekday data")
        blocks: dict[int, list[tuple[time, time]]] = {}
        for day_index, day in enumerate(_WEEKDAYS):
            rows = response.get(day, [])
            if not isinstance(rows, list):
                raise ValueError(f"Schedule {day} data is malformed")
            blocks[day_index] = []
            for row in rows:
                if not isinstance(row, dict):
                    raise ValueError(f"Schedule {day} contains a malformed block")
                start, end = _time(row.get("from")), _time(row.get("to"))
                data = row.get("data", {})
                if start is None or end is None or end <= start:
                    raise ValueError(f"Schedule {day} block needs valid from/to times")
                if not isinstance(data, dict):
                    raise ValueError(f"Schedule {day} block data is malformed")
                blocks[day_index].append((start, end))

        timezone_info = (
            ZoneInfo(self.hass.config.time_zone)
            if self.hass.config.time_zone
            else dt_util.DEFAULT_TIME_ZONE
        )

        def target(at: datetime) -> bool | None:
            if _datetime(at) is None:
                return None
            local = at.astimezone(timezone_info)
            for start, end in blocks[local.weekday()]:
                start_dt = datetime.combine(local.date(), start, tzinfo=timezone_info)
                end_date = local.date() + timedelta(days=1) if end == time.max else local.date()
                end_dt = datetime.combine(
                    end_date, time.min if end == time.max else end, tzinfo=timezone_info
                )
                if start_dt <= local < end_dt:
                    return True
            return False

        return target
