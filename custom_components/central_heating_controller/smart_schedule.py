"""Pure weekly comfort schedule parsing and economical preview planning."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from math import isfinite
from typing import Callable, Mapping


@dataclass(frozen=True)
class ComfortWindow:
    start: time
    end: time


@dataclass(frozen=True)
class SmartSchedulePlan:
    """A forecast-based preview; it never applies targets to a thermostat."""

    points: tuple[object, ...]
    target_at: Callable[[datetime], float]
    daytime_drop: float
    recovery_lead_minutes: int
    total_demand_hours: float
    provisional: bool
    confidence: float
    hours: float
    daily_drops: Mapping[date, float]
    feasible: bool
    comfort_shortfall_c: float
    gap_drops: tuple["GapDrop", ...]
    objective_demand_hours: float
    terminal_recovery_cost_hours: float


@dataclass(frozen=True)
class GapDrop:
    """Daytime setback selected for one occupied gap before warm-up."""

    start: datetime
    end: datetime
    drop: float


_DAYS = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")


def _parse_time(value: object) -> time:
    if not isinstance(value, str):
        raise ValueError("window times must be HH:MM strings")
    try:
        parsed = time.fromisoformat(value)
    except ValueError as err:
        raise ValueError(f"invalid time: {value!r}") from err
    if parsed.tzinfo is not None or parsed.second or parsed.microsecond:
        raise ValueError("window times must use local HH:MM without seconds or timezone")
    return parsed


def parse_weekly_schedule(value: object) -> dict[int, tuple[ComfortWindow, ...]]:
    """Parse day-name JSON data into weekday-indexed local comfort windows.

    A window whose end is earlier than its start continues into the next day.
    Equal start and end is rejected to avoid an ambiguous empty/all-day window.
    """
    if not isinstance(value, Mapping):
        raise ValueError("weekly schedule must be an object keyed by weekday")
    result: dict[int, tuple[ComfortWindow, ...]] = {}
    for day_index, day in enumerate(_DAYS):
        raw_windows = value.get(day, ())
        if not isinstance(raw_windows, (list, tuple)):
            raise ValueError(f"{day} windows must be a list")
        windows: list[ComfortWindow] = []
        for raw in raw_windows:
            if not isinstance(raw, Mapping):
                raise ValueError("each comfort window must be an object")
            start, end = _parse_time(raw.get("start")), _parse_time(raw.get("end"))
            if start == end:
                raise ValueError("comfort window start and end must differ")
            windows.append(ComfortWindow(start, end))
        result[day_index] = tuple(windows)
    return result


def is_comfort_window(at: datetime, schedule: Mapping[int, tuple[ComfortWindow, ...]]) -> bool:
    """Return whether an aware local datetime falls in a configured window."""
    if at.tzinfo is None or at.utcoffset() is None:
        raise ValueError("at must be timezone-aware")
    weekday, local_time = at.weekday(), at.timetz().replace(tzinfo=None)
    previous = (weekday - 1) % 7
    for window in schedule.get(weekday, ()):
        if window.start < window.end and window.start <= local_time < window.end:
            return True
    for window in schedule.get(previous, ()):
        if window.start > window.end and local_time < window.end:
            return True
    return any(w.start > w.end and w.start <= local_time for w in schedule.get(weekday, ()))


def _in_night(at: datetime, start: time, end: time) -> bool:
    current = at.timetz().replace(tzinfo=None)
    if start == end:
        return True
    if start < end:
        return start <= current < end
    return current >= start or current < end


def plan_smart_schedule(
    now: datetime,
    indoor_temperature: float,
    outdoor_temperature: Callable[[datetime], float],
    forecaster: object,
    weekly_schedule: object,
    *,
    occupied_at: Callable[[datetime], bool] | None = None,
    arrival_at: datetime | None = None,
    night_start: str = "23:00",
    night_end: str = "07:00",
    comfort_at: Callable[[datetime], bool] | None = None,
    comfort_temperature: float = 20.0,
    daytime_drop: float = 2.0,
    night_minimum: float = 16.0,
    away_eco: float = 16.0,
    hours: float = 36,
    target_transform: Callable[[float], float] | None = None,
) -> SmartSchedulePlan:
    """Choose a bounded drop and adaptive recovery lead by simulated demand.

    The selected plan must keep predicted indoor temperature above the comfort
    target in occupied comfort windows and above the night floor throughout.
    Model uncertainty is surfaced as ``provisional``; demand is not a savings
    claim. Computation is bounded to five drops and five recovery leads.
    """
    if not isinstance(now, datetime) or now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("now must be timezone-aware")
    if arrival_at is not None and (arrival_at.tzinfo is None or arrival_at.utcoffset() is None):
        raise ValueError("arrival_at must be timezone-aware")
    schedule = weekly_schedule if isinstance(weekly_schedule, Mapping) and all(
        isinstance(v, (list, tuple)) and all(isinstance(w, ComfortWindow) for w in v)
        for v in weekly_schedule.values()
    ) else parse_weekly_schedule(weekly_schedule)
    nstart, nend = _parse_time(night_start), _parse_time(night_end)
    for name, number in (("comfort_temperature", comfort_temperature), ("daytime_drop", daytime_drop),
                         ("night_minimum", night_minimum), ("away_eco", away_eco), ("hours", hours)):
        if isinstance(number, bool) or not isinstance(number, (float, int)) or not isfinite(number):
            raise ValueError(f"{name} must be finite")
    if hours < 24 or hours > 49:
        raise ValueError("hours must be between 24 and 49")
    max_drop = min(5.0, max(0.0, float(daytime_drop)))
    drop_values = [i * 0.5 for i in range(int(max_drop * 2) + 1)]
    if not drop_values or drop_values[-1] < max_drop:
        drop_values.append(max_drop)
    drops = tuple(drop_values)
    leads = (0, 30, 60, 90, 120, 180, 240, 360, 480, 720)
    arrival = arrival_at.astimezone(now.tzinfo) if arrival_at else None
    start_utc = now.astimezone(timezone.utc)
    end_utc = start_utc + timedelta(hours=hours)

    def local(at: datetime) -> datetime:
        return at.astimezone(now.tzinfo)

    def scheduled(at: datetime) -> bool:
        return bool(comfort_at(at) if comfort_at else is_comfort_window(at, schedule))

    def base_target(at: datetime, drop: float) -> float:
        at = local(at)
        occupied = occupied_at(at) if occupied_at else True
        arriving = arrival is not None and at >= arrival
        comfort = scheduled(at) and (occupied or arriving)
        if comfort:
            return float(comfort_temperature)
        if not occupied and not arriving:
            return float(away_eco)
        if _in_night(at, nstart, nend):
            return float(night_minimum)
        return max(float(comfort_temperature) - drop, float(night_minimum))

    def is_occupied(at: datetime) -> bool:
        at = local(at)
        return (occupied_at(at) if occupied_at else True) or (arrival is not None and at >= arrival)

    # Find occupied daytime gaps on the absolute timeline, so DST folds and
    # jumps do not create false or merged local-time intervals.
    gap_ranges: list[tuple[datetime, datetime]] = []
    active_start: datetime | None = None
    cursor = start_utc
    while cursor <= end_utc:
        at = local(cursor)
        arrival_warm = arrival is not None and arrival <= at < arrival + timedelta(hours=1)
        in_gap = is_occupied(at) and not scheduled(at) and not _in_night(at, nstart, nend) and not arrival_warm
        if in_gap and active_start is None:
            active_start = cursor
        elif not in_gap and active_start is not None:
            gap_ranges.append((active_start, cursor))
            active_start = None
        cursor += timedelta(minutes=5)
    if active_start is not None:
        gap_ranges.append((active_start, end_utc + timedelta(minutes=5)))

    # Record comfort deadlines once on the absolute timeline. Recovery targets
    # can then begin the selected lead before the start boundary, including
    # cases where the lead is longer than the comfort window itself.
    comfort_ranges: list[tuple[datetime, datetime]] = []
    comfort_start: datetime | None = None
    cursor = start_utc
    while cursor <= end_utc + timedelta(minutes=5):
        at = local(cursor)
        arrival_warm = arrival is not None and arrival <= at < arrival + timedelta(hours=1)
        active = (scheduled(at) and is_occupied(at)) or arrival_warm
        if active and comfort_start is None:
            comfort_start = cursor
        elif not active and comfort_start is not None:
            comfort_ranges.append((comfort_start, cursor))
            comfort_start = None
        cursor += timedelta(minutes=5)
    if comfort_start is not None:
        comfort_ranges.append((comfort_start, end_utc + timedelta(minutes=5)))

    # Coordinate descent adjusts one gap at a time. Limit the search to 100
    # forecasts, reducing grid density only when a schedule has many gaps.
    optimized_gaps = sorted(
        range(len(gap_ranges)),
        key=lambda index: gap_ranges[index][1] - gap_ranges[index][0],
        reverse=True,
    )[:6]
    if optimized_gaps:
        passes = max(1, min(2, (100 - 2 * len(leads)) // (len(optimized_gaps) * len(drops))))
    else:
        passes = 0
    selected_drops = [max_drop] * len(gap_ranges)

    def selected_gap_drop(at_utc: datetime, values: list[float]) -> float:
        for index, (start, end) in enumerate(gap_ranges):
            if start <= at_utc < end:
                return values[index]
        return max_drop

    def make_target(values: list[float], lead: int) -> Callable[[datetime], float]:
        def raw_target(at: datetime) -> float:
            at_local = local(at)
            at_utc = at.astimezone(timezone.utc)
            drop = selected_gap_drop(at_utc, values)
            arrival_warm = arrival is not None and arrival <= at_local < arrival + timedelta(hours=1)
            if arrival_warm:
                return float(comfort_temperature)
            if lead and any(start <= at_utc + timedelta(minutes=lead) and at_utc < start
                            for start, _ in comfort_ranges):
                return float(comfort_temperature)
            return base_target(at_local, drop)
        def target(at: datetime) -> float:
            value = raw_target(at)
            return target_transform(value) if target_transform else value
        return target

    def evaluate(values: list[float], lead: int):
        target = make_target(values, lead)
        forecast = forecaster.forecast(now, indoor_temperature, outdoor_temperature, target,
                                       hours=hours, step_minutes=5)
        points = tuple(forecast.points)
        shortfall = 0.0
        for point in points:
            point_utc = point.at.astimezone(timezone.utc)
            if point_utc < start_utc or point_utc > end_utc:
                continue
            point_local = local(point.at)
            present = is_occupied(point_local)
            if present and _in_night(point_local, nstart, nend) and point.indoor_temperature < night_minimum - 0.25:
                shortfall = max(shortfall, night_minimum - point.indoor_temperature)
            arrival_warm = arrival is not None and arrival <= point_local < arrival + timedelta(hours=1)
            if (scheduled(point_local) and present) or arrival_warm:
                floor = comfort_temperature
            elif present and not _in_night(point_local, nstart, nend):
                floor = max(night_minimum, comfort_temperature - selected_gap_drop(point_utc, values))
            else:
                floor = None
            if floor is not None and point.indoor_temperature < floor - 0.25:
                shortfall = max(shortfall, floor - point.indoor_temperature)
        demand = sum(
            (max(0.0, float(getattr(a, "demand", 0.0))) + max(0.0, float(getattr(b, "demand", 0.0))))
            * 0.5 * max(0.0, (b.at - a.at).total_seconds() / 3600)
            for a, b in zip(points, points[1:])
        )
        # Compare every candidate against the same terminal comfort reference.
        # This estimates the additional full-demand hours needed to recover
        # stored heat, without changing the simulated setpoint or demand curve.
        raw_gain = getattr(forecaster, "heat_gain_c_per_hour", 1.5)
        if isinstance(raw_gain, bool) or not isinstance(raw_gain, (int, float)) or not isfinite(raw_gain) or raw_gain <= 0:
            raw_gain = 1.5
        bounded_gain = min(8.0, max(0.1, float(raw_gain)))
        terminal_cost = min(24.0, max(0.0, comfort_temperature - points[-1].indoor_temperature) / bounded_gain) if points else 0.0
        objective = demand + terminal_cost
        infeasible = shortfall > 0.25
        return (infeasible, shortfall if infeasible else 0.0, objective, -sum(values), lead,
                points, target, float(getattr(forecast, "confidence", 0.0)), demand, terminal_cost)

    best, lead = min(((evaluate(selected_drops, candidate), candidate) for candidate in leads),
                     key=lambda item: item[0][:5])

    def coordinate_pass(current_lead: int) -> None:
        nonlocal best
        for gap_index in optimized_gaps:
            trials = []
            for drop in drops:
                selected_drops[gap_index] = drop
                trials.append((evaluate(selected_drops, current_lead), drop))
            best, selected_drops[gap_index] = min(trials, key=lambda item: item[0][:5])

    for _ in range(passes):
        coordinate_pass(lead)
    best, lead = min(((evaluate(selected_drops, candidate), candidate) for candidate in leads),
                     key=lambda item: item[0][:5])
    runs_used = 2 * len(leads) + passes * len(optimized_gaps) * len(drops)
    next_round_cost = len(optimized_gaps) * len(drops) + len(leads)
    if optimized_gaps and 100 - runs_used >= next_round_cost:
        coordinate_pass(lead)
        best, lead = min(((evaluate(selected_drops, candidate), candidate) for candidate in leads),
                         key=lambda item: item[0][:5])
    infeasible, shortfall, objective, _, _, points, target, confidence, demand, terminal_cost = best

    gap_drops: list[GapDrop] = []
    daily_drops: dict[date, float] = {}
    for (start, end), drop in zip(gap_ranges, selected_drops):
        start_local, end_local = local(start), local(end)
        gap_drops.append(GapDrop(start_local, end_local, drop))
        daily_drops[start_local.date()] = max(daily_drops.get(start_local.date(), 0.0), drop)
    max_selected_drop = max(selected_drops, default=max_drop)
    provisional = (getattr(forecaster, "sample_count", 0) < 40
                   or not getattr(forecaster, "lag_learned", False)
                   or not getattr(forecaster, "demand_model_learned", False)
                   or confidence < 0.5 or infeasible)
    return SmartSchedulePlan(points, target, max_selected_drop, lead, demand, provisional,
                             confidence, float(hours), daily_drops, not infeasible, shortfall,
                             tuple(gap_drops), objective, terminal_cost)
