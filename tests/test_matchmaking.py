import cv2
import numpy as np
import pytest

from conftest import FIXTURES_DIR, list_screenshot_fixtures, requires_fixtures
from nss_tracker.detection.matchmaking import VS_ROI, is_vs_screen, read_vs_roi_hsv


def _make_frame_with_roi_hsv(h: float, s: float, v: float, roi: tuple[int, int, int, int] = VS_ROI) -> np.ndarray:
    """VS_ROI領域だけを指定したHSV色で塗った合成フレームを作る。

    Issue #116: is_vs_screenの真陽性判定は、実測したライブパイプライン
    (FfmpegFrameReader)のHSV値を直接使った合成フレームで検証する
    (fixture画像/動画では検証できない理由はdetection/matchmaking.pyの
    モジュールdocstring参照)。
    """
    x1, y1, x2, y2 = roi
    frame = np.zeros((y2 + 10, x2 + 10, 3), dtype=np.uint8)
    patch_hsv = np.full((y2 - y1, x2 - x1, 3), (h, s, v), dtype=np.uint8)
    frame[y1:y2, x1:x2] = cv2.cvtColor(patch_hsv, cv2.COLOR_HSV2BGR)
    return frame


# Issue #116実測値(2026-07-24 23:26:02〜23:26:15、750サンプルの安定区間の中心値)
MEASURED_LIVE_PIPELINE_HSV = (99.6, 92.1, 237.0)


def test_is_vs_screen_true_for_measured_live_pipeline_value():
    frame = _make_frame_with_roi_hsv(*MEASURED_LIVE_PIPELINE_HSV)
    assert is_vs_screen(frame)


def test_read_vs_roi_hsv_returns_approximately_the_set_color():
    frame = _make_frame_with_roi_hsv(*MEASURED_LIVE_PIPELINE_HSV)
    h, s, v = read_vs_roi_hsv(frame)
    assert h == pytest.approx(MEASURED_LIVE_PIPELINE_HSV[0], abs=1.0)
    assert s == pytest.approx(MEASURED_LIVE_PIPELINE_HSV[1], abs=1.0)
    assert v == pytest.approx(MEASURED_LIVE_PIPELINE_HSV[2], abs=1.0)


def test_is_vs_screen_false_for_old_pre_issue116_calibrated_value():
    """Issue #68時点のcv2実測値(H84付近)は、Issue #116の閾値変更後はFalseになる
    (過去の閾値決定の経緯を一切考慮しないという方針転換そのものを確認する)。
    """
    frame = _make_frame_with_roi_hsv(84.0, 92.0, 237.0)
    assert not is_vs_screen(frame)


@pytest.mark.parametrize(
    "h,s,v",
    [
        (94.0, 92.0, 237.0),  # Hue下限のすぐ外
        (105.0, 92.0, 237.0),  # Hue上限のすぐ外
        (99.6, 87.0, 237.0),  # Sat下限のすぐ外
        (99.6, 99.0, 237.0),  # Sat上限のすぐ外
        (99.6, 92.0, 229.0),  # Val下限のすぐ外
    ],
)
def test_is_vs_screen_false_just_outside_thresholds(h, s, v):
    frame = _make_frame_with_roi_hsv(h, s, v)
    assert not is_vs_screen(frame)


@requires_fixtures
def test_is_vs_screen_false_for_non_vs_screenshots():
    """ロビー・試合中・結果バナー等、VS画面ではない静止画では常にFalseであることを確認する。

    Issue #116: fixtures/screenshots内にis_vs_screenの真陽性を検証できる画像は
    無い(cv2.imreadの色変換経路がライブパイプラインと異なるため、モジュール
    docstring参照)。そのため全件が非該当のはず、という誤検知防止の観点でのみ使う。
    """
    screenshots = list_screenshot_fixtures(FIXTURES_DIR)
    assert screenshots, "fixtures/screenshots/にpngが見つからない"
    for path in screenshots:
        frame = cv2.imread(str(path))
        assert frame is not None, f"failed to load {path.name}"
        assert not is_vs_screen(frame), f"{path.name}で誤検知した"
