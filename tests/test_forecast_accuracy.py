from datetime import datetime, timedelta, timezone

import pytest

from custom_components.central_heating_controller.forecast_accuracy import (
    ForecastAccuracyTracker,
)
from custom_components.central_heating_controller.forecasting import ForecastPoint

NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)


def points(at: datetime, *, offset: float = 0.0) -> tuple[ForecastPoint, ...]:
    return tuple(
        ForecastPoint(at + timedelta(hours=hours), 20.0 + offset, 5.0, 20.0, False)
        for hours in (1, 6, 24)
    )


def test_scores_observations_near_each_issued_horizon_without_using_future_readings() -> None:
    tracker = ForecastAccuracyTracker()
    assert tracker.issue(NOW, points(NOW))
    assert tracker.metrics()["1h"] == {"mae_c": None, "bias_c": None, "rmse_c": None, "count": 0}

    tracker.observe(NOW + timedelta(hours=1), 21.0)
    tracker.observe(NOW + timedelta(hours=6, minutes=4), 19.0)
    tracker.observe(NOW + timedelta(hours=24, minutes=5), 20.5)

    metrics = tracker.metrics()
    assert metrics["1h"] == {"mae_c": 1.0, "bias_c": 1.0, "rmse_c": 1.0, "count": 1}
    assert metrics["6h"] == {"mae_c": 1.0, "bias_c": -1.0, "rmse_c": 1.0, "count": 1}
    assert metrics["24h"] == {"mae_c": 0.5, "bias_c": 0.5, "rmse_c": 0.5, "count": 1}
    assert tracker.pending_count == 0


def test_issues_only_one_cohort_per_hour() -> None:
    tracker = ForecastAccuracyTracker()
    assert tracker.issue(NOW, points(NOW))
    assert not tracker.issue(NOW + timedelta(minutes=1), points(NOW + timedelta(minutes=1)))
    assert tracker.issue(NOW + timedelta(hours=1), points(NOW + timedelta(hours=1)))
    assert tracker.pending_count == 6


def test_observation_outside_five_minute_match_window_discards_forecast() -> None:
    tracker = ForecastAccuracyTracker()
    tracker.issue(NOW, points(NOW))
    tracker.observe(NOW + timedelta(hours=1, minutes=6), 20.0)
    assert tracker.metrics()["1h"]["count"] == 0
    assert tracker.pending_count == 2


@pytest.mark.parametrize("bad", [None, float("nan"), float("inf"), True])
def test_unavailable_or_nonfinite_actual_is_never_scored(bad: object) -> None:
    tracker = ForecastAccuracyTracker()
    tracker.issue(NOW, points(NOW))
    tracker.observe(NOW + timedelta(hours=1), bad)
    assert tracker.metrics()["1h"]["count"] == 0
    assert tracker.pending_count == 3


def test_nonfinite_predictions_are_skipped_and_pending_state_is_bounded() -> None:
    tracker = ForecastAccuracyTracker()
    bad_points = (
        ForecastPoint(NOW + timedelta(hours=1), float("nan"), 5.0, 20.0, False),
    )
    assert not tracker.issue(NOW, bad_points)

    for hour in range(40):
        at = NOW + timedelta(hours=hour)
        tracker.issue(at, points(at))
    assert tracker.pending_count <= 100


def test_keeps_only_latest_168_errors_per_horizon() -> None:
    tracker = ForecastAccuracyTracker()
    for hour in range(170):
        issued = NOW + timedelta(hours=hour)
        tracker.issue(issued, points(issued, offset=float(hour)))
        tracker.observe(issued + timedelta(hours=1), 20.0)
    assert tracker.metrics()["1h"]["count"] == 168
    assert tracker.metrics()["1h"]["mae_c"] == pytest.approx((2 + 169) / 2)


def test_out_of_order_observation_does_not_score_or_rewind_tracker() -> None:
    tracker = ForecastAccuracyTracker()
    tracker.issue(NOW, points(NOW))
    tracker.observe(NOW + timedelta(hours=2), 21.0)
    tracker.observe(NOW + timedelta(hours=1), 25.0)
    assert tracker.metrics()["1h"]["count"] == 0
    assert tracker.pending_count == 2


