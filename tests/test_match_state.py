import json
from pathlib import Path

import cv2
import numpy as np
import pytest

import nss_tracker.state.match_state as match_state_module
from conftest import requires_video_fixtures
from nss_tracker.detection.motion import StabilityMonitor
from nss_tracker.detection.rank_ocr import GAUGE_ROI_COMPACT, GAUGE_ROI_ENLARGED, RANK_ROI
from nss_tracker.detection.vs_rank import SlotRank
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
    """動画を最後まで流し、状態が切り替わったフレーム番号とMatchResultを収集する。

    main.pyの_make_match_state_machineと同じ設定でMatchStateMachineを構築する
    (Issue #76: 「試合終了」バナーを検知できた動画は短いデバウンス(1.0秒)、
    できなかった動画(21_goal_event_false_positive_*.mp4)は長いデバウンス(2.0秒)に
    自動的に切り替わる。個別のfixtureごとに閾値を指定する必要はない)。
    """
    cap = cv2.VideoCapture(str(path))
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    cap.release()

    confirm_frames = round(fps * 1.0)
    banner_confirm_frames = round(fps * 2.0)
    # Issue #190: main.pyの_make_match_state_machineと同じく1フレームに固定
    match_end_confirm_frames = 1
    machine = MatchStateMachine(
        banner_confirm_frames=banner_confirm_frames,
        banner_confirm_frames_after_match_end=confirm_frames,
        banner_absence_confirm_frames=confirm_frames,
        vs_screen_confirm_frames=confirm_frames,
        match_end_confirm_frames=match_end_confirm_frames,
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


def _assert_rank_matches_tier(rank: float | None, expected_tier: int | None, label: str) -> None:
    """rankはtier(整数)+ゲージの溜まり具合(0.0以上1.0以下)の小数値なので、
    期待する帯番号に対しておおよそその範囲に収まっているかで検証する
    (ゲージの正確な溜まり具合はmetadata.jsonでは正解データ化していない)。

    expected_tierがNoneの場合(結果画面にランクバッジ自体が表示されない試合)は、
    rankもNoneのままであることを検証する。
    """
    if expected_tier is None:
        assert rank is None, f"{label}: 期待はNone(ランクバッジ非表示)だが実際={rank}"
        return
    assert rank is not None, f"{label}: Noneだった(期待は帯{expected_tier})"
    assert expected_tier <= rank <= expected_tier + 1.0, (
        f"{label}: 期待帯={expected_tier} 実際={rank}"
    )


@pytest.mark.slow
@requires_video_fixtures
def test_match_state_machine_matches_expected_metadata(videos_dir, monkeypatch):
    monkeypatch.setenv("GOAL_RECORD_MODE", "allowlist")
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

    frame_idxはテストループ側で1フレームごとに進める(Issue #67の修正により
    is_goal_event=True中はclassify_bannerが呼ばれなくなったため、classify_banner
    呼び出し回数に依存したフレーム進行のカウントはできない)。
    """
    frame_idx = {"n": 0}

    def fake_is_goal_event(frame):
        # 最初の2フレームだけゴールバナーが出ているとみなす
        return frame_idx["n"] < 2

    def fake_classify_banner(frame):
        return None if frame_idx["n"] < 5 else "win"

    monkeypatch.setattr(match_state_module, "is_goal_event", fake_is_goal_event)
    monkeypatch.setattr(match_state_module, "confirm_goal_text", lambda frame: True)
    monkeypatch.setattr(match_state_module, "read_scorer_name", lambda frame: ("Alice", 0.95))
    monkeypatch.setattr(match_state_module, "read_assist_name", lambda frame: None)
    monkeypatch.setattr(match_state_module, "classify_banner", fake_classify_banner)
    monkeypatch.setattr(match_state_module, "read_precise_rank", lambda frame, gauge_roi, rank_number_roi: (10, 10.0))
    monkeypatch.setattr(match_state_module, "read_rank_gauge_fill", lambda frame, roi: 0.0)
    monkeypatch.setattr(match_state_module, "is_league_change_screen", lambda frame: False)
    monkeypatch.setattr(match_state_module, "is_demotion_label_candidate", lambda frame: False)
    monkeypatch.setattr(match_state_module, "is_vs_screen", lambda frame: False)
    monkeypatch.setattr(match_state_module, "is_match_end_screen", lambda frame: False)
    monkeypatch.setattr(match_state_module, "is_full_blackout", lambda frame: False)
    monkeypatch.setenv("GOAL_RECORD_MODE", "allowlist")

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
        frame_idx["n"] += 1
        if result is not None:
            break

    assert result is not None, "MatchResultが確定しなかった"
    assert len(result.goals) == 1
    assert result.goals[0].scorer_name == "Alice"
    assert result.goals[0].assist_name is None


def test_goal_detection_logs_scorer_and_assist_at_info_level(monkeypatch, caplog):
    """Issue #86: ゴール検知した瞬間に、許可リストの判定結果によらず得点者・
    アシスト名と記録対象かどうかの見込みをINFOレベルで出すことを確認する。
    実際にDBへ記録するかどうかの判定は永続化層のままで、ここではログのみ検証する。
    """
    frame_idx = {"n": 0}

    monkeypatch.setattr(match_state_module, "is_goal_event", lambda frame: frame_idx["n"] < 2)
    monkeypatch.setattr(match_state_module, "confirm_goal_text", lambda frame: True)
    monkeypatch.setattr(match_state_module, "read_scorer_name", lambda frame: ("Alice", 0.95))
    monkeypatch.setattr(match_state_module, "read_assist_name", lambda frame: ("Bob", 0.90))
    monkeypatch.setattr(match_state_module, "classify_banner", lambda frame: None)
    monkeypatch.setattr(match_state_module, "is_league_change_screen", lambda frame: False)
    monkeypatch.setattr(match_state_module, "is_demotion_label_candidate", lambda frame: False)
    monkeypatch.setattr(match_state_module, "is_vs_screen", lambda frame: False)
    monkeypatch.setattr(match_state_module, "is_match_end_screen", lambda frame: False)
    monkeypatch.setattr(match_state_module, "is_full_blackout", lambda frame: False)
    monkeypatch.setenv("ALLOWED_PLAYERS", "Alice")
    monkeypatch.setenv("GOAL_RECORD_MODE", "allowlist")

    machine = MatchStateMachine(goal_confirm_frames=2)
    frame = np.zeros((10, 10, 3), dtype=np.uint8)

    with caplog.at_level("INFO", logger="nss_tracker.state"):
        for _ in range(3):
            machine.process_frame(frame)
            frame_idx["n"] += 1

    assert "ゴール検知: scorer=Alice assist=Bob (記録対象)" in caplog.text


def test_goal_detection_logs_not_recorded_when_outside_allowlist(monkeypatch, caplog):
    """得点者・アシストとも許可リストに無い場合、INFOログには実名を出しつつ
    「記録対象外」と分かるようにする(実際に記録しないこと自体は永続化層の責務)。
    """
    frame_idx = {"n": 0}

    monkeypatch.setattr(match_state_module, "is_goal_event", lambda frame: frame_idx["n"] < 2)
    monkeypatch.setattr(match_state_module, "confirm_goal_text", lambda frame: True)
    monkeypatch.setattr(match_state_module, "read_scorer_name", lambda frame: ("Charlie", 0.95))
    monkeypatch.setattr(match_state_module, "read_assist_name", lambda frame: None)
    monkeypatch.setattr(match_state_module, "classify_banner", lambda frame: None)
    monkeypatch.setattr(match_state_module, "is_league_change_screen", lambda frame: False)
    monkeypatch.setattr(match_state_module, "is_demotion_label_candidate", lambda frame: False)
    monkeypatch.setattr(match_state_module, "is_vs_screen", lambda frame: False)
    monkeypatch.setattr(match_state_module, "is_match_end_screen", lambda frame: False)
    monkeypatch.setattr(match_state_module, "is_full_blackout", lambda frame: False)
    monkeypatch.setenv("ALLOWED_PLAYERS", "Alice")
    monkeypatch.setenv("GOAL_RECORD_MODE", "allowlist")

    machine = MatchStateMachine(goal_confirm_frames=2)
    frame = np.zeros((10, 10, 3), dtype=np.uint8)

    with caplog.at_level("INFO", logger="nss_tracker.state"):
        for _ in range(3):
            machine.process_frame(frame)
            frame_idx["n"] += 1

    assert "ゴール検知: scorer=Charlie assist=None (許可リスト外のため記録対象外)" in caplog.text


def test_goal_detection_logs_always_recorded_in_all_mode(monkeypatch, caplog):
    """Issue #88: GOAL_RECORD_MODE=allの場合、許可リストに関係なく常に
    「記録対象」と表示することを確認する。
    """
    frame_idx = {"n": 0}

    monkeypatch.setattr(match_state_module, "is_goal_event", lambda frame: frame_idx["n"] < 2)
    monkeypatch.setattr(match_state_module, "confirm_goal_text", lambda frame: True)
    monkeypatch.setattr(match_state_module, "read_scorer_name", lambda frame: ("Charlie", 0.95))
    monkeypatch.setattr(match_state_module, "read_assist_name", lambda frame: None)
    monkeypatch.setattr(match_state_module, "classify_banner", lambda frame: None)
    monkeypatch.setattr(match_state_module, "is_league_change_screen", lambda frame: False)
    monkeypatch.setattr(match_state_module, "is_demotion_label_candidate", lambda frame: False)
    monkeypatch.setattr(match_state_module, "is_vs_screen", lambda frame: False)
    monkeypatch.setattr(match_state_module, "is_match_end_screen", lambda frame: False)
    monkeypatch.setattr(match_state_module, "is_full_blackout", lambda frame: False)
    monkeypatch.setenv("ALLOWED_PLAYERS", "Alice")
    monkeypatch.setenv("GOAL_RECORD_MODE", "all")

    machine = MatchStateMachine(goal_confirm_frames=2)
    frame = np.zeros((10, 10, 3), dtype=np.uint8)

    with caplog.at_level("INFO", logger="nss_tracker.state"):
        for _ in range(3):
            machine.process_frame(frame)
            frame_idx["n"] += 1

    assert "ゴール検知: scorer=Charlie assist=None (記録対象)" in caplog.text


def test_goal_detection_logs_partial_redact_in_redact_mode(monkeypatch, caplog):
    """Issue #88: GOAL_RECORD_MODE=allowlist_redactで、得点者のみ許可リスト外の
    場合に「一部redactして記録対象」と表示することを確認する。
    """
    frame_idx = {"n": 0}

    monkeypatch.setattr(match_state_module, "is_goal_event", lambda frame: frame_idx["n"] < 2)
    monkeypatch.setattr(match_state_module, "confirm_goal_text", lambda frame: True)
    monkeypatch.setattr(match_state_module, "read_scorer_name", lambda frame: ("たなか", 0.95))
    monkeypatch.setattr(match_state_module, "read_assist_name", lambda frame: ("ブルドッグ", 0.90))
    monkeypatch.setattr(match_state_module, "classify_banner", lambda frame: None)
    monkeypatch.setattr(match_state_module, "is_league_change_screen", lambda frame: False)
    monkeypatch.setattr(match_state_module, "is_demotion_label_candidate", lambda frame: False)
    monkeypatch.setattr(match_state_module, "is_vs_screen", lambda frame: False)
    monkeypatch.setattr(match_state_module, "is_match_end_screen", lambda frame: False)
    monkeypatch.setattr(match_state_module, "is_full_blackout", lambda frame: False)
    monkeypatch.setenv("ALLOWED_PLAYERS", "ブルドッグ")
    monkeypatch.setenv("GOAL_RECORD_MODE", "allowlist_redact")

    machine = MatchStateMachine(goal_confirm_frames=2)
    frame = np.zeros((10, 10, 3), dtype=np.uint8)

    with caplog.at_level("INFO", logger="nss_tracker.state"):
        for _ in range(3):
            machine.process_frame(frame)
            frame_idx["n"] += 1

    assert "ゴール検知: scorer=たなか assist=ブルドッグ (一部redactして記録対象)" in caplog.text


def test_goal_detection_logs_full_record_in_redact_mode_when_both_allowed(monkeypatch, caplog):
    """allowlist_redactでも、両者とも許可リストにいればredactせず「記録対象」と表示する。"""
    frame_idx = {"n": 0}

    monkeypatch.setattr(match_state_module, "is_goal_event", lambda frame: frame_idx["n"] < 2)
    monkeypatch.setattr(match_state_module, "confirm_goal_text", lambda frame: True)
    monkeypatch.setattr(match_state_module, "read_scorer_name", lambda frame: ("Alice", 0.95))
    monkeypatch.setattr(match_state_module, "read_assist_name", lambda frame: ("Bob", 0.90))
    monkeypatch.setattr(match_state_module, "classify_banner", lambda frame: None)
    monkeypatch.setattr(match_state_module, "is_league_change_screen", lambda frame: False)
    monkeypatch.setattr(match_state_module, "is_demotion_label_candidate", lambda frame: False)
    monkeypatch.setattr(match_state_module, "is_vs_screen", lambda frame: False)
    monkeypatch.setattr(match_state_module, "is_match_end_screen", lambda frame: False)
    monkeypatch.setattr(match_state_module, "is_full_blackout", lambda frame: False)
    monkeypatch.setenv("ALLOWED_PLAYERS", "Alice,Bob")
    monkeypatch.setenv("GOAL_RECORD_MODE", "allowlist_redact")

    machine = MatchStateMachine(goal_confirm_frames=2)
    frame = np.zeros((10, 10, 3), dtype=np.uint8)

    with caplog.at_level("INFO", logger="nss_tracker.state"):
        for _ in range(3):
            machine.process_frame(frame)
            frame_idx["n"] += 1

    assert "ゴール検知: scorer=Alice assist=Bob (記録対象)" in caplog.text


def test_goal_detection_logs_no_redact_when_assist_missing_in_redact_mode(monkeypatch, caplog):
    """allowlist_redactで、得点者が許可リストにいてアシストがそもそも存在しない
    (None)場合は「redactするものが無い」ため「記録対象」と表示する。
    """
    frame_idx = {"n": 0}

    monkeypatch.setattr(match_state_module, "is_goal_event", lambda frame: frame_idx["n"] < 2)
    monkeypatch.setattr(match_state_module, "confirm_goal_text", lambda frame: True)
    monkeypatch.setattr(match_state_module, "read_scorer_name", lambda frame: ("Alice", 0.95))
    monkeypatch.setattr(match_state_module, "read_assist_name", lambda frame: None)
    monkeypatch.setattr(match_state_module, "classify_banner", lambda frame: None)
    monkeypatch.setattr(match_state_module, "is_league_change_screen", lambda frame: False)
    monkeypatch.setattr(match_state_module, "is_demotion_label_candidate", lambda frame: False)
    monkeypatch.setattr(match_state_module, "is_vs_screen", lambda frame: False)
    monkeypatch.setattr(match_state_module, "is_match_end_screen", lambda frame: False)
    monkeypatch.setattr(match_state_module, "is_full_blackout", lambda frame: False)
    monkeypatch.setenv("ALLOWED_PLAYERS", "Alice")
    monkeypatch.setenv("GOAL_RECORD_MODE", "allowlist_redact")

    machine = MatchStateMachine(goal_confirm_frames=2)
    frame = np.zeros((10, 10, 3), dtype=np.uint8)

    with caplog.at_level("INFO", logger="nss_tracker.state"):
        for _ in range(3):
            machine.process_frame(frame)
            frame_idx["n"] += 1

    assert "ゴール検知: scorer=Alice assist=None (記録対象)" in caplog.text


def test_rank_read_failure_is_logged(monkeypatch, caplog):
    """ランクバッジのOCRが常に失敗するケースで、結果バナー確定時・試合終了時
    それぞれでログが出ることを確認する(Issue #47)。バッジが表示されていない
    のか読み取りに失敗したのかを、記録結果だけでなくログからも追えるようにする。
    """
    calls = {"n": 0}

    def fake_classify_banner(frame):
        n = calls["n"]
        calls["n"] += 1
        if n < 3:
            return None
        if n < 6:
            return "lose"
        return None

    monkeypatch.setattr(match_state_module, "is_goal_event", lambda frame: False)
    monkeypatch.setattr(match_state_module, "is_vs_screen", lambda frame: False)
    monkeypatch.setattr(match_state_module, "is_match_end_screen", lambda frame: False)
    monkeypatch.setattr(match_state_module, "is_full_blackout", lambda frame: False)
    monkeypatch.setattr(match_state_module, "classify_banner", fake_classify_banner)
    monkeypatch.setattr(match_state_module, "read_precise_rank", lambda frame, gauge_roi, rank_number_roi: None)
    monkeypatch.setattr(match_state_module, "read_rank_gauge_fill", lambda frame, roi: None)
    monkeypatch.setattr(match_state_module, "read_rank", lambda frame, roi: None)
    monkeypatch.setattr(match_state_module, "is_league_change_screen", lambda frame: False)
    monkeypatch.setattr(match_state_module, "is_demotion_label_candidate", lambda frame: False)

    machine = MatchStateMachine(
        banner_confirm_frames=2,
        banner_absence_confirm_frames=2,
        goal_confirm_frames=2,
        league_change_grace_frames=1,
        rank_recheck_interval_frames=1,
        rank_stability_monitor=StabilityMonitor(roi=(0, 0, 5, 5), stable_frames_required=1),
    )

    frame = np.zeros((10, 10, 3), dtype=np.uint8)
    result = None
    with caplog.at_level("INFO", logger="nss_tracker.state"):
        for _ in range(30):
            r = machine.process_frame(frame)
            if r is not None:
                result = r
                break

    assert result is not None, "MatchResultが確定しなかった"
    assert result.rank_before is None
    assert result.rank_after is None
    assert "結果バナー確定時点で" in caplog.text
    assert "試合終了時点でも" in caplog.text


def test_track_rank_grace_tracks_slow_drift_every_frame(monkeypatch):
    """GRACE中にゲージがピクセル差分の閾値を下回る速度で緩やかに変化し続けても、
    毎フレームの継続追跡で真の最終値まで追従できることを確認する
    (fixtures/videos/00_lose_red_2-3.mp4で見つかった、早すぎる確定による誤検知の回帰防止。
    Issue #178でスナップショット確定方式から毎フレーム追跡方式に変更した)。
    値は目視ではなくこのテストのために意図的に用意した架空のシーケンスであり、
    実装の出力を転記したものではない。
    """

    def fake_read_precise_rank(frame, gauge_roi, rank_number_roi):
        return (40, 40.77)  # 結果バナー時点(before)・GRACE突入直後とも遷移途中の値

    fill_sequence = [0.77, 0.77, 0.70, 0.60, 0.50, 0.43]
    fill_calls = {"n": 0}

    def fake_read_rank_gauge_fill(frame, gauge_roi):
        idx = min(fill_calls["n"], len(fill_sequence) - 1)
        fill_calls["n"] += 1
        return fill_sequence[idx]

    monkeypatch.setattr(match_state_module, "classify_banner", lambda frame: "lose")
    monkeypatch.setattr(match_state_module, "read_precise_rank", fake_read_precise_rank)
    monkeypatch.setattr(match_state_module, "read_rank_gauge_fill", fake_read_rank_gauge_fill)
    monkeypatch.setattr(match_state_module, "read_rank", lambda frame, roi: 40)
    monkeypatch.setattr(match_state_module, "is_league_change_screen", lambda frame: False)
    monkeypatch.setattr(match_state_module, "is_demotion_label_candidate", lambda frame: False)
    monkeypatch.setattr(match_state_module, "is_goal_event", lambda frame: False)
    monkeypatch.setattr(match_state_module, "is_vs_screen", lambda frame: False)
    monkeypatch.setattr(match_state_module, "is_match_end_screen", lambda frame: False)
    monkeypatch.setattr(match_state_module, "is_full_blackout", lambda frame: False)

    machine = MatchStateMachine(
        banner_confirm_frames=2,
        league_change_grace_frames=10,
        rank_recheck_interval_frames=3,
        rank_stability_monitor=StabilityMonitor(roi=(0, 0, 5, 5), stable_frames_required=2),
    )

    frame = np.zeros((10, 10, 3), dtype=np.uint8)
    result = None
    for _ in range(60):
        result = machine.process_frame(frame)
        if result is not None:
            break

    assert result is not None, "MatchResultが確定しなかった"
    assert result.rank_after == pytest.approx(40.43), (
        f"古い過渡的な値(40.77)のまま確定してしまっている: {result.rank_after}"
    )


def test_track_rank_periodic_recheck_catches_tier_change(monkeypatch):
    """GRACE突入直後の読み取りでは帯番号の変化(降格)がまだ反映されていない場合でも、
    定期的な再読み取りで正しい帯番号・league_changedにたどり着けることを確認する
    (fixtures/videos/03_lose_blue_2-3.mp4のような、降格演出が専用の全画面演出として
    出ないケースの回帰防止)。
    """

    def fake_read_precise_rank(frame, gauge_roi, rank_number_roi):
        return (40, 40.09)  # 結果バナー時点(before)・GRACE突入直後とも降格前の帯のまま

    tier_sequence = [40, 40, 39]
    tier_calls = {"n": 0}

    def fake_read_rank(frame, roi):
        idx = min(tier_calls["n"], len(tier_sequence) - 1)
        tier_calls["n"] += 1
        return tier_sequence[idx]

    monkeypatch.setattr(match_state_module, "classify_banner", lambda frame: "lose")
    monkeypatch.setattr(match_state_module, "read_precise_rank", fake_read_precise_rank)
    monkeypatch.setattr(match_state_module, "read_rank_gauge_fill", lambda frame, roi: 1.0)
    monkeypatch.setattr(match_state_module, "read_rank", fake_read_rank)
    monkeypatch.setattr(match_state_module, "is_league_change_screen", lambda frame: False)
    monkeypatch.setattr(match_state_module, "is_demotion_label_candidate", lambda frame: False)
    monkeypatch.setattr(match_state_module, "is_goal_event", lambda frame: False)
    monkeypatch.setattr(match_state_module, "is_vs_screen", lambda frame: False)
    monkeypatch.setattr(match_state_module, "is_match_end_screen", lambda frame: False)
    monkeypatch.setattr(match_state_module, "is_full_blackout", lambda frame: False)

    machine = MatchStateMachine(
        banner_confirm_frames=2,
        league_change_grace_frames=10,
        rank_recheck_interval_frames=3,
        rank_stability_monitor=StabilityMonitor(roi=(0, 0, 5, 5), stable_frames_required=2),
    )

    frame = np.zeros((10, 10, 3), dtype=np.uint8)
    result = None
    for _ in range(60):
        result = machine.process_frame(frame)
        if result is not None:
            break

    assert result is not None, "MatchResultが確定しなかった"
    assert result.rank_after == pytest.approx(40.0)
    assert result.league_changed == "down", (
        f"降格を見逃している(帯の変化が反映される前の値で確定した): {result.league_changed}"
    )


def test_tier_jump_recovers_via_rescan(monkeypatch):
    """試合前後で帯番号が不自然に急変(38→15)しても、数フレーム後の再スキャンで
    正しい値(38→39、昇格演出確認済み)にたどり着けることを確認する(Issue #136)。
    値は目視ではなくこのテストのために意図的に用意した架空のシーケンスであり、
    実装の出力を転記したものではない。
    """
    read_calls = {"n": 0}

    def fake_read_precise_rank(frame, gauge_roi, rank_number_roi):
        read_calls["n"] += 1
        if read_calls["n"] == 1:
            return (38, 38.2)  # 結果バナー時点(before)
        if read_calls["n"] == 2:
            return (15, 15.5)  # GRACE突入直後の誤読み(不自然な急変)
        return (39, 39.3)  # 再スキャン後の正しい値(昇格演出確認済み)

    league_change_calls = {"n": 0}

    def fake_is_league_change_screen(frame):
        league_change_calls["n"] += 1
        return league_change_calls["n"] == 1

    monkeypatch.setattr(match_state_module, "classify_banner", lambda frame: "win")
    monkeypatch.setattr(match_state_module, "read_precise_rank", fake_read_precise_rank)
    monkeypatch.setattr(match_state_module, "read_rank_gauge_fill", lambda frame, roi: 0.5)
    monkeypatch.setattr(match_state_module, "is_league_change_screen", fake_is_league_change_screen)
    monkeypatch.setattr(match_state_module, "is_demotion_label_candidate", lambda frame: False)
    monkeypatch.setattr(match_state_module, "is_goal_event", lambda frame: False)
    monkeypatch.setattr(match_state_module, "is_vs_screen", lambda frame: False)
    monkeypatch.setattr(match_state_module, "is_match_end_screen", lambda frame: False)
    monkeypatch.setattr(match_state_module, "is_full_blackout", lambda frame: False)

    machine = MatchStateMachine(
        banner_confirm_frames=2,
        league_change_grace_frames=3,
        rank_recheck_interval_frames=1000,
        rank_tier_rescan_wait_frames=3,
        rank_stability_monitor=StabilityMonitor(roi=(0, 0, 5, 5), stable_frames_required=2),
    )

    frame = np.zeros((10, 10, 3), dtype=np.uint8)
    result = None
    for _ in range(60):
        result = machine.process_frame(frame)
        if result is not None:
            break

    assert result is not None, "MatchResultが確定しなかった"
    assert result.rank_after == pytest.approx(39.3)
    assert result.league_changed == "up"


def test_tier_jump_falls_back_to_gauge_continuity_when_rescan_still_implausible_win(monkeypatch):
    """再スキャンしても帯番号が不自然なまま(勝ちなのに降格演出未確認)の場合、
    帯番号は変えずゲージ小数部の連続性だけを採用することを確認する(Issue #136)。
    """
    read_calls = {"n": 0}

    def fake_read_precise_rank(frame, gauge_roi, rank_number_roi):
        read_calls["n"] += 1
        if read_calls["n"] == 1:
            return (38, 38.2)  # before(小数部0.2)
        if read_calls["n"] == 2:
            return (99, 99.4)  # GRACE突入直後の誤読み(小数部0.4は継続として自然)
        return (7, 7.4)  # 再スキャンでも誤読みのまま(小数部は同じく0.4)

    monkeypatch.setattr(match_state_module, "classify_banner", lambda frame: "win")
    monkeypatch.setattr(match_state_module, "read_precise_rank", fake_read_precise_rank)
    monkeypatch.setattr(match_state_module, "read_rank_gauge_fill", lambda frame, roi: 0.4)
    monkeypatch.setattr(match_state_module, "is_league_change_screen", lambda frame: False)
    monkeypatch.setattr(match_state_module, "is_demotion_label_candidate", lambda frame: False)
    monkeypatch.setattr(match_state_module, "is_goal_event", lambda frame: False)
    monkeypatch.setattr(match_state_module, "is_vs_screen", lambda frame: False)
    monkeypatch.setattr(match_state_module, "is_match_end_screen", lambda frame: False)
    monkeypatch.setattr(match_state_module, "is_full_blackout", lambda frame: False)

    machine = MatchStateMachine(
        banner_confirm_frames=2,
        league_change_grace_frames=3,
        rank_recheck_interval_frames=1000,
        rank_tier_rescan_wait_frames=3,
        rank_stability_monitor=StabilityMonitor(roi=(0, 0, 5, 5), stable_frames_required=2),
    )

    frame = np.zeros((10, 10, 3), dtype=np.uint8)
    result = None
    for _ in range(60):
        result = machine.process_frame(frame)
        if result is not None:
            break

    assert result is not None, "MatchResultが確定しなかった"
    assert result.rank_after == pytest.approx(38.4)
    assert result.league_changed is None


def test_tier_jump_falls_back_to_demotion_via_gauge_continuity_when_losing(monkeypatch):
    """負け試合で再スキャンしても帯番号が不自然なままの場合、ゲージ小数部が
    0を割り込んで大きく増えて見える(0.2→0.9)ことから降格と推測し、
    帯番号を1つ下げて記録することを確認する(Issue #136)。
    """
    read_calls = {"n": 0}

    def fake_read_precise_rank(frame, gauge_roi, rank_number_roi):
        read_calls["n"] += 1
        if read_calls["n"] == 1:
            return (38, 38.2)  # before(小数部0.2)
        if read_calls["n"] == 2:
            return (99, 99.9)  # GRACE突入直後の誤読み(小数部0.9)
        return (5, 5.9)  # 再スキャンでも誤読みのまま(小数部は同じく0.9)

    monkeypatch.setattr(match_state_module, "classify_banner", lambda frame: "lose")
    monkeypatch.setattr(match_state_module, "read_precise_rank", fake_read_precise_rank)
    monkeypatch.setattr(match_state_module, "read_rank_gauge_fill", lambda frame, roi: 0.9)
    monkeypatch.setattr(match_state_module, "is_league_change_screen", lambda frame: False)
    monkeypatch.setattr(match_state_module, "is_demotion_label_candidate", lambda frame: False)
    monkeypatch.setattr(match_state_module, "is_goal_event", lambda frame: False)
    monkeypatch.setattr(match_state_module, "is_vs_screen", lambda frame: False)
    monkeypatch.setattr(match_state_module, "is_match_end_screen", lambda frame: False)
    monkeypatch.setattr(match_state_module, "is_full_blackout", lambda frame: False)

    machine = MatchStateMachine(
        banner_confirm_frames=2,
        league_change_grace_frames=3,
        rank_recheck_interval_frames=1000,
        rank_tier_rescan_wait_frames=3,
        rank_stability_monitor=StabilityMonitor(roi=(0, 0, 5, 5), stable_frames_required=2),
    )

    frame = np.zeros((10, 10, 3), dtype=np.uint8)
    result = None
    for _ in range(60):
        result = machine.process_frame(frame)
        if result is not None:
            break

    assert result is not None, "MatchResultが確定しなかった"
    assert result.rank_after == pytest.approx(37.9)
    assert result.league_changed == "down"


def test_tier_jump_falls_back_to_unchanged_tier_on_draw(monkeypatch):
    """引き分け試合はゲージが全く動かない仕様のため、再スキャンしても帯番号が
    不自然なままの場合は常に試合前の帯番号を据え置くことを確認する(Issue #136)。
    """
    read_calls = {"n": 0}

    def fake_read_precise_rank(frame, gauge_roi, rank_number_roi):
        read_calls["n"] += 1
        if read_calls["n"] == 1:
            return (38, 38.2)  # before(小数部0.2)
        if read_calls["n"] == 2:
            return (99, 99.9)  # GRACE突入直後の誤読み
        return (5, 5.9)  # 再スキャンでも誤読みのまま

    monkeypatch.setattr(match_state_module, "classify_banner", lambda frame: "draw")
    monkeypatch.setattr(match_state_module, "read_precise_rank", fake_read_precise_rank)
    monkeypatch.setattr(match_state_module, "read_rank_gauge_fill", lambda frame, roi: 0.9)
    monkeypatch.setattr(match_state_module, "is_league_change_screen", lambda frame: False)
    monkeypatch.setattr(match_state_module, "is_demotion_label_candidate", lambda frame: False)
    monkeypatch.setattr(match_state_module, "is_goal_event", lambda frame: False)
    monkeypatch.setattr(match_state_module, "is_vs_screen", lambda frame: False)
    monkeypatch.setattr(match_state_module, "is_match_end_screen", lambda frame: False)
    monkeypatch.setattr(match_state_module, "is_full_blackout", lambda frame: False)

    machine = MatchStateMachine(
        banner_confirm_frames=2,
        league_change_grace_frames=3,
        rank_recheck_interval_frames=1000,
        rank_tier_rescan_wait_frames=3,
        rank_stability_monitor=StabilityMonitor(roi=(0, 0, 5, 5), stable_frames_required=2),
    )

    frame = np.zeros((10, 10, 3), dtype=np.uint8)
    result = None
    for _ in range(60):
        result = machine.process_frame(frame)
        if result is not None:
            break

    assert result is not None, "MatchResultが確定しなかった"
    assert result.league_changed is None
    assert result.rank_after == pytest.approx(38.9)


def test_tier_jump_falls_back_to_demotion_via_independent_label_when_gauge_magnitude_is_small(monkeypatch):
    """Issue #176: 降格ラベルを独立信号として確認できた場合、ゲージ小数部の
    増加幅がRANK_TIER_WRAP_MIN_MAGNITUDE未満(従来の間接推測だけでは降格と
    判断できない)であっても、帯番号を1つ下げて記録することを確認する。
    """
    read_calls = {"n": 0}

    def fake_read_precise_rank(frame, gauge_roi, rank_number_roi):
        read_calls["n"] += 1
        if read_calls["n"] == 1:
            return (38, 38.2)  # before(小数部0.2)
        if read_calls["n"] == 2:
            return (99, 99.9)  # GRACE突入直後の誤読み
        return (5, 5.3)  # 再スキャンでも誤読みのまま(小数部0.3、before比+0.1のみ)

    monkeypatch.setattr(match_state_module, "classify_banner", lambda frame: "lose")
    monkeypatch.setattr(match_state_module, "read_precise_rank", fake_read_precise_rank)
    monkeypatch.setattr(match_state_module, "read_rank_gauge_fill", lambda frame, roi: 0.3)
    monkeypatch.setattr(match_state_module, "is_league_change_screen", lambda frame: False)
    monkeypatch.setattr(match_state_module, "is_demotion_label_candidate", lambda frame: True)
    monkeypatch.setattr(match_state_module, "confirm_demotion_label_text", lambda frame: True)
    monkeypatch.setattr(match_state_module, "is_goal_event", lambda frame: False)
    monkeypatch.setattr(match_state_module, "is_vs_screen", lambda frame: False)
    monkeypatch.setattr(match_state_module, "is_match_end_screen", lambda frame: False)
    monkeypatch.setattr(match_state_module, "is_full_blackout", lambda frame: False)

    machine = MatchStateMachine(
        banner_confirm_frames=2,
        league_change_grace_frames=3,
        rank_recheck_interval_frames=1000,
        rank_tier_rescan_wait_frames=3,
        demotion_label_confirm_frames=2,
        rank_stability_monitor=StabilityMonitor(roi=(0, 0, 5, 5), stable_frames_required=2),
    )

    frame = np.zeros((10, 10, 3), dtype=np.uint8)
    result = None
    for _ in range(60):
        result = machine.process_frame(frame)
        if result is not None:
            break

    assert result is not None, "MatchResultが確定しなかった"
    assert result.rank_after == pytest.approx(37.3)
    assert result.league_changed == "down"


def test_demotion_confirmed_but_tier_ocr_reads_unchanged_still_records_demotion(monkeypatch):
    """Issue #202: 降格ラベルを確認できているのに帯番号OCRが「変化なし」を
    返し続けた場合でも、再スキャン経路に合流して最終的に帯番号を1つ下げて
    記録することを確認する(_is_tier_change_plausibleがdelta=0を無条件に
    許容していたため、この独立信号が一切参照されずに降格が記録から漏れる
    バグの回帰テスト)。
    """
    read_calls = {"n": 0}

    def fake_read_precise_rank(frame, gauge_roi, rank_number_roi):
        read_calls["n"] += 1
        if read_calls["n"] == 1:
            return (38, 38.2)  # before(小数部0.2)
        if read_calls["n"] == 2:
            return (38, 38.3)  # GRACE突入直後(帯番号は変化なしのまま)
        return (38, 38.4)  # 再スキャンでも変化なしのまま

    monkeypatch.setattr(match_state_module, "classify_banner", lambda frame: "lose")
    monkeypatch.setattr(match_state_module, "read_precise_rank", fake_read_precise_rank)
    monkeypatch.setattr(match_state_module, "read_rank_gauge_fill", lambda frame, roi: 0.3)
    monkeypatch.setattr(match_state_module, "is_league_change_screen", lambda frame: False)
    monkeypatch.setattr(match_state_module, "is_demotion_label_candidate", lambda frame: True)
    monkeypatch.setattr(match_state_module, "confirm_demotion_label_text", lambda frame: True)
    monkeypatch.setattr(match_state_module, "is_goal_event", lambda frame: False)
    monkeypatch.setattr(match_state_module, "is_vs_screen", lambda frame: False)
    monkeypatch.setattr(match_state_module, "is_match_end_screen", lambda frame: False)
    monkeypatch.setattr(match_state_module, "is_full_blackout", lambda frame: False)

    machine = MatchStateMachine(
        banner_confirm_frames=2,
        league_change_grace_frames=3,
        rank_recheck_interval_frames=1000,
        rank_tier_rescan_wait_frames=3,
        demotion_label_confirm_frames=2,
        rank_stability_monitor=StabilityMonitor(roi=(0, 0, 5, 5), stable_frames_required=2),
    )

    frame = np.zeros((10, 10, 3), dtype=np.uint8)
    result = None
    for _ in range(60):
        result = machine.process_frame(frame)
        if result is not None:
            break

    assert result is not None, "MatchResultが確定しなかった"
    assert result.rank_after == pytest.approx(37.4)
    assert result.league_changed == "down"


def test_unchanged_tier_stays_plausible_without_demotion_confirmation(monkeypatch):
    """Issue #202の修正が通常ケースを壊していないことを確認する。降格ラベルを
    確認できていない(通常の)試合では、帯番号が変化なしと読めた場合は
    再スキャンを挟まず素直に確定することを確認する。
    """
    read_calls = {"n": 0}

    def fake_read_precise_rank(frame, gauge_roi, rank_number_roi):
        read_calls["n"] += 1
        if read_calls["n"] == 1:
            return (38, 38.2)
        return (38, 38.3)

    monkeypatch.setattr(match_state_module, "classify_banner", lambda frame: "lose")
    monkeypatch.setattr(match_state_module, "read_precise_rank", fake_read_precise_rank)
    monkeypatch.setattr(match_state_module, "read_rank_gauge_fill", lambda frame, roi: 0.3)
    monkeypatch.setattr(match_state_module, "is_league_change_screen", lambda frame: False)
    monkeypatch.setattr(match_state_module, "is_demotion_label_candidate", lambda frame: False)
    monkeypatch.setattr(match_state_module, "is_goal_event", lambda frame: False)
    monkeypatch.setattr(match_state_module, "is_vs_screen", lambda frame: False)
    monkeypatch.setattr(match_state_module, "is_match_end_screen", lambda frame: False)
    monkeypatch.setattr(match_state_module, "is_full_blackout", lambda frame: False)

    machine = MatchStateMachine(
        banner_confirm_frames=2,
        league_change_grace_frames=3,
        rank_recheck_interval_frames=1000,
        rank_tier_rescan_wait_frames=3,
        rank_stability_monitor=StabilityMonitor(roi=(0, 0, 5, 5), stable_frames_required=2),
    )

    frame = np.zeros((10, 10, 3), dtype=np.uint8)
    result = None
    for _ in range(60):
        result = machine.process_frame(frame)
        if result is not None:
            break

    assert result is not None, "MatchResultが確定しなかった"
    assert result.rank_after == pytest.approx(38.3)
    assert result.league_changed is None


def test_demotion_label_not_confirmed_falls_back_to_gauge_magnitude_heuristic(monkeypatch):
    """Issue #176: 降格ラベルの候補判定はTrueだがOCR確認に失敗した場合、
    独立信号としては採用されず、従来のゲージ小数部の閾値判定にのみ従うことを
    確認する(小数部の増加幅が閾値未満のため、帯番号は据え置かれるはず)。
    """
    read_calls = {"n": 0}

    def fake_read_precise_rank(frame, gauge_roi, rank_number_roi):
        read_calls["n"] += 1
        if read_calls["n"] == 1:
            return (38, 38.2)
        if read_calls["n"] == 2:
            return (99, 99.9)
        return (5, 5.3)  # before比+0.1のみ(閾値未満)

    monkeypatch.setattr(match_state_module, "classify_banner", lambda frame: "lose")
    monkeypatch.setattr(match_state_module, "read_precise_rank", fake_read_precise_rank)
    monkeypatch.setattr(match_state_module, "read_rank_gauge_fill", lambda frame, roi: 0.3)
    monkeypatch.setattr(match_state_module, "is_league_change_screen", lambda frame: False)
    monkeypatch.setattr(match_state_module, "is_demotion_label_candidate", lambda frame: True)
    monkeypatch.setattr(match_state_module, "confirm_demotion_label_text", lambda frame: False)
    monkeypatch.setattr(match_state_module, "is_goal_event", lambda frame: False)
    monkeypatch.setattr(match_state_module, "is_vs_screen", lambda frame: False)
    monkeypatch.setattr(match_state_module, "is_match_end_screen", lambda frame: False)
    monkeypatch.setattr(match_state_module, "is_full_blackout", lambda frame: False)

    machine = MatchStateMachine(
        banner_confirm_frames=2,
        league_change_grace_frames=3,
        rank_recheck_interval_frames=1000,
        rank_tier_rescan_wait_frames=3,
        demotion_label_confirm_frames=2,
        rank_stability_monitor=StabilityMonitor(roi=(0, 0, 5, 5), stable_frames_required=2),
    )

    frame = np.zeros((10, 10, 3), dtype=np.uint8)
    result = None
    for _ in range(60):
        result = machine.process_frame(frame)
        if result is not None:
            break

    assert result is not None, "MatchResultが確定しなかった"
    assert result.league_changed is None
    assert result.rank_after == pytest.approx(38.3)


def test_fill_grace_candidate_if_missing_uses_enlarged_roi(monkeypatch):
    """GRACE中に候補値が一度も読み取れないまま確定に至った場合の最後のリトライ
    (_fill_grace_candidate_if_missing)は、常に拡大表示用のROI(GAUGE_ROI_ENLARGED)を
    使うことを確認する。GRACE中はランク変動アニメーション開始後の文脈のため、
    結果バナー確定直後専用のGAUGE_ROI_COMPACTを誤って使うとバー幅がずれて
    誤ったゲージ値を返してしまう。
    """
    rois_used: list[tuple[int, int, int, int]] = []
    banner_call_count = {"n": 0}

    def fake_classify_banner(frame):
        banner_call_count["n"] += 1
        # 最初の2回(banner_confirm_frames分)は"lose"を返してTRACKING_RANKへ遷移させ、
        # GRACE突入後の最初の呼び出しでNoneを返してバナー消失(即確定)を発生させる
        return "lose" if banner_call_count["n"] <= 2 else None

    def fake_read_precise_rank(frame, gauge_roi, rank_number_roi):
        rois_used.append(gauge_roi)
        # 結果バナー確定直後(コンパクト表示)・GRACE突入直後(拡大表示)の読み取りは
        # いずれも失敗させ、候補が一度も埋まらない状況を再現する。
        # _fill_grace_candidate_if_missingによる最後のリトライだけ成功させる
        if len(rois_used) <= 2:
            return None
        return (40, 40.5)

    monkeypatch.setattr(match_state_module, "classify_banner", fake_classify_banner)
    monkeypatch.setattr(match_state_module, "read_precise_rank", fake_read_precise_rank)
    monkeypatch.setattr(match_state_module, "read_rank_gauge_fill", lambda frame, roi: None)
    monkeypatch.setattr(match_state_module, "read_rank", lambda frame, roi: None)
    monkeypatch.setattr(match_state_module, "is_league_change_screen", lambda frame: False)
    monkeypatch.setattr(match_state_module, "is_demotion_label_candidate", lambda frame: False)
    monkeypatch.setattr(match_state_module, "is_goal_event", lambda frame: False)
    monkeypatch.setattr(match_state_module, "is_vs_screen", lambda frame: False)
    monkeypatch.setattr(match_state_module, "is_match_end_screen", lambda frame: False)
    monkeypatch.setattr(match_state_module, "is_full_blackout", lambda frame: False)

    machine = MatchStateMachine(
        banner_confirm_frames=2,
        league_change_grace_frames=10,
        rank_recheck_interval_frames=3,
        rank_stability_monitor=StabilityMonitor(roi=(0, 0, 5, 5), stable_frames_required=2),
    )

    frame = np.zeros((10, 10, 3), dtype=np.uint8)
    result = None
    for _ in range(60):
        result = machine.process_frame(frame)
        if result is not None:
            break

    assert result is not None, "MatchResultが確定しなかった"
    assert result.rank_after == pytest.approx(40.5)
    assert rois_used[0] == GAUGE_ROI_COMPACT, "結果バナー確定直後の読み取りはコンパクト表示ROIのはず"
    assert rois_used[-1] == GAUGE_ROI_ENLARGED, (
        f"_fill_grace_candidate_if_missingが拡大表示ROIを使っていない: {rois_used[-1]}"
    )


def test_near_tier_cap_gauge_skips_early_finalize_and_catches_promotion(monkeypatch):
    """Issue #209: ゲージが帯の上限付近(near_tier_cap)のままバナーが消えても、
    早期確定パス(Issue #178)を使わずに待ち続け、その後実際に昇格演出
    (is_league_change_screen)が始まれば正しく帯を+1して記録することを確認する。

    fixtures/videos/30・42番の回帰(昇格演出が始まる前にゲージの踊り場+バナー消灯が
    重なって誤って早期確定していた)を再現するテスト。
    """
    league_change_calls = {"n": 0}
    # 昇格演出が始まる前の「踊り場」を十分な回数再現した後、1回だけ演出が来たことにする
    PROMOTION_AT_CALL = 30

    def fake_is_league_change_screen(frame):
        league_change_calls["n"] += 1
        return league_change_calls["n"] == PROMOTION_AT_CALL

    precise_calls = {"n": 0}

    def fake_read_precise_rank(frame, gauge_roi, rank_number_roi):
        precise_calls["n"] += 1
        # 呼び出し1回目はbanner確定時の(before)読み取り、2回目はGRACE突入時
        # (昇格演出が始まる前)の読み取りで、いずれも帯の上限付近の値を返す。
        # 3回目以降(演出後の再度のGRACE突入時)から昇格後の値を返す
        if precise_calls["n"] <= 2:
            return (37, 37.98)  # 昇格直前、帯の上限付近で踊り場になっている状態
        return (38, 38.06)  # 昇格後

    def fake_read_rank_gauge_fill(frame, roi):
        return 0.98 if precise_calls["n"] <= 2 else 0.06

    banner_call_count = {"n": 0}

    def fake_classify_banner(frame):
        banner_call_count["n"] += 1
        # banner_confirm_frames分は"win"を返して確定させ、以降はTRACKING_RANK中に
        # バナーのテキストが一時的に(または最後まで)消えている状態を再現する
        return "win" if banner_call_count["n"] <= 2 else None

    monkeypatch.setattr(match_state_module, "classify_banner", fake_classify_banner)
    monkeypatch.setattr(match_state_module, "read_precise_rank", fake_read_precise_rank)
    monkeypatch.setattr(match_state_module, "read_rank_gauge_fill", fake_read_rank_gauge_fill)
    monkeypatch.setattr(match_state_module, "read_rank", lambda frame, roi: None)
    monkeypatch.setattr(match_state_module, "is_league_change_screen", fake_is_league_change_screen)
    monkeypatch.setattr(match_state_module, "is_demotion_label_candidate", lambda frame: False)
    monkeypatch.setattr(match_state_module, "is_goal_event", lambda frame: False)
    monkeypatch.setattr(match_state_module, "is_vs_screen", lambda frame: False)
    monkeypatch.setattr(match_state_module, "is_match_end_screen", lambda frame: False)
    monkeypatch.setattr(match_state_module, "is_full_blackout", lambda frame: False)

    machine = MatchStateMachine(
        banner_confirm_frames=2,
        league_change_grace_frames=200,
        rank_recheck_interval_frames=3,
        rank_stability_monitor=StabilityMonitor(roi=(0, 0, 5, 5), stable_frames_required=2),
    )

    frame = np.zeros((10, 10, 3), dtype=np.uint8)
    result = None
    for _ in range(300):
        result = machine.process_frame(frame)
        if result is not None:
            break

    assert result is not None, "MatchResultが確定しなかった"
    assert result.rank_after == pytest.approx(38.06)
    assert result.league_changed == "up", (
        "昇格演出が始まる前に早期確定してしまい、昇格を見逃した(Issue #209の回帰)"
    )


def test_near_tier_cap_gauge_without_promotion_still_finalizes_after_full_grace_period(monkeypatch):
    """Issue #209: ゲージが帯の上限付近でバナーが消えても、実際には昇格演出が
    一度も来ない場合は、早期確定パスを使わないぶん通常より遅くはなるが、
    league_change_grace_frames(通常のタイムアウト)満了時点で正しく確定する
    ことを確認する(帯番号は変化なしのまま)。
    """
    banner_call_count = {"n": 0}

    def fake_classify_banner(frame):
        banner_call_count["n"] += 1
        return "win" if banner_call_count["n"] <= 2 else None

    monkeypatch.setattr(match_state_module, "classify_banner", fake_classify_banner)
    monkeypatch.setattr(match_state_module, "read_precise_rank", lambda frame, gauge_roi, rank_number_roi: (37, 37.98))
    monkeypatch.setattr(match_state_module, "read_rank_gauge_fill", lambda frame, roi: 0.98)
    monkeypatch.setattr(match_state_module, "read_rank", lambda frame, roi: 37)
    monkeypatch.setattr(match_state_module, "is_league_change_screen", lambda frame: False)
    monkeypatch.setattr(match_state_module, "is_demotion_label_candidate", lambda frame: False)
    monkeypatch.setattr(match_state_module, "is_goal_event", lambda frame: False)
    monkeypatch.setattr(match_state_module, "is_vs_screen", lambda frame: False)
    monkeypatch.setattr(match_state_module, "is_match_end_screen", lambda frame: False)
    monkeypatch.setattr(match_state_module, "is_full_blackout", lambda frame: False)

    machine = MatchStateMachine(
        banner_confirm_frames=2,
        league_change_grace_frames=10,
        rank_recheck_interval_frames=3,
        rank_stability_monitor=StabilityMonitor(roi=(0, 0, 5, 5), stable_frames_required=2),
    )

    frame = np.zeros((10, 10, 3), dtype=np.uint8)
    result = None
    for _ in range(60):
        result = machine.process_frame(frame)
        if result is not None:
            break

    assert result is not None, "MatchResultが確定しなかった(通常のタイムアウトでも確定しないのは別の不具合)"
    assert result.rank_after == pytest.approx(37.98)
    assert result.league_changed is None


def test_full_blackout_triggers_immediate_finalize_bypassing_grace_timeout(monkeypatch):
    """Issue #209: 暗転(is_full_blackout)を検知したら、grace_counter・near_tier_cap・
    バナー消灯確認の状態に関わらず直ちに確定することを確認する。

    league_change_grace_framesを通常のタイムアウトでは到底終わらない大きさにし、
    banner・ゲージとも通常どおり(帯の上限付近ではない)動いている想定でも、
    暗転自体が独立した安全装置として機能することを示す回帰テスト。
    """
    blackout_calls = {"n": 0}
    BLACKOUT_AT_CALL = 20

    def fake_is_full_blackout(frame):
        blackout_calls["n"] += 1
        return blackout_calls["n"] >= BLACKOUT_AT_CALL

    monkeypatch.setattr(match_state_module, "classify_banner", lambda frame: "win")
    monkeypatch.setattr(match_state_module, "read_precise_rank", lambda frame, gauge_roi, rank_number_roi: (37, 37.40))
    monkeypatch.setattr(match_state_module, "read_rank_gauge_fill", lambda frame, roi: 0.40)
    monkeypatch.setattr(match_state_module, "read_rank", lambda frame, roi: 37)
    monkeypatch.setattr(match_state_module, "is_league_change_screen", lambda frame: False)
    monkeypatch.setattr(match_state_module, "is_demotion_label_candidate", lambda frame: False)
    monkeypatch.setattr(match_state_module, "is_goal_event", lambda frame: False)
    monkeypatch.setattr(match_state_module, "is_vs_screen", lambda frame: False)
    monkeypatch.setattr(match_state_module, "is_match_end_screen", lambda frame: False)
    monkeypatch.setattr(match_state_module, "is_full_blackout", fake_is_full_blackout)

    machine = MatchStateMachine(
        banner_confirm_frames=2,
        league_change_grace_frames=10_000,
        rank_recheck_interval_frames=3,
        rank_stability_monitor=StabilityMonitor(roi=(0, 0, 5, 5), stable_frames_required=2),
    )

    frame = np.zeros((10, 10, 3), dtype=np.uint8)
    result = None
    for _ in range(100):
        result = machine.process_frame(frame)
        if result is not None:
            break

    assert result is not None, "暗転を検知しても確定しなかった"
    assert result.rank_after == pytest.approx(37.40)
    assert result.league_changed is None


def test_goal_banner_shown_continuously_records_only_one_goal(monkeypatch):
    """同じゴールバナーが表示され続けている間、複数回記録されない(デバウンス)ことを確認する。"""
    monkeypatch.setattr(match_state_module, "is_goal_event", lambda frame: True)
    monkeypatch.setattr(match_state_module, "confirm_goal_text", lambda frame: True)
    monkeypatch.setattr(match_state_module, "read_scorer_name", lambda frame: ("Alice", 0.95))
    monkeypatch.setattr(match_state_module, "read_assist_name", lambda frame: None)
    monkeypatch.setattr(match_state_module, "classify_banner", lambda frame: None)
    monkeypatch.setattr(match_state_module, "is_vs_screen", lambda frame: False)
    monkeypatch.setattr(match_state_module, "is_match_end_screen", lambda frame: False)
    monkeypatch.setattr(match_state_module, "is_full_blackout", lambda frame: False)
    monkeypatch.setenv("GOAL_RECORD_MODE", "allowlist")

    machine = MatchStateMachine(goal_confirm_frames=2)
    frame = np.zeros((10, 10, 3), dtype=np.uint8)
    for _ in range(10):
        machine.process_frame(frame)

    assert len(machine._pending_goals) == 1


def test_goal_candidate_rejected_by_ocr_is_not_recorded(monkeypatch):
    """is_goal_event(色ベース)がTrueでも、confirm_goal_text(OCR)がFalseを返す場合
    (青空・スタジアム天蓋の映り込み等、Issue #186参照)は記録されないことを確認する。
    """
    monkeypatch.setattr(match_state_module, "is_goal_event", lambda frame: True)
    monkeypatch.setattr(match_state_module, "confirm_goal_text", lambda frame: False)
    monkeypatch.setattr(match_state_module, "read_scorer_name", lambda frame: ("Alice", 0.95))
    monkeypatch.setattr(match_state_module, "read_assist_name", lambda frame: None)
    monkeypatch.setattr(match_state_module, "classify_banner", lambda frame: None)
    monkeypatch.setattr(match_state_module, "is_vs_screen", lambda frame: False)
    monkeypatch.setattr(match_state_module, "is_match_end_screen", lambda frame: False)
    monkeypatch.setattr(match_state_module, "is_full_blackout", lambda frame: False)
    monkeypatch.setenv("GOAL_RECORD_MODE", "allowlist")

    machine = MatchStateMachine(goal_confirm_frames=2)
    frame = np.zeros((10, 10, 3), dtype=np.uint8)
    for _ in range(10):
        machine.process_frame(frame)

    assert len(machine._pending_goals) == 0


def test_match_end_confirmed_enables_fast_banner_confirm(monkeypatch):
    """「試合終了」バナーを確認できた場合、banner_confirm_frames_after_match_end
    (短い方)でバナーが確定することを確認する(Issue #76)。
    """
    monkeypatch.setattr(match_state_module, "is_match_end_screen", lambda frame: True)
    monkeypatch.setattr(match_state_module, "confirm_match_end_text", lambda frame: True)
    monkeypatch.setattr(match_state_module, "is_goal_event", lambda frame: False)
    monkeypatch.setattr(match_state_module, "is_vs_screen", lambda frame: False)
    monkeypatch.setattr(match_state_module, "classify_banner", lambda frame: "win")
    monkeypatch.setattr(match_state_module, "read_precise_rank", lambda frame, gauge_roi, rank_number_roi: (10, 10.0))
    monkeypatch.setattr(match_state_module, "read_rank_gauge_fill", lambda frame, roi: 0.0)
    monkeypatch.setattr(match_state_module, "is_league_change_screen", lambda frame: False)
    monkeypatch.setattr(match_state_module, "is_demotion_label_candidate", lambda frame: False)

    machine = MatchStateMachine(
        banner_confirm_frames=100,
        banner_confirm_frames_after_match_end=3,
        match_end_confirm_frames=1,
        league_change_grace_frames=1,
        rank_stability_monitor=StabilityMonitor(roi=(0, 0, 5, 5), stable_frames_required=1),
    )

    frame = np.zeros((10, 10, 3), dtype=np.uint8)
    result = None
    for i in range(10):
        result = machine.process_frame(frame)
        if result is not None:
            assert i < 100, "短いデバウンスが使われず、長い方の閾値まで待ってしまった"
            break

    assert result is not None, "MatchResultが確定しなかった"
    assert result.result == "win"


def test_match_end_candidate_rejected_by_ocr_keeps_slow_banner_confirm(monkeypatch):
    """is_match_end_screen(色ベース)がTrueでも、confirm_match_end_text(OCR)が
    Falseを返す場合(「キックオフ」等の誤認識、Issue #76参照)は、
    banner_confirm_frames(長い方)のまま確定を待つことを確認する。
    """
    monkeypatch.setattr(match_state_module, "is_match_end_screen", lambda frame: True)
    monkeypatch.setattr(match_state_module, "confirm_match_end_text", lambda frame: False)
    monkeypatch.setattr(match_state_module, "is_goal_event", lambda frame: False)
    monkeypatch.setattr(match_state_module, "is_vs_screen", lambda frame: False)
    monkeypatch.setattr(match_state_module, "classify_banner", lambda frame: "win")
    monkeypatch.setattr(match_state_module, "read_precise_rank", lambda frame, gauge_roi, rank_number_roi: (10, 10.0))
    monkeypatch.setattr(match_state_module, "read_rank_gauge_fill", lambda frame, roi: 0.0)
    monkeypatch.setattr(match_state_module, "is_league_change_screen", lambda frame: False)
    monkeypatch.setattr(match_state_module, "is_demotion_label_candidate", lambda frame: False)

    machine = MatchStateMachine(
        banner_confirm_frames=5,
        banner_confirm_frames_after_match_end=1,
        match_end_confirm_frames=1,
        league_change_grace_frames=1,
        rank_stability_monitor=StabilityMonitor(roi=(0, 0, 5, 5), stable_frames_required=1),
    )

    frame = np.zeros((10, 10, 3), dtype=np.uint8)
    result = None
    for i in range(10):
        result = machine.process_frame(frame)
        if result is not None:
            assert i >= 4, f"OCRで却下されたはずの候補で短いデバウンスが使われてしまった(frame={i})"
            break

    assert result is not None, "MatchResultが確定しなかった"


def test_lifecycle_logs_reuse_session_match_number(monkeypatch, caplog):
    """試合開始(VS画面確定)→試合終了(バナー確定)→結果(結果バナー確定)の
    3つのライフサイクルログが、いずれも同じ試合番号(n試合目)で出ることを
    確認する(Issue #71)。
    """
    frame_idx = {"n": 0}

    def fake_is_vs_screen(frame):
        return frame_idx["n"] < 2

    def fake_is_match_end_screen(frame):
        return 5 <= frame_idx["n"] < 7

    def fake_classify_banner(frame):
        return "win" if frame_idx["n"] >= 8 else None

    monkeypatch.setattr(match_state_module, "is_vs_screen", fake_is_vs_screen)
    monkeypatch.setattr(match_state_module, "read_vs_screen_ranks", lambda frame: ([], []))
    monkeypatch.setattr(match_state_module, "is_match_end_screen", fake_is_match_end_screen)
    monkeypatch.setattr(match_state_module, "confirm_match_end_text", lambda frame: True)
    monkeypatch.setattr(match_state_module, "is_goal_event", lambda frame: False)
    monkeypatch.setattr(match_state_module, "classify_banner", fake_classify_banner)
    monkeypatch.setattr(match_state_module, "read_precise_rank", lambda frame, gauge_roi, rank_number_roi: (10, 10.5))
    monkeypatch.setattr(match_state_module, "read_rank_gauge_fill", lambda frame, roi: 0.5)
    monkeypatch.setattr(match_state_module, "is_league_change_screen", lambda frame: False)
    monkeypatch.setattr(match_state_module, "is_demotion_label_candidate", lambda frame: False)

    machine = MatchStateMachine(
        vs_screen_confirm_frames=2,
        match_end_confirm_frames=2,
        banner_confirm_frames_after_match_end=2,
        banner_confirm_frames=100,
        league_change_grace_frames=1,
        rank_stability_monitor=StabilityMonitor(roi=(0, 0, 5, 5), stable_frames_required=1),
    )

    frame = np.zeros((10, 10, 3), dtype=np.uint8)
    result = None
    with caplog.at_level("INFO", logger="nss_tracker.state"):
        for _ in range(20):
            result = machine.process_frame(frame)
            frame_idx["n"] += 1
            if result is not None:
                break

    assert result is not None, "MatchResultが確定しなかった"
    assert "1試合目開始" in caplog.text
    assert "1試合目 試合終了" in caplog.text
    assert "1試合目の結果: 勝ち (ランク: 10.5)" in caplog.text


def test_vs_screen_ranks_attached_to_match_result(monkeypatch):
    """VS画面検知の統合ロジック(確定時に1回だけOCRしてMatchResultへpayoutされる)を
    実映像に依存せず検証する。is_vs_screen自体の判定はtest_matchmaking.pyで、
    read_vs_screen_ranks自体の読み取り精度はtest_vs_rank.pyで別途検証済みのため、
    ここではモックする。
    """
    calls = {"n": 0}

    def fake_is_vs_screen(frame):
        # 最初の3フレームだけVS画面が出ているとみなす
        return calls["n"] < 3

    def fake_classify_banner(frame):
        n = calls["n"]
        calls["n"] += 1
        return None if n < 5 else "win"

    monkeypatch.setattr(match_state_module, "is_vs_screen", fake_is_vs_screen)
    monkeypatch.setattr(
        match_state_module,
        "read_vs_screen_ranks",
        lambda frame: (
            [SlotRank("∞", 38), SlotRank("∞", 1), SlotRank("∞", 24), SlotRank("∞", 9)],
            [SlotRank("∞", 10), SlotRank("∞", 12), SlotRank("∞", 33), SlotRank("∞", 18)],
        ),
    )
    monkeypatch.setattr(match_state_module, "is_goal_event", lambda frame: False)
    monkeypatch.setattr(match_state_module, "classify_banner", fake_classify_banner)
    monkeypatch.setattr(match_state_module, "read_precise_rank", lambda frame, gauge_roi, rank_number_roi: (10, 10.0))
    monkeypatch.setattr(match_state_module, "read_rank_gauge_fill", lambda frame, roi: 0.0)
    monkeypatch.setattr(match_state_module, "is_league_change_screen", lambda frame: False)
    monkeypatch.setattr(match_state_module, "is_demotion_label_candidate", lambda frame: False)
    monkeypatch.setattr(match_state_module, "is_match_end_screen", lambda frame: False)
    monkeypatch.setattr(match_state_module, "is_full_blackout", lambda frame: False)

    machine = MatchStateMachine(
        banner_confirm_frames=2,
        vs_screen_confirm_frames=2,
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
    assert result.vs_mine_ranks == [SlotRank("∞", 38), SlotRank("∞", 1), SlotRank("∞", 24), SlotRank("∞", 9)]
    assert result.vs_opponent_ranks == [SlotRank("∞", 10), SlotRank("∞", 12), SlotRank("∞", 33), SlotRank("∞", 18)]


def test_team_colors_attached_to_match_result(monkeypatch):
    """Issue #113: VS画面確定時にread_team_colors()で1回だけ読み取った
    チームカラーがMatchResultへ払い出されることを確認する
    (read_team_colors自体の実装はtest_team_color.pyで別途検証済みのため、
    ここではモックする)。
    """
    calls = {"n": 0}

    def fake_is_vs_screen(frame):
        return calls["n"] < 3

    def fake_classify_banner(frame):
        n = calls["n"]
        calls["n"] += 1
        return None if n < 5 else "win"

    monkeypatch.setattr(match_state_module, "is_vs_screen", fake_is_vs_screen)
    monkeypatch.setattr(
        match_state_module,
        "read_vs_screen_ranks",
        lambda frame: ([SlotRank("∞", 38)], [SlotRank("∞", 10)]),
    )
    monkeypatch.setattr(match_state_module, "read_team_colors", lambda frame: ("#64bde2", "#f87abe"))
    monkeypatch.setattr(match_state_module, "is_goal_event", lambda frame: False)
    monkeypatch.setattr(match_state_module, "classify_banner", fake_classify_banner)
    monkeypatch.setattr(match_state_module, "read_precise_rank", lambda frame, gauge_roi, rank_number_roi: (10, 10.0))
    monkeypatch.setattr(match_state_module, "read_rank_gauge_fill", lambda frame, roi: 0.0)
    monkeypatch.setattr(match_state_module, "is_league_change_screen", lambda frame: False)
    monkeypatch.setattr(match_state_module, "is_demotion_label_candidate", lambda frame: False)
    monkeypatch.setattr(match_state_module, "is_match_end_screen", lambda frame: False)
    monkeypatch.setattr(match_state_module, "is_full_blackout", lambda frame: False)

    machine = MatchStateMachine(
        banner_confirm_frames=2,
        vs_screen_confirm_frames=2,
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
    assert result.mine_team_color == "#64bde2"
    assert result.opponent_team_color == "#f87abe"


def test_vs_screen_not_detected_results_in_none_team_colors(monkeypatch):
    """VS画面を一度も検知しなかった試合では、mine_team_color/opponent_team_colorが
    Noneのままになることを確認する(vs_mine_ranks等と同じ「任意のエンリッチ」の考え方)。
    """
    calls = {"n": 0}

    def fake_classify_banner(frame):
        n = calls["n"]
        calls["n"] += 1
        return None if n < 5 else "win"

    monkeypatch.setattr(match_state_module, "is_vs_screen", lambda frame: False)
    monkeypatch.setattr(match_state_module, "is_goal_event", lambda frame: False)
    monkeypatch.setattr(match_state_module, "classify_banner", fake_classify_banner)
    monkeypatch.setattr(match_state_module, "read_precise_rank", lambda frame, gauge_roi, rank_number_roi: (10, 10.0))
    monkeypatch.setattr(match_state_module, "read_rank_gauge_fill", lambda frame, roi: 0.0)
    monkeypatch.setattr(match_state_module, "is_league_change_screen", lambda frame: False)
    monkeypatch.setattr(match_state_module, "is_demotion_label_candidate", lambda frame: False)
    monkeypatch.setattr(match_state_module, "is_match_end_screen", lambda frame: False)
    monkeypatch.setattr(match_state_module, "is_full_blackout", lambda frame: False)

    machine = MatchStateMachine(
        banner_confirm_frames=2,
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
    assert result.mine_team_color is None
    assert result.opponent_team_color is None


def test_vs_screen_confirmation_logs_ranks_at_info_level(monkeypatch, caplog):
    """Issue #121: VS画面確定を検知した瞬間(DBへの記録を待たず)に、読み取った
    mine/opponentのランクをSlotRankの簡潔な表記("∞39"等)でINFOログに出すことを
    確認する。
    """
    monkeypatch.setattr(match_state_module, "is_vs_screen", lambda frame: True)
    monkeypatch.setattr(
        match_state_module,
        "read_vs_screen_ranks",
        lambda frame: (
            [SlotRank("∞", 39), SlotRank(None, None)],
            [SlotRank("S", 9), SlotRank(None, None)],
        ),
    )
    monkeypatch.setattr(match_state_module, "is_goal_event", lambda frame: False)
    monkeypatch.setattr(match_state_module, "classify_banner", lambda frame: None)
    monkeypatch.setattr(match_state_module, "is_match_end_screen", lambda frame: False)
    monkeypatch.setattr(match_state_module, "is_full_blackout", lambda frame: False)

    machine = MatchStateMachine(vs_screen_confirm_frames=2)
    frame = np.zeros((10, 10, 3), dtype=np.uint8)

    with caplog.at_level("INFO", logger="nss_tracker.state"):
        for _ in range(3):
            machine.process_frame(frame)

    assert "1試合目 VS画面ランク: mine=[∞39, -] opponent=[S9, -]" in caplog.text


def test_pop_vs_screen_event_fires_once_at_confirmation(monkeypatch):
    """Issue #145: 試合結果確定(MatchResult)を待たず、VS画面確定を検知した直後の
    1フレームだけpop_vs_screen_event()がVsScreenEventを返すことを確認する。
    main.py側がこれをポーリングして即座にDBへ反映するための土台。
    """
    monkeypatch.setattr(match_state_module, "is_vs_screen", lambda frame: True)
    monkeypatch.setattr(
        match_state_module,
        "read_vs_screen_ranks",
        lambda frame: ([SlotRank("∞", 38)], [SlotRank("∞", 10)]),
    )
    monkeypatch.setattr(match_state_module, "read_team_colors", lambda frame: ("#64bde2", "#f87abe"))
    monkeypatch.setattr(match_state_module, "is_goal_event", lambda frame: False)
    monkeypatch.setattr(match_state_module, "classify_banner", lambda frame: None)
    monkeypatch.setattr(match_state_module, "is_match_end_screen", lambda frame: False)
    monkeypatch.setattr(match_state_module, "is_full_blackout", lambda frame: False)

    machine = MatchStateMachine(vs_screen_confirm_frames=2)
    frame = np.zeros((10, 10, 3), dtype=np.uint8)

    assert machine.pop_vs_screen_event() is None, "確定前はNoneのはず"

    machine.process_frame(frame)  # 1フレーム目: streak=1、まだconfirm_frames(=2)未満
    assert machine.pop_vs_screen_event() is None

    machine.process_frame(frame)  # 2フレーム目でconfirm_frames(=2)に到達し確定
    event = machine.pop_vs_screen_event()

    assert event is not None
    assert event.mine_ranks == [SlotRank("∞", 38)]
    assert event.opponent_ranks == [SlotRank("∞", 10)]
    assert event.mine_team_color == "#64bde2"
    assert event.opponent_team_color == "#f87abe"
    # popすると消費されるため、同じ確定を指すイベントを2度は取得できない
    assert machine.pop_vs_screen_event() is None

    machine.process_frame(frame)  # 同じVS画面がまだ表示され続けている3フレーム目
    assert machine.pop_vs_screen_event() is None, "同じVS画面が続いている間は再度発火しない"


def test_vs_screen_not_detected_results_in_empty_vs_ranks(monkeypatch):
    """VS画面を一度も検知しなかった試合では、vs_mine_ranks/vs_opponent_ranksが
    空リストのままになることを確認する(Issue #39: VS画面検知は任意のエンリッチ
    であり、見逃しても既存の結果バナー起点フローは従来通り動作させる)。
    """
    calls = {"n": 0}

    def fake_classify_banner(frame):
        n = calls["n"]
        calls["n"] += 1
        return None if n < 5 else "win"

    monkeypatch.setattr(match_state_module, "is_vs_screen", lambda frame: False)
    monkeypatch.setattr(match_state_module, "is_goal_event", lambda frame: False)
    monkeypatch.setattr(match_state_module, "classify_banner", fake_classify_banner)
    monkeypatch.setattr(match_state_module, "read_precise_rank", lambda frame, gauge_roi, rank_number_roi: (10, 10.0))
    monkeypatch.setattr(match_state_module, "read_rank_gauge_fill", lambda frame, roi: 0.0)
    monkeypatch.setattr(match_state_module, "is_league_change_screen", lambda frame: False)
    monkeypatch.setattr(match_state_module, "is_demotion_label_candidate", lambda frame: False)
    monkeypatch.setattr(match_state_module, "is_match_end_screen", lambda frame: False)
    monkeypatch.setattr(match_state_module, "is_full_blackout", lambda frame: False)

    machine = MatchStateMachine(
        banner_confirm_frames=2,
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
    assert result.vs_mine_ranks == []
    assert result.vs_opponent_ranks == []


def test_in_match_true_after_vs_screen_confirmed_and_false_after_finalize(monkeypatch):
    """Issue #83: OBSシーン自動切替のトリガーであるin_matchが、VS画面確定でTrueになり、
    試合結果確定(ランク確定含む_finalize())でFalseに戻ることを確認する。
    Issue #190対応後は「試合終了」バナーをOCR確認できた試合のみFalseに戻るため、
    ここでは確認できたケースとしてis_match_end_screen/confirm_match_end_textを
    Trueにする。
    """
    frame_idx = {"n": 0}

    def fake_is_vs_screen(frame):
        return frame_idx["n"] < 2

    def fake_is_match_end_screen(frame):
        return 2 <= frame_idx["n"] < 4

    def fake_classify_banner(frame):
        return "win" if frame_idx["n"] >= 5 else None

    monkeypatch.setattr(match_state_module, "is_vs_screen", fake_is_vs_screen)
    monkeypatch.setattr(match_state_module, "read_vs_screen_ranks", lambda frame: ([], []))
    monkeypatch.setattr(match_state_module, "is_match_end_screen", fake_is_match_end_screen)
    monkeypatch.setattr(match_state_module, "confirm_match_end_text", lambda frame: True)
    monkeypatch.setattr(match_state_module, "is_goal_event", lambda frame: False)
    monkeypatch.setattr(match_state_module, "classify_banner", fake_classify_banner)
    monkeypatch.setattr(match_state_module, "read_precise_rank", lambda frame, gauge_roi, rank_number_roi: (10, 10.0))
    monkeypatch.setattr(match_state_module, "read_rank_gauge_fill", lambda frame, roi: 0.0)
    monkeypatch.setattr(match_state_module, "is_league_change_screen", lambda frame: False)
    monkeypatch.setattr(match_state_module, "is_demotion_label_candidate", lambda frame: False)

    machine = MatchStateMachine(
        vs_screen_confirm_frames=2,
        banner_confirm_frames=2,
        banner_confirm_frames_after_match_end=2,
        match_end_confirm_frames=1,
        league_change_grace_frames=1,
        rank_stability_monitor=StabilityMonitor(roi=(0, 0, 5, 5), stable_frames_required=1),
    )

    frame = np.zeros((10, 10, 3), dtype=np.uint8)
    assert machine.in_match is False, "初期状態はFalse(試合間)のはず"

    result = None
    in_match_became_true_frame = None
    for _ in range(30):
        result = machine.process_frame(frame)
        if in_match_became_true_frame is None and machine.in_match:
            in_match_became_true_frame = frame_idx["n"]
        frame_idx["n"] += 1
        if result is not None:
            break

    assert in_match_became_true_frame is not None, "VS画面確定後にin_matchがTrueにならなかった"
    assert result is not None, "MatchResultが確定しなかった"
    assert machine.in_match is False, "「試合終了」を確認できた試合結果確定後はin_matchがFalseに戻るはず"


def test_in_match_stays_true_after_finalize_without_match_end_confirmation(monkeypatch):
    """Issue #190: 「試合終了」バナーをOCR確認できないまま試合結果が確定した場合
    (実プレイ中の背景誤検知がbanner_confirm_framesを突破した可能性を否定できない
    ケース)、OBSシーン切替の誤爆を防ぐためin_matchはTrueのまま維持され、
    (視聴者体験としては)試合中シーンに留まることを確認する。MatchResult自体は
    従来どおり記録される。
    """
    frame_idx = {"n": 0}

    def fake_is_vs_screen(frame):
        return frame_idx["n"] < 2

    def fake_classify_banner(frame):
        return "win" if frame_idx["n"] >= 5 else None

    monkeypatch.setattr(match_state_module, "is_vs_screen", fake_is_vs_screen)
    monkeypatch.setattr(match_state_module, "read_vs_screen_ranks", lambda frame: ([], []))
    monkeypatch.setattr(match_state_module, "is_match_end_screen", lambda frame: False)
    monkeypatch.setattr(match_state_module, "is_full_blackout", lambda frame: False)
    monkeypatch.setattr(match_state_module, "is_goal_event", lambda frame: False)
    monkeypatch.setattr(match_state_module, "classify_banner", fake_classify_banner)
    monkeypatch.setattr(match_state_module, "read_precise_rank", lambda frame, gauge_roi, rank_number_roi: (10, 10.0))
    monkeypatch.setattr(match_state_module, "read_rank_gauge_fill", lambda frame, roi: 0.0)
    monkeypatch.setattr(match_state_module, "is_league_change_screen", lambda frame: False)
    monkeypatch.setattr(match_state_module, "is_demotion_label_candidate", lambda frame: False)

    machine = MatchStateMachine(
        vs_screen_confirm_frames=2,
        banner_confirm_frames=2,
        league_change_grace_frames=1,
        rank_stability_monitor=StabilityMonitor(roi=(0, 0, 5, 5), stable_frames_required=1),
    )

    frame = np.zeros((10, 10, 3), dtype=np.uint8)
    result = None
    for _ in range(30):
        result = machine.process_frame(frame)
        frame_idx["n"] += 1
        if result is not None:
            break

    assert result is not None, "MatchResultが確定しなかった(記録自体は確認結果に関わらず行われるはず)"
    assert machine.in_match is True, "「試合終了」を確認できなかった場合はin_matchがTrueのまま維持されるはず"


def test_vs_screen_shown_continuously_reads_ranks_only_once(monkeypatch):
    """同じVS画面が表示され続けている間、read_vs_screen_ranks()が複数回
    呼ばれない(デバウンス)ことを確認する(重いOCRを毎フレーム呼ばないという
    CLAUDE.mdのサンプリング戦略どおりの挙動)。
    """
    read_calls = {"n": 0}

    def fake_read_vs_screen_ranks(frame):
        read_calls["n"] += 1
        return [1, None, None, None], [None, None, None, None]

    monkeypatch.setattr(match_state_module, "is_vs_screen", lambda frame: True)
    monkeypatch.setattr(match_state_module, "read_vs_screen_ranks", fake_read_vs_screen_ranks)
    monkeypatch.setattr(match_state_module, "is_goal_event", lambda frame: False)
    monkeypatch.setattr(match_state_module, "classify_banner", lambda frame: None)
    monkeypatch.setattr(match_state_module, "is_match_end_screen", lambda frame: False)
    monkeypatch.setattr(match_state_module, "is_full_blackout", lambda frame: False)

    machine = MatchStateMachine(vs_screen_confirm_frames=2)
    frame = np.zeros((10, 10, 3), dtype=np.uint8)
    for _ in range(10):
        machine.process_frame(frame)

    assert read_calls["n"] == 1
