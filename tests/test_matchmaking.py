import cv2
import numpy as np
import pytest

from conftest import FIXTURES_DIR, list_screenshot_fixtures, requires_fixtures
from nss_tracker.detection.matchmaking import (
    LETTERBOX_BOTTOM_ROI,
    LETTERBOX_MIDDLE_ROI,
    LETTERBOX_TOP_ROI,
    VS_ROI,
    is_letterboxed,
    is_vs_screen,
    read_letterbox_brightness,
    read_vs_roi_hsv,
)

FRAME_WIDTH = 1920
FRAME_HEIGHT = 1080


def _make_letterboxed_frame(roi_hsv: tuple[float, float, float] | None = None) -> np.ndarray:
    """VS画面特有の構図(上下黒帯・中央は通常表示)を模した合成フレームを作る。

    Issue #144/#189: is_vs_screenの主判定がis_letterboxed()に移ったため、
    ロゴ色判定の真陽性テストもこの構図を満たすフレームで検証する必要がある
    (中央帯が暗いままだと、色が正しくても暗転扱いでFalseになるため)。
    """
    frame = np.zeros((FRAME_HEIGHT, FRAME_WIDTH, 3), dtype=np.uint8)
    mx1, my1, mx2, my2 = LETTERBOX_MIDDLE_ROI
    frame[my1:my2, mx1:mx2] = 128
    if roi_hsv is not None:
        h, s, v = roi_hsv
        x1, y1, x2, y2 = VS_ROI
        patch_hsv = np.full((y2 - y1, x2 - x1, 3), (h, s, v), dtype=np.uint8)
        frame[y1:y2, x1:x2] = cv2.cvtColor(patch_hsv, cv2.COLOR_HSV2BGR)
    return frame


def _make_frame_with_roi_hsv(h: float, s: float, v: float, roi: tuple[int, int, int, int] = VS_ROI) -> np.ndarray:
    """VS_ROI領域だけを指定したHSV色で塗った合成フレームを作る(レターボックス無し)。

    Issue #116: is_vs_screenの真陽性判定は、実測したライブパイプライン
    (FfmpegFrameReader)のHSV値を直接使った合成フレームで検証する
    (fixture画像/動画では検証できない理由はdetection/matchmaking.pyの
    モジュールdocstring参照)。read_vs_roi_hsv単体の検証用に、レターボックスを
    考慮しない最小限のフレームとして残す。
    """
    x1, y1, x2, y2 = roi
    frame = np.zeros((FRAME_HEIGHT, FRAME_WIDTH, 3), dtype=np.uint8)
    patch_hsv = np.full((y2 - y1, x2 - x1, 3), (h, s, v), dtype=np.uint8)
    frame[y1:y2, x1:x2] = cv2.cvtColor(patch_hsv, cv2.COLOR_HSV2BGR)
    return frame


# Issue #144/#189実測値(2026-07-31、実機ライブパイプライン経由、2試合分の
# VS画面表示区間の中心値。モジュールdocstring参照)
MEASURED_LIVE_PIPELINE_HSV_2026_07_31 = (83.0, 89.6, 223.0)


def test_is_vs_screen_true_for_measured_live_pipeline_value():
    frame = _make_letterboxed_frame(MEASURED_LIVE_PIPELINE_HSV_2026_07_31)
    assert is_vs_screen(frame)


def test_read_vs_roi_hsv_returns_approximately_the_set_color():
    frame = _make_frame_with_roi_hsv(*MEASURED_LIVE_PIPELINE_HSV_2026_07_31)
    h, s, v = read_vs_roi_hsv(frame)
    assert h == pytest.approx(MEASURED_LIVE_PIPELINE_HSV_2026_07_31[0], abs=1.0)
    assert s == pytest.approx(MEASURED_LIVE_PIPELINE_HSV_2026_07_31[1], abs=1.0)
    assert v == pytest.approx(MEASURED_LIVE_PIPELINE_HSV_2026_07_31[2], abs=1.0)


def test_is_vs_screen_false_without_letterbox_even_if_color_matches():
    """Issue #144/#189: レターボックス(is_letterboxed)を満たさない限り、
    ロゴの色が実測値どおりでもFalseになることを確認する(主判定がレターボックスに
    移ったことそのものの確認)。
    """
    frame = _make_frame_with_roi_hsv(*MEASURED_LIVE_PIPELINE_HSV_2026_07_31)
    assert not is_letterboxed(frame)
    assert not is_vs_screen(frame)


