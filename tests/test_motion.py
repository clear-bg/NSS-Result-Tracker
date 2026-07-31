import cv2
import pytest

from conftest import requires_video_fixtures
from nss_tracker.detection.motion import StabilityMonitor, find_confirmed_value, is_full_blackout

VIDEO_NAME = "28_win_red_1-0_hdr_off.mp4"
TARGET_SIZE = (1920, 1080)
RANK_ROI = (90, 600, 420, 930)

# フレーム間差分の実測値と実際の映像を目視確認して決めたフレーム区間
# (StabilityMonitor自体の判定結果をそのまま転記したものではない、60fps)。
# - GAMEPLAY_RANGE: 通常プレイ中で常に映像が動いている区間
# - TRANSITION_RANGE: 暗転明け、結果バナーがフェードインしている途中の区間
# - STABLE_RANGE: バナー表示後、ランク値が確定して静止している区間
GAMEPLAY_RANGE = range(0, 300)
TRANSITION_RANGE = range(1070, 1086)
STABLE_RANGE = range(1150, 1290)


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


def test_find_confirmed_value_requires_consecutive_run():
    assert find_confirmed_value(["lose", "lose", "win", "win", "win"], min_run_length=3) == "win"


def test_find_confirmed_value_none_breaks_the_run():
    assert find_confirmed_value(["win", "win", None, "win", "win"], min_run_length=3) is None


def test_find_confirmed_value_returns_none_when_nothing_confirmed():
    assert find_confirmed_value([None, "lose", "win", None], min_run_length=3) is None


# Issue #209: 実際の映像を目視確認して決めたフレーム区間(is_full_blackout自体の
# 判定結果をそのまま転記したものではない、60fps)。リーグ昇格試合で、ランク確定
# 演出が完全に終わった直後・マッチング画面に戻る前に一度全画面が真っ黒になる
# 区間(detection/motion.pyのモジュールdocstring参照)。
BLACKOUT_VIDEO = "30_win_blue_league_up_hdr_off.mp4"
BLACKOUT_NOT_YET_RANGE = range(2000, 2340)
BLACKOUT_RANGE = range(2365, 2385)
BLACKOUT_AFTER_RANGE = range(2410, 2460)


def _read_frames(path):
    cap = cv2.VideoCapture(str(path))
    try:
        while True:
            ok, frame = cap.read()
            if not ok:
                break
            yield frame
    finally:
        cap.release()


@requires_video_fixtures
def test_is_full_blackout_detects_post_result_blackout(videos_dir):
    video_path = videos_dir / BLACKOUT_VIDEO
    if not video_path.is_file():
        pytest.skip(f"{BLACKOUT_VIDEO} が見つからない")

    detected_blackout = False
    false_positive_frame = None
    for idx, frame in enumerate(_read_frames(video_path)):
        result = is_full_blackout(frame)
        if idx in BLACKOUT_RANGE:
            detected_blackout = detected_blackout or result
        elif idx in BLACKOUT_NOT_YET_RANGE or idx in BLACKOUT_AFTER_RANGE:
            if result and false_positive_frame is None:
                false_positive_frame = idx

    assert detected_blackout, "暗転区間で一度もTrueにならなかった"
    assert false_positive_frame is None, f"暗転区間外(フレーム{false_positive_frame})で誤検知した"


def test_is_full_blackout_true_for_synthetic_black_frame():
    import numpy as np

    black_frame = np.zeros((10, 10, 3), dtype=np.uint8)
    assert is_full_blackout(black_frame)


def test_is_full_blackout_false_for_synthetic_bright_frame():
    import numpy as np

    bright_frame = np.full((10, 10, 3), 200, dtype=np.uint8)
    assert not is_full_blackout(bright_frame)
