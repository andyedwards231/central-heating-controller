"""Tests for controller persistence and coordination."""

import asyncio
from datetime import datetime, timedelta, timezone
import math
from unittest.mock import AsyncMock, Mock, patch

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from homeassistant.const import ATTR_LATITUDE, ATTR_LONGITUDE, STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import CoreState, EVENT_HOMEASSISTANT_STARTED
from homeassistant.exceptions import HomeAssistantError

from custom_components.central_heating_controller import (
    async_setup_entry,
    async_unload_entry,
    async_update_listener,
)
from custom_components.central_heating_controller.const import (
    CONF_ACTIVE_HVAC_MODE,
    CONF_ARRIVAL_TIME,
    CONF_CLIMATE,
    CONF_DESTINATION,
    CONF_DESTINATION_HOME_VALUE,
    CONF_ECO_TEMP,
    CONF_FALLBACK_MINUTES,
    CONF_HIGH_TEMP,
    CONF_HOME_ZONE,
    CONF_LOW_TEMP,
    CONF_MAX_WARMUP_MINUTES,
    CONF_PERSONS,
    CONF_SCHEDULE,
    DOMAIN,
    OPT_RESET_LEARNING_REQUESTED,
)
from custom_components.central_heating_controller.coordinator import ControllerCoordinator
from custom_components.central_heating_controller.models import (
    ControllerRuntimeData,
    ControllerStatus,
    PersistentState,
)
from custom_components.central_heating_controller.storage import ControllerStore


NOW = datetime(2026, 6, 28, 17, 0, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def service_call_mock(hass, monkeypatch) -> AsyncMock:
    """Replace the slotted service registry method for each test."""
    service_call = AsyncMock()
    monkeypatch.setattr(type(hass.services), "async_call", service_call)
    return service_call


@pytest.fixture(autouse=True)
async def cleanup_coordinators(hass):
    """Shut down coordinators created directly by unit-style tests."""
    hass.data["test_coordinators"] = []
    yield
    for coordinator in hass.data["test_coordinators"]:
        await coordinator.async_shutdown()


def _entry(*, options: dict | None = None) -> MockConfigEntry:
    """Return a fully configured test entry."""
    return MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_CLIMATE: "climate.hallway",
            CONF_PERSONS: ["person.andy", "person.alex"],
            CONF_HOME_ZONE: "zone.home",
            CONF_SCHEDULE: "schedule.heating",
            CONF_DESTINATION: "sensor.destination",
            CONF_ARRIVAL_TIME: "sensor.eta",
        },
        options={
            CONF_ACTIVE_HVAC_MODE: "heat",
            CONF_HIGH_TEMP: 20.0,
            CONF_LOW_TEMP: 17.0,
            CONF_ECO_TEMP: 14.0,
            CONF_FALLBACK_MINUTES: 60,
            CONF_MAX_WARMUP_MINUTES: 180,
            CONF_DESTINATION_HOME_VALUE: None,
        }
        | (options or {}),
    )


def _set_baseline_states(hass) -> None:
    """Populate ordinary occupied-low input states."""
    hass.states.async_set(
        "climate.hallway",
        "off",
        {"current_temperature": 16.0, "temperature": 15.0, "hvac_action": "idle"},
    )
    hass.states.async_set(
        "zone.home",
        "1",
        {ATTR_LATITUDE: 51.5, ATTR_LONGITUDE: -0.1, "radius": 100, "friendly_name": "Home"},
    )
    hass.states.async_set("person.andy", "home", {ATTR_LATITUDE: 51.5, ATTR_LONGITUDE: -0.1})
    hass.states.async_set("person.alex", "not_home")
    hass.states.async_set("schedule.heating", "off")
    hass.states.async_set("sensor.destination", "work")
    hass.states.async_set("sensor.eta", "unknown")


async def _setup_coordinator(hass, *, state: PersistentState | None = None, options=None):
    """Create a coordinator with deterministic storage and service calls."""
    entry = _entry(options=options)
    entry.add_to_hass(hass)
    coordinator = ControllerCoordinator(hass, entry)
    coordinator.store.async_load = AsyncMock(return_value=state or PersistentState())
    coordinator.store.async_save = AsyncMock()
    with (
        patch(
            "custom_components.central_heating_controller.coordinator.dt_util.utcnow",
            return_value=NOW,
        ),
        patch.object(
            coordinator,
            "async_config_entry_first_refresh",
            side_effect=coordinator.async_refresh,
        ),
    ):
        await coordinator.async_setup()
    hass.data["test_coordinators"].append(coordinator)
    return coordinator, entry


async def test_storage_round_trip_serializes_only_persistent_fields(hass) -> None:
    """Valid state round-trips through Home Assistant storage."""
    store = ControllerStore(hass, "entry-1")
    state = PersistentState(
        auto_mode=False,
        blast_until=datetime(2026, 6, 28, 18, 0, tzinfo=timezone(timedelta(hours=1))),
        manual_override_target=18.5,
        manual_override_fingerprint=(True, None, "home", 17.0),
        learned_rate=1.25,
        learned_sample_count=4,
    )
    save = AsyncMock()
    store._store.async_save = save

    await store.async_save(state)

    assert save.await_args.args[0] == {
        "auto_mode": False,
        "blast_until": "2026-06-28T17:00:00+00:00",
        "manual_override_target": 18.5,
        "manual_override_fingerprint": [True, None, "home", 17.0],
        "learned_rate": 1.25,
        "learned_sample_count": 4,
    }
    assert "pending_target" not in save.await_args.args[0]


