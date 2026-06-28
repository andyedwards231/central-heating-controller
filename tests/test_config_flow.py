"""Tests for the Central Heating Controller config flow."""

from __future__ import annotations

import json
import math
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import voluptuous as vol

from homeassistant.components.climate import ClimateEntityFeature
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResult, FlowResultType
from homeassistant.helpers import selector
from homeassistant.util.unit_system import US_CUSTOMARY_SYSTEM
from pytest_homeassistant_custom_component.common import MockConfigEntry

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

CLIMATE = "climate.downstairs"
OTHER_CLIMATE = "climate.upstairs"
PERSON = "person.andy"
OTHER_PERSON = "person.alex"
HOME_ZONE = "zone.home"
SCHEDULE = "schedule.heating"
DESTINATION = "input_text.destination"
ARRIVAL_TIME = "sensor.arrival_time"

BASE_INPUT = {
    CONF_CLIMATE: CLIMATE,
    CONF_PERSONS: [PERSON, OTHER_PERSON],
    CONF_HOME_ZONE: HOME_ZONE,
    CONF_SCHEDULE: SCHEDULE,
    CONF_DESTINATION: DESTINATION,
    CONF_ARRIVAL_TIME: ARRIVAL_TIME,
}

SETTINGS_INPUT = {
    CONF_ACTIVE_HVAC_MODE: "heat",
    CONF_HIGH_TEMP: 21.0,
    CONF_LOW_TEMP: 17.0,
    CONF_ECO_TEMP: 14.0,
    CONF_FALLBACK_MINUTES: 60,
    CONF_MAX_WARMUP_MINUTES: 180,
    CONF_DESTINATION_HOME_VALUE: "Home",
}


def _set_required_states(
    hass: HomeAssistant,
    *,
    climate: str = CLIMATE,
    climate_state: str = "heat",
    climate_attributes: dict | None = None,
) -> None:
    """Create all entities needed by the setup flow."""
    attributes = {
        "supported_features": int(ClimateEntityFeature.TARGET_TEMPERATURE),
        "hvac_modes": ["off", "heat", "auto"],
        "min_temp": 5.0,
        "max_temp": 30.0,
        "target_temp_step": 0.5,
    }
    if climate_attributes is not None:
        attributes.update(climate_attributes)
    hass.states.async_set(climate, climate_state, attributes)
    hass.states.async_set(PERSON, "home")
    hass.states.async_set(OTHER_PERSON, "not_home")
    hass.states.async_set(HOME_ZONE, "2")
    hass.states.async_set(SCHEDULE, "on")
    hass.states.async_set(DESTINATION, "Office")
    hass.states.async_set(ARRIVAL_TIME, "2026-06-28T18:00:00+01:00")


async def _start_user_flow(hass: HomeAssistant) -> FlowResult:
    return await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})


async def _reach_settings(hass: HomeAssistant, user_input: dict | None = None) -> FlowResult:
    result = await _start_user_flow(hass)
    return await hass.config_entries.flow.async_configure(
        result["flow_id"], user_input=user_input or dict(BASE_INPUT)
    )


def _schema_fields(result: FlowResult) -> dict[str, tuple[vol.Marker, object]]:
    schema = result["data_schema"]
    assert schema is not None
    return {
        key.schema if isinstance(key, vol.Marker) else key: (key, value)
        for key, value in schema.schema.items()
    }


def _suggested_value(result: FlowResult, field: str) -> object:
    marker, _ = _schema_fields(result)[field]
    assert isinstance(marker, vol.Marker)
    assert marker.description is not None
    return marker.description["suggested_value"]


@pytest.mark.asyncio
async def test_user_and_settings_create_entry(hass: HomeAssistant) -> None:
    """The two-step flow stores base choices in data and settings in options."""
    _set_required_states(hass)

    result = await _start_user_flow(hass)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], user_input=BASE_INPUT
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "settings"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], user_input=SETTINGS_INPUT
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Central Heating Controller"
    assert result["data"] == BASE_INPUT
    assert result["options"] == SETTINGS_INPUT
    assert result["result"].unique_id == CLIMATE


