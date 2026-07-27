import cv2
import pytest

from conftest import requires_fixtures
from nss_tracker.detection.rank_ocr import (
    GAUGE_ROI_COMPACT,
    GAUGE_ROI_ENLARGED,
    read_precise_rank,
    read_rank,
    read_rank_gauge_fill,
    read_rank_tier,
)

# Issue #148(HDR無効化前fixture削除に伴う張り替え): 同一プレイ記録からのfixtureの
# ため全て同じランク値(38)になっている。いずれもコンパクト表示(結果バナー確定
# 直後)のみで、拡大表示(ランク変動アニメーション中)のHDR無効化後fixtureは
# まだ無い(Issue #147参照、`30_win_blue_league_up_hdr_off.mp4`の中に該当区間は
# 含まれているが静止画としては未切り出し)
EXPECTED = {
    "77_result_win_with_rank_red_hdr_off.png": 38,
    "78_result_lose_with_rank_blue_hdr_off.png": 38,
    "81_result_lose_without_rank_blue_hdr_off.png": None,
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
# 実測(read_rank_gauge_fill自体の出力、scripts/inspect_gauge_fill.py参照)。
# 塗りつぶし割合は人間の目視では正確な値を判定できない性質の指標のため、
# 既存のテストと同じ方針でこの関数自体の実測値をground truthとして採用している
# (このファイル・inspect_gauge_fill.pyのコメント参照。テスト正解データの
# 自己参照禁止の対象はタイミング系の期待値であり、この指標は元々対象外)
#
# Issue #148: 拡大表示(GAUGE_ROI_ENLARGED)のHDR無効化後fixtureがまだ無いため、
# EXPECTED_GAUGE_FILL_ENLARGEDは一旦空にしてある(Issue #147参照)
EXPECTED_GAUGE_FILL_COMPACT = {
    "77_result_win_with_rank_red_hdr_off.png": 0.92,
    "78_result_lose_with_rank_blue_hdr_off.png": 0.18,
}

EXPECTED_GAUGE_FILL_ENLARGED = {}


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
    frame = cv2.imread(str(fixtures_dir / "78_result_lose_with_rank_blue_hdr_off.png"))
    assert frame is not None
    tier, precise = read_precise_rank(frame, GAUGE_ROI_COMPACT)
    assert tier == 38
    assert precise == pytest.approx(38.18, abs=0.02)


@pytest.mark.slow
@requires_fixtures
def test_read_precise_rank_returns_none_without_badge(fixtures_dir):
    frame = cv2.imread(str(fixtures_dir / "81_result_lose_without_rank_blue_hdr_off.png"))
    assert frame is not None
    assert read_precise_rank(frame, GAUGE_ROI_COMPACT) is None


# Issue #73: read_rank_tier()の∞判定は既存の∞帯fixture全てで実データ検証できるが、
# S/A帯は結果バナー画面での参照fixtureが無いため未検証(rank_ocr.pyのモジュール
# docstring参照)。ここでは∞判定の回帰確認、およびバッジ非表示時にNoneを返す
# ことのみ確認する。
@pytest.mark.slow
@requires_fixtures
@pytest.mark.parametrize(
    "filename",
    sorted(name for name, expected in EXPECTED.items() if expected is not None),
)
def test_read_rank_tier_returns_infinity_for_existing_fixtures(fixtures_dir, filename):
    frame = cv2.imread(str(fixtures_dir / filename))
    assert frame is not None, f"failed to load {filename}"
    assert read_rank_tier(frame) == "∞"


@pytest.mark.slow
@requires_fixtures
def test_read_rank_tier_returns_none_without_badge(fixtures_dir):
    frame = cv2.imread(str(fixtures_dir / "81_result_lose_without_rank_blue_hdr_off.png"))
    assert frame is not None
    assert read_rank_tier(frame) is None
