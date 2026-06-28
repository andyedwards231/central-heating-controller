"""Immutable data models for the central heating controller."""

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum


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
