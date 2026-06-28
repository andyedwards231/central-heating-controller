from datetime import datetime, timedelta, timezone

import pytest

from custom_components.central_heating_controller.learning import HeatingRateLearner

NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)


def accepted_sample(
    learner: HeatingRateLearner, start_temp: float, end_temp: float, offset: int = 0
) -> float | None:
    start = NOW + timedelta(hours=offset)
    learner.observe(start, start_temp, 21.0, heating=True)
    return learner.observe(start + timedelta(minutes=30), end_temp, 21.0, heating=True)


def test_requires_three_non_overlapping_samples_and_smooths_at_point_three() -> None:
    learner = HeatingRateLearner()
    accepted_sample(learner, 18.0, 19.0, 0)  # 2.0 C/h
    accepted_sample(learner, 18.0, 18.5, 1)  # 1.0 C/h => 1.7
    accepted_sample(learner, 18.0, 19.5, 2)  # 3.0 C/h => 2.09
    assert learner.sample_count == 3
    assert learner.trusted is True
    assert learner.rate == pytest.approx(2.09)


def test_rejects_short_falling_target_reached_and_implausible_windows() -> None:
    learner = HeatingRateLearner()
    learner.observe(NOW, 18.0, 20.0, heating=True)
    learner.observe(NOW + timedelta(minutes=10), 19.0, 20.0, heating=True)
    learner.observe(NOW + timedelta(minutes=30), 17.0, 20.0, heating=True)
    learner.observe(NOW + timedelta(hours=1), 20.0, 20.0, heating=True)
    assert learner.sample_count == 0


def test_warmup_uses_fallback_until_trusted_then_clamps_model() -> None:
    learner = HeatingRateLearner(rate=1.0, sample_count=2)
    assert learner.warmup_minutes(16.0, 20.0, 60, 180) == 60
    learner.sample_count = 3
    assert learner.warmup_minutes(16.0, 20.0, 60, 180) == 180
    assert learner.warmup_minutes(20.0, 20.0, 60, 180) == 0


def test_accepts_exactly_fifteen_minutes_and_returns_smoothed_rate() -> None:
    learner = HeatingRateLearner(rate=1.0, sample_count=1)
    learner.observe(NOW, 18.0, 21.0, heating=True)

    accepted_rate = learner.observe(NOW + timedelta(minutes=15), 18.5, 21.0, heating=True)

    assert accepted_rate == pytest.approx(1.3)
    assert learner.rate == pytest.approx(1.3)
    assert learner.sample_count == 2


def test_frequent_updates_keep_original_baseline() -> None:
    learner = HeatingRateLearner()
    learner.observe(NOW, 18.0, 21.0, heating=True)
    learner.observe(NOW + timedelta(minutes=5), 18.1, 21.0, heating=True)
    learner.observe(NOW + timedelta(minutes=10), 18.2, 21.0, heating=True)

    assert learner.observe(NOW + timedelta(minutes=15), 18.5, 21.0, heating=True) == pytest.approx(
        2.0
    )
    assert learner.sample_count == 1


def test_stopped_heating_resets_the_window() -> None:
    learner = HeatingRateLearner()
    learner.observe(NOW, 18.0, 21.0, heating=True)
    learner.observe(NOW + timedelta(minutes=10), 18.2, 21.0, heating=False)

    assert learner.observe(NOW + timedelta(minutes=20), 18.5, 21.0, heating=True) is None
    assert learner.observe(NOW + timedelta(minutes=30), 19.0, 21.0, heating=True) is None
    assert learner.sample_count == 0


def test_accepted_windows_do_not_overlap() -> None:
    learner = HeatingRateLearner()
    learner.observe(NOW, 18.0, 21.0, heating=True)
    learner.observe(NOW + timedelta(minutes=15), 18.5, 21.0, heating=True)

    assert learner.observe(NOW + timedelta(minutes=20), 18.75, 21.0, heating=True) is None
    assert learner.observe(NOW + timedelta(minutes=30), 19.0, 21.0, heating=True) is None
    assert learner.sample_count == 1


