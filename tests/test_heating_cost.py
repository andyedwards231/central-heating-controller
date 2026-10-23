from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from custom_components.central_heating_controller.heating_cost import HeatingCostModel

BASE = datetime(2026, 1, 1, tzinfo=timezone.utc)


def make_model(base: datetime = BASE) -> HeatingCostModel:
    model = HeatingCostModel()
    for day in range(3):
        day_start = base + timedelta(days=day)
        for index in range(10):
            start = day_start + timedelta(minutes=30 * index)
            demand = 0.0 if index < 3 else (0.5 if index < 7 else 1.0)
            end = start + timedelta(minutes=30)
            at = start
            while at <= end:
                model.observe(at, demand)
                at += timedelta(minutes=5)
            gas = (2 + 10 * demand) * 0.5
            model.ingest([{"start": start, "end": end, "consumption_kwh": gas}], end)
    return model


def test_mixed_boiler_fit_separates_baseline_from_demand_linked_heat() -> None:
    model = make_model()
    fit = model.calibration()
    assert fit["sample_count"] == 30
    assert fit["distinct_dates"] == 3
    assert fit["heat_kw"] == pytest.approx(10)
    assert fit["baseline_kw"] == pytest.approx(2)
    assert fit["fit_median_absolute_error_kw"] == pytest.approx(0)
    assert "may include hot water" in fit["assumption"]


def test_requires_full_observation_coverage_and_historical_half_hour_interval() -> None:
    model = HeatingCostModel()
    start, end = BASE, BASE + timedelta(minutes=30)
    model.observe(start, 0.5)
    model.observe(start + timedelta(minutes=20), 0.5)
    model.observe(end, 0.5)
    assert model.ingest([{"start": start, "end": end, "consumption_kwh": 2}], end) == 0
    assert model.ingest([{"start": start, "end": end + timedelta(minutes=10), "consumption_kwh": 2}], end) == 0
    assert model.ingest([{"start": start, "end": end, "consumption_kwh": 2}], end - timedelta(seconds=1)) == 0


def test_does_not_publish_when_sample_count_or_demand_spread_is_insufficient() -> None:
    model = HeatingCostModel()
    for day in range(3):
        for index in range(10):
            start = BASE + timedelta(days=day, minutes=30 * index)
            end = start + timedelta(minutes=30)
            at = start
            while at <= end:
                model.observe(at, 0.5)
                at += timedelta(minutes=5)
            model.ingest([{"start": start, "end": end, "consumption_kwh": 1}], end)
    assert model.gas_kwh_per_demand_hour is None
    assert model.calibration()["reason"] == "insufficient_samples"


def test_incomplete_current_or_forecast_coverage_is_unavailable() -> None:
    model = make_model()
    now = BASE + timedelta(days=4, hours=12)
    start = now.replace(hour=0)
    result = model.predict_day(start, start + timedelta(days=1), now, [], 0.1)
    assert result["cost_gbp"] is None
    assert result["reason"] == "incomplete_observations"

    result = model.predict_day(start + timedelta(days=1), start + timedelta(days=2), now, [], 0.1)
    assert result["cost_gbp"] is None
    assert result["reason"] == "incomplete_forecast"


def test_prediction_cost_is_heating_attribution_with_diagnostics() -> None:
    model = make_model()
    now = BASE + timedelta(days=4, hours=12)
    start, end = now.replace(hour=0), now.replace(hour=0) + timedelta(days=1)
    at = start
    while at <= now:
        model.observe(at, 0.5)
        at += timedelta(minutes=5)
    forecast = [{"at": now + timedelta(minutes=5 * i), "heating_demand_percent": 50}
                for i in range(145)]
    result = model.predict_day(start, end, now, forecast, 0.1)
    assert result["cost_gbp"] == pytest.approx(12)
    assert result["estimated_kwh"] == pytest.approx(120)
    assert result["attribution"] == "estimated_heating"
    assert result["sample_count"] == 30


def test_state_round_trip_and_invalid_state_handling() -> None:
    model = make_model()
    restored = HeatingCostModel.restore_state(model.export_state(), now=BASE + timedelta(days=5))
    assert restored.gas_kwh_per_demand_hour == pytest.approx(10)
    assert HeatingCostModel.restore_state({"version": 10}).gas_kwh_per_demand_hour is None


def test_revised_interval_replaces_the_previous_sample_without_duplication() -> None:
    model = make_model()
    (start, end), _ = next(iter(model._samples.items()))
    assert model.ingest([{"start": start, "end": end, "consumption_kwh": 1.5}], end) == 1
    assert model.calibration()["sample_count"] == 30
    assert model.export_state()["samples"][0]["consumption_kwh"] == 1.5


def test_coordinator_datetime_forecast_points_and_dst_day_lengths() -> None:
    london = ZoneInfo("Europe/London")
    for local_start, hours in ((datetime(2026, 3, 29, tzinfo=london), 23),
                               (datetime(2026, 10, 25, tzinfo=london), 25)):
        start = local_start.astimezone(timezone.utc)
        end = (local_start + timedelta(days=1)).astimezone(timezone.utc)
        model = make_model(start - timedelta(days=3))
        assert (end - start).total_seconds() == hours * 3600
        forecast = []
        at = start
        while at <= end:
            forecast.append({"datetime": at.isoformat(), "heating_demand_percent": 13})
            at += timedelta(minutes=5)
        result = model.predict_day(local_start, local_start + timedelta(days=1), local_start,
                                   forecast, 0.1)
        assert result["cost_gbp"] == pytest.approx(hours * 0.13 * 10 * 0.1)


def test_calibration_and_prediction_work_with_a_13_percent_demand_ceiling() -> None:
    model = HeatingCostModel()
    for day in range(3):
        for index in range(10):
            start = BASE + timedelta(days=day, minutes=30 * index)
            end = start + timedelta(minutes=30)
            demand = 0.0 if index < 3 else (0.06 if index < 7 else 0.13)
            at = start
            while at <= end:
                model.observe(at, demand)
                at += timedelta(minutes=5)
            model.ingest([{"start": start, "end": end,
                           "consumption_kwh": (2 + 10 * demand) * 0.5}], end)
    assert model.gas_kwh_per_demand_hour == pytest.approx(10)
