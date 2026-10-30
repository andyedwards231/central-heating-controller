"""Exercise thermostat writes and failure recovery for opt-in intelligent heating."""

from datetime import timedelta
from dataclasses import replace
from unittest.mock import AsyncMock, Mock, patch

import pytest
from homeassistant.exceptions import HomeAssistantError

from custom_components.central_heating_controller.models import PersistentState
from custom_components.central_heating_controller.const import CONF_GAS_RATE, CONF_HEATING_ACTIVITY
from custom_components.central_heating_controller.storage import ControllerStore
from tests.test_coordinator import NOW, _set_baseline_states, _setup_coordinator
from tests.test_temperature_forecast import _forecast_config, _set_forecast_states


@pytest.fixture(autouse=True)
async def environment(hass, monkeypatch):
    hass.data["test_coordinators"] = []
    calls = AsyncMock()
    monkeypatch.setattr(type(hass.services), "async_call", calls)
    yield calls
    for coordinator in hass.data["test_coordinators"]:
        await coordinator.async_shutdown()


async def controller(hass, *, enabled=False):
    _set_baseline_states(hass)
    _set_forecast_states(hass)
    coordinator, _ = await _setup_coordinator(hass, data=_forecast_config(),
        state=PersistentState(smart_control_enabled=enabled, thermal_model={
            "version": 2, "sources": ["climate.hallway", "sensor.outdoor_temperature", None],
            "rows": [], "policy_rows": [],
        }))
    for unsubscribe in coordinator._event_unsubscribers:
        unsubscribe()
    coordinator._event_unsubscribers.clear()
    coordinator._target_command_records.clear()
    coordinator._hvac_command_records.clear()
    return coordinator


def ready_pipeline(coordinator, *, target=18.5, ready=True):
    forecast = {"forecast": [{"datetime": (NOW + timedelta(minutes=i * 5)).isoformat(),
                              "temperature": 20.0, "target_temperature": target}
                             for i in range(289)], "temperature_unit": "°C"}
    plan = {"state": "preview", "preview": True, "forecast": forecast["forecast"],
            "generated_at": NOW.isoformat(), "feasible": True, "provisional": False}
    coordinator._smart_target = lambda at: target
    coordinator._async_temperature_forecast = AsyncMock(return_value=forecast)
    coordinator._async_smart_preview = AsyncMock(return_value=plan)
    coordinator._async_heating_cost = AsyncMock(return_value={})
    coordinator._smart_readiness = Mock(side_effect=lambda *args: {
        "ready": ready, "checks": [], "blockers": [] if ready else ["Heating response"]})


async def refresh(coordinator):
    with patch("custom_components.central_heating_controller.coordinator.dt_util.utcnow", return_value=NOW):
        await coordinator.async_refresh()


def temperatures(calls):
    return [call.args[2]["temperature"] for call in calls.await_args_list
            if call.args[:2] == ("climate", "set_temperature")]


async def test_default_preview_never_applies_ready_plan(hass, environment):
    coordinator = await controller(hass)
    ready_pipeline(coordinator)
    environment.reset_mock()
    await refresh(coordinator)
    assert temperatures(environment) == [17]
    assert coordinator.data.result.reason == "schedule_off_or_unavailable"
    assert coordinator.data.smart_readiness["control_state"] == "preview"
    assert coordinator.data.smart_plan["preview"] is True


async def test_smart_plan_exposes_configured_schedule_entity_and_cached_timetable(hass, environment):
    coordinator = await controller(hass)
    coordinator.forecast_inputs._cache[("schedule", "schedule.heating")] = (
        0,
        {"monday": [{"from": "06:30:00", "to": "08:00:00", "data": {"temperature": 21}}]},
    )

    plan = await coordinator._async_smart_preview(NOW, coordinator.data, True, False)

    assert plan["schedule_entity"] == "schedule.heating"
    assert plan["existing_schedule"]["monday"] == [{"start": "06:30", "end": "08:00"}]
    assert "data" not in plan["existing_schedule"]["monday"][0]


