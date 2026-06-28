"""Config flow for the Central Heating Controller integration."""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Any

import voluptuous as vol

from homeassistant.components.climate import (
    ATTR_HVAC_MODES,
    ATTR_MAX_TEMP,
    ATTR_MIN_TEMP,
    ATTR_TARGET_TEMP_STEP,
    ClimateEntityFeature,
    HVACMode,
)
from homeassistant.config_entries import (
    ConfigEntry,
    ConfigEntryState,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.const import ATTR_SUPPORTED_FEATURES, UnitOfTemperature, UnitOfTime
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import selector

from .const import (
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
    CONF_RESET_LEARNING,
    CONF_SCHEDULE,
    DEFAULT_ECO_C,
    DEFAULT_ECO_F,
    DEFAULT_FALLBACK_MINUTES,
    DEFAULT_HIGH_C,
    DEFAULT_HIGH_F,
    DEFAULT_LOW_C,
    DEFAULT_LOW_F,
    DEFAULT_MAX_WARMUP_MINUTES,
    DOMAIN,
    NAME,
    OPT_RESET_LEARNING_REQUESTED,
)

_SETTING_FIELDS = (
    CONF_ACTIVE_HVAC_MODE,
    CONF_HIGH_TEMP,
    CONF_LOW_TEMP,
    CONF_ECO_TEMP,
    CONF_FALLBACK_MINUTES,
    CONF_MAX_WARMUP_MINUTES,
    CONF_DESTINATION_HOME_VALUE,
)


@dataclass(frozen=True, slots=True)
class _ClimateCapabilities:
    """Climate metadata needed to build and validate the settings form."""

    active_modes: tuple[str, ...]
    min_temp: float
    max_temp: float
    temp_step: float | None


class _SemanticNumberSelector(selector.NumberSelector):
    """Keep semantically invalid values for translated step-level validation.

    The selector still advertises frontend limits. Home Assistant's stock number
    selector coerces booleans and rejects out-of-range values before the flow can
    return integration translation keys, so this subclass defers those checks.
    """

    def __call__(self, data: Any) -> Any:
        """Coerce ordinary numbers while preserving values our validator rejects."""
        if isinstance(data, bool):
            return data
        try:
            return float(data)
        except TypeError, ValueError:
            return data


class _SemanticSelectSelector(selector.SelectSelector):
    """Defer unsupported-option handling to translated flow validation."""

    def __call__(self, data: Any) -> Any:
        """Return the selection unchanged for semantic validation."""
        return data


class _NullableEntitySelector(selector.EntitySelector):
    """Allow an optional entity selection to be explicitly cleared."""

    def __call__(self, data: Any) -> Any:
        """Accept None, otherwise retain normal entity selector validation."""
        if data is None:
            return None
        return super().__call__(data)


def _entity_schema() -> vol.Schema:
    """Return the shared entity-selection schema."""
    return vol.Schema(
        {
            vol.Required(CONF_CLIMATE): selector.EntitySelector(
                selector.EntitySelectorConfig(domain="climate")
            ),
            vol.Required(CONF_PERSONS): selector.EntitySelector(
                selector.EntitySelectorConfig(domain="person", multiple=True)
            ),
            vol.Required(CONF_HOME_ZONE): selector.EntitySelector(
                selector.EntitySelectorConfig(domain="zone")
            ),
            vol.Required(CONF_SCHEDULE): selector.EntitySelector(
                selector.EntitySelectorConfig(domain="schedule")
            ),
            vol.Required(CONF_DESTINATION): selector.EntitySelector(),
            vol.Optional(CONF_ARRIVAL_TIME): _NullableEntitySelector(),
        }
    )


def _finite_number(value: Any) -> float | None:
    """Return a finite float, excluding booleans, or None."""
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except TypeError, ValueError:
        return None
    return number if math.isfinite(number) else None


def _climate_capabilities(state: Any) -> tuple[_ClimateCapabilities | None, str | None]:
    """Extract usable climate metadata and its translated error, if any."""
    features = state.attributes.get(ATTR_SUPPORTED_FEATURES)
    if isinstance(features, bool) or not isinstance(features, int):
        return None, "target_temperature_unsupported"
    if not features & ClimateEntityFeature.TARGET_TEMPERATURE:
        return None, "target_temperature_unsupported"

    raw_modes = state.attributes.get(ATTR_HVAC_MODES)
    if not isinstance(raw_modes, (list, tuple)):
        return None, "invalid_climate"
    if not all(isinstance(mode, str) and mode for mode in raw_modes):
        return None, "invalid_climate"
    active_modes = tuple(dict.fromkeys(mode for mode in raw_modes if mode != HVACMode.OFF))
    if not active_modes:
        return None, "no_active_hvac_modes"

    min_temp = _finite_number(state.attributes.get(ATTR_MIN_TEMP))
    max_temp = _finite_number(state.attributes.get(ATTR_MAX_TEMP))
    raw_temp_step = state.attributes.get(ATTR_TARGET_TEMP_STEP)
    temp_step = _finite_number(raw_temp_step) if raw_temp_step is not None else None
    if (
        min_temp is None
        or max_temp is None
        or min_temp > max_temp
        or (raw_temp_step is not None and (temp_step is None or temp_step <= 0))
    ):
        return None, "invalid_climate"

    return _ClimateCapabilities(active_modes, min_temp, max_temp, temp_step), None


def _entry_climate(entry: ConfigEntry) -> Any:
    """Return an entry's effective climate selection."""
    return entry.options.get(CONF_CLIMATE, entry.data.get(CONF_CLIMATE))


def _find_climate_collision(
    hass: HomeAssistant, climate: str, *, exclude_entry: ConfigEntry | None = None
) -> bool:
    """Return whether another controller already uses the climate entity."""
    return any(
        entry is not exclude_entry and _entry_climate(entry) == climate
        for entry in hass.config_entries.async_entries(DOMAIN)
    )


def _climate_collision(
    hass: HomeAssistant, climate: str, *, exclude_entry: ConfigEntry | None = None
) -> bool:
    """Return whether effective selection or unique-ID ownership conflicts."""
    if _find_climate_collision(hass, climate, exclude_entry=exclude_entry):
        return True
    unique_id_owner = hass.config_entries.async_entry_for_domain_unique_id(DOMAIN, climate)
    return unique_id_owner is not None and unique_id_owner is not exclude_entry


def _normalize_entity_input(user_input: dict[str, Any]) -> dict[str, Any]:
    """Normalize optional entity values while retaining immutable choices."""
    return {
        CONF_CLIMATE: user_input[CONF_CLIMATE],
        CONF_PERSONS: list(user_input[CONF_PERSONS]),
        CONF_HOME_ZONE: user_input[CONF_HOME_ZONE],
        CONF_SCHEDULE: user_input[CONF_SCHEDULE],
        CONF_DESTINATION: user_input[CONF_DESTINATION],
        CONF_ARRIVAL_TIME: user_input.get(CONF_ARRIVAL_TIME) or None,
    }


def _validate_entities(
    hass: HomeAssistant,
    user_input: dict[str, Any],
    *,
    exclude_entry: ConfigEntry | None = None,
) -> tuple[dict[str, str], _ClimateCapabilities | None]:
    """Validate entity selections and return climate capabilities."""
    persons = user_input.get(CONF_PERSONS)
    if not persons:
        return {CONF_PERSONS: "persons_required"}, None

    checks: tuple[tuple[str, list[str]], ...] = (
        (CONF_CLIMATE, [user_input[CONF_CLIMATE]]),
        (CONF_PERSONS, persons),
        (CONF_HOME_ZONE, [user_input[CONF_HOME_ZONE]]),
        (CONF_SCHEDULE, [user_input[CONF_SCHEDULE]]),
        (CONF_DESTINATION, [user_input[CONF_DESTINATION]]),
        (
            CONF_ARRIVAL_TIME,
            [user_input[CONF_ARRIVAL_TIME]] if user_input.get(CONF_ARRIVAL_TIME) else [],
        ),
    )
    for field, entity_ids in checks:
        if any(hass.states.get(entity_id) is None for entity_id in entity_ids):
            return {field: "entity_not_found"}, None

    climate = user_input[CONF_CLIMATE]
    if _climate_collision(hass, climate, exclude_entry=exclude_entry):
        return {CONF_CLIMATE: "already_configured"}, None

    climate_state = hass.states.get(climate)
    assert climate_state is not None
    capabilities, error = _climate_capabilities(climate_state)
    if error:
        return {CONF_CLIMATE: error}, None
    return {}, capabilities


def _clamp(value: float, minimum: float, maximum: float) -> float:
    """Clamp a number to an inclusive range."""
    return min(max(value, minimum), maximum)


def _snap_temperature(value: float, capabilities: _ClimateCapabilities) -> float:
    """Clamp and snap a default to the min-anchored thermostat step grid."""
    clamped = _clamp(value, capabilities.min_temp, capabilities.max_temp)
    if capabilities.temp_step is None:
        return clamped
    step = capabilities.temp_step
    max_ticks = math.floor((capabilities.max_temp - capabilities.min_temp) / step + 1e-9)
    ticks = round((clamped - capabilities.min_temp) / step)
    ticks = min(max(ticks, 0), max_ticks)
    return capabilities.min_temp + ticks * step


def _temperature_matches_step(value: float, capabilities: _ClimateCapabilities) -> bool:
    """Return whether a temperature lies on the min-anchored step grid."""
    if capabilities.temp_step is None:
        return True
    step = capabilities.temp_step
    ticks = round((value - capabilities.min_temp) / step)
    expected = capabilities.min_temp + ticks * step
    return math.isclose(
        value,
        expected,
        rel_tol=1e-9,
        abs_tol=max(1e-9, step * 1e-6),
    )


def _default_settings(hass: HomeAssistant, capabilities: _ClimateCapabilities) -> dict[str, Any]:
    """Build locale-aware defaults clamped to thermostat limits."""
    if hass.config.units.temperature_unit == UnitOfTemperature.FAHRENHEIT:
        high, low, eco = DEFAULT_HIGH_F, DEFAULT_LOW_F, DEFAULT_ECO_F
    else:
        high, low, eco = DEFAULT_HIGH_C, DEFAULT_LOW_C, DEFAULT_ECO_C
    active_mode = (
        HVACMode.HEAT
        if HVACMode.HEAT in capabilities.active_modes
        else capabilities.active_modes[0]
    )
    return {
        CONF_ACTIVE_HVAC_MODE: active_mode,
        CONF_HIGH_TEMP: _snap_temperature(high, capabilities),
        CONF_LOW_TEMP: _snap_temperature(low, capabilities),
        CONF_ECO_TEMP: _snap_temperature(eco, capabilities),
        CONF_FALLBACK_MINUTES: DEFAULT_FALLBACK_MINUTES,
        CONF_MAX_WARMUP_MINUTES: DEFAULT_MAX_WARMUP_MINUTES,
        CONF_DESTINATION_HOME_VALUE: None,
    }


def _settings_schema(
    capabilities: _ClimateCapabilities,
    defaults: dict[str, Any],
    *,
    include_reset: bool,
    temperature_unit: str,
) -> vol.Schema:
    """Return the settings schema for setup or options."""
    temperature_config = selector.NumberSelectorConfig(
        min=capabilities.min_temp,
        max=capabilities.max_temp,
        step=capabilities.temp_step if capabilities.temp_step is not None else "any",
        mode=selector.NumberSelectorMode.BOX,
        unit_of_measurement=temperature_unit,
    )
    duration_config = selector.NumberSelectorConfig(
        min=5,
        max=360,
        step=5,
        mode=selector.NumberSelectorMode.BOX,
        unit_of_measurement=UnitOfTime.MINUTES,
    )
    schema: dict[vol.Marker, Any] = {
        vol.Required(
            CONF_ACTIVE_HVAC_MODE, default=defaults[CONF_ACTIVE_HVAC_MODE]
        ): _SemanticSelectSelector(
            selector.SelectSelectorConfig(options=list(capabilities.active_modes))
        ),
        vol.Required(CONF_HIGH_TEMP, default=defaults[CONF_HIGH_TEMP]): _SemanticNumberSelector(
            temperature_config
        ),
        vol.Required(CONF_LOW_TEMP, default=defaults[CONF_LOW_TEMP]): _SemanticNumberSelector(
            temperature_config
        ),
        vol.Required(CONF_ECO_TEMP, default=defaults[CONF_ECO_TEMP]): _SemanticNumberSelector(
            temperature_config
        ),
        vol.Required(
            CONF_FALLBACK_MINUTES, default=defaults[CONF_FALLBACK_MINUTES]
        ): _SemanticNumberSelector(duration_config),
        vol.Required(
            CONF_MAX_WARMUP_MINUTES, default=defaults[CONF_MAX_WARMUP_MINUTES]
        ): _SemanticNumberSelector(duration_config),
        vol.Optional(CONF_DESTINATION_HOME_VALUE): selector.TextSelector(),
    }
    if include_reset:
        schema[vol.Optional(CONF_RESET_LEARNING, default=False)] = selector.BooleanSelector()
    return vol.Schema(schema)


def _validate_settings(
    user_input: dict[str, Any], capabilities: _ClimateCapabilities
) -> tuple[dict[str, str], dict[str, Any] | None]:
    """Validate and normalize editable settings."""
    if user_input.get(CONF_ACTIVE_HVAC_MODE) not in capabilities.active_modes:
        return {CONF_ACTIVE_HVAC_MODE: "unsupported_hvac_mode"}, None

    normalized: dict[str, Any] = {CONF_ACTIVE_HVAC_MODE: user_input[CONF_ACTIVE_HVAC_MODE]}
    for field in (CONF_HIGH_TEMP, CONF_LOW_TEMP, CONF_ECO_TEMP):
        value = _finite_number(user_input.get(field))
        if value is None:
            return {field: "invalid_number"}, None
        if not capabilities.min_temp <= value <= capabilities.max_temp:
            return {field: "temperature_out_of_range"}, None
        if not _temperature_matches_step(value, capabilities):
            return {field: "temperature_step"}, None
        normalized[field] = value

    if not (normalized[CONF_HIGH_TEMP] >= normalized[CONF_LOW_TEMP] >= normalized[CONF_ECO_TEMP]):
        return {"base": "temperature_order"}, None

    for field in (CONF_FALLBACK_MINUTES, CONF_MAX_WARMUP_MINUTES):
        value = _finite_number(user_input.get(field))
        if value is None:
            return {field: "invalid_number"}, None
        if not 5 <= value <= 360 or not math.isclose(value % 5, 0, abs_tol=1e-9):
            return {field: "invalid_duration"}, None
        normalized[field] = int(value)

    if normalized[CONF_FALLBACK_MINUTES] > normalized[CONF_MAX_WARMUP_MINUTES]:
        return {"base": "warmup_order"}, None

    override = user_input.get(CONF_DESTINATION_HOME_VALUE)
    normalized[CONF_DESTINATION_HOME_VALUE] = (
        stripped if isinstance(override, str) and (stripped := override.strip()) else None
    )
    return {}, normalized


class CentralHeatingControllerConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle setup for Central Heating Controller."""

    VERSION = 1

    _entity_input: dict[str, Any]
    _capabilities: _ClimateCapabilities

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        """Return the current-API options flow handler."""
        return CentralHeatingControllerOptionsFlow()

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Collect and validate entity selections."""
        errors: dict[str, str] = {}
        if user_input is not None:
            errors, capabilities = _validate_entities(self.hass, user_input)
            if errors == {CONF_CLIMATE: "already_configured"}:
                await self.async_set_unique_id(user_input[CONF_CLIMATE])
                return self.async_abort(reason="already_configured")
            if not errors:
                await self.async_set_unique_id(user_input[CONF_CLIMATE])
                self._abort_if_unique_id_configured()
                assert capabilities is not None
                self._entity_input = _normalize_entity_input(user_input)
                self._capabilities = capabilities
                return await self.async_step_settings()

        return self.async_show_form(
            step_id="user",
            data_schema=self.add_suggested_values_to_schema(_entity_schema(), user_input),
            errors=errors,
        )

    async def async_step_settings(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Collect editable control settings."""
        defaults = _default_settings(self.hass, self._capabilities)
        errors: dict[str, str] = {}
        if user_input is not None:
            errors, settings = _validate_settings(user_input, self._capabilities)
            if not errors:
                if _climate_collision(self.hass, self._entity_input[CONF_CLIMATE]):
                    return self.async_abort(reason="already_configured")
                self._abort_if_unique_id_configured()
                assert settings is not None
                return self.async_create_entry(
                    title=NAME, data=self._entity_input, options=settings
                )

        schema = _settings_schema(
            self._capabilities,
            defaults,
            include_reset=False,
            temperature_unit=self.hass.config.units.temperature_unit,
        )
        return self.async_show_form(
            step_id="settings",
            data_schema=self.add_suggested_values_to_schema(schema, user_input),
            errors=errors,
        )


class CentralHeatingControllerOptionsFlow(OptionsFlow):
    """Allow all controller choices to be updated."""

    _entity_input: dict[str, Any]
    _capabilities: _ClimateCapabilities

    def _current_values(self) -> dict[str, Any]:
        """Merge legacy data with newer options values."""
        return dict(self.config_entry.data) | dict(self.config_entry.options)

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Collect replacement entity selections."""
        errors: dict[str, str] = {}
        if user_input is not None:
            errors, capabilities = _validate_entities(
                self.hass, user_input, exclude_entry=self.config_entry
            )
            if not errors:
                assert capabilities is not None
                self._entity_input = _normalize_entity_input(user_input)
                self._capabilities = capabilities
                return await self.async_step_settings()

        return self.async_show_form(
            step_id="init",
            data_schema=self.add_suggested_values_to_schema(
                _entity_schema(), user_input or self._current_values()
            ),
            errors=errors,
        )

    async def async_step_settings(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Collect replacement settings and optionally reset learning."""
        current = self._current_values()
        defaults = _default_settings(self.hass, self._capabilities) | {
            field: current[field] for field in _SETTING_FIELDS if field in current
        }
        errors: dict[str, str] = {}
        if user_input is not None:
            errors, settings = _validate_settings(user_input, self._capabilities)
            if not errors:
                selected_climate = self._entity_input[CONF_CLIMATE]
                if _climate_collision(self.hass, selected_climate, exclude_entry=self.config_entry):
                    return self.async_abort(reason="already_configured")
                assert settings is not None
                result = self._entity_input | settings
                reset_requested = user_input.get(CONF_RESET_LEARNING, False)
                if reset_requested:
                    runtime_data = getattr(self.config_entry, "runtime_data", None)
                    coordinator = getattr(runtime_data, "coordinator", None)
                    reset = getattr(coordinator, "async_reset_learning", None)
                    if self.config_entry.state is ConfigEntryState.LOADED and callable(reset):
                        await reset()
                    else:
                        # Task 6 consumes and removes this one-shot internal marker
                        # during setup; the user-facing action itself is never saved.
                        result[OPT_RESET_LEARNING_REQUESTED] = True
                elif self.config_entry.options.get(OPT_RESET_LEARNING_REQUESTED):
                    result[OPT_RESET_LEARNING_REQUESTED] = True
                if _climate_collision(self.hass, selected_climate, exclude_entry=self.config_entry):
                    return self.async_abort(reason="already_configured")
                self.hass.config_entries.async_update_entry(
                    self.config_entry,
                    options=result,
                    unique_id=selected_climate,
                )
                return self.async_create_entry(title="", data=result)

        schema = _settings_schema(
            self._capabilities,
            defaults,
            include_reset=True,
            temperature_unit=self.hass.config.units.temperature_unit,
        )
        return self.async_show_form(
            step_id="settings",
            data_schema=self.add_suggested_values_to_schema(schema, user_input or current),
            errors=errors,
        )
