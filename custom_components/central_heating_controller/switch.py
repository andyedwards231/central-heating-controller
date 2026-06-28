"""Switch entities for the Central Heating Controller integration."""

from typing import Any

from homeassistant.components.switch import SwitchEntity, SwitchEntityDescription
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import CentralHeatingConfigEntry
from .coordinator import ControllerCoordinator
from .entity import ControllerEntity

AUTO_MODE_DESCRIPTION = SwitchEntityDescription(
    key="auto_mode",
    translation_key="auto_mode",
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: CentralHeatingConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the controller switches."""
    async_add_entities([ControllerAutoModeSwitch(entry.runtime_data.coordinator, entry.entry_id)])


class ControllerAutoModeSwitch(ControllerEntity, SwitchEntity):
    """Enable or disable automatic heating control."""

    def __init__(self, coordinator: ControllerCoordinator, entry_id: str) -> None:
        """Initialize the Auto mode switch."""
        super().__init__(coordinator, entry_id, AUTO_MODE_DESCRIPTION)

    @property
    def is_on(self) -> bool:
        """Return whether automatic control is enabled."""
        return self.coordinator.persistent_state.auto_mode

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Enable automatic control and re-evaluate."""
        await self.coordinator.async_set_auto_mode(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Disable automatic control and command strict Off."""
        await self.coordinator.async_set_auto_mode(False)
