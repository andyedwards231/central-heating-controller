"""Focused tests for explicit Auto, Away and Off controller modes."""

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock
from unittest.mock import patch

import pytest

from homeassistant.const import ATTR_LATITUDE, ATTR_LONGITUDE

from custom_components.central_heating_controller.models import PersistentState
from tests.test_coordinator import _setup_coordinator, _set_baseline_states


NOW = datetime(2026, 6, 28, 17, 0, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def register_test_coordinators(hass, monkeypatch):
    """Provide the lifecycle list expected by the shared coordinator helper."""
    hass.data["test_coordinators"] = []
    service_call = AsyncMock()
    monkeypatch.setattr(type(hass.services), "async_call", service_call)
    return service_call


@pytest.fixture(autouse=True)
async def shutdown_test_coordinators(hass):
    """Shut down direct coordinator instances created by the shared helper."""
    yield
    for coordinator in hass.data.get("test_coordinators", []):
        await coordinator.async_shutdown()


@pytest.mark.parametrize(
    ("mode", "auto_mode", "forced_away"),
    [("auto", True, False), ("away", True, True), ("off", False, False)],
)
async def test_setting_controller_mode_persists_each_mode(hass, mode, auto_mode, forced_away):
    """Each selector option maps to durable controller mode state."""
    _set_baseline_states(hass)
    coordinator, _ = await _setup_coordinator(hass)

    await coordinator.async_set_controller_mode(mode)

    assert coordinator.controller_mode == mode
    assert coordinator.persistent_state.auto_mode is auto_mode
    assert coordinator.persistent_state.forced_away is forced_away
    coordinator.store.async_save.assert_awaited()


async def test_away_selected_at_home_waits_for_real_absence_and_return(hass):
    """Selecting Away while home does not count the current home state as absence."""
    _set_baseline_states(hass)
    hass.states.async_set("person.alex", "home", {ATTR_LATITUDE: 51.5, ATTR_LONGITUDE: -0.1})
    coordinator, _ = await _setup_coordinator(hass)

    await coordinator.async_set_controller_mode("away")
    assert coordinator.controller_mode == "away"
    assert coordinator.persistent_state.away_seen_absent == []

    hass.states.async_set("person.andy", "work")
    await hass.async_block_till_done()
    assert coordinator.controller_mode == "away"
    assert "person.andy" in coordinator.persistent_state.away_seen_absent

    hass.states.async_set("person.andy", "home", {ATTR_LATITUDE: 51.5, ATTR_LONGITUDE: -0.1})
    await hass.async_block_till_done()
    assert coordinator.controller_mode == "auto"
    assert coordinator.persistent_state.away_seen_absent == []


async def test_unknown_person_state_is_not_treated_as_absence(hass):
    """Unknown or unavailable tracker states must not arm an Away return."""
    _set_baseline_states(hass)
    hass.states.async_set("person.alex", "home", {ATTR_LATITUDE: 51.5, ATTR_LONGITUDE: -0.1})
    coordinator, _ = await _setup_coordinator(hass)
    await coordinator.async_set_controller_mode("away")

    hass.states.async_set("person.andy", "unknown")
    await hass.async_block_till_done()
    assert coordinator.controller_mode == "away"
    assert coordinator.persistent_state.away_seen_absent == []


async def test_homeward_journey_does_not_clear_away_without_person_return(hass):
    """A valid, current journey home clears Away even before person arrival."""
    _set_baseline_states(hass)
    hass.states.async_set("person.alex", "home", {ATTR_LATITUDE: 51.5, ATTR_LONGITUDE: -0.1})
    coordinator, _ = await _setup_coordinator(hass)
    await coordinator.async_set_controller_mode("away")

    hass.states.async_set("sensor.destination", "home")
    hass.states.async_set("sensor.eta", (datetime.now(timezone.utc) + timedelta(minutes=30)).isoformat())
    await hass.async_block_till_done()

    assert coordinator.controller_mode == "auto"
    assert coordinator.persistent_state.away_seen_absent == []


async def test_homeward_destination_with_invalid_eta_does_not_clear_away(hass):
    """A home destination without a usable ETA does not clear Away mode."""
    _set_baseline_states(hass)
    hass.states.async_set("person.alex", "home", {ATTR_LATITUDE: 51.5, ATTR_LONGITUDE: -0.1})
    coordinator, _ = await _setup_coordinator(hass)
    await coordinator.async_set_controller_mode("away")

    hass.states.async_set("sensor.destination", "home")
    hass.states.async_set("sensor.eta", "unknown")
    await hass.async_block_till_done()

    assert coordinator.controller_mode == "away"


async def test_stale_homeward_destination_does_not_clear_away(hass):
    """A destination older than 30 minutes cannot clear Away mode."""
    _set_baseline_states(hass)
    hass.states.async_set("person.alex", "home", {ATTR_LATITUDE: 51.5, ATTR_LONGITUDE: -0.1})
    coordinator, _ = await _setup_coordinator(hass)
    await coordinator.async_set_controller_mode("away")
    for unsubscribe in coordinator._event_unsubscribers:
        unsubscribe()
    coordinator._event_unsubscribers.clear()
    hass.states.async_set("sensor.destination", "home")
    hass.states.async_set(
        "sensor.eta", (NOW + timedelta(hours=1)).isoformat()
    )
    hass.states.get("sensor.destination").last_reported = (
        NOW - timedelta(minutes=31)
    )
    assert hass.states.get("sensor.destination").last_reported == NOW - timedelta(minutes=31)
    with patch(
        "custom_components.central_heating_controller.coordinator.dt_util.utcnow",
        return_value=NOW,
    ):
        await coordinator.async_refresh()

    assert coordinator.controller_mode == "away"


async def test_invalid_controller_mode_is_rejected_without_state_change(hass):
    """Unsupported mode names fail without changing the saved operating mode."""
    _set_baseline_states(hass)
    coordinator, _ = await _setup_coordinator(hass)

    with pytest.raises(ValueError, match="Unsupported controller mode"):
        await coordinator.async_set_controller_mode("vacation")

    assert coordinator.controller_mode == "auto"
    assert coordinator.persistent_state.auto_mode is True


async def test_arrival_of_another_armed_person_clears_away(hass):
    """A return by another tracked person is recognised after their own departure."""
    _set_baseline_states(hass)
    hass.states.async_set("person.andy", "not_home")
    hass.states.async_set("person.alex", "not_home")
    coordinator, _ = await _setup_coordinator(hass)
    await coordinator.async_set_controller_mode("away")
    assert set(coordinator.persistent_state.away_seen_absent) == {"person.andy", "person.alex"}

    hass.states.async_set("person.alex", "home", {ATTR_LATITUDE: 51.5, ATTR_LONGITUDE: -0.1})
    await hass.async_block_till_done()

    assert coordinator.controller_mode == "auto"
    assert coordinator.persistent_state.away_seen_absent == []


async def test_off_stays_off_when_climate_is_off_and_journey_is_home(hass):
    """A homeward journey cannot re-enable an explicitly disabled controller."""
    _set_baseline_states(hass)
    hass.states.async_set("climate.hallway", "off", {
        "current_temperature": 16.0, "temperature": 15.0, "hvac_action": "idle",
    })
    hass.states.async_set("sensor.destination", "home")
    hass.states.async_set("sensor.eta", NOW.isoformat())
    coordinator, _ = await _setup_coordinator(hass)

    await coordinator.async_set_controller_mode("off")

    assert coordinator.controller_mode == "off"
    assert coordinator.persistent_state.auto_mode is False
    assert coordinator.data.status.value == "off"


async def test_away_mode_is_restored_from_persistent_state(hass):
    """A restart restores Away rather than silently reverting to Auto."""
    _set_baseline_states(hass)
    state = PersistentState(
        auto_mode=True,
        forced_away=True,
        away_seen_absent=["person.alex"],
        away_selected_at=NOW,
    )

    coordinator, _ = await _setup_coordinator(hass, state=state)

    assert coordinator.controller_mode == "away"
    assert coordinator.persistent_state.away_seen_absent == ["person.alex"]

