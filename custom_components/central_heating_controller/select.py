"""Select entities for the Central Heating Controller integration."""

from homeassistant.components.select import SelectEntity, SelectEntityDescription
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import CentralHeatingConfigEntry
from .coordinator import ControllerCoordinator
from .entity import ControllerEntity

CONTROLLER_MODE_DESCRIPTION = SelectEntityDescription(
    key="controller_mode",
    translation_key="controller_mode",
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: CentralHeatingConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the controller mode selector."""
    async_add_entities(
        [ControllerModeSelect(entry.runtime_data.coordinator, entry.entry_id)]
    )


class ControllerModeSelect(ControllerEntity, SelectEntity):
    """Choose Auto, Away or Off control mode."""

    _attr_options = ["auto", "away", "off"]

    def __init__(self, coordinator: ControllerCoordinator, entry_id: str) -> None:
        """Initialise the selector."""
        super().__init__(coordinator, entry_id, CONTROLLER_MODE_DESCRIPTION)

    @property
    def current_option(self) -> str:
        """Return the active controller mode."""
        return self.coordinator.controller_mode

    async def async_select_option(self, option: str) -> None:
        """Apply the selected controller mode."""
        await self.coordinator.async_set_controller_mode(option)
