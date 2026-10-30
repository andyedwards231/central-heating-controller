"""Warm-up estimation uses the learned thermal model with safe fallbacks."""

from datetime import timedelta
from unittest.mock import AsyncMock

import pytest

from homeassistant.const import UnitOfTemperature

from custom_components.central_heating_controller.const import (
    CONF_HEATING_ACTIVITY,
    CONF_HIGH_TEMP,
    CONF_MAX_WARMUP_MINUTES,
    CONF_OUTDOOR_TEMP,
    CONF_WEATHER,
)
from custom_components.central_heating_controller.forecast_accuracy import ForecastAccuracyTracker
from custom_components.central_heating_controller.forecasting import ThermalForecaster
from custom_components.central_heating_controller.forecasting import ForecastPoint
from custom_components.central_heating_controller.models import PersistentState
from custom_components.central_heating_controller.storage import ControllerStore
from tests.test_coordinator import NOW, _set_baseline_states, _setup_coordinator
from tests.test_forecasting import train_partial_inertial


@pytest.fixture(autouse=True)
async def environment(hass, monkeypatch):
    hass.data["test_coordinators"] = []
    calls = AsyncMock()
    monkeypatch.setattr(type(hass.services), "async_call", calls)
    yield calls
    for coordinator in hass.data["test_coordinators"]:
        await coordinator.async_shutdown()


async def controller(hass, *, fahrenheit=False, options=None):
    _set_baseline_states(hass)
    unit = UnitOfTemperature.FAHRENHEIT if fahrenheit else UnitOfTemperature.CELSIUS
    indoor = 64.4 if fahrenheit else 18.0
    outdoor = 68 if fahrenheit else 20.0
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
    hass.states.async_set("sensor.demand", "50", {"unit_of_measurement": "%"})
    data = {CONF_OUTDOOR_TEMP: "sensor.outdoor_temperature", CONF_WEATHER: "weather.home",
            CONF_HEATING_ACTIVITY: "sensor.demand"}
    selected_options = options or {}
    coordinator, entry = await _setup_coordinator(hass, data=data, options=selected_options)
    for entity_id in ("climate.hallway", "sensor.outdoor_temperature", "sensor.demand"):
        hass.states.get(entity_id).last_reported = NOW
    return coordinator, entry


def trained_model() -> ThermalForecaster:
    model = ThermalForecaster()
    train_partial_inertial(model)
    assert model.ready
    return model


def recovery_minute(model, indoor, outdoor, target, maximum):
    prediction = model.forecast(NOW, indoor, lambda _: outdoor, lambda _: target,
                                hours=maximum / 60, step_minutes=1)
    return next((round((point.at - NOW).total_seconds() / 60)
                 for point in prediction.points[1:] if point.indoor_temperature >= target - 0.1), None)


def test_warmup_recovery_minute_matches_learned_fractional_forecast():
    model = trained_model()
    expected = recovery_minute(model, indoor=18, outdoor=20, target=20, maximum=360)

    assert expected is not None
    assert model.warmup_minutes(NOW, 18, 20, 20, 360) == expected


def test_colder_outdoor_temperature_requires_longer_recovery_and_respects_cap():
    model = trained_model()
    warmer = model.warmup_minutes(NOW, 18, 22, 20, 360)
    colder = model.warmup_minutes(NOW, 18, 20, 20, 360)
    capped = model.warmup_minutes(NOW, 18, 20, 20, 120)

    assert warmer is not None and colder is not None
    assert colder > warmer
    assert capped == 120


def test_unreachable_recovery_uses_maximum_lead():
    model = trained_model()

    assert model.warmup_minutes(NOW, 18, 5, 20, 120) == 120


@pytest.mark.parametrize("changes", [
    {"at": NOW.replace(tzinfo=None)}, {"indoor_temperature": float("nan")},
    {"outdoor_temperature": float("inf")}, {"target_temperature": True},
    {"maximum_minutes": -1}, {"maximum_minutes": 361}, {"maximum_minutes": True},
])
def test_invalid_warmup_inputs_request_fallback(changes):
    values = {"at": NOW, "indoor_temperature": 18, "outdoor_temperature": 10,
              "target_temperature": 20, "maximum_minutes": 180}
    assert ThermalForecaster().warmup_minutes(**(values | changes)) is None


def test_at_target_needs_no_warmup_even_before_training():
    assert ThermalForecaster().warmup_minutes(NOW, 20, 10, 20, 180) == 0


async def test_untrained_model_uses_configured_fallback(hass, environment):
    coordinator, _ = await controller(hass)
    coordinator.thermal_model = ThermalForecaster()
    with pytest.MonkeyPatch.context() as monkeypatch:
        monkeypatch.setattr("custom_components.central_heating_controller.coordinator.dt_util.utcnow", lambda: NOW)
        assert coordinator._warmup_estimate(NOW, hass.states.get("climate.hallway")) == (60, "fallback")


async def test_fahrenheit_inputs_convert_to_same_celsius_recovery(hass, environment):
    coordinator, _ = await controller(hass, fahrenheit=True, options={CONF_HIGH_TEMP: 68,
                                                                       CONF_MAX_WARMUP_MINUTES: 360})
    coordinator.thermal_model = trained_model()
    climate = hass.states.get("climate.hallway")
    with pytest.MonkeyPatch.context() as monkeypatch:
        monkeypatch.setattr("custom_components.central_heating_controller.coordinator.dt_util.utcnow", lambda: NOW)
        minutes, source = coordinator._warmup_estimate(NOW, climate)

    assert source == "thermal_model"
    assert minutes == coordinator.thermal_model.warmup_minutes(NOW, 18, 20, 20, 360)


