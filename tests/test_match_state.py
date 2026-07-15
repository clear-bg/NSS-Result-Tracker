import json
from pathlib import Path

import cv2
import numpy as np
import pytest

import nss_tracker.state.match_state as match_state_module
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


def _assert_rank_matches_tier(rank: float | None, expected_tier: int, label: str) -> None:
    """rankはtier(整数)+ゲージの溜まり具合(0.0以上1.0以下)の小数値なので、
    期待する帯番号に対しておおよそその範囲に収まっているかで検証する
    (ゲージの正確な溜まり具合はmetadata.jsonでは正解データ化していない)。
    """
    assert rank is not None, f"{label}: Noneだった(期待は帯{expected_tier})"
    assert expected_tier <= rank <= expected_tier + 1.0, (
        f"{label}: 期待帯={expected_tier} 実際={rank}"
    )


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
        _assert_rank_matches_tier(match.rank_before, expected["expected_rank_before"], f"{path.name}: rank_before")
        _assert_rank_matches_tier(match.rank_after, expected["expected_rank_after"], f"{path.name}: rank_after")
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


def test_goal_detected_during_watching_is_attached_to_match_result(monkeypatch):
    """ゴール検知の統合ロジック(バッファリング→試合終了時にMatchResultへ payoutされる)を
    実映像に依存せず検証する。個々の検知関数(is_goal_event等)は
    tests/test_goal.py・tests/test_banner.py等で別途検証済みのため、ここではモックする。
    """
    calls = {"n": 0}

    def fake_is_goal_event(frame):
        # 最初の2フレームだけゴールバナーが出ているとみなす
        return calls["n"] < 2

    def fake_classify_banner(frame):
        n = calls["n"]
        calls["n"] += 1
        return None if n < 5 else "win"

    monkeypatch.setattr(match_state_module, "is_goal_event", fake_is_goal_event)
    monkeypatch.setattr(match_state_module, "read_scorer_name", lambda frame: "Alice")
    monkeypatch.setattr(match_state_module, "read_assist_name", lambda frame: None)
    monkeypatch.setattr(match_state_module, "classify_banner", fake_classify_banner)
    monkeypatch.setattr(match_state_module, "read_precise_rank", lambda frame: (10, 10.0))
    monkeypatch.setattr(match_state_module, "is_league_change_screen", lambda frame: False)

    machine = MatchStateMachine(
        banner_confirm_frames=2,
        goal_confirm_frames=2,
        league_change_grace_frames=1,
        rank_stability_monitor=StabilityMonitor(roi=(0, 0, 5, 5), stable_frames_required=1),
    )

    frame = np.zeros((10, 10, 3), dtype=np.uint8)
    result = None
    for _ in range(15):
        result = machine.process_frame(frame)
        if result is not None:
            break

    assert result is not None, "MatchResultが確定しなかった"
    assert len(result.goals) == 1
    assert result.goals[0].scorer_name == "Alice"
    assert result.goals[0].assist_name is None


def test_goal_banner_shown_continuously_records_only_one_goal(monkeypatch):
    """同じゴールバナーが表示され続けている間、複数回記録されない(デバウンス)ことを確認する。"""
    monkeypatch.setattr(match_state_module, "is_goal_event", lambda frame: True)
    monkeypatch.setattr(match_state_module, "read_scorer_name", lambda frame: "Alice")
    monkeypatch.setattr(match_state_module, "read_assist_name", lambda frame: None)
    monkeypatch.setattr(match_state_module, "classify_banner", lambda frame: None)

    machine = MatchStateMachine(goal_confirm_frames=2)
    frame = np.zeros((10, 10, 3), dtype=np.uint8)
    for _ in range(10):
        machine.process_frame(frame)

    assert len(machine._pending_goals) == 1