def test_regular_observations_wait_for_due_time_instead_of_scoring_five_minutes_early():
    tracker = ForecastAccuracyTracker()
    tracker.issue(NOW, points(NOW))
    tracker.observe(NOW + timedelta(minutes=55), 25.0)
    assert tracker.metrics()["1h"]["count"] == 0
    tracker.observe(NOW + timedelta(hours=1), 20.0)
    assert tracker.metrics()["1h"] == {"mae_c": 0.0, "bias_c": 0.0, "rmse_c": 0.0, "count": 1}


def test_persistence_round_trip_keeps_pending_forecasts_and_signed_errors() -> None:
    tracker = ForecastAccuracyTracker()
    tracker.issue(NOW, points(NOW))
    tracker.observe(NOW + timedelta(hours=1), 21.0)

    restored = ForecastAccuracyTracker.from_dict(tracker.to_dict(), now=NOW + timedelta(hours=1))
    assert restored.pending_count == 2
    assert restored.metrics()["1h"] == {"mae_c": 1.0, "bias_c": 1.0, "rmse_c": 1.0, "count": 1}
    assert restored.observe(NOW + timedelta(hours=1), 19.0) is False
    assert restored.metrics()["1h"]["bias_c"] == 1.0


def test_restore_discards_expired_and_future_records_and_bounds_bad_input() -> None:
    snapshot = {
        "version": 1,
        "pending": [
            {"horizon": "1h", "target_at": (NOW - timedelta(minutes=6)).isoformat(), "predicted_c": 20},
            {"horizon": "6h", "target_at": (NOW + timedelta(days=2)).isoformat(), "predicted_c": 20},
            {"horizon": "bogus", "target_at": NOW.isoformat(), "predicted_c": 20},
            {"horizon": "24h", "target_at": (NOW + timedelta(hours=24)).isoformat(), "predicted_c": float("nan")},
        ],
        "scores": {"1h": [{"observed_at": (NOW + timedelta(seconds=1)).isoformat(), "error_c": 2},
                           {"observed_at": NOW.isoformat(), "error_c": float("inf")}]},
        "last_issue_hour": int((NOW + timedelta(days=1)).timestamp() // 3600),
    }
    restored = ForecastAccuracyTracker.from_dict(snapshot, now=NOW)
    assert restored.pending_count == 0
    assert restored.metrics()["1h"]["count"] == 0
    assert restored.issue(NOW, points(NOW))


@pytest.mark.parametrize("bad_horizon", [["1h"], {"name": "1h"}])
def test_restore_ignores_unhashable_horizon_values(bad_horizon: object) -> None:
    restored = ForecastAccuracyTracker.from_dict({
        "version": 1,
        "pending": [{"horizon": bad_horizon, "target_at": NOW.isoformat(), "predicted_c": 20}],
    })
    assert restored.pending_count == 0


def test_restore_rejects_implausible_prediction_and_overflowing_error() -> None:
    restored = ForecastAccuracyTracker.from_dict({
        "version": 1,
        "pending": [{"horizon": "1h", "target_at": NOW.isoformat(), "predicted_c": 1e308}],
        "scores": {"1h": [{"observed_at": NOW.isoformat(), "error_c": 1e308}]},
    })
    assert restored.pending_count == 0
    assert restored.metrics()["1h"] == {"mae_c": None, "bias_c": None, "rmse_c": None, "count": 0}


def test_issue_uses_selected_forecast_point_timestamp_as_scoring_target() -> None:
    tracker = ForecastAccuracyTracker()
    target = NOW + timedelta(hours=1, minutes=4)
    assert tracker.issue(NOW, (ForecastPoint(target, 20.0, 5.0, 20.0, False),))
    assert tracker.observe(NOW + timedelta(hours=1), 21.0) is False
    assert tracker.metrics()["1h"]["count"] == 0
    assert tracker.observe(target, 21.0) is True
    assert tracker.metrics()["1h"]["mae_c"] == 1.0


def test_bias_and_rmse_preserve_direction_across_multiple_errors() -> None:
    tracker = ForecastAccuracyTracker()
    for index, actual in enumerate((21.0, 19.0)):
        issued = NOW + timedelta(hours=index)
        tracker.issue(issued, points(issued))
        tracker.observe(issued + timedelta(hours=1), actual)
    assert tracker.metrics()["1h"] == {
        "mae_c": 1.0, "bias_c": 0.0, "rmse_c": 1.0, "count": 2,
    }
