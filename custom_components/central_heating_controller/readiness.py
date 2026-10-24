"""Pure evaluation of the controller's evidence for autonomous readiness."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from math import isfinite


def _number(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        number = float(value)
    except (OverflowError, TypeError, ValueError):
        return None
    return number if isfinite(number) else None


def _timestamp(value: object) -> datetime | None:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value)
        except (TypeError, ValueError, OverflowError):
            return None
    else:
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    try:
        return parsed.astimezone(timezone.utc)
    except (OverflowError, ValueError):
        return None


def evaluate_readiness(
    now: datetime,
    *,
    forecast: dict,
    plan: dict,
    accuracy: dict,
    input_checks: list[dict],
    cost_calibration: dict,
    rate_available: bool,
) -> dict:
    """Evaluate whether the supplied forecast, plan and recent evidence are ready.

    This function is deliberately stateless. It validates all inputs locally,
    treats malformed values as failed checks and returns display-ready details
    alongside stable check keys for the coordinator/UI.
    """
    current = _timestamp(now)
    checks: list[dict[str, object]] = []

    def add(key: str, label: str, passed: bool, detail: str) -> None:
        checks.append({"key": key, "label": label, "passed": bool(passed), "detail": detail})

    valid_now = current is not None
    add("clock", "Current time", valid_now, "The current time is valid." if valid_now else "Current time is missing or invalid.")

    forecast_points = forecast.get("forecast") if isinstance(forecast, dict) else None
    forecast_ok = isinstance(forecast_points, list) and bool(forecast_points)
    samples = _whole_count(forecast.get("learned_samples")) if isinstance(forecast, dict) else None
    model_score = _number(forecast.get("model_score")) if isinstance(forecast, dict) else None
    add(
        "thermal_windows",
        "Thermal learning data",
        samples is not None and samples >= 40,
        f"{min(samples, 40) if samples is not None else 'Invalid'} of 40 thermal windows recorded."
        if samples is None or samples < 40 else "40+ of 40 thermal windows recorded.",
    )
    add(
        "model_score",
        "Heating and cooling coverage",
        model_score is not None and 0.5 <= model_score <= 1.0,
        f"Coverage score {model_score:.3f} (valid range 0.500–1.000)." if model_score is not None else "Coverage score is missing or invalid; it must be between 0.50 and 1.00.",
    )
    delay_learned = isinstance(forecast, dict) and forecast.get("heating_response_learned") is True
    demand_learned = isinstance(forecast, dict) and forecast.get("demand_model_learned") is True
    add(
        "heating_response", "Heating response learned", delay_learned,
        "Heating response delay has been measured." if delay_learned
        else "Still needs enough heating starts and stops to measure the delay.",
    )
    add(
        "demand_model", "Demand model learned", demand_learned,
        "The model has learned how the heating demand signal responds to control changes."
        if demand_learned else "Still needs enough setpoint changes to learn how the heating demand signal responds.",
    )
    add("forecast_available", "Forecast available", forecast_ok, "A non-empty temperature forecast is available." if forecast_ok else "Temperature forecast is missing or empty.")

    plan_points = plan.get("forecast") if isinstance(plan, dict) else None
    plan_ok = (
        isinstance(plan, dict) and plan.get("feasible") is True
        and plan.get("provisional") is False and isinstance(plan_points, list) and bool(plan_points)
    )
    if not isinstance(plan, dict):
        plan_detail = "Plan data is missing or invalid."
    elif plan.get("feasible") is not True:
        plan_detail = "The current plan is not marked feasible."
    elif plan.get("provisional") is not False:
        plan_detail = "The current plan is provisional."
    elif not isinstance(plan_points, list) or not plan_points:
        plan_detail = "The current plan has no heating schedule to follow."
    else:
        plan_detail = "The current plan is feasible and non-provisional."
    add("plan_feasible", "Feasible heating plan", plan_ok, plan_detail)

    if isinstance(input_checks, list):
        if not input_checks:
            add("input_checks", "Forecast inputs", False, "No forecast input checks were supplied.")
        for index, item in enumerate(input_checks):
            if not isinstance(item, dict):
                add(f"input_{index}", "Forecast input", False, "Forecast input check is malformed.")
                continue
            passed = item.get("passed") is True
            label = item.get("label") if isinstance(item.get("label"), str) and item.get("label") else "Forecast input"
            detail = item.get("detail") if isinstance(item.get("detail"), str) else ("Input check passed." if passed else "Input check failed or is malformed.")
            add(str(item.get("key") or f"input_{index}"), label, passed, detail)
    else:
        add("input_checks", "Forecast inputs", False, "Forecast input checks are missing or invalid.")

    calibration_ok = False
    heat_kw: float | None = None
    if isinstance(cost_calibration, dict):
        heat_kw = _number(cost_calibration.get("heat_kw"))
        calibration_ok = cost_calibration.get("reason") is None and heat_kw is not None and heat_kw > 0
    calibration_counts = _calibration_counts(cost_calibration)
    if calibration_ok:
        calibration_detail = f"Gas calibration estimates {heat_kw:g} kW; {calibration_counts}."
    elif not isinstance(cost_calibration, dict):
        calibration_detail = "Gas calibration data is missing or invalid."
    elif cost_calibration.get("reason") is not None:
        reason = cost_calibration.get("reason")
        reason_detail = {
            "insufficient_samples": "needs more gas and heating-demand observations",
            "insufficient_demand_spread": "needs a wider range of heating demand to estimate gas use",
            "unstable_fit": "does not yet produce a stable gas-use estimate",
        }.get(reason) if isinstance(reason, str) else None
        calibration_detail = f"Gas calibration {reason_detail}; {calibration_counts}." if reason_detail else f"Gas calibration is unavailable; {calibration_counts}."
    else:
        calibration_detail = f"Gas calibration needs a finite, positive heat output in kW; {calibration_counts}."
    add("gas_calibration", "Gas cost calibration", calibration_ok, calibration_detail)
    add("rate_available", "Energy rate", rate_available is True, "An energy rate is available." if rate_available is True else "A valid energy rate is unavailable.")

    _add_accuracy_checks(checks, current, accuracy)
    blockers = [str(check["label"]) for check in checks if check["passed"] is not True]
    return {"ready": not blockers, "checks": checks, "blockers": blockers}


def _whole_count(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value if value >= 0 else None


def _calibration_counts(calibration: object) -> str:
    """Describe observed calibration evidence against the model's minimums."""
    if not isinstance(calibration, dict):
        return "sample counts unavailable (need 30 intervals, including 6 heating and 6 non-heating, over 3 dates)"
    count = _whole_count(calibration.get("sample_count"))
    heating = _whole_count(calibration.get("heating_samples"))
    nonheating = _whole_count(calibration.get("nonheating_samples"))
    dates = _whole_count(calibration.get("distinct_dates"))
    def show(value):
        return str(value) if value is not None else "?"
    return (
        f"{show(count)}/30 intervals, {show(heating)}/6 heating, "
        f"{show(nonheating)}/6 non-heating, {show(dates)}/3 dates"
    )


