"""Data models for the central heating controller."""

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .coordinator import ControllerCoordinator


class ControllerStatus(StrEnum):
    """Possible central heating controller states."""

    HIGH = "high"
    LOW = "low"
    PREHEATING = "pre_heating"
    AWAY = "away"
    OFF = "off"
    HEAT_BLAST = "heat_blast"
    MANUAL_OVERRIDE = "manual_override"
    UNAVAILABLE = "unavailable"


@dataclass(frozen=True)
class ControllerSettings:
    """User-editable controller settings."""

    high_temperature: float
    low_temperature: float
    eco_temperature: float
    fallback_warmup_minutes: int
    maximum_warmup_minutes: int
    active_hvac_mode: str
    destination_home_override: str | None


@dataclass(frozen=True)
class ControlInputs:
    """Complete input snapshot for one policy evaluation."""

    thermostat_available: bool
    auto_mode: bool
    blast_active: bool
    manual_override_target: float | None
    occupied: bool
    schedule_high: bool
    journey_home: bool
    preheat_ready: bool
    current_temperature: float | None
    high_temperature: float
    low_temperature: float
    eco_temperature: float
    active_hvac_mode: str
    evaluated_at: datetime


@dataclass(frozen=True)
class ControlResult:
    """Command intent and status produced by policy evaluation."""

    status: ControllerStatus
    target_temperature: float | None
    hvac_mode: str | None
    reason: str


JsonPrimitive = str | int | float | bool | None


@dataclass
class PersistentState:
    """Validated state retained across Home Assistant restarts.

    Validation of untrusted serialized values is performed field-by-field by
    ``ControllerStore`` so a damaged field cannot invalidate its neighbours.
    """

    auto_mode: bool = True
    blast_until: datetime | None = None
    manual_override_target: float | None = None
    manual_override_fingerprint: tuple[JsonPrimitive, ...] | None = None
    learned_rate: float | None = None
    learned_sample_count: int = 0


@dataclass(frozen=True)
class TemperatureCapabilities:
    """Validated thermostat metadata published for entity consumers."""

    minimum: float
    maximum: float
    step: float | None
    unit: str


@dataclass(frozen=True)
class ControllerState:
    """Immutable state published by the coordinator after each evaluation.

    ``learned_rate`` is the learner's raw persisted estimate; consumers must
    consult ``learned_trusted`` before using it for decisions.
    """

    result: ControlResult
    current_temperature: float | None
    learned_rate: float | None
    learned_trusted: bool
    arrival_time: datetime | None
    preheat_start_time: datetime | None
    warmup_minutes: float | int
    temperature_capabilities: TemperatureCapabilities | None = None

    @property
    def status(self) -> ControllerStatus:
        """Return the policy status for entity consumers."""
        return self.result.status


@dataclass(frozen=True)
class ControllerRuntimeData:
    """Runtime data owned by one config entry."""

    coordinator: "ControllerCoordinator"