async def test_storage_load_validates_fields_independently(hass) -> None:
    """One corrupt field does not discard other valid stored values."""
    store = ControllerStore(hass, "entry-1")
    store._store.async_load = AsyncMock(
        return_value={
            "auto_mode": "yes",
            "blast_until": "2026-06-28T18:00:00+01:00",
            "manual_override_target": math.inf,
            "manual_override_fingerprint": [True, {"not": "primitive"}],
            "learned_rate": 1.5,
            "learned_sample_count": 3,
        }
    )

    state = await store.async_load()

    assert state == PersistentState(
        auto_mode=True,
        blast_until=datetime(2026, 6, 28, 17, 0, tzinfo=timezone.utc),
        manual_override_target=None,
        manual_override_fingerprint=None,
        learned_rate=1.5,
        learned_sample_count=3,
    )


@pytest.mark.parametrize(
    "stored",
    [
        None,
        [],
        {"learned_rate": 1.0, "learned_sample_count": 0},
        {"learned_rate": None, "learned_sample_count": 2},
        {"learned_rate": 11.0, "learned_sample_count": 2},
        {"learned_rate": True, "learned_sample_count": 2},
        {"learned_rate": 1.0, "learned_sample_count": True},
        {"blast_until": "2026-06-28T17:00:00"},
    ],
)
async def test_storage_corrupt_data_never_raises(hass, stored) -> None:
    """Malformed storage safely falls back to defaults."""
    store = ControllerStore(hass, "entry-1")
    store._store.async_load = AsyncMock(return_value=stored)

    state = await store.async_load()

    assert state == PersistentState()


async def test_storage_logs_safe_corruption_context(hass, caplog) -> None:
    """Storage corruption is observable without logging stored contents."""
    store = ControllerStore(hass, "entry-1")
    caplog.set_level("DEBUG", logger="custom_components.central_heating_controller.storage")
    store._store.async_load = AsyncMock(return_value=None)
    assert await store.async_load() == PersistentState()
    assert caplog.text == ""

    store._store.async_load = AsyncMock(side_effect=RuntimeError("private raw payload"))

    assert await store.async_load() == PersistentState()
    assert "private raw payload" not in caplog.text
    assert "load" in caplog.text.lower()

    caplog.clear()
    store._store.async_load = AsyncMock(return_value=["private raw payload"])
    assert await store.async_load() == PersistentState()
    assert "private raw payload" not in caplog.text
    assert "root" in caplog.text.lower()

    caplog.clear()
    store._store.async_load = AsyncMock(
        return_value={"auto_mode": "private raw payload", "blast_until": "not-a-date"}
    )
    assert await store.async_load() == PersistentState()
    assert "private raw payload" not in caplog.text
    assert "auto_mode" in caplog.text
    assert "blast_until" in caplog.text


async def test_occupied_schedule_high_commands_mode_then_temperature(hass) -> None:
    """An occupied schedule-high snapshot commands exact climate services in order."""
    _set_baseline_states(hass)
    hass.states.async_set("schedule.heating", "on")

    coordinator, _ = await _setup_coordinator(hass)

    assert coordinator.data.status is ControllerStatus.HIGH
    assert hass.services.async_call.await_args_list == [
        (
            ("climate", "set_hvac_mode", {"entity_id": "climate.hallway", "hvac_mode": "heat"}),
            {"blocking": True},
        ),
        (
            ("climate", "set_temperature", {"entity_id": "climate.hallway", "temperature": 20.0}),
            {"blocking": True},
        ),
    ]


@pytest.mark.parametrize(
    ("person_state", "schedule_state", "destination", "eta", "expected"),
    [
        ("home", "off", "work", "unknown", ControllerStatus.LOW),
        ("not_home", "on", "work", "unknown", ControllerStatus.AWAY),
        ("not_home", "on", "home", "2026-06-28T19:00:00+00:00", ControllerStatus.AWAY),
        ("not_home", "on", "home", "2026-06-28T17:30:00+00:00", ControllerStatus.PREHEATING),
        ("not_home", "on", "home", "malformed", ControllerStatus.PREHEATING),
        ("not_home", "on", "work", "malformed", ControllerStatus.AWAY),
        ("home", STATE_UNAVAILABLE, "work", "unknown", ControllerStatus.LOW),
    ],
)
async def test_snapshot_policy_states(
    hass, person_state, schedule_state, destination, eta, expected
) -> None:
    """Snapshot inputs produce low, away, and timed travel policy states."""
    _set_baseline_states(hass)
    hass.states.async_set("person.andy", person_state)
    hass.states.async_set("person.alex", STATE_UNKNOWN)
    hass.states.async_set("schedule.heating", schedule_state)
    hass.states.async_set("sensor.destination", destination)
    hass.states.async_set("sensor.eta", eta)

    coordinator, _ = await _setup_coordinator(hass)

    assert coordinator.data.status is expected


async def test_occupancy_conservative_and_coordinate_containment(hass) -> None:
    """Unknown people are conservative, while usable coordinates establish home."""
    _set_baseline_states(hass)
    hass.states.async_set("person.andy", STATE_UNKNOWN)
    hass.states.async_set("person.alex", STATE_UNAVAILABLE)
    coordinator, _ = await _setup_coordinator(hass)
    assert coordinator.data.status is ControllerStatus.LOW

    hass.states.async_set("person.andy", "not_home", {ATTR_LATITUDE: 51.5, ATTR_LONGITUDE: -0.1})
    hass.states.async_set("person.alex", STATE_UNKNOWN)
    await coordinator.async_refresh()
    assert coordinator.data.status is ControllerStatus.LOW

    hass.states.async_remove("zone.home")
    await coordinator.async_refresh()
    assert coordinator.data.status is ControllerStatus.LOW


async def test_one_valid_away_person_with_unknown_person_is_away(hass) -> None:
    """At least one usable away person makes all-nonhome occupancy unoccupied."""
    _set_baseline_states(hass)
    hass.states.async_set("person.andy", "work")
    hass.states.async_set("person.alex", STATE_UNKNOWN)

    coordinator, _ = await _setup_coordinator(hass)

    assert coordinator.data.status is ControllerStatus.AWAY