@pytest.mark.asyncio
async def test_optional_arrival_time_and_blank_override_are_normalized(
    hass: HomeAssistant,
) -> None:
    """Optional values may be omitted and are normalized to None."""
    _set_required_states(hass)
    base_input = {key: value for key, value in BASE_INPUT.items() if key != CONF_ARRIVAL_TIME}
    settings_input = SETTINGS_INPUT | {CONF_DESTINATION_HOME_VALUE: "   "}

    result = await _reach_settings(hass, base_input)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], user_input=settings_input
    )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"] == base_input | {CONF_ARRIVAL_TIME: None}
    assert result["options"][CONF_DESTINATION_HOME_VALUE] is None


@pytest.mark.asyncio
async def test_user_schema_uses_constrained_entity_selectors(
    hass: HomeAssistant,
) -> None:
    """Entity fields use the intended Home Assistant selectors."""
    result = await _start_user_flow(hass)
    fields = _schema_fields(result)

    for field in (
        CONF_CLIMATE,
        CONF_PERSONS,
        CONF_HOME_ZONE,
        CONF_SCHEDULE,
        CONF_DESTINATION,
        CONF_ARRIVAL_TIME,
    ):
        assert isinstance(fields[field][1], selector.EntitySelector)

    assert fields[CONF_CLIMATE][1].config["domain"] == ["climate"]
    assert fields[CONF_PERSONS][1].config["domain"] == ["person"]
    assert fields[CONF_PERSONS][1].config["multiple"] is True
    assert fields[CONF_HOME_ZONE][1].config["domain"] == ["zone"]
    assert fields[CONF_SCHEDULE][1].config["domain"] == ["schedule"]
    assert "domain" not in fields[CONF_DESTINATION][1].config
    assert "domain" not in fields[CONF_ARRIVAL_TIME][1].config
    assert isinstance(fields[CONF_ARRIVAL_TIME][0], vol.Optional)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("missing_entity", "error_field"),
    [
        (CLIMATE, CONF_CLIMATE),
        (PERSON, CONF_PERSONS),
        (HOME_ZONE, CONF_HOME_ZONE),
        (SCHEDULE, CONF_SCHEDULE),
        (DESTINATION, CONF_DESTINATION),
        (ARRIVAL_TIME, CONF_ARRIVAL_TIME),
    ],
)
async def test_missing_required_or_selected_entity_is_rejected(
    hass: HomeAssistant, missing_entity: str, error_field: str
) -> None:
    """Every selected entity must exist in the state machine."""
    _set_required_states(hass)
    hass.states.async_remove(missing_entity)

    result = await _reach_settings(hass)

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"
    assert result["errors"] == {error_field: "entity_not_found"}


@pytest.mark.asyncio
async def test_at_least_one_person_is_required(hass: HomeAssistant) -> None:
    """An empty persons selection is rejected."""
    _set_required_states(hass)

    result = await _reach_settings(hass, BASE_INPUT | {CONF_PERSONS: []})

    assert result["errors"] == {CONF_PERSONS: "persons_required"}


@pytest.mark.asyncio
async def test_climate_requires_target_temperature_support(
    hass: HomeAssistant,
) -> None:
    """A thermostat must support setting a target temperature."""
    _set_required_states(hass, climate_attributes={"supported_features": 0})

    result = await _reach_settings(hass)

    assert result["errors"] == {CONF_CLIMATE: "target_temperature_unsupported"}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("attributes", "error"),
    [
        ({"hvac_modes": None}, "invalid_climate"),
        ({"hvac_modes": []}, "no_active_hvac_modes"),
        ({"hvac_modes": ["off"]}, "no_active_hvac_modes"),
        ({"min_temp": None}, "invalid_climate"),
        ({"max_temp": math.inf}, "invalid_climate"),
        ({"target_temp_step": 0}, "invalid_climate"),
        ({"target_temp_step": math.inf}, "invalid_climate"),
        ({"min_temp": 31, "max_temp": 30}, "invalid_climate"),
    ],
)
async def test_climate_requires_usable_attributes(
    hass: HomeAssistant, attributes: dict, error: str
) -> None:
    """Malformed thermostat metadata is shown as a form error."""
    _set_required_states(hass, climate_attributes=attributes)

    result = await _reach_settings(hass)

    assert result["errors"] == {CONF_CLIMATE: error}


