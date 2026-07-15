import re
from pathlib import Path

import cv2
import pytest

from conftest import requires_video_fixtures
from nss_tracker.detection.motion import StabilityMonitor
from nss_tracker.detection.rank_ocr import RANK_ROI
from nss_tracker.state.match_state import MatchStateMachine

TARGET_SIZE = (1920, 1080)
NAME_PATTERN = re.compile(r"^\d+_(win|lose)_(blue|red)_?")


def _read_frames(path: Path):
    cap = cv2.VideoCapture(str(path))
    try:
        while True:
            ok, frame = cap.read()
            if not ok:
                return
            if frame.shape[1::-1] != TARGET_SIZE:
                frame = cv2.resize(frame, TARGET_SIZE)
            yield frame
    finally:
        cap.release()


def _run_state_machine(path: Path):
    cap = cv2.VideoCapture(str(path))
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    cap.release()

    confirm_frames = round(fps * 1.0)
    machine = MatchStateMachine(
        banner_confirm_frames=confirm_frames,
        banner_absence_confirm_frames=confirm_frames,
        league_change_grace_frames=round(fps * 5.0),
        rank_stability_monitor=StabilityMonitor(roi=RANK_ROI, stable_frames_required=round(fps * 0.5)),
    )
    results = []
    for frame in _read_frames(path):
        result = machine.process_frame(frame)
        if result is not None:
            results.append(result)
    return results


@pytest.mark.slow
@requires_video_fixtures
def test_original_clip_records_a_single_win(videos_dir):
    results = _run_state_machine(videos_dir / "試合結果付き動画.mp4")

    assert len(results) == 1
    match = results[0]
    assert match.result == "win"
    assert match.rank_before == 39
    assert match.rank_after == 39
    assert match.league_changed is None


@pytest.mark.slow
@requires_video_fixtures
def test_real_stream_clips_record_matching_result(videos_dir):
    videos = [
        (path, m.group(1))
        for path in sorted(videos_dir.glob("*.mp4"))
        if (m := NAME_PATTERN.match(path.name))
    ]
    assert videos, "命名規則に沿った動画がfixtures/videos/に見つからない"

    for path, expected in videos:
        results = _run_state_machine(path)
        assert len(results) == 1, f"{path.name}: 検知された試合数が{len(results)}件(期待は1件)"
        match = results[0]
        assert match.result == expected, f"{path.name}: 期待={expected} 実際={match.result}"
        assert match.rank_before is not None, f"{path.name}: rank_beforeがNone(OCR失敗)"
        assert match.rank_after is not None, f"{path.name}: rank_afterがNone(OCR失敗)"
        if match.rank_after > match.rank_before:
            assert match.league_changed == "up", f"{path.name}: ランクが増加したのにleague_changedが'up'でない"
        elif match.rank_after < match.rank_before:
            assert match.league_changed == "down", f"{path.name}: ランクが減少したのにleague_changedが'down'でない"
        else:
            assert match.league_changed is None, f"{path.name}: ランクが変化していないのにleague_changedが設定されている"
