import json
from pathlib import Path

import cv2
import pytest

from conftest import requires_video_fixtures
from nss_tracker.detection.motion import StabilityMonitor
from nss_tracker.detection.rank_ocr import RANK_ROI
from nss_tracker.state.match_state import MatchStateMachine

TARGET_SIZE = (1920, 1080)
METADATA_FILENAME = "metadata.json"


def _load_metadata(videos_dir: Path) -> dict:
    return json.loads((videos_dir / METADATA_FILENAME).read_text(encoding="utf-8"))


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
    """動画を最後まで流し、状態が切り替わったフレーム番号とMatchResultを収集する。"""
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

    state_change_frames: dict[str, int] = {}
    results = []
    prev_state = machine.current_state
    for idx, frame in enumerate(_read_frames(path)):
        result = machine.process_frame(frame)
        if machine.current_state != prev_state:
            transition = f"{prev_state}->{machine.current_state}"
            state_change_frames.setdefault(transition, idx)
            prev_state = machine.current_state
        if result is not None:
            results.append(result)
    return results, state_change_frames


@pytest.mark.slow
@requires_video_fixtures
def test_match_state_machine_matches_expected_metadata(videos_dir):
    metadata = _load_metadata(videos_dir)
    videos = [(videos_dir / name, expected) for name, expected in metadata.items() if (videos_dir / name).is_file()]
    assert videos, f"{METADATA_FILENAME}に記載の動画がfixtures/videos/に見つからない"

    for path, expected in videos:
        results, state_change_frames = _run_state_machine(path)

        assert len(results) == 1, f"{path.name}: 検知された試合数が{len(results)}件(期待は1件)"
        match = results[0]

        assert match.result == expected["expected_result"], (
            f"{path.name}: result 期待={expected['expected_result']} 実際={match.result}"
        )
        assert match.rank_before == expected["expected_rank_before"], (
            f"{path.name}: rank_before 期待={expected['expected_rank_before']} 実際={match.rank_before}"
        )
        assert match.rank_after == expected["expected_rank_after"], (
            f"{path.name}: rank_after 期待={expected['expected_rank_after']} 実際={match.rank_after}"
        )
        assert match.league_changed == expected["expected_league_changed"], (
            f"{path.name}: league_changed 期待={expected['expected_league_changed']} 実際={match.league_changed}"
        )

        # フレーム範囲は動画を見ながら手動で確認した値のみ検証する(metadata.jsonでnullの間は未検証)
        banner_range = expected["banner_confirmed_frame_range"]
        if banner_range is not None:
            banner_frame = state_change_frames.get("watching->tracking_rank")
            low, high = banner_range
            assert banner_frame is not None and low <= banner_frame <= high, (
                f"{path.name}: banner確定フレーム={banner_frame} 期待範囲={banner_range}"
            )

        result_range = expected["match_result_frame_range"]
        if result_range is not None:
            result_frame = state_change_frames.get("tracking_rank->cooldown")
            low, high = result_range
            assert result_frame is not None and low <= result_frame <= high, (
                f"{path.name}: 結果確定フレーム={result_frame} 期待範囲={result_range}"
            )
