"""Button entities for the Central Heating Controller integration."""

from homeassistant.components.button import ButtonEntity, ButtonEntityDescription
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import CentralHeatingConfigEntry
from .entity import ControllerEntity

HEAT_BLAST_DESCRIPTION = ButtonEntityDescription(
    key="heat_blast",
    translation_key="heat_blast",
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: CentralHeatingConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the controller buttons."""
    async_add_entities([ControllerHeatBlastButton(entry.runtime_data.coordinator, entry.entry_id)])


class ControllerHeatBlastButton(ControllerEntity, ButtonEntity):
    """Start or restart a fixed-duration heat blast."""

    def __init__(self, coordinator, entry_id: str) -> None:
        """Initialize the Heat Blast button."""
        super().__init__(coordinator, entry_id, HEAT_BLAST_DESCRIPTION)

    async def async_press(self) -> None:
        """Start a heat blast when Auto mode is enabled."""
        await self.coordinator.async_start_blast()
