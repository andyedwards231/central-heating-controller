"""Central Heating Controller integration."""

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import DOMAIN, PLATFORMS
from .coordinator import ControllerCoordinator
from .models import ControllerRuntimeData

type CentralHeatingConfigEntry = ConfigEntry[ControllerRuntimeData]


async def async_setup_entry(hass: HomeAssistant, entry: CentralHeatingConfigEntry) -> bool:
    """Set up a Central Heating Controller config entry."""
    coordinator = ControllerCoordinator(hass, entry)
    await coordinator.async_setup()
    entry.runtime_data = ControllerRuntimeData(coordinator)
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(async_update_listener))
    return True


async def async_unload_entry(hass: HomeAssistant, entry: CentralHeatingConfigEntry) -> bool:
    """Unload platforms before shutting down coordinator resources."""
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        await entry.runtime_data.coordinator.async_shutdown()
        entry.runtime_data = None  # type: ignore[assignment]
    return unloaded


async def async_update_listener(hass: HomeAssistant, entry: CentralHeatingConfigEntry) -> None:
    """Reload an entry after any options update."""
    await hass.config_entries.async_reload(entry.entry_id)


__all__ = (
    "DOMAIN",
    "CentralHeatingConfigEntry",
    "async_setup_entry",
    "async_unload_entry",
    "async_update_listener",
)
