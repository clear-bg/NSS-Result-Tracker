import cv2
import pytest

from conftest import list_screenshot_fixtures, requires_fixtures
from nss_tracker.detection.matchmaking import VS_HUE_RANGE, VS_SAT_RANGE, VS_VAL_MIN, is_vs_screen, read_vs_roi_hsv

# マッチング完了(VS画面)を捉えているfixture。11/12/14/15は本来の目的である
# 「マッチング(ランク有無・チーム別)」画像。70/71はS/A帯バッジのOCR確認用に
# 集めた画像だが、たまたまVS画面そのものを写しているため合わせて真陽性として扱う
VS_SCREEN_SCREENSHOTS = {
    "11_matching_with_rank_blue.png",
    "12_matching_without_rank_blue.png",
    "14_matching_with_rank_red.png",
    "15_matching_without_rank_red.png",
    "70_rank_tier_s.png",
    "71_rank_tier_a.png",
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
