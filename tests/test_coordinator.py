"""Tests for controller persistence and coordination."""

from datetime import datetime, timedelta, timezone
import math
from unittest.mock import AsyncMock, Mock, patch

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from homeassistant.const import ATTR_LATITUDE, ATTR_LONGITUDE, STATE_UNAVAILABLE, STATE_UNKNOWN
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
from custom_components.central_heating_controller.models import PersistentState
from custom_components.central_heating_controller.models import ControllerStatus
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
    from custom_components.central_heating_controller.models import ControllerRuntimeData

    entry.runtime_data = ControllerRuntimeData(coordinator)
    unload = AsyncMock(return_value=True)

    with patch.object(type(hass.config_entries), "async_unload_platforms", unload):
        assert await async_unload_entry(hass, entry) is True

    coordinator.async_shutdown.assert_awaited_once_with()
    assert entry.runtime_data is None