@pytest.mark.asyncio
@pytest.mark.parametrize("state", [STATE_UNKNOWN, STATE_UNAVAILABLE])
async def test_unknown_climate_is_accepted_only_with_usable_attributes(
    hass: HomeAssistant, state: str
) -> None:
    """Availability does not matter, but required climate attributes do."""
    _set_required_states(hass, climate_state=state)

    result = await _reach_settings(hass)
    assert result["step_id"] == "settings"

    hass.states.async_set(CLIMATE, state, {"supported_features": 1})
    result = await _start_user_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], user_input=BASE_INPUT
    )
    assert result["step_id"] == "user"
    assert result["errors"] == {CONF_CLIMATE: "invalid_climate"}


@pytest.mark.asyncio
async def test_duplicate_climate_aborts(hass: HomeAssistant) -> None:
    """Only one controller can own a climate entity."""
    _set_required_states(hass)
    MockConfigEntry(
        domain=DOMAIN,
        unique_id=CLIMATE,
        data={CONF_CLIMATE: CLIMATE},
    ).add_to_hass(hass)

    result = await _reach_settings(hass)

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


@pytest.mark.asyncio
async def test_setup_rechecks_climate_collision_before_create(
    hass: HomeAssistant,
) -> None:
    """A climate claimed while the settings form is open aborts setup."""
    _set_required_states(hass)
    result = await _reach_settings(hass)
    _entry().add_to_hass(hass)

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], user_input=SETTINGS_INPUT
    )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


@pytest.mark.asyncio
@pytest.mark.parametrize("step_is_missing", [False, True])
async def test_missing_temperature_step_uses_any_selector_step(
    hass: HomeAssistant, step_is_missing: bool
) -> None:
    """The optional target temperature step may be absent or None."""
    _set_required_states(hass, climate_attributes={"target_temp_step": None})
    if step_is_missing:
        climate_state = hass.states.get(CLIMATE)
        assert climate_state is not None
        attributes = dict(climate_state.attributes)
        attributes.pop("target_temp_step")
        hass.states.async_set(CLIMATE, climate_state.state, attributes)

    result = await _reach_settings(hass)

    assert result["step_id"] == "settings"
    fields = _schema_fields(result)
    for field in (CONF_HIGH_TEMP, CONF_LOW_TEMP, CONF_ECO_TEMP):
        assert fields[field][1].config["step"] == "any"


@pytest.mark.asyncio
async def test_celsius_defaults_and_settings_selectors(hass: HomeAssistant) -> None:
    """Metric systems get Celsius defaults and climate-specific selector limits."""
    _set_required_states(hass)

    result = await _reach_settings(hass)
    fields = _schema_fields(result)

    assert fields[CONF_ACTIVE_HVAC_MODE][1].config["options"] == ["heat", "auto"]
    assert fields[CONF_ACTIVE_HVAC_MODE][0].default() == "heat"
    assert fields[CONF_HIGH_TEMP][0].default() == 20.0
    assert fields[CONF_LOW_TEMP][0].default() == 17.0
    assert fields[CONF_ECO_TEMP][0].default() == 14.0
    for field in (CONF_HIGH_TEMP, CONF_LOW_TEMP, CONF_ECO_TEMP):
        value_selector = fields[field][1]
        assert isinstance(value_selector, selector.NumberSelector)
        assert value_selector.config["min"] == 5.0
        assert value_selector.config["max"] == 30.0
        assert value_selector.config["step"] == 0.5
        assert value_selector.config["unit_of_measurement"] == "°C"
    assert fields[CONF_FALLBACK_MINUTES][1].config == {
        "min": 5.0,
        "max": 360.0,
        "step": 5.0,
        "mode": "box",
        "unit_of_measurement": "min",
    }
    assert fields[CONF_FALLBACK_MINUTES][0].default() == 60
    assert fields[CONF_MAX_WARMUP_MINUTES][0].default() == 180


