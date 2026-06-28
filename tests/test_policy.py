from dataclasses import FrozenInstanceError
from datetime import datetime, timezone

import pytest

from custom_components.central_heating_controller.models import (
    ControlInputs,
    ControlResult,
    ControllerSettings,
    ControllerStatus,
)
from custom_components.central_heating_controller.policy import evaluate_policy

BASE = dict(
    thermostat_available=True,
    auto_mode=True,
    blast_active=False,
    manual_override_target=None,
    occupied=True,
    schedule_high=False,
    journey_home=False,
    preheat_ready=False,
    current_temperature=16.0,
    high_temperature=20.0,
    low_temperature=17.0,
    eco_temperature=14.0,
    active_hvac_mode="heat",
    evaluated_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
)


@pytest.mark.parametrize(
    ("changes", "status", "target", "mode", "reason"),
    [
        (
            {
                "thermostat_available": False,
                "auto_mode": False,
                "blast_active": True,
                "manual_override_target": 18.5,
                "occupied": False,
                "schedule_high": True,
                "journey_home": True,
                "preheat_ready": True,
            },
            ControllerStatus.UNAVAILABLE,
            None,
            None,
            "thermostat_unavailable",
        ),
        (
            {
                "auto_mode": False,
                "blast_active": True,
                "manual_override_target": 18.5,
                "occupied": False,
                "schedule_high": True,
                "journey_home": True,
                "preheat_ready": True,
            },
            ControllerStatus.OFF,
            None,
            "off",
            "auto_mode_off",
        ),
        (
            {
                "blast_active": True,
                "manual_override_target": 18.5,
                "occupied": False,
            },
            ControllerStatus.HEAT_BLAST,
            20.0,
            "heat",
            "blast_active",
        ),
        (
            {
                "manual_override_target": 18.5,
                "occupied": False,
                "journey_home": True,
                "preheat_ready": True,
            },
            ControllerStatus.MANUAL_OVERRIDE,
            18.5,
            None,
            "external_target_preserved",
        ),
        (
            {
                "occupied": False,
                "schedule_high": True,
                "journey_home": True,
                "preheat_ready": True,
            },
            ControllerStatus.PREHEATING,
            20.0,
            "heat",
            "arrival_within_warmup",
        ),
        (
            {"occupied": False, "schedule_high": True},
            ControllerStatus.AWAY,
            14.0,
            "heat",
            "home_unoccupied",
        ),
        (
            {"schedule_high": True},
            ControllerStatus.HIGH,
            20.0,
            "heat",
            "schedule_on",
        ),
        (
            {},
            ControllerStatus.LOW,
            17.0,
            "heat",
            "schedule_off_or_unavailable",
        ),
    ],
)
def test_policy_priority(changes, status, target, mode, reason) -> None:
    result = evaluate_policy(ControlInputs(**(BASE | changes)))
    assert (result.status, result.target_temperature, result.hvac_mode, result.reason) == (
        status,
        target,
        mode,
        reason,
    )


def test_controller_status_values_are_ordered() -> None:
    assert [status.value for status in ControllerStatus] == [
        "high",
        "low",
        "pre_heating",
        "away",
        "off",
        "heat_blast",
        "manual_override",
        "unavailable",
    ]


@pytest.mark.parametrize(
    ("model", "field", "new_value"),
    [
        (
            ControllerSettings(
                high_temperature=20.0,
                low_temperature=17.0,
                eco_temperature=14.0,
                fallback_warmup_minutes=60,
                maximum_warmup_minutes=180,
                active_hvac_mode="heat",
                destination_home_override=None,
            ),
            "high_temperature",
            21.0,
        ),
        (ControlInputs(**BASE), "occupied", False),
        (
            ControlResult(ControllerStatus.LOW, 17.0, "heat", "schedule_off_or_unavailable"),
            "reason",
            "changed",
        ),
    ],
)
def test_control_models_are_frozen(model, field, new_value) -> None:
    with pytest.raises(FrozenInstanceError):
        setattr(model, field, new_value)