async def test_unavailable_climate_publishes_without_commands(hass) -> None:
    """A missing or unavailable thermostat publishes Unavailable and does nothing."""
    _set_baseline_states(hass)
    hass.states.async_set("climate.hallway", STATE_UNAVAILABLE)

    coordinator, _ = await _setup_coordinator(hass)

    assert coordinator.data.status is ControllerStatus.UNAVAILABLE
    hass.services.async_call.assert_not_awaited()


async def test_redundant_commands_are_suppressed_independently(hass) -> None:
    """Already-correct mode and target each suppress their corresponding service."""
    _set_baseline_states(hass)
    hass.states.async_set(
        "climate.hallway",
        "heat",
        {"current_temperature": 16.0, "temperature": 17.0, "hvac_action": "idle"},
    )
    coordinator, _ = await _setup_coordinator(hass)
    hass.services.async_call.assert_not_awaited()

    hass.states.async_set(
        "climate.hallway",
        "off",
        {"current_temperature": 16.0, "temperature": 17.0, "hvac_action": "idle"},
    )
    await coordinator.async_refresh()
    assert hass.services.async_call.await_args_list[-1].args == (
        "climate",
        "set_hvac_mode",
        {"entity_id": "climate.hallway", "hvac_mode": "heat"},
    )


async def test_service_failure_clears_pending_and_retries(hass) -> None:
    """A failed service is contained and is attempted again on the next refresh."""
    _set_baseline_states(hass)
    coordinator, _ = await _setup_coordinator(hass)
    hass.services.async_call.reset_mock()
    hass.services.async_call.side_effect = HomeAssistantError("offline")

    await coordinator.async_refresh()
    await coordinator.async_refresh()

    assert hass.services.async_call.await_count == 4
    assert coordinator.pending_hvac_mode is None
    assert coordinator.pending_target is None


async def test_blast_auto_and_expiry_actions(hass) -> None:
    """Blast restarts, Auto-off rejects it, and an expired deadline is cleared."""
    _set_baseline_states(hass)
    coordinator, _ = await _setup_coordinator(hass)

    with patch(
        "custom_components.central_heating_controller.coordinator.dt_util.utcnow",
        return_value=NOW,
    ):
        assert await coordinator.async_start_blast() is True
    first = coordinator.persistent_state.blast_until
    with patch(
        "custom_components.central_heating_controller.coordinator.dt_util.utcnow",
        return_value=NOW + timedelta(minutes=5),
    ):
        assert await coordinator.async_start_blast() is True
        assert coordinator.persistent_state.blast_until > first
        await coordinator.async_refresh()
        assert coordinator.data.status is ControllerStatus.HEAT_BLAST
    with patch(
        "custom_components.central_heating_controller.coordinator.dt_util.utcnow",
        return_value=NOW + timedelta(hours=2),
    ):
        await coordinator.async_refresh()

    assert coordinator.persistent_state.blast_until is None
    await coordinator.async_set_auto_mode(False)
    assert coordinator.data.status is ControllerStatus.OFF
    assert await coordinator.async_start_blast() is False


async def test_learning_accepts_sample_and_persists(hass) -> None:
    """Active-heating observations update and persist the learned model."""
    _set_baseline_states(hass)
    hass.states.async_set(
        "climate.hallway",
        "heat",
        {"current_temperature": 16.0, "temperature": 20.0, "hvac_action": "heating"},
    )
    coordinator, _ = await _setup_coordinator(hass)
    coordinator.store.async_save.reset_mock()
    hass.states.async_set(
        "climate.hallway",
        "heat",
        {"current_temperature": 16.5, "temperature": 20.0, "hvac_action": "heating"},
    )

    with patch(
        "custom_components.central_heating_controller.coordinator.dt_util.utcnow",
        return_value=NOW + timedelta(minutes=30),
    ):
        await coordinator.async_refresh()

    assert coordinator.data.learned_rate == 1.0
    assert coordinator.persistent_state.learned_sample_count == 1
    coordinator.store.async_save.assert_awaited()


async def test_reset_marker_and_explicit_reset(hass) -> None:
    """Setup consumes the reset marker before listeners and explicit reset persists."""
    _set_baseline_states(hass)
    options = {OPT_RESET_LEARNING_REQUESTED: True}
    coordinator, entry = await _setup_coordinator(
        hass, state=PersistentState(learned_rate=1.0, learned_sample_count=3), options=options
    )

    assert OPT_RESET_LEARNING_REQUESTED not in entry.options
    assert coordinator.learner.rate is None
    coordinator.learner._rate = 2.0
    coordinator.learner._sample_count = 4
    await coordinator.async_reset_learning()
    assert coordinator.learner.rate is None
    assert coordinator.persistent_state.learned_sample_count == 0


async def test_shutdown_removes_all_state_listeners(hass) -> None:
    """Coordinator shutdown invokes every event unsubscribe callback."""
    _set_baseline_states(hass)
    unsubs = [Mock() for _ in range(8)]
    with patch(
        "custom_components.central_heating_controller.coordinator.async_track_state_change_event",
        side_effect=unsubs,
    ) as track:
        coordinator, _ = await _setup_coordinator(hass)
        await coordinator.async_shutdown()

    assert track.call_count == 7
    for unsub in unsubs[:7]:
        unsub.assert_called_once_with()


