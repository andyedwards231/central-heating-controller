from dataclasses import FrozenInstanceError
from datetime import datetime, timedelta, timezone, tzinfo
from zoneinfo import ZoneInfo

import pytest

from custom_components.central_heating_controller.travel import (
    PreheatTiming,
    destination_is_home,
    parse_arrival_time,
    preheat_timing,
)


class UnusableTimezone(tzinfo):
    """Timezone implementation that cannot supply an offset."""

    def utcoffset(self, dt: datetime | None) -> timedelta | None:
        raise NotImplementedError

    def dst(self, dt: datetime | None) -> timedelta | None:
        raise NotImplementedError

    def tzname(self, dt: datetime | None) -> str | None:
        raise NotImplementedError


@pytest.mark.parametrize("destination", ["home", "Home", "zone.home", "My_Home", "my home"])
def test_destination_matches_zone_forms(destination) -> None:
    assert destination_is_home(destination, "zone.home", "My Home", None)


@pytest.mark.parametrize(
    "destination",
    [" zone.HOME ", "my-home", " my___home ", "my - home"],
)
def test_destination_normalizes_whitespace_case_zone_prefix_and_separators(destination) -> None:
    assert destination_is_home(destination, "zone.home", "My Home", None)


def test_optional_exact_destination_value_is_additive() -> None:
    assert destination_is_home("HOME_ADDRESS", "zone.home", "My Home", "HOME_ADDRESS")
    assert destination_is_home("home", "zone.home", "My Home", "HOME_ADDRESS")


def test_optional_exact_destination_value_is_normalized() -> None:
    assert destination_is_home(" home-address ", "zone.other", "Elsewhere", "HOME_ADDRESS")


@pytest.mark.parametrize(
    "destination",
    [None, "", "   ", "unknown", " UNKNOWN ", "unavailable", "zone.away", "work"],
)
def test_destination_non_matches(destination) -> None:
    assert not destination_is_home(destination, "zone.home", "My Home", "HOME_ADDRESS")


@pytest.mark.parametrize("raw", ["2026-01-01T12:30:00+00:00", 1767270600, "1767270600"])
def test_arrival_formats(raw) -> None:
    assert parse_arrival_time(raw, timezone.utc) == datetime(
        2026, 1, 1, 12, 30, tzinfo=timezone.utc
    )


@pytest.mark.parametrize("raw", [None, "", "  ", "unknown", "unavailable", "not-a-date"])
def test_invalid_arrival_returns_none(raw) -> None:
    assert parse_arrival_time(raw, timezone.utc) is None


@pytest.mark.parametrize(
    "raw",
    ["2026-02-31T12:00:00", "2026-01-01T25:00:00"],
)
def test_malformed_iso_arrival_returns_none(raw) -> None:
    assert parse_arrival_time(raw, timezone.utc) is None


def test_naive_iso_arrival_uses_supplied_local_timezone_and_returns_utc() -> None:
    local_tz = timezone(timedelta(hours=2))
    assert parse_arrival_time("2026-01-01T12:30:00", local_tz) == datetime(
        2026, 1, 1, 10, 30, tzinfo=timezone.utc
    )


def test_aware_iso_arrival_converts_to_utc() -> None:
    assert parse_arrival_time("2026-01-01T12:30:00-05:00", timezone.utc) == datetime(
        2026, 1, 1, 17, 30, tzinfo=timezone.utc
    )


def test_finite_float_unix_timestamp_preserves_fractional_seconds() -> None:
    assert parse_arrival_time(1767270600.5, timezone.utc) == datetime(
        2026, 1, 1, 12, 30, 0, 500000, tzinfo=timezone.utc
    )


@pytest.mark.parametrize(
    "raw",
    [True, False, float("nan"), float("inf"), float("-inf"), 10**100, 10**1000],
)
def test_non_finite_boolean_and_out_of_range_timestamps_are_rejected(raw) -> None:
    assert parse_arrival_time(raw, timezone.utc) is None


