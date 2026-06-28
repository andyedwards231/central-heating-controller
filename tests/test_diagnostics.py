"""Tests for privacy-safe integration diagnostics."""

from datetime import datetime, timedelta, timezone
import json
from unittest.mock import AsyncMock, patch

from pytest_homeassistant_custom_component.common import MockConfigEntry

from homeassistant.components.diagnostics import REDACTED
from homeassistant.const import ATTR_LATITUDE, ATTR_LONGITUDE, STATE_UNAVAILABLE

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
)
from custom_components.central_heating_controller.coordinator import ControllerCoordinator
from custom_components.central_heating_controller.diagnostics import (
    async_get_config_entry_diagnostics,
)
from custom_components.central_heating_controller.models import (
    ControllerRuntimeData,
    PersistentState,
)


NOW = datetime(2026, 6, 28, 17, 0, tzinfo=timezone.utc)


async def test_config_entry_diagnostics_are_useful_and_explicitly_redacted(
    hass, monkeypatch
) -> None:
    """Diagnostics retain safe control data without location or journey details."""
    monkeypatch.setattr(type(hass.services), "async_call", AsyncMock())
    hass.states.async_set(
        "climate.secret_thermostat",
        "heat",
        {"current_temperature": 16.0, "temperature": 20.0, "hvac_action": "idle"},
    )
    hass.states.async_set(
        "zone.private_home",
        "0",
        {
            ATTR_LATITUDE: 51.501234,
            ATTR_LONGITUDE: -0.141234,
            "friendly_name": "Private Home",
        },
    )
    hass.states.async_set(
        "person.private_person",
        "not_home",
        {ATTR_LATITUDE: 52.123456, ATTR_LONGITUDE: -1.234567},
    )
    hass.states.async_set("schedule.private_schedule", "on")
    hass.states.async_set("sensor.private_vehicle_destination", "Private Home")
    hass.states.async_set("sensor.private_vehicle_eta", "2026-06-28T17:30:00+00:00")
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_CLIMATE: "climate.secret_thermostat",
            CONF_PERSONS: ["person.private_person"],
            CONF_HOME_ZONE: "zone.private_home",
            CONF_SCHEDULE: "schedule.private_schedule",
            CONF_DESTINATION: "sensor.private_vehicle_destination",
            CONF_ARRIVAL_TIME: "sensor.private_vehicle_eta",
        },
        options={
            CONF_ACTIVE_HVAC_MODE: "heat",
            CONF_HIGH_TEMP: 20.0,
            CONF_LOW_TEMP: 17.0,
            CONF_ECO_TEMP: 14.0,
            CONF_FALLBACK_MINUTES: 60,
            CONF_MAX_WARMUP_MINUTES: 180,
            CONF_DESTINATION_HOME_VALUE: "Private Home",
        },
    )
    entry.add_to_hass(hass)
    state = PersistentState(
        auto_mode=True,
        blast_until=NOW + timedelta(minutes=30),
        manual_override_fingerprint=(
            False,
            True,
            "Private Home",
            "2026-06-28T17:30:00+00:00",
        ),
        learned_rate=1.25,
        learned_sample_count=4,
    )
    coordinator = ControllerCoordinator(hass, entry)
    coordinator.store.async_load = AsyncMock(return_value=state)
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
    entry.runtime_data = ControllerRuntimeData(coordinator)

    with patch(
        "custom_components.central_heating_controller.diagnostics.dt_util.utcnow",
        return_value=NOW,
    ):
        diagnostics = await async_get_config_entry_diagnostics(hass, entry)

    assert diagnostics["entry"]["data"] == {
        CONF_CLIMATE: REDACTED,
        CONF_PERSONS: REDACTED,
        CONF_HOME_ZONE: REDACTED,
        CONF_SCHEDULE: REDACTED,
        CONF_DESTINATION: REDACTED,
        CONF_ARRIVAL_TIME: REDACTED,
    }
    assert diagnostics["entry"]["options"] == {
        CONF_ACTIVE_HVAC_MODE: "heat",
        CONF_HIGH_TEMP: 20.0,
        CONF_LOW_TEMP: 17.0,
        CONF_ECO_TEMP: 14.0,
        CONF_FALLBACK_MINUTES: 60,
        CONF_MAX_WARMUP_MINUTES: 180,
        CONF_DESTINATION_HOME_VALUE: REDACTED,
    }
    assert diagnostics["controller"] == {
        "status": "heat_blast",
        "reason": "blast_active",
        "auto_mode": True,
        "blast_active": True,
        "learned_sample_count": 4,
        "learned_rate": 1.25,
    }
    assert diagnostics["entity_availability"] == {
        CONF_CLIMATE: True,
        CONF_PERSONS: True,
        CONF_HOME_ZONE: True,
        CONF_SCHEDULE: True,
        CONF_DESTINATION: True,
        CONF_ARRIVAL_TIME: True,
    }
    assert diagnostics["sensitive_state"] == {
        "arrival_time": REDACTED,
        "preheat_start_time": REDACTED,
        "blast_until": REDACTED,
        "manual_override_fingerprint": REDACTED,
    }

    serialized = json.dumps(diagnostics)
    for secret in (
        "climate.secret_thermostat",
        "person.private_person",
        "zone.private_home",
        "schedule.private_schedule",
        "sensor.private_vehicle_destination",
        "sensor.private_vehicle_eta",
        "Private Home",
        "51.501234",
        "-0.141234",
        "52.123456",
        "-1.234567",
        "2026-06-28T17:30:00+00:00",
        "2026-06-28T17:30:00Z",
    ):
        assert secret not in serialized

    hass.config_entries.async_update_entry(
        entry,
        options=dict(entry.options) | {CONF_ARRIVAL_TIME: None},
    )
    diagnostics = await async_get_config_entry_diagnostics(hass, entry)
    assert diagnostics["entity_availability"][CONF_ARRIVAL_TIME] is None

    hass.config_entries.async_update_entry(
        entry,
        options=dict(entry.options) | {CONF_ARRIVAL_TIME: "sensor.removed_eta"},
    )
    diagnostics = await async_get_config_entry_diagnostics(hass, entry)
    assert diagnostics["entity_availability"][CONF_ARRIVAL_TIME] is False

    hass.config_entries.async_update_entry(
        entry,
        options=dict(entry.options) | {CONF_ARRIVAL_TIME: "sensor.private_vehicle_eta"},
    )
    hass.states.async_set("sensor.private_vehicle_eta", STATE_UNAVAILABLE)
    diagnostics = await async_get_config_entry_diagnostics(hass, entry)
    assert diagnostics["entity_availability"][CONF_ARRIVAL_TIME] is False

    await coordinator.async_shutdown()
