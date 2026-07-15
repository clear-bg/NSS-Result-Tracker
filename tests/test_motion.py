import cv2
import pytest

from conftest import requires_video_fixtures
from nss_tracker.detection.motion import StabilityMonitor

VIDEO_NAME = "試合結果付き動画.mp4"
TARGET_SIZE = (1920, 1080)
RANK_ROI = (90, 600, 420, 930)

# scripts/inspect_video_timeline.py で実測したフレーム区間(1280x720を1920x1080に
# リサイズした場合のフレーム番号)。
# - GAMEPLAY_RANGE: 通常プレイ中で常に映像が動いている区間
# - TRANSITION_RANGE: 結果バナー・ランクバッジがフェードインしている途中の区間
# - STABLE_RANGE: バナー表示後、ランク値が確定して静止している区間
GAMEPLAY_RANGE = range(700, 720)
TRANSITION_RANGE = range(738, 750)
STABLE_RANGE = range(760, 810)


def _read_frames(path):
    cap = cv2.VideoCapture(str(path))
    try:
        while True:
            ok, frame = cap.read()
            if not ok:
                break
            yield cv2.resize(frame, TARGET_SIZE)
    finally:
        cap.release()


@requires_video_fixtures
def test_stability_monitor_distinguishes_stable_from_changing(videos_dir):
    video_path = videos_dir / VIDEO_NAME
    monitor = StabilityMonitor(roi=RANK_ROI)

    stable_during_gameplay = False
    stable_during_transition = False
    stable_during_result = False

    for idx, frame in enumerate(_read_frames(video_path)):
        monitor.update(frame)
        if idx in GAMEPLAY_RANGE and monitor.is_stable:
            stable_during_gameplay = True
        if idx in TRANSITION_RANGE and monitor.is_stable:
            stable_during_transition = True
        if idx in STABLE_RANGE and monitor.is_stable:
            stable_during_result = True

    assert not stable_during_gameplay, "プレイ中の映像が動いている区間でis_stableがTrueになった"
    assert not stable_during_transition, "バナー/バッジのフェードイン中にis_stableがTrueになった"
    assert stable_during_result, "ランク値が確定しているはずの区間でis_stableがTrueにならなかった"


def test_stability_monitor_requires_consecutive_stable_frames():
    import numpy as np

    frame_a = np.zeros((10, 10, 3), dtype=np.uint8)
    frame_b = frame_a.copy()
    frame_b[:] = 255

    monitor = StabilityMonitor(roi=(0, 0, 10, 10), diff_threshold=1.0, stable_frames_required=3)
    monitor.update(frame_a)
    assert not monitor.is_stable

    monitor.update(frame_b)  # 大きな差分 → streakリセット
    assert not monitor.is_stable

    monitor.update(frame_b)
    monitor.update(frame_b)
    assert not monitor.is_stable  # まだ3回連続に届いていない

    monitor.update(frame_b)
    assert monitor.is_stable  # 変化なしが3回連続した


def test_stability_monitor_reset():
    import numpy as np

    frame = np.zeros((10, 10, 3), dtype=np.uint8)
    monitor = StabilityMonitor(roi=(0, 0, 10, 10), diff_threshold=1.0, stable_frames_required=2)
    monitor.update(frame)
    monitor.update(frame)
    monitor.update(frame)
    assert monitor.is_stable

    monitor.reset()
    assert not monitor.is_stable
