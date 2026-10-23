"""Integration coverage for coordinator temperature forecasts."""

from datetime import timedelta
from unittest.mock import AsyncMock

import pytest

from homeassistant.const import UnitOfTemperature

from custom_components.central_heating_controller.const import (
    CONF_HEATING_ACTIVITY,
    CONF_OUTDOOR_TEMP,
    CONF_WEATHER,
)
from custom_components.central_heating_controller.forecast_inputs import ForecastInputsResult
from tests.test_coordinator import NOW, _set_baseline_states, _setup_coordinator


@pytest.fixture(autouse=True)
def service_call_mock(hass, monkeypatch):
    mock = AsyncMock()
    monkeypatch.setattr(type(hass.services), "async_call", mock)
    return mock


@pytest.fixture(autouse=True)
async def cleanup_coordinators(hass):
    hass.data["test_coordinators"] = []
    yield
    for coordinator in hass.data["test_coordinators"]:
        await coordinator.async_shutdown()


def _forecast_config():
    return {
        CONF_WEATHER: "weather.home",
        CONF_OUTDOOR_TEMP: "sensor.outdoor_temperature",
    }


def _set_forecast_states(hass, *, fahrenheit=False):
    unit = UnitOfTemperature.FAHRENHEIT if fahrenheit else UnitOfTemperature.CELSIUS
    indoor = 68 if fahrenheit else 20
    outdoor = 50 if fahrenheit else 10
    hass.states.async_set("climate.hallway", "heat", {
        "current_temperature": indoor,
        "temperature": indoor,
        "hvac_action": "idle",
        "supported_features": 1,
        "hvac_modes": ["off", "heat", "auto"],
        "min_temp": 45 if fahrenheit else 7,
        "max_temp": 95 if fahrenheit else 35,
        "target_temp_step": 0.5,
        "unit_of_measurement": unit,
    })
    hass.states.async_set("sensor.outdoor_temperature", str(outdoor), {"unit_of_measurement": unit})
    hass.states.async_set("weather.home", "sunny", {"temperature_unit": unit})


async def _forecast(coordinator, *, schedule_high, occupied=True, journey_home=False,
                    preheat_ready=False):
    """Run one deterministic coordinator forecast using supplied external inputs."""
    coordinator.forecast_inputs.async_load = AsyncMock(return_value=ForecastInputsResult(
        outdoor_temperature=lambda at: 10,
        schedule_high=schedule_high,
        current_outdoor_temperature=10,
    ))
    climate = coordinator.hass.states.get("climate.hallway")
    return await coordinator._async_temperature_forecast(
        NOW, coordinator.data, climate, occupied, schedule_high(NOW),
        journey_home, preheat_ready,
    )


@pytest.mark.parametrize(
    ("at_hour", "high", "expected"),
    [(0, False, 17), (0, True, 20), (6, False, 20)],
)
async def test_forecast_follows_high_and_low_schedule(hass, at_hour, high, expected):
    _set_baseline_states(hass)
    _set_forecast_states(hass)
    coordinator, _ = await _setup_coordinator(hass, data=_forecast_config())
    forecast = await _forecast(
        coordinator,
        schedule_high=lambda at: high if at < NOW + timedelta(hours=6) else True,
    )
    point = forecast["forecast"][at_hour * 12]
    assert point["target_temperature"] == expected


async def test_forecast_changes_from_away_to_high_at_known_arrival(hass):
    _set_baseline_states(hass)
    _set_forecast_states(hass)
    hass.states.async_set("person.andy", "not_home")
    hass.states.async_set("person.alex", "not_home")
    hass.states.async_set("sensor.destination", "home")
    hass.states.async_set("sensor.eta", (NOW + timedelta(hours=3)).isoformat())
    coordinator, _ = await _setup_coordinator(hass, data=_forecast_config())
    forecast = await _forecast(coordinator, schedule_high=lambda at: True, occupied=False,
                               journey_home=True, preheat_ready=False)
    assert forecast["forecast"][0]["target_temperature"] == 14
    assert forecast["forecast"][36]["target_temperature"] == 20