async def test_auto_off_commands_only_hvac_off(hass) -> None:
    """Strict Off never accompanies its HVAC command with a target write."""
    _set_baseline_states(hass)
    hass.states.async_set(
        "climate.hallway",
        "heat",
        {"current_temperature": 16.0, "temperature": 17.0, "hvac_action": "idle"},
    )
    coordinator, _ = await _setup_coordinator(hass)
    hass.services.async_call.reset_mock()

    await coordinator.async_set_auto_mode(False)

    assert hass.services.async_call.await_args_list == [
        (
            ("climate", "set_hvac_mode", {"entity_id": "climate.hallway", "hvac_mode": "off"}),
            {"blocking": True},
        )
    ]


async def test_update_setting_uses_shared_capability_validation(hass) -> None:
    """Runtime settings use config-flow range, ordering, and step validation."""
    from homeassistant.components.climate import ClimateEntityFeature

    _set_baseline_states(hass)
    hass.states.async_set(
        "climate.hallway",
        "heat",
        {
            "current_temperature": 16.0,
            "temperature": 17.0,
            "hvac_action": "idle",
            "supported_features": ClimateEntityFeature.TARGET_TEMPERATURE,
            "hvac_modes": ["off", "heat"],
            "min_temp": 7.0,
            "max_temp": 35.0,
            "target_temp_step": 0.5,
        },
    )
    coordinator, entry = await _setup_coordinator(hass)

    with pytest.raises(HomeAssistantError):
        await coordinator.async_update_setting(CONF_LOW_TEMP, 21.0)
    await coordinator.async_update_setting(CONF_LOW_TEMP, 18.0)

    assert entry.options[CONF_LOW_TEMP] == 18.0


async def test_update_setting_relies_on_single_reload_without_old_writer(hass) -> None:
    """An option update schedules reload without refreshing the old coordinator."""
    from homeassistant.components.climate import ClimateEntityFeature

    _set_baseline_states(hass)
    hass.states.async_set(
        "climate.hallway",
        "heat",
        {
            "current_temperature": 16.0,
            "temperature": 17.0,
            "hvac_action": "idle",
            "supported_features": ClimateEntityFeature.TARGET_TEMPERATURE,
            "hvac_modes": ["off", "heat"],
            "min_temp": 7.0,
            "max_temp": 35.0,
            "target_temp_step": 0.5,
        },
    )
    coordinator, entry = await _setup_coordinator(hass)
    entry.add_update_listener(async_update_listener)
    hass.services.async_call.reset_mock()
    reload_entry = AsyncMock()

    with patch.object(type(hass.config_entries), "async_reload", reload_entry):
        await coordinator.async_update_setting(CONF_LOW_TEMP, 18.0)
        await hass.async_block_till_done()

    reload_entry.assert_awaited_once_with(entry.entry_id)
    hass.services.async_call.assert_not_awaited()


async def test_state_event_requests_refresh(hass) -> None:
    """A subscribed state event requests a coordinator refresh."""
    _set_baseline_states(hass)
    actions = []

    def track(_hass, _entity_id, action):
        actions.append(action)
        return Mock()

    with patch(
        "custom_components.central_heating_controller.coordinator.async_track_state_change_event",
        side_effect=track,
    ):
        coordinator, _ = await _setup_coordinator(hass)
    coordinator.async_request_refresh = AsyncMock()

    actions[0](Mock())
    await hass.async_block_till_done()

    coordinator.async_request_refresh.assert_awaited_once_with()


async def test_entry_setup_forwards_and_reload_listener_runs(hass) -> None:
    """Entry setup publishes runtime data, forwards platforms, and reloads on update."""
    _set_baseline_states(hass)
    entry = _entry()
    entry.add_to_hass(hass)
    coordinator = Mock(async_setup=AsyncMock(), async_shutdown=AsyncMock())
    forward = AsyncMock()
    reload_entry = AsyncMock()

    with (
        patch(
            "custom_components.central_heating_controller.ControllerCoordinator",
            return_value=coordinator,
        ),
        patch.object(type(hass.config_entries), "async_forward_entry_setups", forward),
        patch.object(type(hass.config_entries), "async_reload", reload_entry),
    ):
        assert await async_setup_entry(hass, entry) is True
        await async_update_listener(hass, entry)

    coordinator.async_setup.assert_awaited_once_with()
    assert entry.runtime_data.coordinator is coordinator
    forward.assert_awaited_once()
    reload_entry.assert_awaited_once_with(entry.entry_id)


async def test_entry_unload_shuts_down_only_after_platform_success(hass) -> None:
    """Successful platform unload shuts down and clears coordinator runtime data."""
    entry = _entry()
    entry.add_to_hass(hass)
    coordinator = Mock(async_shutdown=AsyncMock())
    runtime_data = ControllerRuntimeData(coordinator)
    entry.runtime_data = runtime_data
    unload = AsyncMock(return_value=True)

    with patch.object(type(hass.config_entries), "async_unload_platforms", unload):
        assert await async_unload_entry(hass, entry) is True

    coordinator.async_shutdown.assert_awaited_once_with()
    assert entry.runtime_data is runtime_data


@pytest.mark.parametrize(
    ("current_target", "target_step", "expected_calls"),
    [
        (17.24, 0.5, 0),
        (17.26, 0.5, 1),
        (17.005, 0, 0),
        (17.02, -1, 1),
    ],
)
async def test_target_step_half_tolerance_controls_target_write(
    hass, current_target, target_step, expected_calls
) -> None:
    """Half a valid thermostat step is the target redundancy tolerance."""
    _set_baseline_states(hass)
    hass.states.async_set(
        "climate.hallway",
        "heat",
        {
            "current_temperature": 16.0,
            "temperature": current_target,
            "target_temp_step": target_step,
            "hvac_action": "idle",
        },
    )

    await _setup_coordinator(hass)

    assert hass.services.async_call.await_count == expected_calls


