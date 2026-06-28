"""Privacy-safe diagnostics for Central Heating Controller."""

from typing import Any

from homeassistant.components.diagnostics import REDACTED
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, State
from homeassistant.util import dt as dt_util

from .const import (
    CONF_ACTIVE_HVAC_MODE,
    CONF_ARRIVAL_TIME,
    CONF_CLIMATE,
    CONF_DESTINATION,
    CONF_ECO_TEMP,
    CONF_FALLBACK_MINUTES,
    CONF_HIGH_TEMP,
    CONF_HOME_ZONE,
    CONF_LOW_TEMP,
    CONF_MAX_WARMUP_MINUTES,
    CONF_PERSONS,
    CONF_SCHEDULE,
)
from .models import ControllerRuntimeData

_ENTITY_KEYS = (
    CONF_CLIMATE,
    CONF_PERSONS,
    CONF_HOME_ZONE,
    CONF_SCHEDULE,
    CONF_DESTINATION,
    CONF_ARRIVAL_TIME,
)
_SAFE_OPTION_KEYS = {
    CONF_ACTIVE_HVAC_MODE,
    CONF_HIGH_TEMP,
    CONF_LOW_TEMP,
    CONF_ECO_TEMP,
    CONF_FALLBACK_MINUTES,
    CONF_MAX_WARMUP_MINUTES,
}
_INVALID_STATES = {"unknown", "unavailable"}


def _available(state: State | None) -> bool:
    """Return whether an entity currently has a usable state."""
    return state is not None and state.state not in _INVALID_STATES


def _redacted_entry_data(entry: ConfigEntry) -> dict[str, Any]:
    """Return entry data with no entity identifiers."""
    return {
        key: REDACTED if entry.data.get(key) is not None else None
        for key in _ENTITY_KEYS
        if key in entry.data
    }


def _redacted_entry_options(entry: ConfigEntry) -> dict[str, Any]:
    """Return only explicitly approved settings, redacting all other values."""
    return {
        key: (value if key in _SAFE_OPTION_KEYS else REDACTED if value is not None else None)
        for key, value in entry.options.items()
    }


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant,
    entry: ConfigEntry[ControllerRuntimeData],
) -> dict[str, Any]:
    """Return useful controller diagnostics without location or journey data."""
    coordinator = entry.runtime_data.coordinator
    config = coordinator.config
    state = coordinator.data
    people = config.get(CONF_PERSONS, ())
    arrival_entity = config.get(CONF_ARRIVAL_TIME)
    blast_until = coordinator.persistent_state.blast_until

    return {
        "entry": {
            "data": _redacted_entry_data(entry),
            "options": _redacted_entry_options(entry),
        },
        "controller": {
            "status": state.status.value if state is not None else None,
            "reason": state.result.reason if state is not None else None,
            "auto_mode": coordinator.persistent_state.auto_mode,
            "blast_active": (blast_until is not None and blast_until > dt_util.utcnow()),
            "learned_sample_count": coordinator.learner.sample_count,
            "learned_rate": coordinator.learner.rate,
        },
        "entity_availability": {
            CONF_CLIMATE: _available(hass.states.get(config[CONF_CLIMATE])),
            CONF_PERSONS: bool(people)
            and all(_available(hass.states.get(entity_id)) for entity_id in people),
            CONF_HOME_ZONE: _available(hass.states.get(config[CONF_HOME_ZONE])),
            CONF_SCHEDULE: _available(hass.states.get(config[CONF_SCHEDULE])),
            CONF_DESTINATION: _available(hass.states.get(config[CONF_DESTINATION])),
            CONF_ARRIVAL_TIME: bool(arrival_entity) and _available(hass.states.get(arrival_entity)),
        },
        "sensitive_state": {
            "arrival_time": REDACTED,
            "preheat_start_time": REDACTED,
            "blast_until": REDACTED,
            "manual_override_fingerprint": REDACTED,
        },
    }
