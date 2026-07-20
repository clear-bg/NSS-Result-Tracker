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
    """動画を最後まで流し、状態が切り替わったフレーム番号とMatchResultを収集する。"""
    cap = cv2.VideoCapture(str(path))
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    cap.release()

    confirm_frames = round(fps * 1.0)
    machine = MatchStateMachine(
        banner_confirm_frames=confirm_frames,
        banner_absence_confirm_frames=confirm_frames,
        vs_screen_confirm_frames=confirm_frames,
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
    monkeypatch.setattr(match_state_module, "read_scorer_name", lambda frame: "Alice")
    monkeypatch.setattr(match_state_module, "read_assist_name", lambda frame: None)
    monkeypatch.setattr(match_state_module, "classify_banner", fake_classify_banner)
    monkeypatch.setattr(match_state_module, "read_precise_rank", lambda frame, gauge_roi: (10, 10.0))
    monkeypatch.setattr(match_state_module, "is_league_change_screen", lambda frame: False)
    monkeypatch.setattr(match_state_module, "is_vs_screen", lambda frame: False)

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
    monkeypatch.setattr(match_state_module, "classify_banner", fake_classify_banner)
    monkeypatch.setattr(match_state_module, "read_precise_rank", lambda frame, gauge_roi: None)
    monkeypatch.setattr(match_state_module, "is_league_change_screen", lambda frame: False)

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


def test_track_rank_grace_recheck_catches_slow_drift(monkeypatch):
    """GRACE中にゲージがピクセル差分の閾値を下回る速度で緩やかに変化し続けても、
    定期的な再読み取りで真の最終値まで追従できることを確認する
    (fixtures/videos/00_lose_red_2-3.mp4で見つかった、早すぎる確定による誤検知の回帰防止)。
    値は目視ではなくこのテストのために意図的に用意した架空のシーケンスであり、
    実装の出力を転記したものではない。
    """
    call_count = {"n": 0}

    def fake_read_precise_rank(frame, gauge_roi):
        call_count["n"] += 1
        # 1回目("結果バナー時点"の読み取り)・2回目(GRACE突入直後の読み取り)は
        # まだ遷移途中の値、3回目以降は真の最終値を返す
        if call_count["n"] <= 2:
            return (40, 40.77)
        return (40, 40.43)

    monkeypatch.setattr(match_state_module, "classify_banner", lambda frame: "lose")
    monkeypatch.setattr(match_state_module, "read_precise_rank", fake_read_precise_rank)
    monkeypatch.setattr(match_state_module, "is_league_change_screen", lambda frame: False)
    monkeypatch.setattr(match_state_module, "is_goal_event", lambda frame: False)
    monkeypatch.setattr(match_state_module, "is_vs_screen", lambda frame: False)

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


def test_track_rank_grace_recheck_catches_tier_change(monkeypatch):
    """GRACE突入直後の読み取りでは帯番号の変化(降格)がまだ反映されていない場合でも、
    定期的な再読み取りで正しい帯番号・league_changedにたどり着けることを確認する
    (fixtures/videos/03_lose_blue_2-3.mp4のような、降格演出が専用の全画面演出として
    出ないケースの回帰防止)。
    """
    call_count = {"n": 0}

    def fake_read_precise_rank(frame, gauge_roi):
        call_count["n"] += 1
        if call_count["n"] == 1:
            return (40, 40.09)  # 結果バナー時点(before)
        if call_count["n"] <= 3:
            return (40, 40.16)  # GRACE突入直後、まだ降格前の帯のまま
        return (39, 40.0)  # 真の最終値(降格後)

    monkeypatch.setattr(match_state_module, "classify_banner", lambda frame: "lose")
    monkeypatch.setattr(match_state_module, "read_precise_rank", fake_read_precise_rank)
    monkeypatch.setattr(match_state_module, "is_league_change_screen", lambda frame: False)
    monkeypatch.setattr(match_state_module, "is_goal_event", lambda frame: False)
    monkeypatch.setattr(match_state_module, "is_vs_screen", lambda frame: False)

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

    def fake_read_precise_rank(frame, gauge_roi):
        rois_used.append(gauge_roi)
        # 結果バナー確定直後(コンパクト表示)・GRACE突入直後(拡大表示)の読み取りは
        # いずれも失敗させ、候補が一度も埋まらない状況を再現する。
        # _fill_grace_candidate_if_missingによる最後のリトライだけ成功させる
        if len(rois_used) <= 2:
            return None
        return (40, 40.5)

    monkeypatch.setattr(match_state_module, "classify_banner", fake_classify_banner)
    monkeypatch.setattr(match_state_module, "read_precise_rank", fake_read_precise_rank)
    monkeypatch.setattr(match_state_module, "is_league_change_screen", lambda frame: False)
    monkeypatch.setattr(match_state_module, "is_goal_event", lambda frame: False)
    monkeypatch.setattr(match_state_module, "is_vs_screen", lambda frame: False)

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


def test_goal_banner_shown_continuously_records_only_one_goal(monkeypatch):
    """同じゴールバナーが表示され続けている間、複数回記録されない(デバウンス)ことを確認する。"""
    monkeypatch.setattr(match_state_module, "is_goal_event", lambda frame: True)
    monkeypatch.setattr(match_state_module, "read_scorer_name", lambda frame: "Alice")
    monkeypatch.setattr(match_state_module, "read_assist_name", lambda frame: None)
    monkeypatch.setattr(match_state_module, "classify_banner", lambda frame: None)
    monkeypatch.setattr(match_state_module, "is_vs_screen", lambda frame: False)

    machine = MatchStateMachine(goal_confirm_frames=2)
    frame = np.zeros((10, 10, 3), dtype=np.uint8)
    for _ in range(10):
        machine.process_frame(frame)

    assert len(machine._pending_goals) == 1


def test_goal_event_suppresses_banner_confirmation(monkeypatch):
    """ゴール演出中(is_goal_event=True)にclassify_bannerが誤って結果バナーの
    判定を返し続けても、banner_confirm_framesを満たして誤確定しないことを
    確認する(Issue #67: 背景のSOCCERトランジション帯がBANNER_ROIに写り込み、
    デバウンスをすり抜けて試合が誤って分割された回帰防止)。
    ゴール演出が終わった後は通常どおり本物の結果バナーを確定できることも
    あわせて確認する。
    """
    frame_idx = {"n": 0}
    # 最初の10フレームはゴール演出中(誤って"lose"判定され続ける)、
    # その後ゴール演出が終わり、本物の"win"バナーが表示される
    GOAL_EVENT_FRAMES = 10

    def fake_is_goal_event(frame):
        return frame_idx["n"] < GOAL_EVENT_FRAMES

    def fake_classify_banner(frame):
        return "lose" if frame_idx["n"] < GOAL_EVENT_FRAMES else "win"

    monkeypatch.setattr(match_state_module, "is_goal_event", fake_is_goal_event)
    monkeypatch.setattr(match_state_module, "read_scorer_name", lambda frame: None)
    monkeypatch.setattr(match_state_module, "read_assist_name", lambda frame: None)
    monkeypatch.setattr(match_state_module, "classify_banner", fake_classify_banner)
    monkeypatch.setattr(match_state_module, "read_precise_rank", lambda frame, gauge_roi: (10, 10.0))
    monkeypatch.setattr(match_state_module, "is_league_change_screen", lambda frame: False)
    monkeypatch.setattr(match_state_module, "is_vs_screen", lambda frame: False)

    machine = MatchStateMachine(
        banner_confirm_frames=5,
        goal_confirm_frames=2,
        league_change_grace_frames=1,
        rank_stability_monitor=StabilityMonitor(roi=(0, 0, 5, 5), stable_frames_required=1),
    )

    frame = np.zeros((10, 10, 3), dtype=np.uint8)
    result = None
    for i in range(30):
        result = machine.process_frame(frame)
        if result is not None:
            assert i >= GOAL_EVENT_FRAMES, (
                f"ゴール演出中(is_goal_event=True)の誤った'lose'判定でフレーム{i}に確定してしまった"
            )
            break
        frame_idx["n"] += 1

    assert result is not None, "MatchResultが確定しなかった(ゴール演出終了後の本物のバナーも検知できていない)"
    assert result.result == "win"


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
    monkeypatch.setattr(match_state_module, "read_precise_rank", lambda frame, gauge_roi: (10, 10.0))
    monkeypatch.setattr(match_state_module, "is_league_change_screen", lambda frame: False)

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
    monkeypatch.setattr(match_state_module, "read_precise_rank", lambda frame, gauge_roi: (10, 10.0))
    monkeypatch.setattr(match_state_module, "is_league_change_screen", lambda frame: False)

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

    machine = MatchStateMachine(vs_screen_confirm_frames=2)
    frame = np.zeros((10, 10, 3), dtype=np.uint8)
    for _ in range(10):
        machine.process_frame(frame)

    assert read_calls["n"] == 1