def test_accepts_rate_of_exactly_ten_and_rejects_rate_above_ten() -> None:
    learner = HeatingRateLearner()
    learner.observe(NOW, 18.0, 21.0, heating=True)
    assert learner.observe(NOW + timedelta(minutes=15), 20.5, 21.0, heating=True) == pytest.approx(
        10.0
    )

    learner.observe(NOW + timedelta(hours=1), 18.0, 21.0, heating=True)
    assert learner.observe(NOW + timedelta(hours=1, minutes=15), 20.6, 21.0, heating=True) is None
    assert learner.sample_count == 1


@pytest.mark.parametrize(
    ("current_temperature", "target_temperature"),
    [
        (None, 21.0),
        (18.0, None),
        (float("nan"), 21.0),
        (18.0, float("inf")),
        (True, 21.0),
    ],
)
def test_invalid_temperatures_reset_the_window(
    current_temperature: float | None, target_temperature: float | None
) -> None:
    learner = HeatingRateLearner()
    learner.observe(NOW, 18.0, 21.0, heating=True)
    learner.observe(
        NOW + timedelta(minutes=5),
        current_temperature,
        target_temperature,
        heating=True,
    )

    assert learner.observe(NOW + timedelta(minutes=15), 18.5, 21.0, heating=True) is None
    assert learner.sample_count == 0


def test_naive_and_non_monotonic_timestamps_reset_the_window() -> None:
    learner = HeatingRateLearner()
    learner.observe(NOW, 18.0, 21.0, heating=True)
    learner.observe(NOW.replace(tzinfo=None) + timedelta(minutes=5), 18.2, 21.0, heating=True)
    learner.observe(NOW + timedelta(minutes=10), 18.3, 21.0, heating=True)
    learner.observe(NOW + timedelta(minutes=9), 18.4, 21.0, heating=True)

    assert learner.observe(NOW + timedelta(minutes=25), 18.8, 21.0, heating=True) is None
    assert learner.sample_count == 0


@pytest.mark.parametrize(
    ("rate", "sample_count"),
    [
        (None, 1),
        (0.0, 1),
        (-1.0, 1),
        (float("nan"), 1),
        (float("inf"), 1),
        (1.0, -1),
        (1.0, 1.5),
        (1.0, True),
        (1.0, 0),
    ],
)
def test_invalid_initial_state_normalizes_to_empty(rate: float | None, sample_count: int) -> None:
    learner = HeatingRateLearner(rate=rate, sample_count=sample_count)

    assert learner.rate is None
    assert learner.sample_count == 0
    assert learner.trusted is False
    assert learner.to_dict() == {"rate": None, "sample_count": 0}


def test_reset_clears_learned_and_transient_state() -> None:
    learner = HeatingRateLearner(rate=2.0, sample_count=3)
    learner.observe(NOW, 18.0, 21.0, heating=True)

    learner.reset()

    assert learner.to_dict() == {"rate": None, "sample_count": 0}
    assert learner.trusted is False
    assert learner.observe(NOW + timedelta(minutes=15), 18.5, 21.0, heating=True) is None


def test_warmup_fallback_for_unavailable_values_and_ceil_for_trusted_model() -> None:
    learner = HeatingRateLearner(rate=2.0, sample_count=3)

    assert learner.warmup_minutes(None, 20.0, 60, 180) == 60
    assert learner.warmup_minutes(float("nan"), 20.0, 60, 180) == 60
    assert learner.warmup_minutes(19.0, float("inf"), 60, 180) == 60
    assert learner.warmup_minutes(19.01, 20.0, 60, 180) == 30
    assert learner.warmup_minutes(10.0, 20.0, 60, 180) == 180


@pytest.mark.parametrize(
    ("fallback", "maximum"),
    [
        (-1, 180),
        (60, -1),
        (181, 180),
        (float("nan"), 180),
        (60, float("inf")),
        (True, 180),
    ],
)
def test_warmup_rejects_invalid_durations(fallback: float, maximum: float) -> None:
    with pytest.raises(ValueError):
        HeatingRateLearner().warmup_minutes(18.0, 20.0, fallback, maximum)
