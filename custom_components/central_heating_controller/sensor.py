"""Sensor entities for the Central Heating Controller integration."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import CentralHeatingConfigEntry
from .coordinator import ControllerCoordinator
from .entity import ControllerEntity
from .models import ControllerStatus

STATUS_DESCRIPTION = SensorEntityDescription(
    key="status",
    translation_key="status",
    device_class=SensorDeviceClass.ENUM,
    options=[status.value for status in ControllerStatus],
)
EFFECTIVE_TARGET_DESCRIPTION = SensorEntityDescription(
    key="effective_target_temperature",
    translation_key="effective_target_temperature",
    device_class=SensorDeviceClass.TEMPERATURE,
    state_class=SensorStateClass.MEASUREMENT,
)
LEARNED_RATE_DESCRIPTION = SensorEntityDescription(
    key="learned_heating_rate",
    translation_key="learned_heating_rate",
    state_class=SensorStateClass.MEASUREMENT,
    suggested_display_precision=2,
)
PREHEAT_START_DESCRIPTION = SensorEntityDescription(
    key="preheat_start_time",
    translation_key="preheat_start_time",
    device_class=SensorDeviceClass.TIMESTAMP,
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: CentralHeatingConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up controller state sensors."""
    coordinator = entry.runtime_data.coordinator
    async_add_entities(
        (
            ControllerStatusSensor(coordinator, entry.entry_id),
            ControllerEffectiveTargetSensor(coordinator, entry.entry_id),
            ControllerLearnedRateSensor(coordinator, entry.entry_id),
            ControllerPreheatStartSensor(coordinator, entry.entry_id),
        )
    )


class ControllerStatusSensor(ControllerEntity, SensorEntity):
    """Publish the controller's current policy status."""

    def __init__(self, coordinator: ControllerCoordinator, entry_id: str) -> None:
        """Initialize the status sensor."""
        super().__init__(coordinator, entry_id, STATUS_DESCRIPTION)

    @property
    def native_value(self) -> str:
        """Return the stable enum value."""
        return self.coordinator.data.status.value

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return only currently meaningful controller details."""
        data = self.coordinator.data
        values = {
            "reason": data.result.reason,
            "current_temperature": data.current_temperature,
            "effective_target_temperature": data.result.target_temperature,
            "learned_heating_rate": data.learned_rate if data.learned_trusted else None,
            "manual_override": (True if data.status is ControllerStatus.MANUAL_OVERRIDE else None),
            "arrival_time": data.arrival_time,
            "preheat_start_time": data.preheat_start_time,
        }
        return {key: value for key, value in values.items() if value is not None}


class ControllerEffectiveTargetSensor(ControllerEntity, SensorEntity):
    """Publish the target selected by the current policy."""

    def __init__(self, coordinator: ControllerCoordinator, entry_id: str) -> None:
        """Initialize the effective target sensor."""
        super().__init__(coordinator, entry_id, EFFECTIVE_TARGET_DESCRIPTION)

    @property
    def available(self) -> bool:
        """Return availability only when the policy has a target."""
        return super().available and self.coordinator.data.result.target_temperature is not None

    @property
    def native_value(self) -> float | None:
        """Return the effective target."""
        return self.coordinator.data.result.target_temperature

    @property
    def native_unit_of_measurement(self) -> str:
        """Return the selected thermostat's native temperature unit."""
        return self.thermostat_temperature_unit


class ControllerLearnedRateSensor(ControllerEntity, SensorEntity):
    """Publish a trusted learned temperature increase per hour."""

    def __init__(self, coordinator: ControllerCoordinator, entry_id: str) -> None:
        """Initialize the learned rate sensor."""
        super().__init__(coordinator, entry_id, LEARNED_RATE_DESCRIPTION)

    @property
    def available(self) -> bool:
        """Return availability only after the learner is trusted."""
        return (
            super().available
            and self.coordinator.data.learned_trusted
            and self.coordinator.data.learned_rate is not None
        )

    @property
    def native_value(self) -> float | None:
        """Return the learned heating rate."""
        return self.coordinator.data.learned_rate

    @property
    def native_unit_of_measurement(self) -> str:
        """Return temperature increase per hour in the thermostat unit."""
        return f"{self.thermostat_temperature_unit}/h"


class ControllerPreheatStartSensor(ControllerEntity, SensorEntity):
    """Publish the calculated start of a valid timed home journey."""

    def __init__(self, coordinator: ControllerCoordinator, entry_id: str) -> None:
        """Initialize the pre-heat start sensor."""
        super().__init__(coordinator, entry_id, PREHEAT_START_DESCRIPTION)

    @property
    def available(self) -> bool:
        """Return availability only for a valid timed journey."""
        data = self.coordinator.data
        return (
            super().available
            and data.arrival_time is not None
            and data.preheat_start_time is not None
        )

    @property
    def native_value(self) -> datetime | None:
        """Return the calculated pre-heat start."""
        return self.coordinator.data.preheat_start_time
