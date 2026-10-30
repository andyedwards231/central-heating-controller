"""Sensor entities for the Central Heating Controller integration."""

from __future__ import annotations

from datetime import datetime
from math import isfinite
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
            ControllerTemperatureForecastSensor(coordinator, entry.entry_id),
            SmartHeatingPlanSensor(coordinator, entry.entry_id),
            SmartHeatingReadinessSensor(coordinator, entry.entry_id),
            PredictedHeatingCostSensor(coordinator, entry.entry_id, "today"),
            PredictedHeatingCostSensor(coordinator, entry.entry_id, "tomorrow"),
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
            "climate_entity": self.coordinator.config["climate_entity"],
            "schedule_entity": self.coordinator.config["schedule_entity"],
            "reason": data.result.reason,
            "current_temperature": data.current_temperature,
            "effective_target_temperature": data.result.target_temperature,
            "current_heating_demand_percent": self.coordinator.current_heating_demand_percent(),
            "learned_heating_rate": data.learned_rate if data.learned_trusted else None,
            "warmup_minutes": data.warmup_minutes,
            "warmup_source": data.warmup_source,
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
    """Publish thermal heat gain; retain the entity ID for existing dashboards."""

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


class ControllerTemperatureForecastSensor(ControllerEntity, SensorEntity):
    """Forecast temperature at +24h with the full curve in attributes."""

    _unrecorded_attributes = frozenset({"forecast", "assumptions"})

    def __init__(self, coordinator: ControllerCoordinator, entry_id: str) -> None:
        super().__init__(coordinator, entry_id, SensorEntityDescription(
            key="indoor_temperature_forecast", translation_key="indoor_temperature_forecast",
            device_class=SensorDeviceClass.TEMPERATURE, suggested_display_precision=1,
        ))

    @property
    def native_value(self) -> float | None:
        points = self.coordinator.data.temperature_forecast.get("forecast", [])
        return points[-1]["temperature"] if points else None

    @property
    def native_unit_of_measurement(self) -> str:
        return self.thermostat_temperature_unit

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return self.coordinator.data.temperature_forecast or {
            "forecast": [], "confidence": "learning",
            "reason": "Select a weather forecast and outdoor temperature sensor in integration options",
        }


class PredictedHeatingCostSensor(ControllerEntity, SensorEntity):
    """Publish a forecast heating cost in GBP when available."""

    _unrecorded_attributes = frozenset({"reason", "calibration", "details"})

    def __init__(self, coordinator: ControllerCoordinator, entry_id: str, period: str) -> None:
        description = SensorEntityDescription(
            key=f"predicted_heating_cost_{period}",
            translation_key=f"predicted_heating_cost_{period}",
            device_class=SensorDeviceClass.MONETARY,
            suggested_display_precision=2,
        )
        super().__init__(coordinator, entry_id, description)
        self._period = period

    @property
    def available(self) -> bool:
        # Keep the entity available while the model is learning so HA publishes
        # its reason and calibration attributes alongside an unknown value.
        return super().available

    @property
    def native_value(self) -> float | None:
        period_data = self.coordinator.data.heating_cost.get(self._period, {})
        if not isinstance(period_data, dict):
            return None
        value = period_data.get("cost_gbp")
        return value if isinstance(value, (int, float)) and not isinstance(value, bool) and isfinite(value) else None

    @property
    def native_unit_of_measurement(self) -> str:
        return "GBP"

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        details = self.coordinator.data.heating_cost.get(self._period, {})
        return dict(details) if isinstance(details, dict) else {}


class SmartHeatingPlanSensor(ControllerEntity, SensorEntity):
    """Expose the proposed or active intelligent schedule."""

    _unrecorded_attributes = frozenset({"forecast", "settings", "gap_drops"})

    def __init__(self, coordinator: ControllerCoordinator, entry_id: str) -> None:
        super().__init__(coordinator, entry_id, SensorEntityDescription(
            key="smart_heating_plan", translation_key="smart_heating_plan",
        ))

    @property
    def native_value(self) -> str:
        return self.coordinator.data.smart_plan.get("state", "unavailable")

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return self.coordinator.data.smart_plan or {"settings": self.coordinator.smart_settings(), "preview": True, "forecast": []}


class SmartHeatingReadinessSensor(ControllerEntity, SensorEntity):
    """Explain the learning and safety checks required for intelligent control."""

    _unrecorded_attributes = frozenset({"checks", "blockers"})

    def __init__(self, coordinator: ControllerCoordinator, entry_id: str) -> None:
        super().__init__(coordinator, entry_id, SensorEntityDescription(
            key="smart_heating_readiness", translation_key="smart_heating_readiness",
            icon="mdi:clipboard-check-outline",
        ))

    @property
    def native_value(self) -> str:
        return self.coordinator.data.smart_readiness.get("state", "learning")

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return self.coordinator.data.smart_readiness
