from datetime import datetime, timedelta, timezone
from math import sin

import pytest

from custom_components.central_heating_controller.forecasting import ThermalForecaster

NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)


def train(
    model: ThermalForecaster, *, gain: float = 2.0, loss: float = 0.08, noise: float = 0.0
) -> None:
    """Provide varied outdoor and heating conditions with a known model."""
    indoor = 19.0
    for index in range(1200):
        at = NOW + timedelta(minutes=index)
        outdoor = 5.0 + (index % 120) * 0.05
        heating = (index // 15) % 2 == 0
        if index:
            previous_outdoor = 5.0 + ((index - 1) % 120) * 0.05
            delta = indoor - (previous_outdoor + outdoor) / 2
            previous_heating = ((index - 1) // 15) % 2 == 0
            duty = (float(previous_heating) + float(heating)) / 2
            indoor += (gain * duty - loss * delta) / 60
        model.observe(at, indoor + noise * sin(index * 1.7), outdoor, heating)


def test_learns_bounded_heat_gain_and_loss_from_observations() -> None:
    model = ThermalForecaster()
    train(model)

    assert model.sample_count >= 70
    assert model.heat_gain_c_per_hour == pytest.approx(2.0, abs=0.1)
    assert model.heat_loss_per_hour == pytest.approx(0.08, abs=0.01)
    assert model.confidence > 0.5


@pytest.mark.parametrize("bad_value", [None, float("nan"), float("inf"), True, 10**1000])
def test_invalid_observation_breaks_sequence_without_raising(bad_value: object) -> None:
    model = ThermalForecaster()
    model.observe(NOW, 19.0, 5.0, False)
    assert not model.observe(NOW + timedelta(minutes=5), bad_value, 5.0, False)
    assert not model.observe(NOW + timedelta(minutes=10), 19.2, 5.0, False)
    assert model.sample_count == 0


@pytest.mark.parametrize(
    ("indoor", "outdoor"), [(61.0, 5.0), (-21.0, 5.0), (19.0, 61.0), (60.0, -60.0)]
)
def test_observation_rejects_out_of_range_temperatures(indoor: float, outdoor: float) -> None:
    model = ThermalForecaster()
    assert not model.observe(NOW, indoor, outdoor, False)
    assert model.sample_count == 0


def test_single_sensor_jump_restarts_window_without_learning_it() -> None:
    model = ThermalForecaster()
    model.observe(NOW, 19.0, 5.0, False)
    assert not model.observe(NOW + timedelta(minutes=1), 22.0, 5.0, False)
    for minute in range(2, 18):
        model.observe(NOW + timedelta(minutes=minute), 22.0, 5.0, False)
    assert model.sample_count == 1


def test_regressions_and_large_gaps_are_skipped() -> None:
    model = ThermalForecaster()
    model.observe(NOW, 19.0, 5.0, False)
    assert not model.observe(NOW - timedelta(minutes=5), 19.1, 5.0, False)
    assert not model.observe(NOW + timedelta(hours=1), 19.2, 5.0, False)
    assert model.sample_count == 0


def test_forecast_uses_schedule_outdoor_curve_and_thermostat_cycling() -> None:
    model = ThermalForecaster()
    result = model.forecast(
        NOW,
        18.0,
        lambda _at: 5.0,
        lambda at: 20.0 if at < NOW + timedelta(hours=2) else 16.0,
        hours=3,
        step_minutes=5,
    )

    assert len(result.points) == 37
    assert result.points[0].heating is True
    assert result.points[24].target_temperature == 16.0
    assert result.points[-1].indoor_temperature < result.points[24].indoor_temperature
    assert result.confidence == 0
    assert result.residual_rate_c_per_hour >= 0


def test_forecast_rejects_invalid_inputs_and_nonfinite_outdoor_forecast() -> None:
    model = ThermalForecaster()
    with pytest.raises(ValueError):
        model.forecast(NOW, float("nan"), lambda _: 5.0, lambda _: 20.0)
    with pytest.raises(ValueError):
        model.forecast(NOW, 19.0, lambda _: float("nan"), lambda _: 20.0, hours=1)


def test_serialization_round_trip_and_malformed_data_falls_back_to_prior() -> None:
    model = ThermalForecaster()
    train(model)
    restored = ThermalForecaster.from_dict(model.to_dict())
    assert restored.sample_count == model.sample_count
    assert restored.heat_gain_c_per_hour == pytest.approx(model.heat_gain_c_per_hour)
    assert restored.heat_loss_per_hour == pytest.approx(model.heat_loss_per_hour)

    for malformed in (None, {}, {"version": 1, "rows": [[float("nan"), 0, 1]]}):
        reset = ThermalForecaster.from_dict(malformed)
        assert reset.sample_count == 0
        assert reset.heat_gain_c_per_hour == 1.5
        assert reset.heat_loss_per_hour == 0.08


def test_one_minute_observations_form_fifteen_minute_learning_windows() -> None:
    model = ThermalForecaster()
    indoor = 19.0
    for minute in range(46):
        heating = minute < 30
        assert model.observe(NOW + timedelta(minutes=minute), indoor, 5.0, heating) is (
            minute in (15, 30, 45)
        )
        indoor += (0.2 if heating else 0.0) / 60
    assert model.sample_count == 3


def test_sub_tenth_degree_change_is_not_zeroed() -> None:
    model = ThermalForecaster()
    indoor = 10.0
    for minute in range(16):
        model.observe(NOW + timedelta(minutes=minute), indoor, 9.8, False)
        indoor -= 0.001
    assert model.sample_count == 1
    assert model._rows[0][2] < 0


def test_confidence_stays_zero_without_separate_heating_and_cooling_coverage() -> None:
    model = ThermalForecaster()
    indoor = 19.0
    for minute in range(120):
        model.observe(NOW + timedelta(minutes=minute), indoor, 5.0, True)
        indoor += 0.01
    assert model.sample_count >= 7
    assert model.confidence == 0


def test_forecast_integrates_each_minute_and_stops_outdoor_calls_at_horizon() -> None:
    model = ThermalForecaster()
    calls: list[datetime] = []
    end = NOW + timedelta(hours=1)

    def outdoor(at: datetime) -> float:
        calls.append(at)
        return 5.0

    result = model.forecast(NOW, 18.0, outdoor, lambda _: 20.0, hours=1, step_minutes=5)
    assert len(result.points) == 13
    assert len(calls) == 61
    assert max(calls) <= end


def test_held_out_forecast_tracks_synthetic_plant_with_noisy_training_data() -> None:
    model = ThermalForecaster()
    train(model, noise=0.025)
    true_gain, true_loss = 2.0, 0.08
    start_temperature = 18.0
    horizon_minutes = 6 * 60

    def outside(at: datetime) -> float:
        hours_from_start = (at - NOW).total_seconds() / 3600
        return 4.0 + 3.0 * sin(hours_from_start / 2)

    def target(at: datetime) -> float:
        elapsed_hours = (at - NOW).total_seconds() / 3600
        return 20.0 if elapsed_hours < 3 else 17.0

    forecast = model.forecast(NOW, start_temperature, outside, target, hours=6)
    truth = start_temperature
    truth_by_minute = [truth]
    heating = False
    for minute in range(horizon_minutes):
        at = NOW + timedelta(minutes=minute)
        goal = target(at)
        if heating:
            heating = truth < goal
        else:
            heating = truth < goal - 0.15
        truth += (true_gain * float(heating) - true_loss * (truth - outside(at))) / 60
        truth_by_minute.append(truth)

    errors = [
        abs(point.indoor_temperature - truth_by_minute[index * 5])
        for index, point in enumerate(forecast.points)
    ]
    assert max(errors) < 0.6


def train_partial_inertial(model: ThermalForecaster) -> tuple[datetime, float, float]:
    """Train on a separate 72-hour run of three-minute observations."""
    from math import exp

    indoor = 19.0
    response = 0.0
    for index in range(1440):
        at = NOW + timedelta(minutes=3 * index)
        outside = 5.0 + 2.0 * sin(index / 75)
        target = 21.0 if (index // 40) % 2 == 0 else 16.0
        demand = min(0.65, max(0.0, 0.55 * (target - indoor)))
        model.observe(
            at,
            indoor + 0.008 * sin(index * 1.7),
            outside,
            demand,
            target_temperature=target,
        )
        decay = exp(-3 / 20)
        mean_response = demand + (response - demand) * 20 / 3 * (1 - decay)
        response = demand + (response - demand) * decay
        indoor += (2.4 * mean_response - 0.075 * (indoor - outside)) * 3 / 60
    return NOW + timedelta(minutes=1440 * 3), indoor, response


def test_partial_demand_and_lag_are_learned_from_three_minute_samples() -> None:
    model = ThermalForecaster()
    train_partial_inertial(model)

    assert model.sample_count > 250
    assert max(row[0] for row in model._rows) < 0.8
    assert model.heat_gain_c_per_hour == pytest.approx(2.4, abs=0.2)
    assert model.heat_loss_per_hour == pytest.approx(0.075, abs=0.01)
    assert model.lag_learned
    assert model.lag_minutes == 20
    assert model.demand_model_learned
    assert model.confidence > 0.5


def test_held_out_forecast_tracks_partial_demand_and_residual_heat() -> None:
    from math import exp

    model = ThermalForecaster()
    start, indoor, response = train_partial_inertial(model)

    def outside(at: datetime) -> float:
        return 5.0 + 2.0 * sin(((at - NOW).total_seconds() / 180) / 75)

    def target(at: datetime) -> float | None:
        return 21.0 if at < start + timedelta(hours=3) else None

    predicted = model.forecast(start, indoor, outside, target, hours=6)
    instant_binary = ThermalForecaster().forecast(start, indoor, outside, target, hours=6)
    truth = [indoor]
    for minute in range(360):
        at = start + timedelta(minutes=minute)
        goal = target(at)
        demand = min(0.65, max(0.0, 0.55 * (goal - indoor))) if goal is not None else 0.0
        decay = exp(-1 / 20)
        mean_response = demand + (response - demand) * 20 * (1 - decay)
        response = demand + (response - demand) * decay
        indoor += (2.4 * mean_response - 0.075 * (indoor - outside(at))) / 60
        truth.append(indoor)

    learned_errors = [
        abs(point.indoor_temperature - truth[index * 5])
        for index, point in enumerate(predicted.points)
    ]
    binary_errors = [
        abs(point.indoor_temperature - truth[index * 5])
        for index, point in enumerate(instant_binary.points)
    ]
    assert sum(learned_errors) / len(learned_errors) < 0.18
    assert sum(learned_errors) < 0.65 * sum(binary_errors)
    assert predicted.lag_minutes == 20
    assert predicted.lag_learned
    assert predicted.demand_model_learned
    assert all(0 <= point.demand <= 1 for point in predicted.points)
    assert any(0 < point.demand < 0.8 for point in predicted.points)
    assert all(not point.heating and point.demand == 0 for point in predicted.points[37:])


def test_lag_and_demand_policy_survive_round_trip_but_transient_heat_does_not() -> None:
    model = ThermalForecaster()
    start, indoor, _ = train_partial_inertial(model)
    restored = ThermalForecaster.from_dict(model.to_dict())

    assert restored.lag_minutes == model.lag_minutes
    assert restored.lag_learned == model.lag_learned
    assert restored.demand_model_learned == model.demand_model_learned
    assert restored.heat_gain_c_per_hour == pytest.approx(model.heat_gain_c_per_hour)
    assert restored._previous is None
    assert (
        restored.forecast(start, indoor, lambda _: 5.0, lambda _: None, hours=1)
        .points[-1]
        .indoor_temperature
        < indoor
    )
    assert ThermalForecaster.from_dict({"version": 1, "rows": []}).sample_count == 0


@pytest.mark.parametrize("demand", [-0.01, 1.01, float("nan"), float("inf"), None])
def test_invalid_demand_breaks_transient_sequence(demand: float | None) -> None:
    model = ThermalForecaster()
    model.observe(NOW, 19.0, 5.0, 0.6)
    assert not model.observe(NOW + timedelta(minutes=3), 19.1, 5.0, demand)
    assert not model.observe(NOW + timedelta(minutes=6), 19.1, 5.0, 0.0)
    assert model.sample_count == 0
    assert model._response == [0.0] * 6


def test_measured_fraction_is_used_immediately_at_unchanged_setpoint() -> None:
    model = ThermalForecaster()
    model.observe(NOW, 21.0, 5.0, 0.13, target_temperature=21.0)

    same = model.forecast(NOW, 21.0, lambda _: 5.0, lambda _: 21.0, hours=1)
    changed = model.forecast(NOW, 21.0, lambda _: 5.0, lambda _: 16.0, hours=1)
    disabled = model.forecast(NOW, 21.0, lambda _: 5.0, lambda _: None, hours=1)

    assert same.points[0].demand == pytest.approx(0.13)
    assert 0 < same.points[1].demand < 0.5
    assert changed.points[0].demand == 0
    assert disabled.points[0].demand == 0


def test_learns_maintenance_demand_at_setpoint() -> None:
    model = ThermalForecaster()
    for minute in range(24 * 15 + 1):
        at = NOW + timedelta(minutes=minute)
        near_setpoint = (minute // 30) % 2 == 0
        model.observe(
            at,
            21.0,
            5.0,
            0.13 if near_setpoint else 0.0,
            target_temperature=21.0 if near_setpoint else 16.0,
        )
    assert model.demand_model_learned

    start = NOW + timedelta(minutes=24 * 15)
    restored = ThermalForecaster.from_dict(model.to_dict())
    result = restored.forecast(start, 21.0, lambda _: 5.0, lambda _: 21.0, hours=1)
    assert result.points[0].demand == pytest.approx(0.13, abs=0.02)
    assert 0.1 < result.points[1].demand < 0.25


def test_zero_lag_can_be_validated_as_learned() -> None:
    model = ThermalForecaster()
    train(model)
    assert model.lag_minutes == 0
    assert model.lag_learned


def test_thirteen_percent_cycles_can_calibrate_without_full_heat() -> None:
    from math import exp

    model = ThermalForecaster()
    indoor = 19.0
    response = 0.0
    for index in range(1440):
        at = NOW + timedelta(minutes=3 * index)
        outside = 5.0 + 2.0 * sin(index / 75)
        demand = 0.13 if (index // 40) % 2 == 0 else 0.0
        model.observe(at, indoor + 0.005 * sin(index * 1.7), outside, demand)
        decay = exp(-3 / 20)
        mean_response = demand + (response - demand) * 20 / 3 * (1 - decay)
        response = demand + (response - demand) * decay
        indoor += (7.0 * mean_response - 0.05 * (indoor - outside)) * 3 / 60

    assert model.heat_gain_c_per_hour == pytest.approx(7.0, abs=0.8)
    assert model.heat_loss_per_hour == pytest.approx(0.05, abs=0.01)
    assert model.lag_minutes == 20
    assert model.lag_learned
    assert model.confidence > 0.5


def test_measured_demand_anchor_expires_after_elapsed_time() -> None:
    model = ThermalForecaster()
    model.observe(NOW, 21.0, 5.0, 0.13, target_temperature=21.0)

    immediate = model.forecast(NOW, 21.0, lambda _: 5.0, lambda _: 21.0, hours=1)
    aged = model.forecast(NOW + timedelta(minutes=15), 21.0, lambda _: 5.0, lambda _: 21.0, hours=1)
    assert immediate.points[0].demand == pytest.approx(0.13)
    assert aged.points[0].demand > immediate.points[0].demand


def test_extended_cost_horizon_keeps_default_chart_at_24_hours():
    model = ThermalForecaster()
    normal = model.forecast(NOW, 20, lambda _: 8, lambda _: 16)
    extended = model.forecast(NOW, 20, lambda _: 8, lambda _: 16, hours=49)
    assert len(normal.points) == 289
    assert extended.points[-1].at == NOW + timedelta(hours=49)
    with pytest.raises(ValueError):
        model.forecast(NOW, 20, lambda _: 8, lambda _: 16, hours=50)