def _add_accuracy_checks(checks: list[dict[str, object]], now: datetime | None, accuracy: object) -> None:
    requirements = {
        "1h": (24, 0.3, 0.2, 3, 48.0),
        "6h": (12, 0.5, 0.3, 3, 48.0),
        "24h": (6, 1.0, 0.5, 2, 24.0),
    }
    for horizon, (min_count, max_mae, max_bias, min_dates, min_span) in requirements.items():
        data = accuracy.get(horizon) if isinstance(accuracy, dict) else None
        valid_data = isinstance(data, dict)
        count = _whole_count(data.get("count")) if valid_data else None
        mae = _number(data.get("mae_c")) if valid_data else None
        bias = _number(data.get("bias_c")) if valid_data else None
        dates = _whole_count(data.get("distinct_dates")) if valid_data else None
        span = _number(data.get("span_hours")) if valid_data else None
        latest = _timestamp(data.get("latest_at")) if valid_data else None
        fresh = now is not None and latest is not None and latest <= now and now - latest <= timedelta(hours=6)
        structurally_valid = (
            count is not None and count <= 168
            and dates is not None and dates <= 8 and dates <= count
            and span is not None and 0 <= span <= 168
            and mae is not None and mae >= 0
        )
        quality = structurally_valid and count >= min_count and mae <= max_mae and bias is not None and abs(bias) <= max_bias
        coverage = structurally_valid and dates >= min_dates and span >= min_span
        ok = quality and coverage and fresh
        if count is None or mae is None or bias is None:
            measured = "No valid recent accuracy measurements"
        else:
            measured = f"{count}/{min_count} samples; average error {mae:.2f}°C (limit {max_mae:.2f}°C); bias {bias:+.2f}°C (within ±{max_bias:.2f}°C)"
        if dates is None or span is None:
            coverage_text = f"; date coverage unavailable (need {min_dates} dates over {min_span:g} hours)"
        else:
            coverage_text = f"; {dates}/{min_dates} dates over {span:.1f}/{min_span:g} hours"
        if latest is None:
            freshness_text = "; latest sample time is missing or invalid (must be within 6 hours)"
        elif now is None:
            freshness_text = "; current time is invalid"
        elif latest > now:
            freshness_text = "; latest sample is in the future"
        else:
            age_hours = (now - latest).total_seconds() / 3600
            freshness_text = f"; latest sample {age_hours:.1f} hours ago (maximum 6 hours)"
        checks.append({
            "key": f"accuracy_{horizon}",
            "label": f"Recent {horizon} forecast accuracy",
            "passed": bool(ok),
            "detail": measured + coverage_text + freshness_text + ("." if ok else ". More recent, accurate observations are needed."),
        })