async def test_opted_in_ready_controller_sends_only_smart_target_and_scores_it(hass, environment):
    coordinator = await controller(hass, enabled=True)
    ready_pipeline(coordinator)
    environment.reset_mock()
    await refresh(coordinator)
    assert temperatures(environment) == [18.5]
    assert coordinator.data.result.reason == "intelligent_schedule"
    assert coordinator.data.smart_plan["preview"] is False
    assert coordinator.data.smart_readiness["control_state"] == "active"
    assert coordinator._async_temperature_forecast.await_args_list[0].kwargs["issue_accuracy"] is False
    assert coordinator._async_temperature_forecast.await_args_list[1].kwargs["smart_target"] is coordinator._smart_target
    assert coordinator.forecast_accuracy.pending_count == 3


async def test_readiness_failure_immediately_uses_existing_schedule(hass, environment):
    coordinator = await controller(hass, enabled=True)
    ready_pipeline(coordinator, ready=False)
    environment.reset_mock()
    await refresh(coordinator)
    assert temperatures(environment) == [17]
    assert coordinator.data.smart_readiness["control_state"] == "fallback"
    assert "Heating response" in coordinator.data.smart_readiness["fallback_reason"]
    assert coordinator.persistent_state.smart_control_enabled is True


@pytest.mark.parametrize("mode,expected", [("off", None), ("away", 14)])
async def test_explicit_modes_always_override_opted_in_smart_targets(hass, environment, mode, expected):
    coordinator = await controller(hass, enabled=True)
    ready_pipeline(coordinator)
    environment.reset_mock()
    with patch("custom_components.central_heating_controller.coordinator.dt_util.utcnow", return_value=NOW):
        await coordinator.async_set_controller_mode(mode)
    assert coordinator.data.result.target_temperature == expected
    assert 18.5 not in temperatures(environment)
    assert coordinator.data.smart_readiness["control_state"] == "paused"


@pytest.mark.parametrize("override,blast,expected", [(19, False, 19), (None, True, 20)])
async def test_manual_override_and_blast_keep_priority(hass, environment, override, blast, expected):
    coordinator = await controller(hass, enabled=True)
    ready_pipeline(coordinator)
    if blast:
        coordinator.persistent_state.blast_until = NOW + timedelta(minutes=30)
    else:
        hass.states.async_set("climate.hallway", "heat", {
            **hass.states.get("climate.hallway").attributes, "temperature": override,
        })
        coordinator.persistent_state.manual_override_target = override
        coordinator.persistent_state.manual_override_fingerprint = (
            True, False, False, False, 20, 17, 14, "heat", coordinator._smart_period(NOW))
    environment.reset_mock()
    await refresh(coordinator)
    assert coordinator.data.result.target_temperature == expected
    assert 18.5 not in temperatures(environment)
    assert coordinator.data.smart_readiness["control_state"] == "paused"


async def test_failed_live_forecast_falls_back_in_same_cycle(hass, environment):
    coordinator = await controller(hass, enabled=True)
    ready_pipeline(coordinator)
    first = coordinator._async_temperature_forecast.return_value
    coordinator._async_temperature_forecast.side_effect = [first, {"forecast": [], "reason": "Weather failed"}]
    environment.reset_mock()
    await refresh(coordinator)
    assert temperatures(environment) == [17]
    assert coordinator.data.smart_readiness["ready"] is False
    assert coordinator.data.smart_readiness["control_state"] == "fallback"


async def test_mode_change_during_planning_cannot_apply_stale_smart_target(hass, environment):
    coordinator = await controller(hass, enabled=True)
    ready_pipeline(coordinator)
    plan = coordinator._async_smart_preview.return_value
    async def change_mode(*args):
        coordinator.persistent_state.auto_mode = False
        return plan
    coordinator._async_smart_preview.side_effect = change_mode
    environment.reset_mock()
    await refresh(coordinator)
    assert temperatures(environment) == []
    assert coordinator.data.result.hvac_mode == "off"
    assert coordinator.data.smart_readiness["control_state"] == "paused"
    assert coordinator.forecast_accuracy.pending_count == 0