@pytest.mark.parametrize("stale_input", [
    "climate", "outdoor", "demand", "missing_outdoor", "missing_indoor",
])
async def test_stale_or_missing_required_input_uses_fallback(hass, environment, stale_input):
    coordinator, _ = await controller(hass)
    coordinator.thermal_model = trained_model()
    climate = hass.states.get("climate.hallway")
    outside = hass.states.get("sensor.outdoor_temperature")
    if stale_input == "climate":
        climate.last_reported = NOW - timedelta(minutes=31)
    elif stale_input == "outdoor":
        outside.last_reported = NOW - timedelta(hours=2, seconds=1)
    elif stale_input == "demand":
        hass.states.get("sensor.demand").last_reported = NOW - timedelta(minutes=31)
    elif stale_input == "missing_indoor":
        hass.states.async_set("climate.hallway", "heat", {
            key: value for key, value in climate.attributes.items() if key != "current_temperature"
        })
    else:
        hass.states.async_remove("sensor.outdoor_temperature")
    with pytest.MonkeyPatch.context() as monkeypatch:
        monkeypatch.setattr("custom_components.central_heating_controller.coordinator.dt_util.utcnow", lambda: NOW)
        assert coordinator._warmup_estimate(NOW, hass.states.get("climate.hallway")) == (60, "fallback")


async def test_runtime_eta_uses_thermal_model_not_archived_rate(hass, environment):
    coordinator, _ = await controller(hass, options={CONF_MAX_WARMUP_MINUTES: 360})
    coordinator.thermal_model = trained_model()
    coordinator.persistent_state.learned_rate = 10.0
    coordinator.persistent_state.learned_sample_count = 999
    hass.states.async_set("sensor.destination", "home")
    hass.states.get("sensor.destination").last_reported = NOW
    hass.states.async_set("sensor.eta", (NOW + timedelta(minutes=240)).isoformat())
    hass.states.get("sensor.eta").last_reported = NOW
    with pytest.MonkeyPatch.context() as monkeypatch:
        monkeypatch.setattr("custom_components.central_heating_controller.coordinator.dt_util.utcnow", lambda: NOW)
        minutes, source = coordinator._warmup_estimate(NOW, hass.states.get("climate.hallway"))
        is_ready = coordinator._preheat_ready(hass.states.get("sensor.eta"), NOW)

    assert source == "thermal_model"
    assert minutes == coordinator.thermal_model.warmup_minutes(NOW, 18, 20, 20, 360)
    assert minutes > 240  # The ETA is still before warm-up despite the archived one-hour default.
    assert is_ready is True


async def test_learning_and_legacy_persistence_survive_setup_copy_and_storage_round_trip(hass, environment):
    model = trained_model()
    sources = ["climate.hallway", "sensor.outdoor_temperature", "sensor.demand"]
    rows = model.to_dict()
    accuracy_tracker = ForecastAccuracyTracker()
    issue_time = NOW - timedelta(hours=2)
    assert accuracy_tracker.issue(issue_time, (ForecastPoint(
        issue_time + timedelta(hours=1), 18.0, 10.0, 20.0, True,
    ),))
    assert accuracy_tracker.observe(issue_time + timedelta(hours=1), 18.2)
    accuracy = accuracy_tracker.to_dict()
    cost = {"version": 1, "observations": [{"at": NOW.isoformat(), "demand": 0.5}], "samples": []}
    state = PersistentState(
        learned_rate=1.375,
        learned_sample_count=87,
        thermal_model=rows | {"sources": sources},
        forecast_accuracy=accuracy,
        heating_cost=cost | {"sources": ["climate.hallway", "sensor.demand", None, None]},
    )
    coordinator, _ = await _setup_coordinator(
        hass,
        data={CONF_OUTDOOR_TEMP: "sensor.outdoor_temperature", CONF_WEATHER: "weather.home",
              CONF_HEATING_ACTIVITY: "sensor.demand"},
        state=state,
    )

    assert coordinator.thermal_model.sample_count == model.sample_count
    assert coordinator.persistent_state.learned_rate == 1.375
    assert coordinator.persistent_state.learned_sample_count == 87
    assert coordinator.persistent_state.forecast_accuracy == accuracy
    assert len(coordinator.heating_cost_model.export_state()["observations"]) == 1
    coordinator._copy_learning_state()
    assert coordinator.persistent_state.thermal_model["rows"] == rows["rows"]
    assert coordinator.persistent_state.heating_cost["observations"] == cost["observations"]
    assert coordinator.persistent_state.learned_rate == 1.375
    assert coordinator.persistent_state.learned_sample_count == 87

    store = ControllerStore(hass, "warmup-round-trip")
    save = AsyncMock()
    store._store.async_save = save
    await store.async_save(coordinator.persistent_state)
    store._store.async_load = AsyncMock(return_value=save.await_args.args[0])
    restored = await store.async_load()
    assert restored.learned_rate == 1.375
    assert restored.learned_sample_count == 87
    assert restored.thermal_model["rows"] == rows["rows"]
    restored_accuracy = ForecastAccuracyTracker.from_dict(restored.forecast_accuracy, now=NOW)
    assert restored_accuracy.metrics()["1h"]["count"] == 1
    assert restored.heating_cost["observations"] == cost["observations"]