async def test_person_coordinates_override_stale_home_state(hass) -> None:
    """Usable coordinates outside the zone override a stale home state."""
    _set_baseline_states(hass)
    hass.states.async_set("person.andy", "home", {ATTR_LATITUDE: 52.0, ATTR_LONGITUDE: -1.0})
    hass.states.async_set("person.alex", STATE_UNKNOWN)

    coordinator, _ = await _setup_coordinator(hass)

    assert coordinator.data.status is ControllerStatus.AWAY


async def test_changed_state_notifies_once_before_service_call(hass) -> None:
    """A changed snapshot reaches listeners once before climate I/O starts."""
    _set_baseline_states(hass)
    coordinator, _ = await _setup_coordinator(hass)
    while coordinator._event_unsubscribers:
        coordinator._event_unsubscribers.pop()()
    events = []
    remove_listener = coordinator.async_add_listener(
        lambda: events.append(("listener", coordinator.data.status))
    )

    async def service_call(*_args, **_kwargs):
        events.append(("service", coordinator.data.status))

    hass.services.async_call.side_effect = service_call
    hass.states.async_set("schedule.heating", "on")

    await coordinator.async_refresh()

    remove_listener()
    assert events == [
        ("listener", ControllerStatus.HIGH),
        ("service", ControllerStatus.HIGH),
        ("service", ControllerStatus.HIGH),
    ]


async def test_startup_publishes_without_commanding_until_running(hass) -> None:
    """Setup evaluates during startup but defers climate writes until running."""
    _set_baseline_states(hass)
    hass.set_state(CoreState.starting)

    coordinator, _ = await _setup_coordinator(hass)

    assert coordinator.data.status is ControllerStatus.LOW
    hass.services.async_call.assert_not_awaited()

    hass.set_state(CoreState.running)
    await coordinator.async_refresh()
    assert hass.services.async_call.await_count == 2


async def test_unexpected_service_failure_notifies_failure_and_recovery(hass) -> None:
    """Early publication never hides coordinator failure or recovery notifications."""
    _set_baseline_states(hass)
    coordinator, _ = await _setup_coordinator(hass)
    while coordinator._event_unsubscribers:
        coordinator._event_unsubscribers.pop()()
    observations = []
    remove_listener = coordinator.async_add_listener(
        lambda: observations.append((coordinator.data.status, coordinator.last_update_success))
    )
    hass.states.async_set("schedule.heating", "on")
    hass.services.async_call.side_effect = RuntimeError("unexpected")

    await coordinator.async_refresh()

    assert observations == [
        (ControllerStatus.HIGH, True),
        (ControllerStatus.HIGH, False),
    ]
    hass.services.async_call.side_effect = None
    await coordinator.async_refresh()
    remove_listener()
    assert observations[-1] == (ControllerStatus.HIGH, True)


async def test_shutdown_waits_for_evaluation_and_stops_followup_target(hass) -> None:
    """Unload waits for active mode I/O and prevents the old target write."""
    _set_baseline_states(hass)
    coordinator, entry = await _setup_coordinator(hass)
    entry.runtime_data = ControllerRuntimeData(coordinator)
    hass.services.async_call.reset_mock()
    mode_started = asyncio.Event()
    release_mode = asyncio.Event()
    services = []

    async def blocked_service(_domain, service, _data, *, blocking):
        services.append(service)
        if service == "set_hvac_mode":
            mode_started.set()
            await release_mode.wait()

    hass.services.async_call.side_effect = blocked_service
    refresh = asyncio.create_task(coordinator.async_refresh())
    await mode_started.wait()
    unload_platforms = AsyncMock(return_value=True)
    with patch.object(type(hass.config_entries), "async_unload_platforms", unload_platforms):
        unload = asyncio.create_task(async_unload_entry(hass, entry))
        await asyncio.sleep(0)
        assert not unload.done()
        release_mode.set()
        await refresh
        assert await unload is True

    assert services == ["set_hvac_mode"]
    hass.services.async_call.side_effect = None
    hass.services.async_call.reset_mock()
    replacement, _ = await _setup_coordinator(hass)
    assert replacement.data.status is ControllerStatus.LOW
    assert hass.services.async_call.await_count == 2


async def test_first_refresh_failure_cleans_state_subscriptions(hass) -> None:
    """Coordinator setup failure removes every acquired listener and timer."""
    _set_baseline_states(hass)
    entry = _entry()
    entry.add_to_hass(hass)
    coordinator = ControllerCoordinator(hass, entry)
    coordinator.store.async_load = AsyncMock(return_value=PersistentState())
    unsubs = [Mock() for _ in range(7)]

    with (
        patch(
            "custom_components.central_heating_controller.coordinator.async_track_state_change_event",
            side_effect=unsubs,
        ),
        patch.object(
            coordinator,
            "async_config_entry_first_refresh",
            AsyncMock(side_effect=RuntimeError("refresh failed")),
        ),
        pytest.raises(RuntimeError, match="refresh failed"),
    ):
        await coordinator.async_setup()

    assert coordinator._shutdown_requested is True
    for unsub in unsubs:
        unsub.assert_called_once_with()


async def test_forward_failure_rolls_back_platforms_and_coordinator(hass) -> None:
    """Platform forwarding failure leaves an inert runtime without masking the error."""
    entry = _entry()
    entry.add_to_hass(hass)
    coordinator = Mock(async_setup=AsyncMock(), async_shutdown=AsyncMock())
    forward = AsyncMock(side_effect=RuntimeError("forward failed"))
    unload = AsyncMock(return_value=True)

    with (
        patch(
            "custom_components.central_heating_controller.ControllerCoordinator",
            return_value=coordinator,
        ),
        patch.object(type(hass.config_entries), "async_forward_entry_setups", forward),
        patch.object(type(hass.config_entries), "async_unload_platforms", unload),
        pytest.raises(RuntimeError, match="forward failed"),
    ):
        await async_setup_entry(hass, entry)

    coordinator.async_shutdown.assert_awaited_once_with()
    unload.assert_awaited_once()
    assert entry.runtime_data.coordinator is coordinator