async def test_manual_override_ends_when_future_policy_schedule_changes(hass):
    _set_baseline_states(hass)
    _set_forecast_states(hass)
    coordinator, _ = await _setup_coordinator(hass, data=_forecast_config())
    coordinator.persistent_state.manual_override_target = 18.5
    forecast = await _forecast(coordinator, schedule_high=lambda at: at >= NOW + timedelta(hours=2))
    assert forecast["forecast"][0]["target_temperature"] == 18.5
    assert forecast["forecast"][24]["target_temperature"] == 20


async def test_heat_blast_target_expires_after_its_deadline(hass):
    _set_baseline_states(hass)
    _set_forecast_states(hass)
    coordinator, _ = await _setup_coordinator(hass, data=_forecast_config())
    coordinator.persistent_state.blast_until = NOW + timedelta(minutes=30)
    forecast = await _forecast(coordinator, schedule_high=lambda at: False)
    assert forecast["forecast"][0]["target_temperature"] == 20
    assert forecast["forecast"][12]["target_temperature"] == 17


async def test_auto_off_forecast_has_no_heating_targets(hass):
    _set_baseline_states(hass)
    _set_forecast_states(hass)
    coordinator, _ = await _setup_coordinator(hass, data=_forecast_config())
    coordinator.persistent_state.auto_mode = False
    forecast = await _forecast(coordinator, schedule_high=lambda at: True)
    assert all(point["target_temperature"] is None for point in forecast["forecast"])


async def test_forecast_values_are_returned_in_thermostat_native_unit(hass):
    _set_baseline_states(hass)
    _set_forecast_states(hass, fahrenheit=True)
    coordinator, _ = await _setup_coordinator(
        hass, data=_forecast_config(), options={"high_temperature": 68, "low_temperature": 62,
                                                "eco_temperature": 58}
    )
    forecast = await _forecast(coordinator, schedule_high=lambda at: True)
    assert forecast["temperature_unit"] == UnitOfTemperature.FAHRENHEIT
    assert forecast["forecast"][0]["target_temperature"] == 68
    assert forecast["forecast"][0]["outdoor_temperature"] == 50


async def test_weather_failure_does_not_block_heating_control(hass, service_call_mock):
    _set_baseline_states(hass)
    _set_forecast_states(hass)
    hass.states.async_set("climate.hallway", "heat", {
        **hass.states.get("climate.hallway").attributes, "current_temperature": 15,
        "temperature": 15,
    })
    hass.states.async_set("schedule.heating", "on")

    async def fail_weather(domain, service, data, **kwargs):
        if domain == "weather":
            raise RuntimeError("weather service failed")
        return None

    service_call_mock.side_effect = fail_weather
    coordinator, _ = await _setup_coordinator(hass, data=_forecast_config())
    assert coordinator.data.status.value == "high"
    assert any(call.args[:2] == ("climate", "set_temperature")
               for call in service_call_mock.await_args_list)
    assert coordinator.data.temperature_forecast["forecast"] == []
    assert coordinator.data.temperature_forecast["reason"]


async def test_heating_activity_signal_takes_precedence_and_fallback_uses_hvac_action(hass):
    _set_baseline_states(hass)
    _set_forecast_states(hass)
    hass.states.async_set("binary_sensor.boiler", "off")
    coordinator, _ = await _setup_coordinator(
        hass, data=_forecast_config() | {CONF_HEATING_ACTIVITY: "binary_sensor.boiler"}
    )
    assert coordinator._heating_active(hass.states.get("climate.hallway")) is False
    hass.states.async_set("binary_sensor.boiler", "heating")
    assert coordinator._heating_active(hass.states.get("climate.hallway")) is True
    hass.states.async_set("climate.hallway", "heat", {**hass.states.get("climate.hallway").attributes,
                                                        "hvac_action": "heating"})
    assert coordinator._heating_active(hass.states.get("climate.hallway")) is True
    fallback, _ = await _setup_coordinator(hass, data=_forecast_config())
    hass.states.async_set("climate.hallway", "heat", {**hass.states.get("climate.hallway").attributes,
                                                        "hvac_action": "idle"})
    assert fallback._heating_active(hass.states.get("climate.hallway")) is False
    hass.states.async_set("climate.hallway", "heat", {**hass.states.get("climate.hallway").attributes,
                                                        "hvac_action": "heating"})
    assert fallback._heating_active(hass.states.get("climate.hallway")) is True


