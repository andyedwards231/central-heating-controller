"""Shared entities for the Central Heating Controller integration."""

from __future__ import annotations

from homeassistant.const import UnitOfTemperature
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.entity import EntityDescription
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, NAME
from .coordinator import ControllerCoordinator


class ControllerEntity(CoordinatorEntity[ControllerCoordinator]):
    """Base class for entities belonging to one controller service device."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: ControllerCoordinator,
        entry_id: str,
        description: EntityDescription,
    ) -> None:
        """Initialize a controller entity."""
        super().__init__(coordinator)
        self.entity_description = description
        self._attr_unique_id = f"{entry_id}_{description.key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry_id)},
            name=NAME,
            entry_type=DeviceEntryType.SERVICE,
        )

    @property
    def thermostat_temperature_unit(self) -> str:
        """Return the selected thermostat's native temperature unit."""
        capabilities = self.coordinator.data.temperature_capabilities
        if capabilities is not None:
            return capabilities.unit
        fallback = self.coordinator.hass.config.units.temperature_unit
        return fallback if fallback in UnitOfTemperature else UnitOfTemperature.CELSIUS