async def test_started_event_refreshes_without_polling_listener(hass) -> None:
    """A pre-running setup commands immediately when Home Assistant starts."""
    _set_baseline_states(hass)
    hass.set_state(CoreState.starting)
    coordinator, _ = await _setup_coordinator(hass)
    hass.services.async_call.assert_not_awaited()

    hass.set_state(CoreState.running)
    hass.bus.async_fire(EVENT_HOMEASSISTANT_STARTED)
    await hass.async_block_till_done()

    assert coordinator.data.status is ControllerStatus.LOW
    assert hass.services.async_call.await_count == 2


async def test_reset_marker_remains_when_storage_save_fails(hass) -> None:
    """A failed durable reset retains its one-shot marker for setup retry."""
    _set_baseline_states(hass)
    entry = _entry(options={OPT_RESET_LEARNING_REQUESTED: True})
    entry.add_to_hass(hass)
    coordinator = ControllerCoordinator(hass, entry)
    coordinator.store.async_load = AsyncMock(
        return_value=PersistentState(learned_rate=1.0, learned_sample_count=3)
    )
    coordinator.store.async_save = AsyncMock(side_effect=RuntimeError("disk full"))

    with pytest.raises(RuntimeError, match="disk full"):
        await coordinator.async_setup()

    assert entry.options[OPT_RESET_LEARNING_REQUESTED] is True


async def test_cancelled_refresh_does_not_suppress_next_changed_snapshot(hass) -> None:
    """Genuine cancellation clears early-publication duplicate suppression."""
    _set_baseline_states(hass)
    coordinator, _ = await _setup_coordinator(hass)
    while coordinator._event_unsubscribers:
        coordinator._event_unsubscribers.pop()()
    observations = []
    remove_listener = coordinator.async_add_listener(
        lambda: observations.append(coordinator.data.status)
    )
    service_started = asyncio.Event()

    async def blocked_service(*_args, **_kwargs):
        service_started.set()
        await asyncio.Event().wait()

    hass.services.async_call.side_effect = blocked_service
    hass.states.async_set("schedule.heating", "on")
    refresh = asyncio.create_task(coordinator.async_refresh())
    await service_started.wait()
    assert observations == [ControllerStatus.HIGH]

    refresh.cancel()
    with pytest.raises(asyncio.CancelledError):
        await refresh

    hass.services.async_call.side_effect = None
    hass.states.async_set("schedule.heating", "off")
    await coordinator.async_refresh()
    remove_listener()
    assert observations == [ControllerStatus.HIGH, ControllerStatus.LOW]


async def test_unchanged_setting_refreshes_current_coordinator_without_reload(hass) -> None:
    """A no-op option write republishes cleared runtime state locally."""
    from homeassistant.components.climate import ClimateEntityFeature

    _set_baseline_states(hass)
    hass.states.async_set(
        "climate.hallway",
        "heat",
        {
            "current_temperature": 16.0,
            "temperature": 17.0,
            "hvac_action": "idle",
            "supported_features": ClimateEntityFeature.TARGET_TEMPERATURE,
            "hvac_modes": ["off", "heat"],
            "min_temp": 7.0,
            "max_temp": 35.0,
            "target_temp_step": 0.5,
        },
    )
    coordinator, entry = await _setup_coordinator(hass)
    entry.add_update_listener(async_update_listener)
    hass.states.async_set(
        "climate.hallway",
        "heat",
        dict(hass.states.get("climate.hallway").attributes) | {"temperature": 18.5},
    )
    await hass.async_block_till_done()
    assert coordinator.data.status is ControllerStatus.MANUAL_OVERRIDE
    hass.services.async_call.reset_mock()
    reload_entry = AsyncMock()

    with patch.object(type(hass.config_entries), "async_reload", reload_entry):
        await coordinator.async_update_setting(CONF_LOW_TEMP, 17.0)
        await hass.async_block_till_done()

    reload_entry.assert_not_awaited()
    assert coordinator.persistent_state.manual_override_target is None
    assert coordinator.data.status is ControllerStatus.LOW
    assert hass.services.async_call.await_args.args == (
        "climate",
        "set_temperature",
        {"entity_id": "climate.hallway", "temperature": 17.0},
    )


def _set_synchronised_climate(hass, target: float = 17.0, **attributes) -> None:
    """Set an available thermostat already matching the ordinary low policy."""
    hass.states.async_set(
        "climate.hallway",
        "heat",
        {
            "current_temperature": 16.0,
            "temperature": target,
            "target_temp_step": 0.5,
            "hvac_action": "idle",
        }
        | attributes,
    )


async def test_own_target_acknowledgement_clears_pending_without_override(hass) -> None:
    """The thermostat echo of our target write is an acknowledgement."""
    _set_baseline_states(hass)
    coordinator, _ = await _setup_coordinator(hass)
    assert coordinator.pending_target == 17.0

    _set_synchronised_climate(hass)
    await hass.async_block_till_done()

    assert coordinator.pending_target is None
    assert coordinator.persistent_state.manual_override_target is None
    assert coordinator.data.status is ControllerStatus.LOW


async def test_own_hvac_acknowledgement_clears_pending_without_override(hass) -> None:
    """The thermostat echo of our HVAC write does not become an override."""
    _set_baseline_states(hass)
    coordinator, _ = await _setup_coordinator(hass)
    assert coordinator.pending_hvac_mode == "heat"
    old = hass.states.get("climate.hallway")
    assert old is not None

    hass.states.async_set("climate.hallway", "heat", dict(old.attributes))
    await hass.async_block_till_done()

    assert coordinator.pending_hvac_mode is None
    assert coordinator.persistent_state.manual_override_target is None