@pytest.mark.parametrize(
    "h,s,v",
    [
        (10.0, 89.6, 223.0),  # Hue下限の大きく外側(全く違う色)
        (150.0, 89.6, 223.0),  # Hue上限の大きく外側
        (83.0, 10.0, 223.0),  # Sat下限の大きく外側(ほぼ無彩色)
        (83.0, 89.6, 50.0),  # Val下限の大きく外側(暗すぎる)
    ],
)
def test_is_vs_screen_false_for_clearly_different_colors(h, s, v):
    """Issue #144/#189: 色判定は主判定(レターボックス)を補助する大まかな除外
    フィルタに格下げしたため、閾値ぎりぎりの境界ではなく「明らかに違う色」でのみ
    Falseになることを確認する(境界厳密さはもう要求しない)。
    """
    frame = _make_letterboxed_frame((h, s, v))
    assert not is_vs_screen(frame)


def test_is_letterboxed_true_for_black_bars_with_bright_middle():
    frame = _make_letterboxed_frame()
    assert is_letterboxed(frame)


def test_is_letterboxed_false_for_full_blackout():
    """試合間の暗転(画面全体が暗い)は、中央帯も暗いままなのでFalseになることを確認する。"""
    frame = np.zeros((FRAME_HEIGHT, FRAME_WIDTH, 3), dtype=np.uint8)
    assert not is_letterboxed(frame)


def test_is_letterboxed_false_when_top_or_bottom_not_black():
    """通常プレイ中(画面全体に背景・キャラクターが表示され、上下も暗くない)ではFalseになることを確認する。"""
    frame = np.full((FRAME_HEIGHT, FRAME_WIDTH, 3), 128, dtype=np.uint8)
    assert not is_letterboxed(frame)


def test_read_letterbox_brightness_returns_approximately_expected_values():
    frame = _make_letterboxed_frame()
    top, bottom, middle = read_letterbox_brightness(frame)
    assert top == pytest.approx(0.0, abs=1.0)
    assert bottom == pytest.approx(0.0, abs=1.0)
    assert middle == pytest.approx(128.0, abs=1.0)


def test_letterbox_rois_are_within_frame_bounds():
    for roi in (LETTERBOX_TOP_ROI, LETTERBOX_BOTTOM_ROI, LETTERBOX_MIDDLE_ROI):
        x1, y1, x2, y2 = roi
        assert 0 <= x1 < x2 <= FRAME_WIDTH
        assert 0 <= y1 < y2 <= FRAME_HEIGHT


# Issue #144/#189: fixtures/screenshots内の実際のVS画面スクリーンショット
# ("matching"を含むファイル名)。モジュールdocstring参照。レターボックス判定を
# 主判定にした後は、cv2.imread経由でもこれらが正しくTrueと判定されることを
# 確認済みのため、真陽性のregressionテストにそのまま使える
KNOWN_VS_SCREEN_SCREENSHOTS = [
    "72_matching_hdr_off_1.png",
    "73_matching_hdr_off_2.png",
    "82_matching_with_rank_4v3_hdr_off.png",
    "86_matching_with_rank_4v4_hdr_off.png",
    "87_matching_hdr_off_3.png",
]


@requires_fixtures
@pytest.mark.parametrize("filename", KNOWN_VS_SCREEN_SCREENSHOTS)
def test_is_vs_screen_true_for_matching_screenshots(filename):
    """実際のVS画面スクリーンショット(cv2.imread経由)でTrueになることを確認する。

    Issue #144/#189: レターボックス判定を主判定にしたことで、cv2.imreadの
    色変換経路の違い(Issue #116参照)があってもfixture画像での真陽性検証が
    できるようになった(モジュールdocstring参照)。
    """
    path = FIXTURES_DIR / filename
    if not path.exists():
        pytest.skip(f"{filename} が見つからない")
    frame = cv2.imread(str(path))
    assert frame is not None, f"failed to load {filename}"
    assert is_vs_screen(frame), f"{filename}でVS画面を検知できなかった"


@requires_fixtures
def test_is_vs_screen_false_for_non_vs_screenshots():
    """ロビー・試合中・結果バナー等、VS画面ではない静止画では常にFalseであることを確認する。

    KNOWN_VS_SCREEN_SCREENSHOTS(実際のVS画面)は除外し、それ以外の全件が
    非該当であることを誤検知防止の観点で確認する。
    """
    screenshots = list_screenshot_fixtures(FIXTURES_DIR)
    assert screenshots, "fixtures/screenshots/にpngが見つからない"
    for path in screenshots:
        if path.name in KNOWN_VS_SCREEN_SCREENSHOTS:
            continue
        frame = cv2.imread(str(path))
        assert frame is not None, f"failed to load {path.name}"
        assert not is_vs_screen(frame), f"{path.name}で誤検知した"