async def test_thermal_model_persists_and_reset_clears_it(hass):
    _set_baseline_states(hass)
    _set_forecast_states(hass)
    coordinator, _ = await _setup_coordinator(hass, data=_forecast_config())
    coordinator.thermal_model.observe(NOW, 20, 10, True)
    coordinator.thermal_model.observe(NOW + timedelta(minutes=20), 20.5, 10, True)
    coordinator._copy_learning_state()
    saved = coordinator.persistent_state.thermal_model
    assert saved["version"] == 2
    assert len(saved["rows"]) == 1
    await coordinator.async_reset_learning()
    assert coordinator.persistent_state.thermal_model["rows"] == []
    assert coordinator.thermal_model.sample_count == 0


@pytest.mark.parametrize(("value", "unit", "expected"), [
    ("13", "%", 0.13), ("0", "%", 0.0), ("100", "%", 1.0),
    ("-1", "%", None), ("101", "%", None), ("nan", "%", None),
    ("inf", "%", None), ("unknown", "%", None), ("unavailable", "%", None),
    ("13", None, None), ("13", "W", None),
])
async def test_percentage_demand_is_validated_and_not_used_as_binary_heat(hass, value, unit, expected):
    _set_baseline_states(hass)
    _set_forecast_states(hass)
    hass.states.async_set("sensor.hall_heating_power", value, {"unit_of_measurement": unit})
    coordinator, _ = await _setup_coordinator(
        hass, data=_forecast_config() | {CONF_HEATING_ACTIVITY: "sensor.hall_heating_power"}
    )
    climate = hass.states.get("climate.hallway")
    assert coordinator._heating_demand(climate) == expected
    assert coordinator._heating_active(climate) is None


async def test_demand_and_observed_setpoint_reach_model_and_forecast_attributes(hass):
    from unittest.mock import Mock
    _set_baseline_states(hass)
    _set_forecast_states(hass)
    hass.states.async_set("sensor.hall_heating_power", "13", {"unit_of_measurement": "%"})
    coordinator, _ = await _setup_coordinator(
        hass, data=_forecast_config() | {CONF_HEATING_ACTIVITY: "sensor.hall_heating_power"}
    )
    coordinator.thermal_model.observe = Mock(return_value=False)
    forecast = await _forecast(coordinator, schedule_high=lambda _: True)
    call = coordinator.thermal_model.observe.call_args
    assert call.args[3] == 0.13
    assert call.kwargs["target_temperature"] == 20
    assert forecast["current_heating_demand_percent"] == 13
    assert "heating_response_minutes" in forecast
    assert all(0 <= point["heating_demand_percent"] <= 100 for point in forecast["forecast"])


async def test_stale_demand_pauses_learning(hass):
    from unittest.mock import patch
    from homeassistant.util import dt as dt_util
    _set_baseline_states(hass)
    _set_forecast_states(hass)
    hass.states.async_set("sensor.hall_heating_power", "13", {"unit_of_measurement": "%"})
    coordinator, _ = await _setup_coordinator(
        hass, data=_forecast_config() | {CONF_HEATING_ACTIVITY: "sensor.hall_heating_power"}
    )
    with patch("custom_components.central_heating_controller.coordinator.dt_util.utcnow",
               return_value=dt_util.utcnow() + timedelta(minutes=31)):
        assert coordinator._heating_demand(hass.states.get("climate.hallway")) is None


