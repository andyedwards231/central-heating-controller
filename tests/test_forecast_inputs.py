"""Tests for Home Assistant weather and weekly schedule adapters."""

import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from custom_components.central_heating_controller.forecast_inputs import ForecastInputs


class FakeServices:
    def __init__(self, weather, schedule):
        self.weather = weather
        self.schedule = schedule
        self.calls = 0
        self.fail = False

    async def async_call(self, domain, service, data, **kwargs):
        self.calls += 1
        if self.fail:
            raise RuntimeError("temporary failure")
        return self.weather if domain == "weather" else self.schedule


class FakeStates:
    def get(self, entity_id):
        if entity_id.startswith("weather."):
            return SimpleNamespace(state="", attributes={"temperature_unit": "°C"})
        return SimpleNamespace(state="50", attributes={"unit_of_measurement": "°F"})


def _inputs(weather, schedule, *, timezone_name="Europe/London"):
    hass = SimpleNamespace(
        services=FakeServices(weather, schedule),
        states=FakeStates(),
        config=SimpleNamespace(
            time_zone=timezone_name, units=SimpleNamespace(temperature_unit="°C")
        ),
    )
    return ForecastInputs(hass), hass.services


def _schedule(**days):
    result = {
        name: []
        for name in ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")
    }
    result.update(days)
    return result


@pytest.mark.asyncio
async def test_interpolates_weather_and_corrects_to_live_outdoor_reading():
    now = datetime(2025, 1, 6, 12, tzinfo=timezone.utc)
    weather = {
        "forecast": [
            {"datetime": (now + timedelta(hours=h)).isoformat(), "temperature": float(h)}
            for h in range(0, 26)
        ]
    }
    adapter, _ = _inputs(weather, _schedule())
    result = await adapter.async_load(now, "weather.home", "sensor.outdoor", "schedule.heating")
    assert result.available
    # 50°F is 10°C, so the initial forecast is corrected from 0°C to 10°C.
    assert result.outdoor_temperature(now) == pytest.approx(10)
    # The correction fades linearly to zero over three hours.
    assert result.outdoor_temperature(now + timedelta(hours=1, minutes=30)) == pytest.approx(6.5)
    assert result.outdoor_temperature(now + timedelta(hours=4)) == pytest.approx(4)
    assert result.current_outdoor_temperature == pytest.approx(10)
    assert result.schedule_high(now) is False


@pytest.mark.asyncio
async def test_schedule_uses_local_weekday_end_exclusive_and_24_hour_end():
    now = datetime(2025, 1, 6, 12, tzinfo=timezone.utc)  # Monday, London local time
    weather = {
        "forecast": [
            {"datetime": (now + timedelta(hours=h)).isoformat(), "temperature": 5}
            for h in range(26)
        ]
    }
    schedule = _schedule(monday=[{"from": "12:00:00", "to": "24:00", "data": {}}])
    adapter, _ = _inputs(weather, schedule)
    result = await adapter.async_load(now, "weather.home", "sensor.outdoor", "schedule.heating")
    assert result.available
    assert result.schedule_high(now) is True
    assert result.schedule_high(datetime(2025, 1, 6, 23, 59, tzinfo=timezone.utc)) is True
    assert result.schedule_high(datetime(2025, 1, 7, 0, 0, tzinfo=timezone.utc)) is False


@pytest.mark.asyncio
async def test_accepts_23_hour_forecast_with_explicit_tail_hold_assumption():
    now = datetime(2025, 1, 6, 12, tzinfo=timezone.utc)
    weather = {
        "forecast": [
            {"datetime": (now + timedelta(hours=h)).isoformat(), "temperature": 5}
            for h in range(24)
        ]
    }
    adapter, _ = _inputs(weather, _schedule())
    result = await adapter.async_load(now, "weather.home", "sensor.outdoor", "schedule.heating")
    assert result.available
    assert result.assumptions
    assert result.outdoor_temperature(now + timedelta(hours=24)) == pytest.approx(5)


@pytest.mark.asyncio
async def test_invalid_forecast_returns_reason():
    now = datetime(2025, 1, 6, 12, tzinfo=timezone.utc)
    adapter, _ = _inputs({"forecast": []}, _schedule())
    result = await adapter.async_load(now, "weather.home", "sensor.outdoor", "schedule.heating")
    assert not result.available
    assert result.reason is not None
    assert result.current_outdoor_temperature is None
    assert "hourly weather forecast" in result.reason.lower()


@pytest.mark.asyncio
async def test_uses_weather_entity_unit_and_accepts_long_forecast():
    now = datetime(2025, 1, 6, 12, tzinfo=timezone.utc)
    weather = {
        "forecast": [
            {
                "datetime": (now + timedelta(minutes=30 + 60 * h)).isoformat(),
                "temperature": 50 + h * 1.8,
            }
            for h in range(48)
        ]
    }
    adapter, _ = _inputs(weather, _schedule())
    weather_state = adapter.hass.states.get
    adapter.hass.states.get = lambda entity_id: (
        SimpleNamespace(state="", attributes={"temperature_unit": "°F"})
        if entity_id == "weather.home"
        else weather_state(entity_id)
    )
    result = await adapter.async_load(now, "weather.home", "sensor.outdoor", "schedule.heating")
    assert result.available
    # Forecast baseline is 10°C at the first point, interpolated at now.
    assert result.outdoor_temperature(now) == pytest.approx(10)


