import cv2
import pytest

from conftest import requires_fixtures
from nss_tracker.detection.rank_ocr import (
    GAUGE_ROI_COMPACT,
    GAUGE_ROI_ENLARGED,
    RANK_NUMBER_ROI_COMPACT,
    RANK_NUMBER_ROI_ENLARGED,
    read_precise_rank,
    read_rank,
    read_rank_gauge_fill,
    read_rank_tier,
)

# Issue #148(HDR無効化前fixture削除に伴う張り替え): 同一プレイ記録からのfixtureの
# ため全て同じランク値(38)になっている。read_rank()はコンパクト表示・拡大表示で
# バッジの実寸が異なるRANK_NUMBER_ROI_COMPACT/ENLARGEDを使う(Issue #143)ため、
# 表示サイズごとに別々のfixture・期待値で検証する。
#
# Issue #143で判明: 77は当初コンパクト表示として扱われていたが、実際には
# バッジが一回り大きく描画される拡大表示だった(RANK_ROIがコンパクト・拡大
# どちらの表示サイズも1つの領域でカバーする設計だったため、この誤分類に
# 長らく気付かなかった。RANK_NUMBER_ROI_COMPACT/ENLARGEDを追加して初めて
# 表面化した。scripts/inspect_gauge_fill.pyのコメントも参照)
EXPECTED_COMPACT = {
    "78_result_lose_with_rank_blue_hdr_off.png": 38,
    "81_result_lose_without_rank_blue_hdr_off.png": None,
    "84_result_win_with_rank_blue_hdr_off.png": 38,
}
EXPECTED_ENLARGED = {
    "77_result_win_with_rank_red_hdr_off.png": 38,
    "85_result_win_with_rank_enlarged_blue_hdr_off.png": 38,
}


@pytest.mark.slow
@requires_fixtures
@pytest.mark.parametrize("filename, expected", sorted(EXPECTED_COMPACT.items()))
def test_read_rank_compact(fixtures_dir, filename, expected):
    frame = cv2.imread(str(fixtures_dir / filename))
    assert frame is not None, f"failed to load {filename}"
    assert read_rank(frame, RANK_NUMBER_ROI_COMPACT) == expected


@pytest.mark.slow
@requires_fixtures
@pytest.mark.parametrize("filename, expected", sorted(EXPECTED_ENLARGED.items()))
def test_read_rank_enlarged(fixtures_dir, filename, expected):
    frame = cv2.imread(str(fixtures_dir / filename))
    assert frame is not None, f"failed to load {filename}"
    assert read_rank(frame, RANK_NUMBER_ROI_ENLARGED) == expected


# バッジはコンパクト表示(結果バナー確定直後、アニメーション開始前)と
# 拡大表示(アニメーション開始後〜暗転まで)でバーの実寸(幅・位置)が異なるため、
# GAUGE_ROI_COMPACT/GAUGE_ROI_ENLARGEDそれぞれに対応するfixtureで別々に検証する。
# 実測(read_rank_gauge_fill自体の出力、scripts/inspect_gauge_fill.py参照)。
# 塗りつぶし割合は人間の目視では正確な値を判定できない性質の指標のため、
# 既存のテストと同じ方針でこの関数自体の実測値をground truthとして採用している
# (このファイル・inspect_gauge_fill.pyのコメント参照。テスト正解データの
# 自己参照禁止の対象はタイミング系の期待値であり、この指標は元々対象外)
#
EXPECTED_GAUGE_FILL_COMPACT = {
    "78_result_lose_with_rank_blue_hdr_off.png": 0.18,
    "84_result_win_with_rank_blue_hdr_off.png": 0.47,
}

# Issue #147で拡大表示(ランク変動アニメーション中)のHDR無効化後fixtureを収集した。
# 77は当初コンパクト表示として誤って扱われていたが、実際には拡大表示だった
# ため、正しいGAUGE_ROI_ENLARGEDでの実測値に差し替えてこちらに移した(Issue #143、
# 上記EXPECTED_COMPACT/ENLARGEDのコメント参照)
EXPECTED_GAUGE_FILL_ENLARGED = {
    "77_result_win_with_rank_red_hdr_off.png": 0.69,
    "85_result_win_with_rank_enlarged_blue_hdr_off.png": 0.91,
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
    frame = cv2.imread(str(fixtures_dir / "78_result_lose_with_rank_blue_hdr_off.png"))
    assert frame is not None
    tier, precise = read_precise_rank(frame, GAUGE_ROI_COMPACT, RANK_NUMBER_ROI_COMPACT)
    assert tier == 38
    assert precise == pytest.approx(38.18, abs=0.02)


@pytest.mark.slow
@requires_fixtures
def test_read_precise_rank_returns_none_without_badge(fixtures_dir):
    frame = cv2.imread(str(fixtures_dir / "81_result_lose_without_rank_blue_hdr_off.png"))
    assert frame is not None
    assert read_precise_rank(frame, GAUGE_ROI_COMPACT, RANK_NUMBER_ROI_COMPACT) is None


@pytest.mark.slow
@requires_fixtures
def test_read_precise_rank_combines_tier_and_gauge_fill_enlarged(fixtures_dir):
    frame = cv2.imread(str(fixtures_dir / "85_result_win_with_rank_enlarged_blue_hdr_off.png"))
    assert frame is not None
    tier, precise = read_precise_rank(frame, GAUGE_ROI_ENLARGED, RANK_NUMBER_ROI_ENLARGED)
    assert tier == 38
    assert precise == pytest.approx(38.91, abs=0.02)


# Issue #73: read_rank_tier()の∞判定は既存の∞帯fixture全てで実データ検証できるが、
# S/A帯は結果バナー画面での参照fixtureが無いため未検証(rank_ocr.pyのモジュール
# docstring参照)。ここでは∞判定の回帰確認、およびバッジ非表示時にNoneを返す
# ことのみ確認する。read_rank_tier()はRANK_NUMBER_ROIではなくRANK_ROI(バッジ全体)
# を引き続き使うため、表示サイズを問わずEXPECTED_COMPACT/ENLARGED両方が対象
@pytest.mark.slow
@requires_fixtures
@pytest.mark.parametrize(
    "filename",
    sorted(name for name, expected in {**EXPECTED_COMPACT, **EXPECTED_ENLARGED}.items() if expected is not None),
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
