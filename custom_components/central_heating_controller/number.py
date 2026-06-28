"""Number entities for the Central Heating Controller integration."""

from __future__ import annotations

from homeassistant.components.climate import (
    ATTR_MAX_TEMP,
    ATTR_MIN_TEMP,
    ATTR_TARGET_TEMP_STEP,
)
from homeassistant.components.number import (
    NumberDeviceClass,
    NumberEntity,
    NumberEntityDescription,
)
from homeassistant.const import UnitOfTime
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import CentralHeatingConfigEntry
from .const import (
    CONF_CLIMATE,
    CONF_ECO_TEMP,
    CONF_FALLBACK_MINUTES,
    CONF_HIGH_TEMP,
    CONF_LOW_TEMP,
    CONF_MAX_WARMUP_MINUTES,
)
from .entity import ControllerEntity

TEMPERATURE_KEYS = frozenset({CONF_HIGH_TEMP, CONF_LOW_TEMP, CONF_ECO_TEMP})

NUMBER_DESCRIPTIONS: tuple[NumberEntityDescription, ...] = (
    NumberEntityDescription(
        key=CONF_HIGH_TEMP,
        translation_key=CONF_HIGH_TEMP,
        device_class=NumberDeviceClass.TEMPERATURE,
    ),
    NumberEntityDescription(
        key=CONF_LOW_TEMP,
        translation_key=CONF_LOW_TEMP,
        device_class=NumberDeviceClass.TEMPERATURE,
    ),
    NumberEntityDescription(
        key=CONF_ECO_TEMP,
        translation_key=CONF_ECO_TEMP,
        device_class=NumberDeviceClass.TEMPERATURE,
    ),
    NumberEntityDescription(
        key=CONF_FALLBACK_MINUTES,
        translation_key=CONF_FALLBACK_MINUTES,
        device_class=NumberDeviceClass.DURATION,
        native_min_value=5,
        native_max_value=360,
        native_step=5,
        native_unit_of_measurement=UnitOfTime.MINUTES,
    ),
    NumberEntityDescription(
        key=CONF_MAX_WARMUP_MINUTES,
        translation_key=CONF_MAX_WARMUP_MINUTES,
        device_class=NumberDeviceClass.DURATION,
        native_min_value=5,
        native_max_value=360,
        native_step=5,
        native_unit_of_measurement=UnitOfTime.MINUTES,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: CentralHeatingConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up controller setting numbers."""
    coordinator = entry.runtime_data.coordinator
    async_add_entities(
        ControllerSettingNumber(coordinator, entry.entry_id, description)
        for description in NUMBER_DESCRIPTIONS
    )


class ControllerSettingNumber(ControllerEntity, NumberEntity):
    """A validated controller setting."""

    entity_description: NumberEntityDescription

    def __init__(
        self,
        coordinator,
        entry_id: str,
        description: NumberEntityDescription,
    ) -> None:
        """Initialize a setting number."""
        super().__init__(coordinator, entry_id, description)

    @property
    def _is_temperature(self) -> bool:
        return self.entity_description.key in TEMPERATURE_KEYS

    @property
    def native_value(self) -> float:
        """Return the current setting value."""
        return float(getattr(self.coordinator.settings, self.entity_description.key))

    @property
    def native_unit_of_measurement(self) -> str | None:
        """Return the thermostat unit or the duration unit."""
        if self._is_temperature:
            return self.thermostat_temperature_unit
        return self.entity_description.native_unit_of_measurement

    @property
    def native_min_value(self) -> float:
        """Return the thermostat or duration minimum."""
        if not self._is_temperature:
            assert self.entity_description.native_min_value is not None
            return self.entity_description.native_min_value
        return self._climate_number_attribute(ATTR_MIN_TEMP)

    @property
    def native_max_value(self) -> float:
        """Return the thermostat or duration maximum."""
        if not self._is_temperature:
            assert self.entity_description.native_max_value is not None
            return self.entity_description.native_max_value
        return self._climate_number_attribute(ATTR_MAX_TEMP)

    @property
    def native_step(self) -> float:
        """Return the thermostat or duration step."""
        if not self._is_temperature:
            assert self.entity_description.native_step is not None
            return self.entity_description.native_step
        return self._climate_number_attribute(ATTR_TARGET_TEMP_STEP)

    def _climate_number_attribute(self, attribute: str) -> float:
        """Return a required numeric attribute from the selected thermostat."""
        climate = self.coordinator.hass.states.get(self.coordinator.config[CONF_CLIMATE])
        assert climate is not None
        return float(climate.attributes[attribute])

    async def async_set_native_value(self, value: float) -> None:
        """Validate and update this setting through the coordinator."""
        await self.coordinator.async_update_setting(self.entity_description.key, value)