@pytest.mark.asyncio
async def test_rejects_forecast_starting_more_than_one_hour_later():
    now = datetime(2025, 1, 6, 12, tzinfo=timezone.utc)
    weather = {
        "forecast": [
            {"datetime": (now + timedelta(hours=2 + h)).isoformat(), "temperature": 5}
            for h in range(26)
        ]
    }
    adapter, _ = _inputs(weather, _schedule())
    result = await adapter.async_load(now, "weather.home", "sensor.outdoor", "schedule.heating")
    assert not result.available
    assert "starts more than one hour" in result.reason


@pytest.mark.asyncio
async def test_schedule_uses_local_dst_transition_instants():
    # On the 2025 spring transition, London jumps from 01:00 GMT to 02:00 BST.
    now = datetime(2025, 3, 30, 0, tzinfo=timezone.utc)
    weather = {
        "forecast": [
            {"datetime": (now + timedelta(hours=h)).isoformat(), "temperature": 5}
            for h in range(26)
        ]
    }
    schedule = _schedule(sunday=[{"from": "01:30", "to": "03:30", "data": {}}])
    adapter, _ = _inputs(weather, schedule)
    result = await adapter.async_load(now, "weather.home", "sensor.outdoor", "schedule.heating")
    assert result.available
    assert result.schedule_high(datetime(2025, 3, 30, 1, 0, tzinfo=timezone.utc)) is True
    assert result.schedule_high(datetime(2025, 3, 30, 2, 30, tzinfo=timezone.utc)) is False


@pytest.mark.asyncio
async def test_empty_schedule_response_without_weekdays_is_rejected():
    now = datetime(2025, 1, 6, 12, tzinfo=timezone.utc)
    weather = {
        "forecast": [
            {"datetime": (now + timedelta(hours=h)).isoformat(), "temperature": 5}
            for h in range(26)
        ]
    }
    adapter, _ = _inputs(weather, {})
    result = await adapter.async_load(now, "weather.home", "sensor.outdoor", "schedule.heating")
    assert not result.available
    assert result.reason == "Schedule service returned no weekday data"


@pytest.mark.asyncio
async def test_service_uses_recent_stale_cache_on_failure_then_expires_it():
    now = datetime(2025, 1, 6, 12, tzinfo=timezone.utc)
    weather = {
        "forecast": [
            {"datetime": (now + timedelta(hours=h)).isoformat(), "temperature": 5}
            for h in range(26)
        ]
    }
    adapter, services = _inputs(weather, _schedule())
    assert (
        await adapter.async_load(now, "weather.home", "sensor.outdoor", "schedule.heating")
    ).available

    loop = asyncio.get_running_loop()
    for key, (saved_at, response) in adapter._cache.items():
        adapter._cache[key] = (saved_at - 16 * 60, response)
    services.fail = True
    stale = await adapter.async_load(
        now + timedelta(minutes=1), "weather.home", "sensor.outdoor", "schedule.heating"
    )
    assert stale.available

    # Beyond the 20-minute stale limit, the failed refresh must surface a reason.
    for key, (saved_at, response) in adapter._cache.items():
        adapter._cache[key] = (saved_at - 5 * 60, response)
        adapter._failed_at[key] = loop.time() - 6 * 60
    expired = await adapter.async_load(
        now + timedelta(minutes=2), "weather.home", "sensor.outdoor", "schedule.heating"
    )
    assert not expired.available
    assert "temporary failure" in expired.reason


@pytest.mark.asyncio
async def test_service_responses_are_cached_for_fifteen_minutes():
    now = datetime(2025, 1, 6, 12, tzinfo=timezone.utc)
    weather = {
        "forecast": [
            {"datetime": (now + timedelta(hours=h)).isoformat(), "temperature": 5}
            for h in range(26)
        ]
    }
    adapter, services = _inputs(weather, _schedule())
    await adapter.async_load(now, "weather.home", "sensor.outdoor", "schedule.heating")
    calls = services.calls
    await adapter.async_load(
        now + timedelta(minutes=10), "weather.home", "sensor.outdoor", "schedule.heating"
    )
    assert services.calls == calls


@pytest.mark.asyncio
async def test_stale_outdoor_sensor_is_not_used_for_forecast():
    now = datetime(2025, 1, 6, 12, tzinfo=timezone.utc)
    weather = {"forecast": [
        {"datetime": (now + timedelta(hours=h)).isoformat(), "temperature": 8}
        for h in range(26)
    ]}
    adapter, _ = _inputs(weather, _schedule())
    adapter.hass.states.get = lambda _: SimpleNamespace(
        state="8", attributes={"unit_of_measurement": "°C"},
        last_reported=now - timedelta(hours=3),
    )
    result = await adapter.async_load(now, "weather.home", "sensor.outdoor", "schedule.heating")
    assert not result.available
    assert "not reported" in result.reason


@pytest.mark.asyncio
async def test_schedule_edit_invalidates_only_schedule_cache():
    now = datetime(2025, 1, 6, 12, tzinfo=timezone.utc)
    weather = {"forecast": [
        {"datetime": (now + timedelta(hours=h)).isoformat(), "temperature": 8}
        for h in range(26)
    ]}
    adapter, services = _inputs(weather, _schedule())
    result = await adapter.async_load(now, "weather.home", "sensor.outdoor", "schedule.heating")
    assert result.schedule_high(now) is False
    services.schedule = _schedule(monday=[{"from": "12:00", "to": "13:00"}])
    adapter.invalidate_schedule("schedule.heating")
    result = await adapter.async_load(now, "weather.home", "sensor.outdoor", "schedule.heating")
    assert result.schedule_high(now) is True
    assert services.calls == 3
