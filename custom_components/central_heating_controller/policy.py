"""Pure control policy for the central heating controller."""

from .models import ControlInputs, ControlResult, ControllerStatus


def evaluate_policy(inputs: ControlInputs) -> ControlResult:
    """Evaluate an input snapshot according to strict controller priority."""
    if not inputs.thermostat_available:
        return ControlResult(ControllerStatus.UNAVAILABLE, None, None, "thermostat_unavailable")
    if not inputs.auto_mode:
        return ControlResult(ControllerStatus.OFF, None, "off", "auto_mode_off")
    if inputs.blast_active:
        return ControlResult(
            ControllerStatus.HEAT_BLAST,
            inputs.high_temperature,
            inputs.active_hvac_mode,
            "blast_active",
        )
    if inputs.manual_override_target is not None:
        return ControlResult(
            ControllerStatus.MANUAL_OVERRIDE,
            inputs.manual_override_target,
            None,
            "external_target_preserved",
        )
    if not inputs.occupied and inputs.journey_home and inputs.preheat_ready:
        return ControlResult(
            ControllerStatus.PREHEATING,
            inputs.high_temperature,
            inputs.active_hvac_mode,
            "arrival_within_warmup",
        )
    if not inputs.occupied:
        return ControlResult(
            ControllerStatus.AWAY,
            inputs.eco_temperature,
            inputs.active_hvac_mode,
            "home_unoccupied",
        )
    if inputs.schedule_high:
        return ControlResult(
            ControllerStatus.HIGH,
            inputs.high_temperature,
            inputs.active_hvac_mode,
            "schedule_on",
        )
    return ControlResult(
        ControllerStatus.LOW,
        inputs.low_temperature,
        inputs.active_hvac_mode,
        "schedule_off_or_unavailable",
    )
