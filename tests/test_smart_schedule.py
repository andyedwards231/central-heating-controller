from datetime import datetime, timedelta
from math import exp, sin
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from custom_components.central_heating_controller.forecasting import ThermalForecaster
from custom_components.central_heating_controller.smart_schedule import (
    is_comfort_window,
    parse_weekly_schedule,
    plan_smart_schedule,
)


def test_schedule_parses_cross_midnight_window_and_week_wrap() -> None:
    schedule = parse_weekly_schedule({"sunday": [{"start": "22:30", "end": "06:15"}]})
    zone = ZoneInfo("Europe/London")
    assert is_comfort_window(datetime(2026, 10, 25, 23, 0, tzinfo=zone), schedule)
    assert is_comfort_window(datetime(2026, 10, 26, 6, 14, tzinfo=zone), schedule)
    assert not is_comfort_window(datetime(2026, 10, 26, 6, 15, tzinfo=zone), schedule)


def test_schedule_uses_wall_clock_through_dst_transition() -> None:
    schedule = parse_weekly_schedule({"sunday": [{"start": "01:00", "end": "02:30"}]})
    zone = ZoneInfo("Europe/London")
    # Both occurrences of 01:30 during the autumn fold remain inside the window.
    first = datetime(2026, 10, 25, 1, 30, tzinfo=zone, fold=0)
    second = datetime(2026, 10, 25, 1, 30, tzinfo=zone, fold=1)
    assert is_comfort_window(first, schedule)
    assert is_comfort_window(second, schedule)


@pytest.mark.parametrize("value", [None, [], {"monday": "08:00"}, {"monday": [{"start": "08:00", "end": "08:00"}]}])
def test_rejects_malformed_schedule(value) -> None:
    with pytest.raises(ValueError):
        parse_weekly_schedule(value)


def test_plan_is_bounded_and_marks_untrained_model_provisional() -> None:
    now = datetime(2026, 10, 20, 5, 30, tzinfo=ZoneInfo("Europe/London"))
    model = ThermalForecaster()
    plan = plan_smart_schedule(
        now, 18.0, lambda _: 7.0, model,
        {"monday": [{"start": "07:00", "end": "09:00"}],
         "tuesday": [{"start": "07:00", "end": "09:00"}]},
        occupied_at=lambda at: at.hour >= 7 and at.hour < 9,
        arrival_at=datetime(2026, 10, 20, 7, 0, tzinfo=now.tzinfo),
    )
    assert 0 <= plan.daytime_drop <= 2
    assert plan.daytime_drop * 2 == int(plan.daytime_drop * 2)
    assert plan.recovery_lead_minutes in {0, 30, 60, 120, 240}
    assert plan.hours >= 24
    assert plan.provisional
    assert plan.daily_drops
    # The prior thermal model cannot necessarily recover this quickly; the
    # preview must report the predicted comfort shortfall honestly.
    assert not plan.feasible
    assert plan.points
    assert plan.total_demand_hours >= 0
    assert plan.target_at(now) >= 16
    assert plan.target_at(datetime(2026, 10, 20, 7, 30, tzinfo=now.tzinfo)) == 20
    assert plan.target_at(
        datetime(2026, 10, 20, 7, 0, tzinfo=now.tzinfo)
        - timedelta(minutes=plan.recovery_lead_minutes)
    ) == 20


def test_away_plan_uses_eco_target_and_arrival_is_timezone_validated() -> None:
    now = datetime(2026, 10, 20, 12, 0, tzinfo=ZoneInfo("Europe/London"))
    model = ThermalForecaster()
    plan = plan_smart_schedule(now, 19, lambda _: 10, model, {}, occupied_at=lambda _: False)
    assert plan.target_at(now) == 16
    with pytest.raises(ValueError, match="arrival_at"):
        plan_smart_schedule(now, 19, lambda _: 10, model, {}, arrival_at=datetime(2026, 10, 20, 13))


def test_unreachable_comfort_deadline_is_reported_as_infeasible() -> None:
    now = datetime(2026, 10, 20, 6, 30, tzinfo=ZoneInfo("Europe/London"))
    plan = plan_smart_schedule(
        now, 10, lambda _: 5, ThermalForecaster(), {},
        comfort_at=lambda at: now <= at < now.replace(hour=7, minute=0),
        occupied_at=lambda _: True,
        hours=24,
    )
    assert not plan.feasible
    assert plan.provisional
    assert plan.comfort_shortfall_c > 0


def test_coordinate_search_selects_different_drops_for_separate_gaps() -> None:
    class GapPreferenceForecaster:
        sample_count = 100
        lag_learned = True
        demand_model_learned = True
        calls = 0

        def forecast(self, start, indoor, outside, target, *, hours, step_minutes):
            self.calls += 1
            points = []
            utc_start = start.astimezone(ZoneInfo("UTC"))
            for index in range(int(hours * 12) + 1):
                at = utc_start + timedelta(minutes=index * 5)
                local = at.astimezone(start.tzinfo)
                setpoint = target(at)
                if 7 <= local.hour < 8:
                    demand = abs(setpoint - 20) / 10
                elif 9 <= local.hour < 12:
                    demand = abs(setpoint - 18) / 10
                else:
                    demand = 0
                points.append(SimpleNamespace(at=at, indoor_temperature=20, demand=demand))
            return SimpleNamespace(points=tuple(points), confidence=0.9)

    now = datetime(2026, 10, 19, 6, 0, tzinfo=ZoneInfo("Europe/London"))
    model = GapPreferenceForecaster()
    plan = plan_smart_schedule(
        now, 20, lambda _: 8, model,
        {"monday": [{"start": "08:00", "end": "09:00"},
                    {"start": "12:00", "end": "13:00"}]},
        occupied_at=lambda _: True,
        hours=24,
    )
    drops = {(gap.start.hour, gap.end.hour): gap.drop for gap in plan.gap_drops}
    assert drops[(7, 8)] == 0
    assert drops[(9, 12)] == 2
    assert model.calls <= 100


