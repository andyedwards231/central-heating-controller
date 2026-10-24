"""Central Heating Controller integration."""

import logging

import voluptuous as vol
from homeassistant.config_entries import ConfigEntry
from homeassistant.config_entries import ConfigEntryState
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import entity_registry as er
from homeassistant.core import HomeAssistant

from .const import DOMAIN, PLATFORMS
from .coordinator import ControllerCoordinator
from .models import ControllerRuntimeData
from .repairs import delete_entry_issues

_LOGGER = logging.getLogger(__name__)

_SMART_SCHEDULE_SERVICE = "set_smart_schedule"
_SMART_SCHEDULE_SCHEMA = vol.Schema(
    {
        vol.Required("entity_id"): cv.entity_ids,
        vol.Required("enabled"): cv.boolean,
        vol.Required("weekly_schedule"): dict,
        vol.Required("night_start"): str,
        vol.Required("night_end"): str,
        vol.Required("night_temperature"): vol.Coerce(float),
        vol.Required("daytime_drop"): vol.Coerce(float),
    }
)

type CentralHeatingConfigEntry = ConfigEntry[ControllerRuntimeData]


async def async_setup(hass: HomeAssistant, config: dict) -> bool:
    """Register integration services."""
    async def async_set_smart_schedule(call) -> None:
        registry = er.async_get(hass)
        entry_ids = {
            registry_entry.config_entry_id
            for entity_id in call.data["entity_id"]
            if (registry_entry := registry.async_get(entity_id)) is not None
            and registry_entry.platform == DOMAIN
        }
        if len(entry_ids) != 1 or any(
            (registry_entry := registry.async_get(entity_id)) is None
            or registry_entry.platform != DOMAIN
            for entity_id in call.data["entity_id"]
        ):
            raise HomeAssistantError(
                "entity_id must identify entities from one Central Heating Controller entry"
            )
        entry = hass.config_entries.async_get_entry(entry_ids.pop())
        if entry is None or entry.state is not ConfigEntryState.LOADED:
            raise HomeAssistantError("The selected controller is not loaded")
        payload = {key: value for key, value in call.data.items() if key != "entity_id"}
        await entry.runtime_data.coordinator.async_update_smart_schedule(payload)

    hass.services.async_register(
        DOMAIN,
        _SMART_SCHEDULE_SERVICE,
        async_set_smart_schedule,
        schema=_SMART_SCHEDULE_SCHEMA,
    )
    return True


async def async_setup_entry(hass: HomeAssistant, entry: CentralHeatingConfigEntry) -> bool:
    """Set up a Central Heating Controller config entry."""
    coordinator = ControllerCoordinator(hass, entry)
    try:
        await coordinator.async_setup()
    except BaseException:
        try:
            await coordinator.async_shutdown()
        except Exception:
            _LOGGER.exception("Failed to clean up coordinator after setup failure")
        raise
    entry.runtime_data = ControllerRuntimeData(coordinator)
    try:
        await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    except BaseException:
        try:
            await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
        except Exception:
            _LOGGER.exception("Failed to unload partially forwarded controller platforms")
        try:
            await coordinator.async_shutdown()
        except Exception:
            _LOGGER.exception("Failed to clean up coordinator after platform setup failure")
        raise
    entry.async_on_unload(entry.add_update_listener(async_update_listener))
    return True


async def async_unload_entry(hass: HomeAssistant, entry: CentralHeatingConfigEntry) -> bool:
    """Unload platforms before shutting down coordinator resources."""
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        await entry.runtime_data.coordinator.async_shutdown()
    return unloaded


async def async_update_listener(hass: HomeAssistant, entry: CentralHeatingConfigEntry) -> None:
    """Reload source changes; runtime setting edits already refresh in place."""
    coordinator = getattr(getattr(entry, "runtime_data", None), "coordinator", None)
    handled = getattr(coordinator, "handles_options_in_place", None)
    if callable(handled) and handled(entry.options) is True:
        return
    await hass.config_entries.async_reload(entry.entry_id)


async def async_remove_entry(hass: HomeAssistant, entry: CentralHeatingConfigEntry) -> None:
    """Delete repair issues when a config entry is removed."""
    delete_entry_issues(hass, entry)


__all__ = (
    "DOMAIN",
    "CentralHeatingConfigEntry",
    "async_remove_entry",
    "async_setup_entry",
    "async_unload_entry",
    "async_update_listener",
)
