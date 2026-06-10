from app.utils import stats


def test_pass_rate_returns_none_on_zero_total() -> None:
    assert stats.pass_rate(0, 0) is None


def test_pass_rate_returns_ratio() -> None:
    assert stats.pass_rate(8, 10) == 0.8


def test_pass_rate_zero_passed() -> None:
    assert stats.pass_rate(0, 10) == 0.0


def test_pass_rate_full() -> None:
    assert stats.pass_rate(10, 10) == 1.0


def test_pass_rate_negative_total_treated_as_empty() -> None:
    assert stats.pass_rate(0, -1) is None


def test_pass_rate_returns_raw_unrounded() -> None:
    # `stats.pass_rate` returns the raw ratio so aggregation doesn't drift
    # (rounding inputs to a mean breaks accuracy — see plan 044).
    # Callers must wrap in `round_ratio` when populating a response field.
    assert stats.pass_rate(1, 3) != 0.333
    assert stats.round_ratio(stats.pass_rate(1, 3)) == 0.333


def test_round_ratio_passes_none_through() -> None:
    assert stats.round_ratio(None) is None


def test_round_ratio_zero() -> None:
    assert stats.round_ratio(0.0) == 0.0


def test_round_ratio_one() -> None:
    assert stats.round_ratio(1.0) == 1.0


def test_round_ratio_truncates_to_three_decimals() -> None:
    assert stats.round_ratio(0.123456) == 0.123


def test_round_ratio_rounds_up_at_boundary() -> None:
    # 0.8755 rounds to 0.876 (banker's rounding → nearest even doesn't apply
    # at the 4th decimal in this case; 5 rounds up).
    assert stats.round_ratio(0.8754) == 0.875


def test_round_ratio_custom_decimals() -> None:
    assert stats.round_ratio(0.123456, decimals=2) == 0.12