@pytest.mark.asyncio
async def test_fahrenheit_and_narrow_range_defaults_preserve_order(
    hass: HomeAssistant,
) -> None:
    """Fahrenheit defaults clamp independently without reversing their ordering."""
    hass.config.units = US_CUSTOMARY_SYSTEM
    _set_required_states(
        hass,
        climate_attributes={"min_temp": 64.0, "max_temp": 65.0, "target_temp_step": 1.0},
    )

    result = await _reach_settings(hass)

    assert _schema_fields(result)[CONF_HIGH_TEMP][0].default() == 65.0
    assert _schema_fields(result)[CONF_LOW_TEMP][0].default() == 64.0
    assert _schema_fields(result)[CONF_ECO_TEMP][0].default() == 64.0
    for field in (CONF_HIGH_TEMP, CONF_LOW_TEMP, CONF_ECO_TEMP):
        assert _schema_fields(result)[field][1].config["unit_of_measurement"] == "°F"


@pytest.mark.asyncio
async def test_temperature_defaults_snap_to_thermostat_step(
    hass: HomeAssistant,
) -> None:
    """Defaults use values on the thermostat's min-anchored step grid."""
    _set_required_states(
        hass,
        climate_attributes={"min_temp": 5.1, "max_temp": 30.0, "target_temp_step": 0.3},
    )

    result = await _reach_settings(hass)
    defaults = [
        _schema_fields(result)[field][0].default()
        for field in (CONF_HIGH_TEMP, CONF_LOW_TEMP, CONF_ECO_TEMP)
    ]

    assert defaults == pytest.approx([20.1, 17.1, 14.1])
    assert defaults[0] >= defaults[1] >= defaults[2]
    for value in defaults:
        assert (value - 5.1) / 0.3 == pytest.approx(round((value - 5.1) / 0.3))


@pytest.mark.asyncio
async def test_non_heat_climate_uses_first_active_mode_default(
    hass: HomeAssistant,
) -> None:
    """The first supported active mode is the fallback when heat is unavailable."""
    _set_required_states(hass, climate_attributes={"hvac_modes": ["off", "cool", "auto"]})

    result = await _reach_settings(hass)

    assert _schema_fields(result)[CONF_ACTIVE_HVAC_MODE][0].default() == "cool"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("updates", "field", "error"),
    [
        ({CONF_ACTIVE_HVAC_MODE: "cool"}, CONF_ACTIVE_HVAC_MODE, "unsupported_hvac_mode"),
        ({CONF_HIGH_TEMP: 31}, CONF_HIGH_TEMP, "temperature_out_of_range"),
        ({CONF_LOW_TEMP: 4}, CONF_LOW_TEMP, "temperature_out_of_range"),
        ({CONF_ECO_TEMP: 31}, CONF_ECO_TEMP, "temperature_out_of_range"),
        ({CONF_HIGH_TEMP: 16}, "base", "temperature_order"),
        ({CONF_LOW_TEMP: 13}, "base", "temperature_order"),
        ({CONF_HIGH_TEMP: math.nan}, CONF_HIGH_TEMP, "invalid_number"),
        ({CONF_LOW_TEMP: math.inf}, CONF_LOW_TEMP, "invalid_number"),
        ({CONF_ECO_TEMP: True}, CONF_ECO_TEMP, "invalid_number"),
        ({CONF_HIGH_TEMP: 21.1}, CONF_HIGH_TEMP, "temperature_step"),
        ({CONF_FALLBACK_MINUTES: 0}, CONF_FALLBACK_MINUTES, "invalid_duration"),
        ({CONF_MAX_WARMUP_MINUTES: 365}, CONF_MAX_WARMUP_MINUTES, "invalid_duration"),
        ({CONF_FALLBACK_MINUTES: 62}, CONF_FALLBACK_MINUTES, "invalid_duration"),
        ({CONF_MAX_WARMUP_MINUTES: False}, CONF_MAX_WARMUP_MINUTES, "invalid_number"),
        (
            {CONF_FALLBACK_MINUTES: 185, CONF_MAX_WARMUP_MINUTES: 180},
            "base",
            "warmup_order",
        ),
    ],
)
async def test_settings_validation_errors(
    hass: HomeAssistant, updates: dict, field: str, error: str
) -> None:
    """Invalid settings return translated form errors."""
    _set_required_states(hass)
    result = await _reach_settings(hass)

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], user_input=SETTINGS_INPUT | updates
    )

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "settings"
    assert result["errors"] == {field: error}


