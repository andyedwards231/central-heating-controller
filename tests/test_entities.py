"""Tests for Central Heating Controller entities."""

from datetime import datetime, timezone
import json
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from homeassistant.components.climate import ClimateEntityFeature
from homeassistant.components.number import ATTR_MAX, ATTR_MIN, ATTR_STEP
from homeassistant.components.sensor import (
    ATTR_OPTIONS,
    ATTR_STATE_CLASS,
    SensorDeviceClass,
    SensorStateClass,
)
from homeassistant.const import (
    ATTR_DEVICE_CLASS,
    ATTR_TEMPERATURE,
    ATTR_UNIT_OF_MEASUREMENT,
    UnitOfTemperature,
)
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import device_registry as dr, entity_registry as er

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
from custom_components.central_heating_controller.models import ControllerStatus, PersistentState

ENTITY_KEYS = (
    "auto_mode",
    "heat_blast",
    "high_temperature",
    "low_temperature",
    "eco_temperature",
    "fallback_warmup_minutes",
    "maximum_warmup_minutes",
    "status",
    "effective_target_temperature",
    "learned_heating_rate",
    "preheat_start_time",
)


def _entry() -> MockConfigEntry:
    """Return a fully configured controller entry."""
    return MockConfigEntry(
        domain=DOMAIN,
        title="Central Heating Controller",
        data={
            CONF_CLIMATE: "climate.hallway",
            CONF_PERSONS: ["person.andy"],
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
        },
    )


def _set_states(hass) -> None:
    """Set a stable occupied-low controller snapshot."""
    hass.states.async_set(
        "climate.hallway",
        "heat",
        {
            "current_temperature": 16.0,
            ATTR_TEMPERATURE: 17.0,
            "hvac_action": "idle",
            "supported_features": ClimateEntityFeature.TARGET_TEMPERATURE,
            "hvac_modes": ["off", "heat"],
            "min_temp": 7.0,
            "max_temp": 35.0,
            "target_temp_step": 0.5,
            ATTR_UNIT_OF_MEASUREMENT: UnitOfTemperature.CELSIUS,
        },
    )
    hass.states.async_set(
        "zone.home",
        "1",
        {
            "latitude": 51.5,
            "longitude": -0.1,
            "radius": 100,
            "friendly_name": "Home",
        },
    )
    hass.states.async_set("person.andy", "home", {"latitude": 51.5, "longitude": -0.1})
    hass.states.async_set("schedule.heating", "off")
    hass.states.async_set("sensor.destination", "work")
    hass.states.async_set("sensor.eta", "unknown")