def test_real_thermal_model_selects_preheat_and_reports_confidence_and_shortfall() -> None:
    model = ThermalForecaster()
    training_start = datetime(2026, 1, 1, tzinfo=ZoneInfo("UTC"))
    indoor, response = 19.0, 0.0
    for index in range(1440):
        at = training_start + timedelta(minutes=3 * index)
        outside = 5.0 + 2.0 * sin(index / 75)
        target = 21.0 if (index // 40) % 2 == 0 else 16.0
        demand = min(0.65, max(0.0, 0.55 * (target - indoor)))
        model.observe(at, indoor + 0.008 * sin(index * 1.7), outside, demand,
                      target_temperature=target)
        decay = exp(-3 / 20)
        mean_response = demand + (response - demand) * 20 / 3 * (1 - decay)
        response = demand + (response - demand) * decay
        indoor += (2.4 * mean_response - 0.075 * (indoor - outside)) * 3 / 60

    now = training_start + timedelta(hours=72)
    comfort_windows = (
        (now + timedelta(hours=8), now + timedelta(hours=10)),
        (now + timedelta(hours=18), now + timedelta(hours=21)),
        (now + timedelta(hours=32), now + timedelta(hours=34)),
    )

    def comfort_at(at):
        return any(start <= at < end for start, end in comfort_windows)

    def outside_at(at):
        elapsed_minutes = (at - now).total_seconds() / 60
        return 5.0 + 2.0 * sin(elapsed_minutes / 75)

    plan = plan_smart_schedule(now, indoor, outside_at, model, {},
                               comfort_at=comfort_at, occupied_at=lambda _: True,
                               hours=36)
    assert plan.recovery_lead_minutes >= 240
    assert plan.target_at(now) == 20
    assert isinstance(plan.confidence, float)
    assert plan.confidence == model.confidence
    assert model.lag_learned and model.demand_model_learned
    assert not plan.feasible
    assert plan.provisional
    assert plan.comfort_shortfall_c > 0

    no_recovery = model.forecast(
        now, indoor, outside_at,
        lambda at: 20 if comfort_at(at) else 16 if 23 <= at.hour or at.hour < 7 else 18,
        hours=36,
    )
    first_start, first_end = comfort_windows[0]
    planned_min = min(p.indoor_temperature for p in plan.points if first_start <= p.at < first_end)
    passive_min = min(p.indoor_temperature for p in no_recovery.points if first_start <= p.at < first_end)
    assert planned_min > passive_min

    terminal = plan.points[-1]
    if terminal.indoor_temperature < plan.target_at(terminal.at) - 0.25:
        assert not plan.feasible


def test_away_house_is_not_marked_infeasible_for_missing_occupied_floors() -> None:
    class ColdForecaster:
        def forecast(self, start, indoor, outside, target, *, hours, step_minutes):
            first = start.astimezone(ZoneInfo("UTC"))
            points = tuple(
                SimpleNamespace(
                    at=first + timedelta(minutes=index * 5),
                    indoor_temperature=14.0,
                    demand=0.0,
                )
                for index in range(int(hours * 12) + 1)
            )
            return SimpleNamespace(points=points, confidence=0.8)

    now = datetime(2026, 10, 20, 1, 0, tzinfo=ZoneInfo("Europe/London"))
    plan = plan_smart_schedule(now, 14, lambda _: 5, ColdForecaster(), {},
                               occupied_at=lambda _: False, hours=24)
    assert plan.target_at(now) == 16
    assert plan.feasible
    assert plan.comfort_shortfall_c == 0


def test_common_terminal_cost_prevents_deferred_cold_recovery_and_keeps_demands_separate() -> None:
    class SameDemandForecaster:
        heat_gain_c_per_hour = 2.0
        seen_targets = set()
        terminal_temperatures = set()

        def forecast(self, start, indoor, outside, target, *, hours, step_minutes):
            first = start.astimezone(ZoneInfo("UTC"))
            points = []
            for index in range(int(hours * 12) + 1):
                at = first + timedelta(minutes=index * 5)
                setpoint = target(at)
                self.seen_targets.add(round(setpoint, 2))
                points.append(SimpleNamespace(at=at, indoor_temperature=setpoint, demand=0.0))
            self.terminal_temperatures.add(round(points[-1].indoor_temperature, 2))
            return SimpleNamespace(points=tuple(points), confidence=0.9)

    now = datetime(2026, 10, 20, 10, 0, tzinfo=ZoneInfo("Europe/London"))
    model = SameDemandForecaster()
    plan = plan_smart_schedule(
        now, 20, lambda _: 10, model, {},
        comfort_at=lambda at: now + timedelta(hours=1) <= at < now + timedelta(hours=2),
        occupied_at=lambda _: True,
        daytime_drop=1.8,
        hours=24,
    )
    assert plan.total_demand_hours == 0
    assert plan.objective_demand_hours == plan.total_demand_hours + plan.terminal_recovery_cost_hours
    assert plan.terminal_recovery_cost_hours == 0
    assert plan.points[-1].indoor_temperature == 20
    assert 18.2 in model.terminal_temperatures  # The configured 1.8°C endpoint is searched.

    # A colder candidate has exactly the same simulated demand but incurs the
    # common-reference recovery cost, so it cannot win by pushing heat beyond
    # the forecast horizon.
    assert 0.9 > plan.objective_demand_hours  # (20°C - 18.2°C) / 2°C per hour.