async def test_accuracy_uses_sensor_report_time_and_persists(hass):
    from unittest.mock import Mock
    _set_baseline_states(hass)
    _set_forecast_states(hass)
    coordinator, _ = await _setup_coordinator(hass, data=_forecast_config())
    coordinator.forecast_accuracy.observe = Mock(return_value=True)
    await _forecast(coordinator, schedule_high=lambda _: True)
    call = coordinator.forecast_accuracy.observe.call_args
    assert call.args[0] == hass.states.get("climate.hallway").last_reported
    assert coordinator.persistent_state.forecast_accuracy["version"] == 1
    assert coordinator.store.async_save.called


async def test_accuracy_history_restores_only_for_matching_learning_sources(hass):
    from custom_components.central_heating_controller.forecasting import ForecastPoint
    _set_baseline_states(hass)
    _set_forecast_states(hass)
    first, _ = await _setup_coordinator(hass, data=_forecast_config())
    from custom_components.central_heating_controller.forecast_accuracy import ForecastAccuracyTracker
    first.forecast_accuracy = ForecastAccuracyTracker()
    first.forecast_accuracy.issue(NOW - timedelta(hours=1), (
        ForecastPoint(NOW, 19.0, 10.0, 20.0, False),
    ))
    first.forecast_accuracy.observe(NOW, 20.0)
    first._copy_learning_state()
    same, _ = await _setup_coordinator(hass, data=_forecast_config(), state=first.persistent_state)
    assert same.forecast_accuracy.metrics()["1h"]["mae_c"] == 1.0
    different, _ = await _setup_coordinator(
        hass, data=_forecast_config() | {CONF_HEATING_ACTIVITY: "sensor.other_demand"},
        state=first.persistent_state,
    )
    assert different.forecast_accuracy.metrics()["1h"]["count"] == 0
    await same.async_reset_learning()
    assert same.forecast_accuracy.metrics()["1h"]["count"] == 0
    assert same.persistent_state.forecast_accuracy["scores"]["1h"] == []


async def test_smart_preview_is_separate_and_failure_does_not_control(hass, service_call_mock):
    """Preview computation cannot issue thermostat commands, even on failure."""
    from unittest.mock import patch
    _set_baseline_states(hass)
    _set_forecast_states(hass)
    coordinator, _ = await _setup_coordinator(hass, data=_forecast_config())
    await _forecast(coordinator, schedule_high=lambda at: 7 <= at.hour < 9 or 18 <= at.hour < 22)
    service_call_mock.reset_mock()
    preview = await coordinator._async_smart_preview(NOW, coordinator.data, True, False)
    assert preview['state'] == 'preview'
    assert len(preview['forecast']) == 289
    assert preview['settings']['night_temperature'] == 16
    assert preview['settings']['daytime_drop'] == 2
    assert preview['provisional'] is True
    service_call_mock.assert_not_awaited()
    coordinator._preview_cache = None
    with patch('custom_components.central_heating_controller.coordinator.plan_smart_schedule', side_effect=ValueError('bad weather')):
        failed = await coordinator._async_smart_preview(NOW, coordinator.data, True, False)
    assert failed['state'] == 'unavailable'
    assert failed['settings'] == preview['settings']
    service_call_mock.assert_not_awaited()


async def test_smart_schedule_updates_preserve_sources_and_validate_atomically(hass):
    from custom_components.central_heating_controller.const import CONF_SMART_NIGHT_START
    from homeassistant.exceptions import HomeAssistantError
    _set_baseline_states(hass)
    _set_forecast_states(hass)
    coordinator, entry = await _setup_coordinator(hass, data=_forecast_config())
    payload = coordinator.smart_settings() | {'night_start': '22:30', 'weekly_schedule': {'monday': [{'start': '07:00', 'end': '09:00'}]}}
    await coordinator.async_update_smart_schedule(payload)
    assert entry.options[CONF_SMART_NIGHT_START] == '22:30'
    assert coordinator.config[CONF_WEATHER] == 'weather.home'
    previous = dict(entry.options)
    with pytest.raises(HomeAssistantError):
        await coordinator.async_update_smart_schedule(payload | {'daytime_drop': float('nan')})
    assert dict(entry.options) == previous
    await coordinator.async_update_setting('high_temperature', 21)
    assert entry.options[CONF_SMART_NIGHT_START] == '22:30'
