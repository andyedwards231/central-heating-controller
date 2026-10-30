from datetime import datetime, timedelta, timezone

import pytest

from custom_components.central_heating_controller.readiness import evaluate_readiness

NOW = datetime(2026, 10, 23, 12, tzinfo=timezone.utc)


def good_accuracy() -> dict:
    return {
        "1h": {"count": 24, "mae_c": 0.2, "bias_c": 0.1, "distinct_dates": 3, "span_hours": 50, "latest_at": (NOW - timedelta(hours=1)).isoformat()},
        "6h": {"count": 12, "mae_c": 0.4, "bias_c": -0.2, "distinct_dates": 3, "span_hours": 50, "latest_at": (NOW - timedelta(hours=2)).isoformat()},
        "24h": {"count": 6, "mae_c": 0.9, "bias_c": 0.4, "distinct_dates": 2, "span_hours": 25, "latest_at": (NOW - timedelta(hours=3)).isoformat()},
    }


def evaluate(**overrides: object) -> dict:
    args: dict = {
        "now": NOW,
        "forecast": {"model_score": 0.7, "learned_samples": 50, "heating_response_learned": True, "demand_model_learned": True, "forecast": [{"temperature": 20}]},
        "plan": {"feasible": True, "provisional": False, "forecast": [{"start": "08:00"}]},
        "accuracy": good_accuracy(),
        "input_checks": [{"key": "fresh", "label": "Fresh data", "passed": True, "detail": "Data is fresh."}],
        "cost_calibration": {
            "reason": None, "heat_kw": 12.0, "sample_count": 30,
            "heating_samples": 6, "nonheating_samples": 6, "distinct_dates": 3,
        },
        "rate_available": True,
    }
    args.update(overrides)
    now = args.pop("now")
    return evaluate_readiness(now, **args)


def check(result: dict, key: str) -> dict:
    return next(item for item in result["checks"] if item["key"] == key)


def test_complete_current_evidence_passes_with_plain_progress_and_threshold_details() -> None:
    result = evaluate()

    assert result["ready"] is True
    assert result["blockers"] == []
    assert check(result, "thermal_windows")["detail"] == "40+ of 40 thermal windows recorded."
    assert "average error 0.20°C" in check(result, "accuracy_1h")["detail"]
    assert "within ±0.20°C" in check(result, "accuracy_1h")["detail"]
    assert result["cost_status"]["available"] is True
    gas = next(item for item in result["cost_status"]["checks"] if item["key"] == "gas_calibration")
    assert "30/30 intervals" in gas["detail"]


@pytest.mark.parametrize(
    ("horizon", "changes"),
    [
        ("1h", {"count": 23}), ("1h", {"mae_c": 0.31}), ("1h", {"bias_c": 0.21}),
        ("1h", {"distinct_dates": 2}), ("1h", {"span_hours": 47.9}),
        ("6h", {"count": 11}), ("6h", {"mae_c": 0.51}), ("6h", {"bias_c": -0.31}),
        ("6h", {"span_hours": 47.9}), ("24h", {"count": 5}), ("24h", {"mae_c": 1.01}),
        ("24h", {"bias_c": 0.51}), ("24h", {"distinct_dates": 1}), ("24h", {"span_hours": 23.9}),
    ],
)
def test_accuracy_thresholds_and_coverage_are_enforced(horizon: str, changes: dict) -> None:
    accuracy = good_accuracy()
    accuracy[horizon].update(changes)

    result = evaluate(accuracy=accuracy)

    assert result["ready"] is False
    assert check(result, f"accuracy_{horizon}")["passed"] is False


def test_stale_and_future_accuracy_never_passes_even_with_good_old_scores() -> None:
    accuracy = good_accuracy()
    accuracy["1h"]["latest_at"] = (NOW - timedelta(hours=6, seconds=1)).isoformat()
    accuracy["6h"]["latest_at"] = (NOW + timedelta(seconds=1)).isoformat()

    result = evaluate(accuracy=accuracy)

    assert check(result, "accuracy_1h")["passed"] is False
    assert check(result, "accuracy_6h")["passed"] is False
    assert result["ready"] is False


@pytest.mark.parametrize("bad", [True, float("nan"), float("inf"), "24", None])
def test_malformed_or_nonfinite_accuracy_values_fail_closed(bad: object) -> None:
    accuracy = good_accuracy()
    accuracy["1h"]["mae_c"] = bad

    assert check(evaluate(accuracy=accuracy), "accuracy_1h")["passed"] is False


def test_invalid_cost_diagnostics_do_not_block_thermal_readiness() -> None:
    result = evaluate(cost_calibration={"reason": None, "heat_kw": float("nan")}, rate_available=1,
                      gas_fresh=False)

    assert result["ready"] is True
    assert result["cost_status"]["available"] is False
    assert {item["key"] for item in result["cost_status"]["checks"]} == {"gas_calibration", "rate_available", "gas_fresh"}


def test_malformed_accuracy_or_input_check_collection_fails_closed() -> None:
    result = evaluate(accuracy={"1h": [], "6h": None}, input_checks=None)

    assert result["ready"] is False
    assert all(check(result, f"accuracy_{horizon}")["passed"] is False for horizon in ("1h", "6h", "24h"))
    assert check(result, "input_checks")["passed"] is False


@pytest.mark.parametrize("bad_score", [-0.01, 1.01, float("nan"), True])
def test_coverage_score_must_be_finite_and_between_zero_and_one(bad_score: object) -> None:
    result = evaluate(forecast={
        "model_score": bad_score, "learned_samples": 50,
        "heating_response_learned": True, "demand_model_learned": True,
        "forecast": [{"temperature": 20}],
    })

    assert check(result, "model_score")["passed"] is False


@pytest.mark.parametrize("bad", [-0.01, True, float("nan")])
def test_negative_or_malformed_accuracy_error_fails_closed(bad: object) -> None:
    accuracy = good_accuracy()
    accuracy["1h"]["mae_c"] = bad

    assert check(evaluate(accuracy=accuracy), "accuracy_1h")["passed"] is False


@pytest.mark.parametrize(
    ("field", "bad"),
    [("count", -1), ("distinct_dates", -1), ("distinct_dates", 25), ("span_hours", -0.1), ("span_hours", 169)],
)
def test_accuracy_sample_coverage_must_be_structurally_plausible(field: str, bad: object) -> None:
    accuracy = good_accuracy()
    accuracy["1h"][field] = bad

    assert check(evaluate(accuracy=accuracy), "accuracy_1h")["passed"] is False


@pytest.mark.parametrize(
    ("forecast", "plan"),
    [
        ({"model_score": 0.7, "learned_samples": 50, "heating_response_learned": True, "demand_model_learned": True, "forecast": []}, {"feasible": True, "provisional": False, "forecast": [{"start": "08:00"}]}),
        ({"model_score": 0.7, "learned_samples": 50, "heating_response_learned": True, "demand_model_learned": True, "forecast": [{"temperature": 20}]}, {"feasible": True, "provisional": False, "forecast": []}),
    ],
)
def test_forecast_and_feasible_plan_each_need_nonempty_forecast_lists(forecast: dict, plan: dict) -> None:
    result = evaluate(forecast=forecast, plan=plan)

    assert check(result, "forecast_available")["passed"] is bool(forecast["forecast"])
    assert check(result, "plan_feasible")["passed"] is bool(plan["forecast"])