async def test_external_target_creates_and_persists_manual_override(hass) -> None:
    """An external finite target captures the target and stable policy fingerprint."""
    _set_baseline_states(hass)
    _set_synchronised_climate(hass)
    coordinator, _ = await _setup_coordinator(hass)
    coordinator.store.async_save.reset_mock()

    _set_synchronised_climate(hass, 18.5)
    await hass.async_block_till_done()

    expected = (True, False, False, False, 20.0, 17.0, 14.0, "heat")
    assert coordinator.data.status is ControllerStatus.MANUAL_OVERRIDE
    assert coordinator.persistent_state.manual_override_target == 18.5
    assert coordinator.persistent_state.manual_override_fingerprint == expected
    coordinator.store.async_save.assert_awaited_once_with(coordinator.persistent_state)


async def test_mismatched_pending_target_is_external_override(hass) -> None:
    """A target echo differing from our outstanding command is external."""
    _set_baseline_states(hass)
    coordinator, _ = await _setup_coordinator(hass)
    assert coordinator.pending_target == 17.0

    _set_synchronised_climate(hass, 18.5)
    await hass.async_block_till_done()

    assert coordinator.pending_target is None
    assert coordinator.persistent_state.manual_override_target == 18.5
    assert coordinator.data.status is ControllerStatus.MANUAL_OVERRIDE


async def test_external_hvac_only_change_is_corrected_without_override(hass) -> None:
    """Changing only HVAC mode invokes policy correction, not target capture."""
    _set_baseline_states(hass)
    _set_synchronised_climate(hass)
    coordinator, _ = await _setup_coordinator(hass)
    hass.services.async_call.reset_mock()
    old = hass.states.get("climate.hallway")
    assert old is not None

    hass.states.async_set("climate.hallway", "off", dict(old.attributes))
    await hass.async_block_till_done()

    assert coordinator.persistent_state.manual_override_target is None
    assert hass.services.async_call.await_args_list == [
        (
            ("climate", "set_hvac_mode", {"entity_id": "climate.hallway", "hvac_mode": "heat"}),
            {"blocking": True},
        )
    ]


@pytest.mark.parametrize("mode", ["auto_off", "blast"])
async def test_external_target_is_ignored_outside_normal_auto_policy(hass, mode) -> None:
    """Strict Off and Heat blast cannot be replaced by a manual override."""
    _set_baseline_states(hass)
    _set_synchronised_climate(hass)
    coordinator, _ = await _setup_coordinator(hass)
    if mode == "auto_off":
        await coordinator.async_set_auto_mode(False)
    else:
        await coordinator.async_start_blast()

    _set_synchronised_climate(hass, 18.5)
    await hass.async_block_till_done()

    assert coordinator.persistent_state.manual_override_target is None
    assert coordinator.data.status is (
        ControllerStatus.OFF if mode == "auto_off" else ControllerStatus.HEAT_BLAST
    )


async def _create_override(hass, target: float = 18.5):
    _set_baseline_states(hass)
    _set_synchronised_climate(hass)
    coordinator, _ = await _setup_coordinator(hass)
    _set_synchronised_climate(hass, target)
    await hass.async_block_till_done()
    assert coordinator.data.status is ControllerStatus.MANUAL_OVERRIDE
    coordinator.store.async_save.reset_mock()
    hass.services.async_call.reset_mock()
    return coordinator


@pytest.mark.parametrize("change", ["temperature", "action", "minute", "eta"])
async def test_non_policy_changes_retain_manual_override(hass, change) -> None:
    """Transient thermostat, time, and raw ETA changes retain an override."""
    coordinator = await _create_override(hass)
    if change == "temperature":
        _set_synchronised_climate(hass, 18.5, current_temperature=16.5)
        await hass.async_block_till_done()
    elif change == "action":
        _set_synchronised_climate(hass, 18.5, hvac_action="heating")
        await hass.async_block_till_done()
    elif change == "eta":
        hass.states.async_set("sensor.eta", "2026-06-28T20:00:00+00:00")
        await hass.async_block_till_done()
    else:
        with patch(
            "custom_components.central_heating_controller.coordinator.dt_util.utcnow",
            return_value=NOW + timedelta(minutes=1),
        ):
            await coordinator.async_refresh()

    assert coordinator.data.status is ControllerStatus.MANUAL_OVERRIDE
    assert coordinator.persistent_state.manual_override_target == 18.5
    coordinator.store.async_save.assert_not_awaited()


@pytest.mark.parametrize("change", ["schedule", "occupancy", "destination", "preheat"])
async def test_policy_fingerprint_changes_clear_override(hass, change) -> None:
    """Every policy-relevant state transition ends a manual override."""
    if change == "preheat":
        _set_baseline_states(hass)
        hass.states.async_set("person.andy", "work")
        hass.states.async_set("sensor.destination", "home")
        hass.states.async_set("sensor.eta", "2026-06-28T18:30:00+00:00")
        _set_synchronised_climate(hass, 14.0)
        coordinator, _ = await _setup_coordinator(hass)
        with patch(
            "custom_components.central_heating_controller.coordinator.dt_util.utcnow",
            return_value=NOW,
        ):
            _set_synchronised_climate(hass, 18.5)
            await hass.async_block_till_done()
        assert coordinator.data.status is ControllerStatus.MANUAL_OVERRIDE
        coordinator.store.async_save.reset_mock()
        hass.services.async_call.reset_mock()
        with patch(
            "custom_components.central_heating_controller.coordinator.dt_util.utcnow",
            return_value=NOW,
        ):
            hass.states.async_set("sensor.eta", "2026-06-28T17:30:00+00:00")
            await hass.async_block_till_done()
    else:
        coordinator = await _create_override(hass)
    if change == "schedule":
        hass.states.async_set("schedule.heating", "on")
    elif change == "occupancy":
        hass.states.async_set("person.andy", "work")
    elif change == "destination":
        hass.states.async_set("sensor.destination", "home")
    await hass.async_block_till_done()
    with patch(
        "custom_components.central_heating_controller.coordinator.dt_util.utcnow",
        return_value=NOW,
    ):
        await coordinator.async_refresh()

    assert coordinator.data.status is not ControllerStatus.MANUAL_OVERRIDE
    assert coordinator.persistent_state.manual_override_target is None
    coordinator.store.async_save.assert_awaited()
    expected_target = {
        "schedule": 20.0,
        "occupancy": 14.0,
        "destination": 17.0,
        "preheat": 20.0,
    }[change]
    assert (
        "climate",
        "set_temperature",
        {"entity_id": "climate.hallway", "temperature": expected_target},
    ) in [call.args for call in hass.services.async_call.await_args_list]