def test_naive_arrival_without_a_valid_local_timezone_is_rejected() -> None:
    assert parse_arrival_time("2026-01-01T12:30:00", None) is None


def test_naive_arrival_with_unusable_local_timezone_returns_none() -> None:
    assert parse_arrival_time("2026-01-01T12:30:00", UnusableTimezone()) is None


def test_zoneinfo_timezone_remains_supported() -> None:
    assert parse_arrival_time("2026-06-01T12:30:00", ZoneInfo("Europe/London")) == datetime(
        2026, 6, 1, 11, 30, tzinfo=timezone.utc
    )


def test_bad_or_past_arrival_means_immediate_preheat() -> None:
    now = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
    timing = preheat_timing("unavailable", now, warmup_minutes=60, local_tz=timezone.utc)
    assert timing.ready is True
    assert timing.arrival is None
    assert timing.start is None


@pytest.mark.parametrize(
    "arrival",
    ["2026-01-01T11:59:59+00:00", "2026-01-01T12:00:00+00:00"],
)
def test_past_or_equal_arrival_means_immediate_preheat(arrival) -> None:
    now = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
    assert preheat_timing(arrival, now, 60, timezone.utc) == PreheatTiming(True, None, None)


def test_future_arrival_waits_until_calculated_start() -> None:
    now = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
    timing = preheat_timing("2026-01-01T14:00:00+00:00", now, 60, timezone.utc)
    assert timing.ready is False
    assert timing.arrival == datetime(2026, 1, 1, 14, 0, tzinfo=timezone.utc)
    assert timing.start == datetime(2026, 1, 1, 13, 0, tzinfo=timezone.utc)


@pytest.mark.parametrize(
    ("now", "expected_ready"),
    [
        (datetime(2026, 1, 1, 12, 59, 59, tzinfo=timezone.utc), False),
        (datetime(2026, 1, 1, 13, 0, tzinfo=timezone.utc), True),
    ],
)
def test_future_arrival_flips_ready_exactly_at_start(now, expected_ready) -> None:
    timing = preheat_timing("2026-01-01T14:00:00+00:00", now, 60, timezone.utc)
    assert timing.ready is expected_ready


def test_aware_now_is_normalized_to_utc() -> None:
    now = datetime(2026, 1, 1, 14, 0, tzinfo=timezone(timedelta(hours=1)))
    timing = preheat_timing("2026-01-01T14:00:00+00:00", now, 60, timezone.utc)
    assert timing.ready is True


def test_negative_warmup_minutes_are_rejected() -> None:
    now = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
    with pytest.raises(ValueError, match="warmup_minutes"):
        preheat_timing("2026-01-01T14:00:00+00:00", now, -1, timezone.utc)


def test_naive_now_is_rejected() -> None:
    with pytest.raises(ValueError, match="aware"):
        preheat_timing(
            "2026-01-01T14:00:00+00:00",
            datetime(2026, 1, 1, 12, 0),
            60,
            timezone.utc,
        )


def test_unusable_now_timezone_is_rejected() -> None:
    now = datetime(2026, 1, 1, 12, 0, tzinfo=UnusableTimezone())
    with pytest.raises(ValueError, match="now.*timezone|timezone.*now"):
        preheat_timing("2026-01-01T14:00:00+00:00", now, 60, timezone.utc)


def test_invalid_local_timezone_is_rejected() -> None:
    now = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
    with pytest.raises(ValueError, match="local_tz"):
        preheat_timing("2026-01-01T14:00:00+00:00", now, 60, None)


def test_unusable_local_timezone_is_rejected_before_calculations() -> None:
    now = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
    with pytest.raises(ValueError, match="local_tz"):
        preheat_timing(
            "2026-01-01T14:00:00+00:00",
            now,
            60,
            UnusableTimezone(),
        )


def test_preheat_timing_is_frozen() -> None:
    timing = PreheatTiming(True, None, None)
    with pytest.raises(FrozenInstanceError):
        timing.ready = False
