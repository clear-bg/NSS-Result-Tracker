"""Issue #470: OBS Virtual Camera停止中のプレースホルダー画像の検知と、その状態監視のテスト。

実機のプレースホルダー画像はOBSのロゴを含むためリポジトリには置かず、
判定に使う各領域を実測色で塗った合成フレームで検証する。
"""

import cv2
import numpy as np
import pytest

from conftest import list_screenshot_fixtures, requires_fixtures
from nss_tracker import virtual_camera_status
from nss_tracker.detection.virtual_camera import PLACEHOLDER_SAMPLES, is_virtual_camera_placeholder
from nss_tracker.virtual_camera_status import VirtualCameraMonitor


def _placeholder_frame() -> np.ndarray:
    frame = np.zeros((1080, 1920, 3), dtype=np.uint8)
    for x1, y1, x2, y2, b, g, r in PLACEHOLDER_SAMPLES:
        frame[y1:y2, x1:x2] = (b, g, r)
    return frame


def _game_frame() -> np.ndarray:
    return np.full((1080, 1920, 3), (40, 160, 60), dtype=np.uint8)


@pytest.fixture(autouse=True)
def _reset_status():
    virtual_camera_status.reset_status()
    yield
    virtual_camera_status.reset_status()


def test_placeholder_frame_is_detected():
    assert is_virtual_camera_placeholder(_placeholder_frame())


def test_placeholder_detection_tolerates_small_color_shift():
    frame = _placeholder_frame().astype(np.int16) + 8
    assert is_virtual_camera_placeholder(np.clip(frame, 0, 255).astype(np.uint8))


def test_frame_with_one_region_off_is_not_placeholder():
    """6箇所すべてが一致したときだけTrue(1箇所でも違えばゲーム画面とみなす)。"""
    frame = _placeholder_frame()
    x1, y1, x2, y2, *_ = PLACEHOLDER_SAMPLES[-1]
    frame[y1:y2, x1:x2] = (0, 200, 0)
    assert not is_virtual_camera_placeholder(frame)


def test_undersized_frame_is_not_placeholder():
    assert not is_virtual_camera_placeholder(np.zeros((10, 10, 3), dtype=np.uint8))


@requires_fixtures
def test_game_screenshots_are_never_placeholder(fixtures_dir):
    paths = list_screenshot_fixtures(fixtures_dir)
    assert [p.name for p in paths if is_virtual_camera_placeholder(cv2.imread(str(p)))] == []


def test_status_stays_waiting_until_placeholder_continues(caplog):
    monitor = VirtualCameraMonitor(inactive_after_seconds=5.0)
    with caplog.at_level("WARNING", logger="nss_tracker.virtual_camera"):
        monitor.observe(_placeholder_frame(), 0.0)
        monitor.observe(_placeholder_frame(), 4.9)
    assert virtual_camera_status.get_status() == "waiting"
    assert caplog.records == []


def test_warning_after_placeholder_continues_and_repeats(caplog):
    monitor = VirtualCameraMonitor(inactive_after_seconds=5.0, repeat_warning_seconds=30.0)
    with caplog.at_level("WARNING", logger="nss_tracker.virtual_camera"):
        for now in (0.0, 5.0, 6.0, 34.9, 35.0):
            monitor.observe(_placeholder_frame(), now)
    assert virtual_camera_status.get_status() == "inactive"
    warnings = [record for record in caplog.records if record.levelname == "WARNING"]
    # 5秒経過時点で1回、そこから30秒後に1回(間のフレームでは出し直さない)
    assert len(warnings) == 2
    assert "仮想カメラ開始" in warnings[0].getMessage()


def test_real_frame_marks_active_and_resets_placeholder_timer(caplog):
    monitor = VirtualCameraMonitor(inactive_after_seconds=5.0)
    with caplog.at_level("INFO", logger="nss_tracker.virtual_camera"):
        monitor.observe(_placeholder_frame(), 0.0)
        monitor.observe(_placeholder_frame(), 5.0)
        assert virtual_camera_status.get_status() == "inactive"
        monitor.observe(_game_frame(), 6.0)
        assert virtual_camera_status.get_status() == "active"
        # 映像が来た後は、プレースホルダーが再び5秒続くまで警告しない
        monitor.observe(_placeholder_frame(), 7.0)
        monitor.observe(_placeholder_frame(), 11.9)
    assert virtual_camera_status.get_status() == "active"
    assert [r.levelname for r in caplog.records] == ["WARNING", "INFO"]


def test_active_info_is_logged_only_once(caplog):
    monitor = VirtualCameraMonitor()
    with caplog.at_level("INFO", logger="nss_tracker.virtual_camera"):
        monitor.observe(_game_frame(), 0.0)
        monitor.observe(_game_frame(), 1.0)
    assert virtual_camera_status.get_status() == "active"
    assert len(caplog.records) == 1