async def test_person_change_without_aggregate_occupancy_change_retains_override(hass) -> None:
    """Movement by one person keeps override when someone remains home."""
    coordinator = await _create_override(hass)
    hass.states.async_set("person.alex", "home")
    await hass.async_block_till_done()
    coordinator.store.async_save.reset_mock()

    hass.states.async_set("person.andy", "work")
    await hass.async_block_till_done()

    assert coordinator.data.status is ControllerStatus.MANUAL_OVERRIDE
    coordinator.store.async_save.assert_not_awaited()


@pytest.mark.parametrize("live_target", [18.6, 18.8])
async def test_override_target_tolerance_on_refresh(hass, live_target) -> None:
    """Half-step target tolerance retains near values and rejects larger drift."""
    coordinator = await _create_override(hass)
    while coordinator._event_unsubscribers:
        coordinator._event_unsubscribers.pop()()
    _set_synchronised_climate(hass, live_target)

    await coordinator.async_refresh()

    if live_target == 18.6:
        assert coordinator.data.status is ControllerStatus.MANUAL_OVERRIDE
    else:
        assert coordinator.data.status is ControllerStatus.LOW
        assert coordinator.persistent_state.manual_override_target is None


@pytest.mark.parametrize("mismatch", [None, "target", "fingerprint"])
async def test_restart_restores_override_only_when_target_and_fingerprint_match(
    hass, mismatch
) -> None:
    """Restart restoration validates both the live target and policy fingerprint."""
    _set_baseline_states(hass)
    _set_synchronised_climate(hass, 18.0 if mismatch == "target" else 18.5)
    fingerprint = (
        True,
        mismatch == "fingerprint",
        False,
        False,
        20.0,
        17.0,
        14.0,
        "heat",
    )
    state = PersistentState(
        manual_override_target=18.5,
        manual_override_fingerprint=fingerprint,
    )

    coordinator, _ = await _setup_coordinator(hass, state=state)

    assert coordinator.data.status is (
        ControllerStatus.MANUAL_OVERRIDE if mismatch is None else ControllerStatus.LOW
    )
    assert coordinator.persistent_state.manual_override_target == (
        18.5 if mismatch is None else None
    )


async def test_unavailable_restart_retains_override_then_validates_recovery(hass) -> None:
    """Unavailable startup retains durable override until a live target returns."""
    _set_baseline_states(hass)
    hass.states.async_set("climate.hallway", STATE_UNAVAILABLE)
    state = PersistentState(
        manual_override_target=18.5,
        manual_override_fingerprint=(True, False, False, False, 20.0, 17.0, 14.0, "heat"),
    )
    coordinator, _ = await _setup_coordinator(hass, state=state)
    assert coordinator.data.status is ControllerStatus.UNAVAILABLE
    assert coordinator.persistent_state.manual_override_target == 18.5

    _set_synchronised_climate(hass, 17.0)
    await hass.async_block_till_done()

    assert coordinator.data.status is ControllerStatus.LOW
    assert coordinator.persistent_state.manual_override_target is None


@pytest.mark.parametrize("future", [True, False])
async def test_restart_restores_only_future_heat_blast(hass, future) -> None:
    """Future blast deadlines survive restart while expired deadlines are cleaned."""
    _set_baseline_states(hass)
    _set_synchronised_climate(hass)
    deadline = NOW + (timedelta(minutes=10) if future else -timedelta(minutes=1))
    coordinator, _ = await _setup_coordinator(hass, state=PersistentState(blast_until=deadline))

    assert coordinator.data.status is (
        ControllerStatus.HEAT_BLAST if future else ControllerStatus.LOW
    )
    assert coordinator.persistent_state.blast_until == (deadline if future else None)


async def test_shutdown_drains_active_external_event_and_blocks_followup_work(hass) -> None:
    """Unload waits for event persistence and prevents refresh or later writes."""
    _set_baseline_states(hass)
    _set_synchronised_climate(hass)
    coordinator, _ = await _setup_coordinator(hass)
    save_started = asyncio.Event()
    release_save = asyncio.Event()

    async def blocked_save(_state):
        save_started.set()
        await release_save.wait()

    coordinator.store.async_save.side_effect = blocked_save
    hass.services.async_call.reset_mock()
    _set_synchronised_climate(hass, 18.5)
    await save_started.wait()

    shutdown = asyncio.create_task(coordinator.async_shutdown())
    await asyncio.sleep(0)
    assert not shutdown.done()
    release_save.set()
    await shutdown
    hass.services.async_call.assert_not_awaited()

    coordinator.store.async_save.reset_mock()
    _set_synchronised_climate(hass, 19.0)
    await hass.async_block_till_done()
    coordinator.store.async_save.assert_not_awaited()