def _entry(*, options: dict | None = None, climate: str = CLIMATE) -> MockConfigEntry:
    base = dict(BASE_INPUT)
    base[CONF_CLIMATE] = climate
    return MockConfigEntry(
        domain=DOMAIN,
        unique_id=climate,
        data=base,
        options=options or dict(SETTINGS_INPUT),
        title="Central Heating Controller",
    )


@pytest.mark.asyncio
async def test_options_forms_are_prefilled_and_store_merged_values(
    hass: HomeAssistant,
) -> None:
    """Options can replace both base choices and settings."""
    _set_required_states(hass)
    _set_required_states(hass, climate=OTHER_CLIMATE)
    entry = _entry()
    entry.add_to_hass(hass)

    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["step_id"] == "init"
    assert _suggested_value(result, CONF_CLIMATE) == CLIMATE
    assert _suggested_value(result, CONF_PERSONS) == [PERSON, OTHER_PERSON]

    entity_input = dict(BASE_INPUT) | {
        CONF_CLIMATE: OTHER_CLIMATE,
        CONF_PERSONS: [PERSON],
        CONF_ARRIVAL_TIME: None,
    }
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], user_input=entity_input
    )
    assert result["step_id"] == "settings"
    assert _suggested_value(result, CONF_HIGH_TEMP) == 21.0

    settings_input = SETTINGS_INPUT | {
        CONF_ACTIVE_HVAC_MODE: "auto",
        CONF_HIGH_TEMP: 22.0,
        CONF_DESTINATION_HOME_VALUE: "home",
    }
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], user_input=settings_input | {"reset_learning": False}
    )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"] == entity_input | settings_input
    assert "reset_learning" not in result["data"]
    assert entry.options == entity_input | settings_input
    assert entry.unique_id == OTHER_CLIMATE


@pytest.mark.asyncio
async def test_options_revalidate_entities_settings_and_climate_collision(
    hass: HomeAssistant,
) -> None:
    """Options share setup validation and exclude only their own entry."""
    _set_required_states(hass)
    _set_required_states(hass, climate=OTHER_CLIMATE)
    entry = _entry()
    entry.add_to_hass(hass)
    _entry(climate=OTHER_CLIMATE).add_to_hass(hass)

    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], user_input=BASE_INPUT | {CONF_CLIMATE: OTHER_CLIMATE}
    )
    assert result["step_id"] == "init"
    assert result["errors"] == {CONF_CLIMATE: "already_configured"}

    result = await hass.config_entries.options.async_configure(
        result["flow_id"], user_input=BASE_INPUT
    )
    assert result["step_id"] == "settings"
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], user_input=SETTINGS_INPUT | {CONF_LOW_TEMP: 99}
    )
    assert result["errors"] == {CONF_LOW_TEMP: "temperature_out_of_range"}


@pytest.mark.asyncio
async def test_options_rechecks_collision_before_reset(hass: HomeAssistant) -> None:
    """Options abort without resetting when the selected climate was just claimed."""
    _set_required_states(hass)
    _set_required_states(hass, climate=OTHER_CLIMATE)
    entry = _entry()
    entry.add_to_hass(hass)
    reset = AsyncMock()
    entry.mock_state(hass, ConfigEntryState.LOADED)
    entry.runtime_data = SimpleNamespace(coordinator=SimpleNamespace(async_reset_learning=reset))

    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], user_input=BASE_INPUT | {CONF_CLIMATE: OTHER_CLIMATE}
    )
    _entry(climate=OTHER_CLIMATE).add_to_hass(hass)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], user_input=SETTINGS_INPUT | {"reset_learning": True}
    )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"
    reset.assert_not_awaited()