@pytest.mark.parametrize("target", [float("nan"), 50, 10])
async def test_invalid_or_unbounded_plan_target_falls_back(hass, environment, target):
    coordinator = await controller(hass, enabled=True)
    ready_pipeline(coordinator, target=target)
    environment.reset_mock()
    await refresh(coordinator)
    assert temperatures(environment) == [17]
    assert coordinator.data.smart_readiness["control_state"] == "fallback"


async def test_enable_requires_fresh_readiness_and_leaves_flag_off_on_rejection(hass):
    coordinator = await controller(hass)
    ready_pipeline(coordinator, ready=False)
    with pytest.raises(HomeAssistantError, match="not ready"):
        await coordinator.async_set_smart_control(True)
    assert coordinator.persistent_state.smart_control_enabled is False


async def test_explicit_enable_and_disable_are_persisted_and_disable_restores_legacy(hass, environment):
    coordinator = await controller(hass)
    ready_pipeline(coordinator)
    with patch("custom_components.central_heating_controller.coordinator.dt_util.utcnow", return_value=NOW):
        await coordinator.async_set_smart_control(True)
        assert coordinator.persistent_state.smart_control_enabled is True
        assert coordinator.data.smart_readiness["control_state"] == "active"
        environment.reset_mock()
        await coordinator.async_set_smart_control(False)
    assert coordinator.persistent_state.smart_control_enabled is False
    assert temperatures(environment) == [17]
    assert coordinator.data.smart_readiness["control_state"] == "preview"


async def test_corrupt_or_old_storage_never_opts_in(hass):
    store = ControllerStore(hass, "test")
    for raw in ({}, {"smart_control_enabled": "true"}, {"smart_control_enabled": 1}):
        store._store.async_load = AsyncMock(return_value=raw)
        assert (await store.async_load()).smart_control_enabled is False
    store._store.async_load = AsyncMock(return_value={"smart_control_enabled": True})
    assert (await store.async_load()).smart_control_enabled is True


async def test_native_step_grid_is_used_in_both_simulation_and_command(hass):
    coordinator = await controller(hass)
    cap = replace(coordinator.data.temperature_capabilities, step=0.5)
    transform = coordinator._target_transform(cap)
    assert transform(18.2) == 18.5
    coordinator._smart_target = lambda at: transform(18.2)
    assert coordinator._safe_smart_target(NOW, replace(coordinator.data, temperature_capabilities=cap)) == 18.5


