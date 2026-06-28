from datetime import datetime, timezone

import pytest

from custom_components.central_heating_controller.models import (
    ControlInputs,
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
    ("changes", "status", "target", "mode"),
    [
        (
            {"thermostat_available": False},
            ControllerStatus.UNAVAILABLE,
            None,
            None,
        ),
        ({"auto_mode": False}, ControllerStatus.OFF, None, "off"),
        ({"blast_active": True}, ControllerStatus.HEAT_BLAST, 20.0, "heat"),
        (
            {"manual_override_target": 18.5},
            ControllerStatus.MANUAL_OVERRIDE,
            18.5,
            None,
        ),
        (
            {"occupied": False, "journey_home": True, "preheat_ready": True},
            ControllerStatus.PREHEATING,
            20.0,
            "heat",
        ),
        ({"occupied": False}, ControllerStatus.AWAY, 14.0, "heat"),
        ({"schedule_high": True}, ControllerStatus.HIGH, 20.0, "heat"),
        ({}, ControllerStatus.LOW, 17.0, "heat"),
    ],
)
def test_policy_priority(changes, status, target, mode) -> None:
    result = evaluate_policy(ControlInputs(**(BASE | changes)))
    assert (result.status, result.target_temperature, result.hvac_mode) == (
        status,
        target,
        mode,
    )