@pytest.fixture
async def controller(hass):
    """Set up the complete integration and return its entry and coordinator."""
    _set_states(hass)
    entry = _entry()
    entry.add_to_hass(hass)

    async def set_hvac_mode(call) -> None:
        state = hass.states.get("climate.hallway")
        assert state is not None
        hass.states.async_set(
            state.entity_id,
            call.data["hvac_mode"],
            state.attributes,
            context=call.context,
        )

    async def set_temperature(call) -> None:
        state = hass.states.get("climate.hallway")
        assert state is not None
        hass.states.async_set(
            state.entity_id,
            state.state,
            dict(state.attributes) | {ATTR_TEMPERATURE: call.data[ATTR_TEMPERATURE]},
            context=call.context,
        )

    hass.services.async_register("climate", "set_hvac_mode", set_hvac_mode)
    hass.services.async_register("climate", "set_temperature", set_temperature)

    with (
        patch(
            "custom_components.central_heating_controller.storage.ControllerStore.async_load",
            AsyncMock(return_value=PersistentState()),
        ),
        patch(
            "custom_components.central_heating_controller.storage.ControllerStore.async_save",
            AsyncMock(),
        ),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        yield entry, entry.runtime_data.coordinator
        await hass.config_entries.async_unload(entry.entry_id)


def _entity_id(hass, entry, platform: str, key: str) -> str:
    """Resolve one entity by its required stable unique ID."""
    entity_id = er.async_get(hass).async_get_entity_id(platform, DOMAIN, f"{entry.entry_id}_{key}")
    assert entity_id is not None
    return entity_id


async def test_entities_share_one_service_device_and_stable_unique_ids(hass, controller) -> None:
    """All entities belong to one service device and keep exact unique IDs."""
    entry, _coordinator = controller
    entity_registry = er.async_get(hass)
    device_registry = dr.async_get(hass)

    entries = [
        entity_registry.async_get_entity_id(platform, DOMAIN, f"{entry.entry_id}_{key}")
        for platform, key in (
            ("switch", "auto_mode"),
            ("button", "heat_blast"),
            ("number", "high_temperature"),
            ("number", "low_temperature"),
            ("number", "eco_temperature"),
            ("number", "fallback_warmup_minutes"),
            ("number", "maximum_warmup_minutes"),
            ("sensor", "status"),
            ("sensor", "effective_target_temperature"),
            ("sensor", "learned_heating_rate"),
            ("sensor", "preheat_start_time"),
        )
    ]

    assert all(entries)
    registry_entries = [entity_registry.async_get(entity_id) for entity_id in entries]
    assert {item.unique_id for item in registry_entries if item is not None} == {
        f"{entry.entry_id}_{key}" for key in ENTITY_KEYS
    }
    assert len({item.device_id for item in registry_entries if item is not None}) == 1
    device = device_registry.async_get(next(iter(registry_entries)).device_id)
    assert device is not None
    assert device.name == "Central Heating Controller"
    assert device.identifiers == {(DOMAIN, entry.entry_id)}
    assert device.entry_type is dr.DeviceEntryType.SERVICE


async def test_entity_states_availability_classes_units_and_attributes(hass, controller) -> None:
    """Entities publish modern state metadata and omit absent status details."""
    entry, _coordinator = controller

    assert hass.states.get(_entity_id(hass, entry, "switch", "auto_mode")).state == "on"
    assert hass.states.get(_entity_id(hass, entry, "button", "heat_blast")).state != "unavailable"

    expected_numbers = {
        "high_temperature": ("20.0", UnitOfTemperature.CELSIUS, 7.0, 35.0, 0.5),
        "low_temperature": ("17.0", UnitOfTemperature.CELSIUS, 7.0, 35.0, 0.5),
        "eco_temperature": ("14.0", UnitOfTemperature.CELSIUS, 7.0, 35.0, 0.5),
        "fallback_warmup_minutes": ("60.0", "min", 5.0, 360.0, 5.0),
        "maximum_warmup_minutes": ("180.0", "min", 5.0, 360.0, 5.0),
    }
    for key, (value, unit, minimum, maximum, step) in expected_numbers.items():
        state = hass.states.get(_entity_id(hass, entry, "number", key))
        assert state is not None
        assert state.state == value
        assert state.attributes[ATTR_UNIT_OF_MEASUREMENT] == unit
        assert state.attributes[ATTR_MIN] == minimum
        assert state.attributes[ATTR_MAX] == maximum
        assert state.attributes[ATTR_STEP] == step

    status = hass.states.get(_entity_id(hass, entry, "sensor", "status"))
    assert status is not None
    assert status.state == ControllerStatus.LOW
    assert status.attributes[ATTR_DEVICE_CLASS] == SensorDeviceClass.ENUM
    assert status.attributes[ATTR_OPTIONS] == [item.value for item in ControllerStatus]
    assert status.attributes["reason"] == "schedule_off_or_unavailable"
    assert status.attributes["current_temperature"] == 16.0
    assert status.attributes["effective_target_temperature"] == 17.0
    for absent in (
        "learned_heating_rate",
        "manual_override",
        "arrival_time",
        "preheat_start_time",
    ):
        assert absent not in status.attributes

    target = hass.states.get(_entity_id(hass, entry, "sensor", "effective_target_temperature"))
    assert target is not None
    assert target.state == "17.0"
    assert target.attributes[ATTR_DEVICE_CLASS] == SensorDeviceClass.TEMPERATURE
    assert target.attributes[ATTR_STATE_CLASS] == SensorStateClass.MEASUREMENT
    assert target.attributes[ATTR_UNIT_OF_MEASUREMENT] == UnitOfTemperature.CELSIUS

    assert (
        hass.states.get(_entity_id(hass, entry, "sensor", "learned_heating_rate")).state
        == "unavailable"
    )
    assert (
        hass.states.get(_entity_id(hass, entry, "sensor", "preheat_start_time")).state
        == "unavailable"
    )


async def test_auto_and_heat_blast_services(hass, controller) -> None:
    """Auto immediately re-evaluates and Heat Blast remains safely pressable."""
    entry, coordinator = controller
    auto_entity = _entity_id(hass, entry, "switch", "auto_mode")
    blast_entity = _entity_id(hass, entry, "button", "heat_blast")
    status_entity = _entity_id(hass, entry, "sensor", "status")

    await hass.services.async_call("switch", "turn_off", {"entity_id": auto_entity}, blocking=True)
    await hass.async_block_till_done()
    assert coordinator.persistent_state.auto_mode is False
    assert hass.states.get(auto_entity).state == "off"
    assert hass.states.get(status_entity).state == ControllerStatus.OFF
    assert hass.states.get(blast_entity).state != "unavailable"

    await hass.services.async_call("button", "press", {"entity_id": blast_entity}, blocking=True)
    await hass.async_block_till_done()
    assert coordinator.persistent_state.blast_until is None
    assert hass.states.get(status_entity).state == ControllerStatus.OFF

    await hass.services.async_call("switch", "turn_on", {"entity_id": auto_entity}, blocking=True)
    await hass.async_block_till_done()
    assert coordinator.persistent_state.auto_mode is True
    assert hass.states.get(status_entity).state == ControllerStatus.LOW

    await hass.services.async_call("button", "press", {"entity_id": blast_entity}, blocking=True)
    await hass.async_block_till_done()
    assert coordinator.persistent_state.blast_until is not None
    assert hass.states.get(status_entity).state == ControllerStatus.HEAT_BLAST


async def test_all_number_services_delegate_to_shared_setting_update(hass, controller) -> None:
    """Every number writes its option through the coordinator validation boundary."""
    entry, coordinator = controller
    reload_entry = AsyncMock()
    original_update = coordinator.async_update_setting
    coordinator.async_update_setting = AsyncMock(wraps=original_update)
    changes = (
        (CONF_HIGH_TEMP, 21.0),
        (CONF_LOW_TEMP, 18.0),
        (CONF_ECO_TEMP, 15.0),
        (CONF_FALLBACK_MINUTES, 90),
        (CONF_MAX_WARMUP_MINUTES, 240),
    )

    with patch.object(type(hass.config_entries), "async_reload", reload_entry):
        for key, value in changes:
            await hass.services.async_call(
                "number",
                "set_value",
                {"entity_id": _entity_id(hass, entry, "number", key), "value": value},
                blocking=True,
            )
            await hass.async_block_till_done()

    assert entry.options | {} == {
        CONF_ACTIVE_HVAC_MODE: "heat",
        CONF_HIGH_TEMP: 21.0,
        CONF_LOW_TEMP: 18.0,
        CONF_ECO_TEMP: 15.0,
        CONF_FALLBACK_MINUTES: 90,
        CONF_MAX_WARMUP_MINUTES: 240,
        CONF_DESTINATION_HOME_VALUE: None,
    }
    assert [call.args for call in coordinator.async_update_setting.await_args_list] == list(changes)


@pytest.mark.parametrize(
    ("key", "invalid_value"),
    (
        (CONF_HIGH_TEMP, 16.0),
        (CONF_LOW_TEMP, 21.0),
        (CONF_ECO_TEMP, 18.0),
        (CONF_FALLBACK_MINUTES, 185),
    ),
)
async def test_invalid_cross_number_values_raise_without_updating_options(
    hass, controller, key, invalid_value
) -> None:
    """Cross-value ordering errors surface and leave config options untouched."""
    entry, _coordinator = controller
    before = dict(entry.options)

    with pytest.raises(HomeAssistantError):
        await hass.services.async_call(
            "number",
            "set_value",
            {"entity_id": _entity_id(hass, entry, "number", key), "value": invalid_value},
            blocking=True,
        )

    assert entry.options == before


async def test_dynamic_sensor_availability_and_status_details(hass, controller) -> None:
    """Derived sensors follow coordinator trust, target, and timed-journey data."""
    entry, coordinator = controller
    status_id = _entity_id(hass, entry, "sensor", "status")
    target_id = _entity_id(hass, entry, "sensor", "effective_target_temperature")
    rate_id = _entity_id(hass, entry, "sensor", "learned_heating_rate")
    preheat_id = _entity_id(hass, entry, "sensor", "preheat_start_time")
    arrival = datetime(2026, 6, 28, 19, 0, tzinfo=timezone.utc)
    start = datetime(2026, 6, 28, 18, 0, tzinfo=timezone.utc)

    data = coordinator.data
    coordinator.persistent_state.manual_override_target = 18.5
    coordinator.learner._rate = 1.25
    coordinator.learner._sample_count = 3
    coordinator.async_set_updated_data(
        type(data)(
            result=type(data.result)(
                ControllerStatus.MANUAL_OVERRIDE,
                18.5,
                None,
                "external_target_preserved",
            ),
            current_temperature=16.5,
            learned_rate=1.25,
            learned_trusted=True,
            arrival_time=arrival,
            preheat_start_time=start,
            warmup_minutes=60,
        )
    )
    await hass.async_block_till_done()

    status = hass.states.get(status_id)
    assert status is not None
    assert status.state == ControllerStatus.MANUAL_OVERRIDE
    assert status.attributes["manual_override"] is True
    assert status.attributes["learned_heating_rate"] == 1.25
    assert status.attributes["arrival_time"] == arrival
    assert status.attributes["preheat_start_time"] == start
    assert hass.states.get(target_id).state == "18.5"
    assert hass.states.get(rate_id).state == "1.25"
    assert hass.states.get(rate_id).attributes[ATTR_STATE_CLASS] == SensorStateClass.MEASUREMENT
    assert hass.states.get(rate_id).attributes[ATTR_UNIT_OF_MEASUREMENT] == "°C/h"
    assert hass.states.get(preheat_id).state == start.isoformat()
    assert hass.states.get(preheat_id).attributes[ATTR_DEVICE_CLASS] == SensorDeviceClass.TIMESTAMP

    coordinator.async_set_updated_data(
        type(data)(
            result=type(data.result)(
                ControllerStatus.OFF,
                None,
                "off",
                "auto_mode_off",
            ),
            current_temperature=None,
            learned_rate=1.25,
            learned_trusted=False,
            arrival_time=None,
            preheat_start_time=None,
            warmup_minutes=60,
        )
    )
    await hass.async_block_till_done()

    assert hass.states.get(target_id).state == "unavailable"
    assert hass.states.get(rate_id).state == "unavailable"
    assert hass.states.get(preheat_id).state == "unavailable"


def test_entity_translations_are_complete_and_aligned() -> None:
    """Source and English translations expose identical modern entity keys."""
    strings = json.loads(
        Path("custom_components/central_heating_controller/strings.json").read_text()
    )
    english = json.loads(
        Path("custom_components/central_heating_controller/translations/en.json").read_text()
    )

    assert strings["entity"] == english["entity"]
    assert set(strings["entity"]) == {"switch", "button", "number", "sensor"}
    assert set(strings["entity"]["sensor"]["status"]["state"]) == {
        item.value for item in ControllerStatus
    }