async def test_real_readiness_requires_uninterrupted_hour_and_drops_on_stale_demand(hass):
    """Exercise real evaluator, timestamps and stability instead of bypassing gates."""
    coordinator = await controller(hass)
    hass.config_entries.async_update_entry(coordinator.entry, options=dict(coordinator.entry.options) | {
        CONF_GAS_RATE: "sensor.gas_rate", CONF_HEATING_ACTIVITY: "sensor.demand",
    })
    hass.states.async_set("sensor.demand", "10", {"unit_of_measurement": "%"})
    coordinator.heating_cost_model.calibration = Mock(return_value={
        "reason": "insufficient_samples", "heat_kw": None, "sample_count": 4, "heating_samples": 1,
        "nonheating_samples": 3, "distinct_dates": 1,
        "latest_interval_end": (NOW - timedelta(days=5)).isoformat(),
    })
    coordinator.forecast_accuracy.readiness_metrics = Mock(side_effect=lambda now: {
        h: {"count": count, "mae_c": .1, "bias_c": 0, "distinct_dates": 3,
            "span_hours": 50, "latest_at": now.isoformat()}
        for h, count in (("1h", 24), ("6h", 12), ("24h", 6))})
    state = replace(coordinator.data, temperature_forecast={
        "forecast": [{"temperature": 20}], "learned_samples": 50, "model_score": .8,
        "heating_response_learned": True, "demand_model_learned": True,
    })

    def evaluate(at, *, stale=False):
        for entity in ("climate.hallway", "sensor.outdoor_temperature", "sensor.demand"):
            hass.states.get(entity).last_reported = at
        if stale:
            hass.states.get("sensor.demand").last_reported = at - timedelta(minutes=31)
        plan = {"generated_at": at.isoformat(), "forecast": [{"temperature": 20}],
                "feasible": True, "provisional": False}
        with patch("custom_components.central_heating_controller.coordinator.dt_util.utcnow", return_value=at):
            return coordinator._smart_readiness(at, state, plan, True, False)

    first = evaluate(NOW)
    assert [c["key"] for c in first["checks"] if not c["passed"]] == ["stable"]
    assert first["cost_status"]["available"] is False
    for minute in range(1, 61):
        result = evaluate(NOW + timedelta(minutes=minute))
    assert result["ready"] is True
    assert result["cost_status"]["available"] is False
    coordinator.heating_cost_model.calibration.side_effect = RuntimeError("bad gas history")
    throwing_calibration = evaluate(NOW + timedelta(minutes=61))
    assert throwing_calibration["ready"] is True
    assert throwing_calibration["cost_status"]["available"] is False
    coordinator.heating_cost_model.calibration.side_effect = None
    coordinator.heating_cost_model.calibration.return_value = None
    malformed_calibration = evaluate(NOW + timedelta(minutes=62))
    assert malformed_calibration["ready"] is True
    assert malformed_calibration["cost_status"]["available"] is False
    stale = evaluate(NOW + timedelta(minutes=63), stale=True)
    assert stale["ready"] is False
    assert "Heating activity signal" in stale["blockers"]
    assert evaluate(NOW + timedelta(minutes=64))["ready"] is False
    # A gap in actual coordinator checks must not count as uninterrupted success.
    assert evaluate(NOW + timedelta(hours=3))["ready"] is False


async def test_reset_learning_also_removes_live_opt_in(hass):
    coordinator = await controller(hass, enabled=True)
    coordinator._smart_healthy_since = NOW - timedelta(hours=1)
    await coordinator.async_reset_learning()
    assert coordinator.persistent_state.smart_control_enabled is False
    assert coordinator._smart_healthy_since is None


async def test_changed_learning_sources_do_not_reuse_live_consent(hass):
    _set_baseline_states(hass)
    _set_forecast_states(hass)
    coordinator, _ = await _setup_coordinator(hass, data=_forecast_config(), state=PersistentState(
        smart_control_enabled=True,
        thermal_model={"version": 2, "sources": ["climate.different_room", "sensor.outdoor_temperature", None]},
    ))
    assert coordinator.persistent_state.smart_control_enabled is False


@pytest.mark.parametrize("change,expected", [("off", None), ("away", 14), ("disable", 17), ("blast", 20), ("manual", 19)])
async def test_priority_change_while_mode_command_is_awaiting_blocks_old_target(hass, environment, change, expected):
    coordinator = await controller(hass, enabled=True)
    ready_pipeline(coordinator)
    hass.states.async_set("climate.hallway", "off", dict(hass.states.get("climate.hallway").attributes))
    changed = False
    async def service(domain, name, data, **kwargs):
        nonlocal changed
        if domain == "climate" and name == "set_hvac_mode" and not changed:
            changed = True
            if change == "off":
                coordinator.persistent_state.auto_mode = False
            elif change == "away":
                coordinator.persistent_state.forced_away = True
            elif change == "disable":
                coordinator.persistent_state.smart_control_enabled = False
            elif change == "blast":
                coordinator.persistent_state.blast_until = NOW + timedelta(minutes=30)
            else:
                coordinator.persistent_state.manual_override_target = 19
    environment.side_effect = service
    environment.reset_mock()
    await refresh(coordinator)
    assert 18.5 not in temperatures(environment)
    if change not in ("blast", "manual"):
        assert temperatures(environment) == ([] if expected is None else [expected])
    assert coordinator.data.result.target_temperature == expected
    assert coordinator.data.smart_readiness["control_state"] != "active"


