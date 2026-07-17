import cv2
import pytest

from conftest import requires_fixtures
from nss_tracker.detection.rank_ocr import (
    GAUGE_ROI_COMPACT,
    GAUGE_ROI_ENLARGED,
    read_precise_rank,
    read_rank,
    read_rank_gauge_fill,
)

# 同一プレイ記録からのfixtureのため全て同じランク値(38)になっているが、
# バッジの表示有無・通常表示/昇格・降格アニメ中の拡大表示それぞれで
# 正しく読み取れる(またはバッジ非表示時にNoneを返す)ことを確認する。
# "_in_rank_increase_"/"_in_rank_decrease_"のfixture(ランク増加中/減少中、
# 遷移演出の途中)は含めない。state.match_state側もこのタイミングで
# read_rank/read_precise_rankを呼ぶことはなく(呼ぶのは常にコンパクト表示=
# 増加/減少前、または安定後の拡大表示=増加/減少後のみ)、現行システムの
# 判断材料になっていないため
EXPECTED = {
    "43_result_win_without_rank_blue.png": None,
    "44_result_lose_with_rank_blue.png": 38,
    "46_result_lose_after_rank_decrease_blue.png": 38,
    "50_result_win_with_rank_red.png": 38,
    "52_result_after_rank_increase_red.png": 38,
    "54_result_lose_with_rank_red.png": 38,
    "56_result_lose_after_rank_decrease_red.png": 38,
}


@pytest.mark.slow
@requires_fixtures
@pytest.mark.parametrize("filename, expected", sorted(EXPECTED.items()))
def test_read_rank(fixtures_dir, filename, expected):
    frame = cv2.imread(str(fixtures_dir / filename))
    assert frame is not None, f"failed to load {filename}"
    assert read_rank(frame) == expected


# バッジはコンパクト表示(結果バナー確定直後、アニメーション開始前)と
# 拡大表示(アニメーション開始後〜暗転まで)でバーの実寸(幅・位置)が異なるため、
# GAUGE_ROI_COMPACT/GAUGE_ROI_ENLARGEDそれぞれに対応するfixtureで別々に検証する。
# 実測(fixtures/screenshotsの行ごとのHSV値を直接スキャンして確認、
# scripts/inspect_gauge_fill.py参照): 同一の勝敗ペアでもコンパクト表示と
# 拡大表示では塗りつぶし率が明確に異なる値になる(例: 44は0.75だが対応する
# 46は0.56。勝敗による塗りつぶし量の増減が正しく反映されている)。
#
# "_in_rank_increase_"/"_in_rank_decrease_"のfixtureを含めない理由はEXPECTED
# 同様(このファイル冒頭のコメント参照)。加えてread_rank_gauge_fillは
# 「安定している瞬間」にのみ呼び出す前提の関数であり、遷移演出中はゲージの
# 見た目自体がグラデーション表示になり塗りつぶし割合として意味を持たない
EXPECTED_GAUGE_FILL_COMPACT = {
    "44_result_lose_with_rank_blue.png": 0.75,
    "50_result_win_with_rank_red.png": 0.06,
    "54_result_lose_with_rank_red.png": 0.24,
}

EXPECTED_GAUGE_FILL_ENLARGED = {
    "46_result_lose_after_rank_decrease_blue.png": 0.56,
    "52_result_after_rank_increase_red.png": 0.32,
    "56_result_lose_after_rank_decrease_red.png": 0.08,
}


@requires_fixtures
@pytest.mark.parametrize("filename, expected", sorted(EXPECTED_GAUGE_FILL_COMPACT.items()))
def test_read_rank_gauge_fill_compact(fixtures_dir, filename, expected):
    frame = cv2.imread(str(fixtures_dir / filename))
    assert frame is not None, f"failed to load {filename}"
    assert read_rank_gauge_fill(frame, GAUGE_ROI_COMPACT) == pytest.approx(expected, abs=0.02)


@requires_fixtures
@pytest.mark.parametrize("filename, expected", sorted(EXPECTED_GAUGE_FILL_ENLARGED.items()))
def test_read_rank_gauge_fill_enlarged(fixtures_dir, filename, expected):
    frame = cv2.imread(str(fixtures_dir / filename))
    assert frame is not None, f"failed to load {filename}"
    assert read_rank_gauge_fill(frame, GAUGE_ROI_ENLARGED) == pytest.approx(expected, abs=0.02)


@pytest.mark.slow
@requires_fixtures
def test_read_precise_rank_combines_tier_and_gauge_fill(fixtures_dir):
    frame = cv2.imread(str(fixtures_dir / "44_result_lose_with_rank_blue.png"))
    assert frame is not None
    tier, precise = read_precise_rank(frame, GAUGE_ROI_COMPACT)
    assert tier == 38
    assert precise == pytest.approx(38.75, abs=0.02)


@pytest.mark.slow
@requires_fixtures
def test_read_precise_rank_returns_none_without_badge(fixtures_dir):
    frame = cv2.imread(str(fixtures_dir / "43_result_win_without_rank_blue.png"))
    assert frame is not None
    assert read_precise_rank(frame, GAUGE_ROI_COMPACT) is None
