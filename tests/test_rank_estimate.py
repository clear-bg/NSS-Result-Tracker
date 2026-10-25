import pytest

from nss_tracker.rank_estimate import (
    MIN_REGRESSION_SAMPLES,
    Sample,
    convert_rank_tier_to_unified_scale,
    estimate_rank_after,
    team_rank_diff,
)


@pytest.mark.parametrize(
    "tier_label, tier_value, expected",
    [
        ("∞", 42, 42),
        ("∞", 0, 0),
        ("S", 9, -1),
        ("S", 0, -10),
        ("A", 29, -11),
        ("A", 0, -40),
        (None, None, None),
        ("∞", None, None),
        ("B", 5, None),
    ],
)
def testconvert_rank_tier_to_unified_scale(tier_label, tier_value, expected):
    assert convert_rank_tier_to_unified_scale(tier_label, tier_value) == expected


def test_convert_rank_tier_unified_scale_preserves_promotion_order():
    # A29(-11) < S0(-10) < S9(-1) < 無限0(0) の順序が保たれること
    a29 = convert_rank_tier_to_unified_scale("A", 29)
    s0 = convert_rank_tier_to_unified_scale("S", 0)
    s9 = convert_rank_tier_to_unified_scale("S", 9)
    mu0 = convert_rank_tier_to_unified_scale("∞", 0)

    assert a29 < s0 < s9 < mu0


def _all_infinity(*values):
    return [("∞", v) for v in values]


def test_team_rank_diff_converts_s_and_a_tiers():
    mine = [("∞", 45), ("S", 9), ("A", 29), ("∞", 0)]  # 45 - 1 - 11 + 0 = 33
    opponent = _all_infinity(10, 10, 10, 0)  # 30

    assert team_rank_diff(mine, opponent) == 3


@pytest.mark.parametrize(
    "mine",
    [
        [("∞", 45), ("∞", 40), ("∞", 40)],  # 3人しかいない
        [("∞", 45), ("∞", 40), ("∞", 40), (None, None)],  # 未読
        [("∞", 45), ("∞", 40), ("∞", 40), ("B", 5)],  # B~E帯
    ],
)
def test_team_rank_diff_returns_none_when_any_slot_is_unknown(mine):
    assert team_rank_diff(mine, _all_infinity(40, 40, 40, 40)) is None


def _linear_samples(result, intercept, slope, n=MIN_REGRESSION_SAMPLES):
    return [Sample(result, d, intercept + slope * d) for d in range(-n // 2, n - n // 2)]


def test_estimate_uses_regression_on_team_rank_diff():
    samples = _linear_samples("win", 0.20, -0.02) + _linear_samples("lose", -0.20, -0.01)

    assert estimate_rank_after(44.30, "win", -5, None, False, samples) == pytest.approx(44.60)
    assert estimate_rank_after(44.30, "lose", 10, None, False, samples) == pytest.approx(44.00)


def test_estimate_falls_back_to_median_without_team_rank_diff():
    samples = _linear_samples("win", 0.20, -0.02)

    assert estimate_rank_after(44.30, "win", None, None, False, samples) == pytest.approx(44.51)


def test_estimate_falls_back_to_median_with_too_few_regression_samples():
    samples = [Sample("win", 0, 0.20), Sample("win", 10, 0.10), Sample("win", None, 0.30)]

    assert estimate_rank_after(44.30, "win", 5, None, False, samples) == pytest.approx(44.50)


def test_estimate_keeps_rank_for_draw_and_player_shortage():
    samples = _linear_samples("win", 0.20, -0.02)

    assert estimate_rank_after(44.30, "draw", 0, None, False, samples) == 44.30
    assert estimate_rank_after(44.30, "win", 0, None, True, samples) == 44.30


def test_estimate_returns_none_without_rank_before_or_samples():
    samples = _linear_samples("win", 0.20, -0.02)

    assert estimate_rank_after(None, "win", 0, None, False, samples) is None
    assert estimate_rank_after(44.30, "lose", 0, None, False, samples) is None


def test_estimate_snaps_to_next_tier_when_promotion_detected():
    samples = _linear_samples("win", 0.20, 0.0)

    assert estimate_rank_after(44.75, "win", 0, "up", False, samples) == 45.00


def test_estimate_snaps_below_tier_when_demotion_detected():
    samples = _linear_samples("lose", -0.20, 0.0)

    assert estimate_rank_after(45.30, "lose", 0, "down", False, samples) == 44.99


def test_estimate_ignores_label_when_correction_is_too_large():
    # id=191の実例: 44.03の勝ちで昇格を誤検知していた。45.00まで寄せると大きく外れる
    samples = _linear_samples("win", 0.20, 0.0)

    assert estimate_rank_after(44.03, "win", 0, "up", False, samples) == 44.23


def test_estimate_does_not_clamp_crossing_without_label():
    # 昇格/降格を見逃すことがあるため、検知していなくても予測が帯をまたぐのは許す
    samples = _linear_samples("win", 0.20, 0.0)

    assert estimate_rank_after(44.90, "win", 0, None, False, samples) == 45.10