async def test_setting_change_during_plan_uses_new_legacy_target(hass, environment):
    coordinator = await controller(hass, enabled=True)
    ready_pipeline(coordinator)
    plan = coordinator._async_smart_preview.return_value
    async def change_setting(*args):
        hass.config_entries.async_update_entry(coordinator.entry,
            options=dict(coordinator.entry.options) | {"low_temperature": 19})
        return plan
    coordinator._async_smart_preview.side_effect = change_setting
    environment.reset_mock()
    await refresh(coordinator)
    assert temperatures(environment) == [19]
    assert coordinator.data.smart_readiness["control_state"] == "fallback"


async def test_failed_mode_write_never_sends_smart_target_or_claims_active(hass, environment):
    coordinator = await controller(hass, enabled=True)
    ready_pipeline(coordinator)
    hass.states.async_set("climate.hallway", "off", dict(hass.states.get("climate.hallway").attributes))
    environment.side_effect = HomeAssistantError("Device unreachable")
    environment.reset_mock()
    await refresh(coordinator)
    assert temperatures(environment) == []
    assert coordinator.data.smart_readiness["control_state"] == "fallback"
    assert coordinator.data.smart_readiness["ready"] is False


async def test_unexpected_planning_error_restores_legacy_in_same_cycle(hass, environment):
    coordinator = await controller(hass, enabled=True)
    ready_pipeline(coordinator)
    coordinator._async_smart_preview.side_effect = RuntimeError("Unexpected planning error")
    environment.reset_mock()
    await refresh(coordinator)
    assert temperatures(environment) == [17]
    assert coordinator.data.smart_readiness["control_state"] == "fallback"


async def test_throwing_gas_cost_estimate_does_not_deactivate_smart_control(hass, environment):
    coordinator = await controller(hass, enabled=True)
    ready_pipeline(coordinator)
    coordinator._async_heating_cost.side_effect = RuntimeError("meter unavailable")
    environment.reset_mock()
    await refresh(coordinator)
    assert temperatures(environment) == [18.5]
    assert coordinator.data.smart_readiness["control_state"] == "active"
    assert coordinator.data.heating_cost["today"]["cost_gbp"] is None
    assert coordinator.persistent_state.smart_control_enabled is True


async def test_disable_during_target_write_immediately_restores_existing_schedule(hass, environment):
    coordinator = await controller(hass, enabled=True)
    ready_pipeline(coordinator)
    async def service(domain, name, data, **kwargs):
        if domain == "climate" and name == "set_temperature" and data["temperature"] == 18.5:
            coordinator.persistent_state.smart_control_enabled = False
    environment.side_effect = service
    environment.reset_mock()
    await refresh(coordinator)
    assert temperatures(environment) == [18.5, 17]
    assert coordinator.data.result.target_temperature == 17
    assert coordinator.data.smart_readiness["control_state"] == "preview"
    assert coordinator.forecast_accuracy.pending_count == 0


async def test_rejected_smart_target_attempts_legacy_target_in_same_cycle(hass, environment):
    coordinator = await controller(hass, enabled=True)
    ready_pipeline(coordinator)
    async def service(domain, name, data, **kwargs):
        if domain == "climate" and name == "set_temperature" and data["temperature"] == 18.5:
            raise HomeAssistantError("Rejected target")
    environment.side_effect = service
    environment.reset_mock()
    await refresh(coordinator)
    assert temperatures(environment) == [18.5, 17]
    assert coordinator.data.result.target_temperature == 17
    assert coordinator.data.smart_readiness["control_state"] == "fallback"