@pytest.mark.asyncio
async def test_options_rechecks_collision_after_awaited_reset(
    hass: HomeAssistant,
) -> None:
    """A climate claimed while reset awaits is not committed to options."""
    _set_required_states(hass)
    _set_required_states(hass, climate=OTHER_CLIMATE)
    entry = _entry()
    entry.add_to_hass(hass)

    async def claim_climate() -> None:
        _entry(climate=OTHER_CLIMATE).add_to_hass(hass)

    reset = AsyncMock(side_effect=claim_climate)
    entry.mock_state(hass, ConfigEntryState.LOADED)
    entry.runtime_data = SimpleNamespace(coordinator=SimpleNamespace(async_reset_learning=reset))
    original_options = dict(entry.options)

    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], user_input=BASE_INPUT | {CONF_CLIMATE: OTHER_CLIMATE}
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], user_input=SETTINGS_INPUT | {"reset_learning": True}
    )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"
    reset.assert_awaited_once_with()
    assert dict(entry.options) == original_options
    assert entry.unique_id == CLIMATE


@pytest.mark.asyncio
async def test_options_atomically_updates_unique_id_and_options(
    hass: HomeAssistant,
) -> None:
    """A loaded entry listener observes one coherent options update."""
    _set_required_states(hass)
    _set_required_states(hass, climate=OTHER_CLIMATE)
    entry = _entry()
    entry.add_to_hass(hass)
    entry.mock_state(hass, ConfigEntryState.LOADED)
    observed: list[tuple[str | None, dict]] = []

    async def capture_update(_hass: HomeAssistant, updated_entry: MockConfigEntry) -> None:
        observed.append((updated_entry.unique_id, dict(updated_entry.options)))

    entry.add_update_listener(capture_update)
    entity_input = BASE_INPUT | {CONF_CLIMATE: OTHER_CLIMATE}
    expected_options = entity_input | SETTINGS_INPUT

    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], user_input=entity_input
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], user_input=SETTINGS_INPUT | {"reset_learning": False}
    )
    await hass.async_block_till_done()

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert observed == [(OTHER_CLIMATE, expected_options)]
    assert entry.unique_id == OTHER_CLIMATE
    assert dict(entry.options) == expected_options


@pytest.mark.asyncio
async def test_loaded_options_reset_learning_is_invoked_once_and_not_stored(
    hass: HomeAssistant,
) -> None:
    """A loaded coordinator handles the one-shot reset immediately."""
    _set_required_states(hass)
    entry = _entry()
    entry.add_to_hass(hass)
    reset = AsyncMock()
    entry.mock_state(hass, ConfigEntryState.LOADED)
    entry.runtime_data = SimpleNamespace(coordinator=SimpleNamespace(async_reset_learning=reset))

    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], user_input=BASE_INPUT
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], user_input=SETTINGS_INPUT | {"reset_learning": True}
    )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    reset.assert_awaited_once_with()
    assert "reset_learning" not in result["data"]
    assert "_reset_learning_requested" not in result["data"]


@pytest.mark.asyncio
async def test_unloaded_options_reset_learning_is_saved_as_internal_request(
    hass: HomeAssistant,
) -> None:
    """An unloaded entry leaves Task 6 a one-shot internal request to consume."""
    _set_required_states(hass)
    entry = _entry()
    entry.add_to_hass(hass)
    reset = AsyncMock()
    entry.runtime_data = SimpleNamespace(coordinator=SimpleNamespace(async_reset_learning=reset))

    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], user_input=BASE_INPUT
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], user_input=SETTINGS_INPUT | {"reset_learning": True}
    )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    reset.assert_not_awaited()
    assert "reset_learning" not in result["data"]
    assert result["data"]["_reset_learning_requested"] is True


def test_translation_files_are_valid_and_structurally_match() -> None:
    """English translation files contain the same config-flow content."""
    component = Path(__file__).parents[1] / "custom_components" / DOMAIN
    strings = json.loads((component / "strings.json").read_text())
    english = json.loads((component / "translations" / "en.json").read_text())

    assert strings == english
    assert strings["title"] == "Central Heating Controller"
    assert "already_configured" in strings["config"]["abort"]
    assert {
        "entity_not_found",
        "persons_required",
        "target_temperature_unsupported",
        "invalid_climate",
        "no_active_hvac_modes",
        "unsupported_hvac_mode",
        "temperature_out_of_range",
        "temperature_step",
        "temperature_order",
        "invalid_number",
        "invalid_duration",
        "warmup_order",
        "already_configured",
    } <= strings["config"]["error"].keys()
