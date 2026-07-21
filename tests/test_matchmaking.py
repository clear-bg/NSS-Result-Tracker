import cv2
import pytest

from conftest import list_screenshot_fixtures, requires_fixtures
from nss_tracker.detection.matchmaking import VS_HUE_RANGE, VS_SAT_RANGE, VS_VAL_MIN, is_vs_screen, read_vs_roi_hsv

# マッチング完了(VS画面)を捉えているfixture。
#
# Issue #68: 11/12/14/15(「マッチング ランク有無・チーム別」本来の目的の画像)・
# 70/71(S/A帯バッジOCR確認用、たまたまVS画面を写していた画像)は、いずれも
# 2026-07-21のHDR無効化後は再現しない色(旧VS_HUE_RANGE=62-77等)で撮影されて
# いたため、is_vs_screenの真陽性テストからは外した(画像自体は元の目的の
# fixtureとして引き続き使う)。代わりに同日のHDR無効化後ローカル録画から
# 切り出した72/73番を使う(detection/matchmaking.pyのモジュールdocstring参照)
VS_SCREEN_SCREENSHOTS = {
    "72_matching_hdr_off_1.png",
    "73_matching_hdr_off_2.png",
}


@requires_fixtures
@pytest.mark.parametrize("filename", sorted(VS_SCREEN_SCREENSHOTS))
def test_is_vs_screen_true_for_vs_screenshots(fixtures_dir, filename):
    frame = cv2.imread(str(fixtures_dir / filename))
    assert frame is not None, f"failed to load {filename}"
    assert is_vs_screen(frame), f"{filename}でVS画面を検知できなかった"


@requires_fixtures
@pytest.mark.parametrize("filename", sorted(VS_SCREEN_SCREENSHOTS))
def test_read_vs_roi_hsv_matches_is_vs_screen_thresholds(fixtures_dir, filename):
    """read_vs_roi_hsv(診断用、Issue #68)が返す値が、is_vs_screenの閾値判定と
    整合していることを確認する(既知のVS画面fixtureなので閾値内のはず)。
    """
    frame = cv2.imread(str(fixtures_dir / filename))
    assert frame is not None, f"failed to load {filename}"
    h, s, v = read_vs_roi_hsv(frame)
    assert VS_HUE_RANGE[0] <= h <= VS_HUE_RANGE[1]
    assert VS_SAT_RANGE[0] <= s <= VS_SAT_RANGE[1]
    assert v >= VS_VAL_MIN


@requires_fixtures
def test_is_vs_screen_false_for_non_vs_screenshots(fixtures_dir):
    """ロビー・試合中・結果バナー等、VS画面ではない静止画では常にFalseであることを確認する。

    fixtures/screenshots/*.pngのうちVS_SCREEN_SCREENSHOTS以外は
    VS画面を含まないため、全件が非該当のはず。
    """
    screenshots = list_screenshot_fixtures(fixtures_dir)
    assert screenshots, "fixtures/screenshots/にpngが見つからない"
    for path in screenshots:
        if path.name in VS_SCREEN_SCREENSHOTS:
            continue
        frame = cv2.imread(str(path))
        assert frame is not None, f"failed to load {path.name}"
        assert not is_vs_screen(frame), f"{path.name}で誤検知した"
