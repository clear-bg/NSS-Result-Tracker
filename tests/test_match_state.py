import concurrent.futures
import json
import logging
from pathlib import Path

import cv2
import numpy as np
import pytest

import nss_tracker.state.match_state as match_state_module
from conftest import requires_video_fixtures
from nss_tracker.detection.motion import BlackoutObservation, StabilityMonitor
from nss_tracker.detection.rank_ocr import (
    GAUGE_ROI_COMPACT,
    GAUGE_ROI_ENLARGED,
    RANK_NUMBER_ROI_COMPACT,
    RANK_NUMBER_ROI_ENLARGED,
    RANK_ROI,
)
from nss_tracker.detection.vs_rank import SlotRank
from nss_tracker.state.match_state import (
    MatchStateMachine,
    _run_rank_before_ocr,
    _run_vs_screen_ocr,
)

TARGET_SIZE = (1920, 1080)
METADATA_FILENAME = "metadata.json"


class FakeClock:
    """呼ばれるたびにstep秒ずつ進む偽の時計(Issue #388)。

    MatchStateMachineのnow_fnに注入し、process_frame()をタイトループで
    呼ぶだけのテストでも実時間の経過を模擬する。既定step=1.0(1回の
    process_frame呼び出し=1秒)にすることで、既存テストが小さい整数値
    (例: banner_confirm_seconds=2)を「2フレーム」のつもりで渡していた
    箇所も、そのまま「2回のprocess_frame呼び出しで確定する」という
    以前と同じ反復回数で成立する(1呼び出しごとにちょうど閾値の単位分
    だけ進むため)。fixture動画をそのfpsのリアルタイム再生と同じ経過秒数で
    処理したい場合(_run_state_machine参照)は、step=1.0/fpsを明示的に渡す。
    """

    def __init__(self, step: float = 1.0) -> None:
        self._now = 0.0
        self._step = step

    def __call__(self) -> float:
        self._now += self._step
        return self._now


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
    できなかった動画は長いデバウンス(2.0秒)に自動的に切り替わる。個別の
    fixtureごとに閾値を指定する必要はない)。

    Issue #388: デバウンス閾値は実時間(秒)ベースになったが、このテストは
    OpenCVでの動画デコード速度のまま(real-timeペーシング無し)で処理するため、
    now_fn を実時間(time.monotonic)任せにすると動画自体の収録fps・尺と無関係な
    処理速度依存の値になってしまう。動画自身のfpsで1フレームあたりstep秒
    (=1/fps)進むFakeClockを注入し、「その動画をそのfpsでリアルタイム再生
    した場合の経過秒数」を処理速度に関係なく再現する(main.py側のfps換算が
    無くなったのと対になる形で、こちらも秒数値をそのまま渡すだけになった)。
    """
    cap = cv2.VideoCapture(str(path))
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    cap.release()

    machine = MatchStateMachine(
        now_fn=FakeClock(step=1.0 / fps),
        banner_confirm_seconds=2.0,
        banner_confirm_seconds_after_match_end=1.0,
        banner_absence_confirm_seconds=1.0,
        vs_screen_confirm_seconds=1.0,
        # Issue #190: main.pyの_make_match_state_machineと同じく即時(0.0秒)に固定
        match_end_confirm_seconds=0.0,
        league_change_grace_seconds=5.0,
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
        # Issue #388: goal_confirm_seconds=2(FakeClock step=1.0)を確定させるには
        # 経過2.0秒(=3フレーム分のスパン)必要なため、3フレーム分ゴールバナーが
        # 出ているとみなす
        return frame_idx["n"] < 3

    def fake_classify_banner(frame):
        return None if frame_idx["n"] < 5 else "win"

    monkeypatch.setattr(match_state_module, "is_goal_event", fake_is_goal_event)
    monkeypatch.setattr(match_state_module, "confirm_goal_text", lambda frame: True)
    monkeypatch.setattr(match_state_module, "is_own_goal_event", lambda frame: False)
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
        now_fn=FakeClock(),
        banner_confirm_seconds=2,
        goal_confirm_seconds=2,
        league_change_grace_seconds=1,
        rank_stability_monitor=StabilityMonitor(roi=(0, 0, 5, 5), stable_frames_required=1),
    )

    # Issue #229: 試合の区切りをVS画面確定に一本化したため、このテストの
    # 関心事(VS画面確定より後の挙動)を検証するには、VS画面を確認済みとして扱う
    machine._vs_confirmed_this_match = True
    # Issue #235: VS画面でランクを検知した(=ランクを賭けた)試合として扱うための
    # ショートカット(_vs_confirmed_this_matchと同じ理由でVS画面確定の全過程は再現しない)
    machine._pending_vs_mine_ranks = [SlotRank("∞", 10)]
    frame = np.zeros((10, 10, 3), dtype=np.uint8)
    result = None
    for _ in range(30):
        result = machine.process_frame(frame)
        frame_idx["n"] += 1
        if result is not None:
            break

    assert result is not None, "MatchResultが確定しなかった"
    assert len(result.goals) == 1
    assert result.goals[0].scorer_name == "Alice"
    assert result.goals[0].assist_name is None


def _stub_all_detections(monkeypatch) -> None:
    """検知系をすべて「何も見えていない」状態に倒す。個別のテストが必要な分だけ上書きする。"""
    for name in (
        "is_goal_event",
        "is_league_change_screen",
        "is_demotion_label_candidate",
        "is_vs_screen",
        "is_match_end_screen",
        "is_full_blackout",
    ):
        monkeypatch.setattr(match_state_module, name, lambda frame: False)
    monkeypatch.setattr(match_state_module, "classify_banner", lambda frame: None)


def test_dropped_match_after_match_end_confirmation_logs_warning(monkeypatch, caplog):
    """Issue #423: 「試合終了」確認済みなのに結果バナーを確定できないまま次の試合が
    始まった場合、WARNINGで気づけるようにする。

    2026-09-08の専用部屋配信では、この経路で18試合中8試合(すべて負け)が何の警告も
    出ないまま消えており、配信録画と突き合わせるまで気づけなかった。
    """
    screen = {"vs": False, "match_end": False}
    _stub_all_detections(monkeypatch)
    monkeypatch.setattr(match_state_module, "is_vs_screen", lambda frame: screen["vs"])
    monkeypatch.setattr(match_state_module, "is_match_end_screen", lambda frame: screen["match_end"])
    monkeypatch.setattr(match_state_module, "confirm_match_end_text", lambda frame: True)
    monkeypatch.setattr(match_state_module, "read_vs_screen_ranks", lambda frame: ([], []))

    machine = MatchStateMachine(
        now_fn=FakeClock(),
        vs_screen_confirm_seconds=1,
        match_end_confirm_seconds=1,
        # ロックアウトを無効にし、1回目のVS画面の直後に2回目を検知させる
        vs_screen_lockout_seconds=0,
    )
    frame = np.zeros((10, 10, 3), dtype=np.uint8)

    with caplog.at_level("INFO", logger="nss_tracker.state"):
        screen["vs"] = True  # 1試合目開始
        for _ in range(2):
            machine.process_frame(frame)
        screen["vs"] = False
        screen["match_end"] = True  # 「試合終了」を確認
        for _ in range(2):
            machine.process_frame(frame)
        assert machine.match_end_seen, "「試合終了」確認後はmatch_end_seenがTrueになるはず"
        screen["match_end"] = False
        screen["vs"] = True  # 結果バナーを確定できないまま次の試合が始まる
        for _ in range(2):
            machine.process_frame(frame)

    assert "「試合終了」を確認済みなのに結果バナーを確定できないまま" in caplog.text
    warnings = [record for record in caplog.records if record.levelname == "WARNING"]
    assert warnings, "消失はWARNINGで報告されるはず(INFOのままだと一覧して見ても気づけない)"


def test_dropped_match_without_match_end_confirmation_stays_info(monkeypatch, caplog):
    """Issue #423: 「試合終了」を確認できていない場合は従来どおりINFOのままにする。

    通信切断等によるゲーム強制終了でも起こりうる正常な状態遷移のため(Issue #243)。
    """
    screen = {"vs": False}
    _stub_all_detections(monkeypatch)
    monkeypatch.setattr(match_state_module, "is_vs_screen", lambda frame: screen["vs"])
    monkeypatch.setattr(match_state_module, "read_vs_screen_ranks", lambda frame: ([], []))

    machine = MatchStateMachine(
        now_fn=FakeClock(), vs_screen_confirm_seconds=1, vs_screen_lockout_seconds=0
    )
    frame = np.zeros((10, 10, 3), dtype=np.uint8)

    with caplog.at_level("INFO", logger="nss_tracker.state"):
        screen["vs"] = True  # 1試合目開始
        for _ in range(2):
            machine.process_frame(frame)
        screen["vs"] = False
        for _ in range(2):
            machine.process_frame(frame)
        screen["vs"] = True  # 結果バナーを確定できないまま次の試合が始まる
        for _ in range(2):
            machine.process_frame(frame)

    assert "前の試合が結果画面確定前に次のVS画面を検知しました" in caplog.text
    assert "「試合終了」を確認済みなのに" not in caplog.text
    assert [record for record in caplog.records if record.levelname == "WARNING"] == []


def test_banner_roi_stats_logged_only_after_match_end_confirmation(monkeypatch, caplog):
    """Issue #423: 閾値の再較正に使う実測値を「試合終了」確認後の区間だけDEBUGに残す。"""
    screen = {"match_end": False}
    _stub_all_detections(monkeypatch)
    monkeypatch.setattr(match_state_module, "is_match_end_screen", lambda frame: screen["match_end"])
    monkeypatch.setattr(match_state_module, "confirm_match_end_text", lambda frame: True)

    machine = MatchStateMachine(now_fn=FakeClock(), match_end_confirm_seconds=1)
    # BANNER_ROISが収まる実解像度のフレームでないと実測値を採れない
    frame = np.full((1080, 1920, 3), 40, dtype=np.uint8)

    with caplog.at_level("DEBUG", logger="nss_tracker.state"):
        machine.process_frame(frame)
        assert "試合終了後のバナーROI実測" not in caplog.text, "確認前は出さない"
        screen["match_end"] = True
        for _ in range(2):
            machine.process_frame(frame)

    assert "試合終了後のバナーROI実測" in caplog.text
    assert "H=" in caplog.text and "hue_std=" in caplog.text


def test_banner_roi_stats_log_is_throttled_while_value_is_unchanged(monkeypatch, caplog):
    """Issue #423: 値が動かない間は出し続けない(Issue #384のゲージログと同じ考え方)。"""
    _stub_all_detections(monkeypatch)
    monkeypatch.setattr(match_state_module, "is_match_end_screen", lambda frame: True)
    monkeypatch.setattr(match_state_module, "confirm_match_end_text", lambda frame: True)

    machine = MatchStateMachine(now_fn=FakeClock(), match_end_confirm_seconds=1)
    frame = np.full((1080, 1920, 3), 40, dtype=np.uint8)

    with caplog.at_level("DEBUG", logger="nss_tracker.state"):
        for _ in range(10):
            machine.process_frame(frame)

    logged = [record for record in caplog.records if "試合終了後のバナーROI実測" in record.message]
    assert len(logged) == 1, f"同じ値が続く間は1回だけのはず(実際は{len(logged)}回)"


def test_goal_detection_logs_scorer_and_assist_at_info_level(monkeypatch, caplog):
    """Issue #86: ゴール検知した瞬間に、許可リストの判定結果によらず得点者・
    アシスト名と記録対象かどうかの見込みをINFOレベルで出すことを確認する。
    実際にDBへ記録するかどうかの判定は永続化層のままで、ここではログのみ検証する。
    """
    frame_idx = {"n": 0}

    monkeypatch.setattr(match_state_module, "is_goal_event", lambda frame: frame_idx["n"] < 3)
    monkeypatch.setattr(match_state_module, "confirm_goal_text", lambda frame: True)
    monkeypatch.setattr(match_state_module, "is_own_goal_event", lambda frame: False)
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

    machine = MatchStateMachine(now_fn=FakeClock(), goal_confirm_seconds=2)
    frame = np.zeros((10, 10, 3), dtype=np.uint8)

    with caplog.at_level("INFO", logger="nss_tracker.state"):
        for _ in range(4):
            machine.process_frame(frame)
            frame_idx["n"] += 1

    assert "ゴール検知: scorer=Alice assist=Bob (記録対象)" in caplog.text


def test_own_goal_sets_is_own_goal_flag_and_logs_all_mode_status(monkeypatch, caplog):
    """Issue #217: オウンゴールは得点者名パネル自体が表示されないため
    read_scorer_name/read_assist_nameはどちらもNoneのままだが、
    is_own_goal_event()の結果がGoalEvent.is_own_goalに反映されること、
    GOAL_RECORD_MODE=allの場合は「記録対象(オウンゴール)」とログに出ることを確認する。
    """
    frame_idx = {"n": 0}

    monkeypatch.setattr(match_state_module, "is_goal_event", lambda frame: frame_idx["n"] < 3)
    monkeypatch.setattr(match_state_module, "confirm_goal_text", lambda frame: True)
    monkeypatch.setattr(match_state_module, "is_own_goal_event", lambda frame: True)
    monkeypatch.setattr(match_state_module, "read_scorer_name", lambda frame: None)
    monkeypatch.setattr(match_state_module, "read_assist_name", lambda frame: None)
    monkeypatch.setattr(match_state_module, "classify_banner", lambda frame: None)
    monkeypatch.setattr(match_state_module, "is_league_change_screen", lambda frame: False)
    monkeypatch.setattr(match_state_module, "is_demotion_label_candidate", lambda frame: False)
    monkeypatch.setattr(match_state_module, "is_vs_screen", lambda frame: False)
    monkeypatch.setattr(match_state_module, "is_match_end_screen", lambda frame: False)
    monkeypatch.setattr(match_state_module, "is_full_blackout", lambda frame: False)
    monkeypatch.setenv("GOAL_RECORD_MODE", "all")

    machine = MatchStateMachine(now_fn=FakeClock(), goal_confirm_seconds=2)
    frame = np.zeros((10, 10, 3), dtype=np.uint8)

    with caplog.at_level("INFO", logger="nss_tracker.state"):
        for _ in range(4):
            machine.process_frame(frame)
            frame_idx["n"] += 1

    assert "ゴール検知: scorer=None assist=None (記録対象(オウンゴール))" in caplog.text
    assert len(machine._pending_goals) == 1
    assert machine._pending_goals[0].is_own_goal is True


def test_own_goal_logs_not_recorded_when_mode_is_not_all(monkeypatch, caplog):
    """Issue #217: allowlist/allowlist_redactモードではオウンゴールに許可リストと
    照合できる実名が無いため、記録対象外である旨をログに出す
    (実際に記録しないこと自体は永続化層の責務)。
    """
    frame_idx = {"n": 0}

    monkeypatch.setattr(match_state_module, "is_goal_event", lambda frame: frame_idx["n"] < 3)
    monkeypatch.setattr(match_state_module, "confirm_goal_text", lambda frame: True)
    monkeypatch.setattr(match_state_module, "is_own_goal_event", lambda frame: True)
    monkeypatch.setattr(match_state_module, "read_scorer_name", lambda frame: None)
    monkeypatch.setattr(match_state_module, "read_assist_name", lambda frame: None)
    monkeypatch.setattr(match_state_module, "classify_banner", lambda frame: None)
    monkeypatch.setattr(match_state_module, "is_league_change_screen", lambda frame: False)
    monkeypatch.setattr(match_state_module, "is_demotion_label_candidate", lambda frame: False)
    monkeypatch.setattr(match_state_module, "is_vs_screen", lambda frame: False)
    monkeypatch.setattr(match_state_module, "is_match_end_screen", lambda frame: False)
    monkeypatch.setattr(match_state_module, "is_full_blackout", lambda frame: False)
    monkeypatch.setenv("GOAL_RECORD_MODE", "allowlist")

    machine = MatchStateMachine(now_fn=FakeClock(), goal_confirm_seconds=2)
    frame = np.zeros((10, 10, 3), dtype=np.uint8)

    with caplog.at_level("INFO", logger="nss_tracker.state"):
        for _ in range(4):
            machine.process_frame(frame)
            frame_idx["n"] += 1

    assert "ゴール検知: scorer=None assist=None (オウンゴールのため記録対象外)" in caplog.text
    assert machine._pending_goals[0].is_own_goal is True


def test_goal_detection_logs_not_recorded_when_outside_allowlist(monkeypatch, caplog):
    """得点者・アシストとも許可リストに無い場合、INFOログには実名を出しつつ
    「記録対象外」と分かるようにする(実際に記録しないこと自体は永続化層の責務)。
    """
    frame_idx = {"n": 0}

    monkeypatch.setattr(match_state_module, "is_goal_event", lambda frame: frame_idx["n"] < 3)
    monkeypatch.setattr(match_state_module, "confirm_goal_text", lambda frame: True)
    monkeypatch.setattr(match_state_module, "is_own_goal_event", lambda frame: False)
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

    machine = MatchStateMachine(now_fn=FakeClock(), goal_confirm_seconds=2)
    frame = np.zeros((10, 10, 3), dtype=np.uint8)

    with caplog.at_level("INFO", logger="nss_tracker.state"):
        for _ in range(4):
            machine.process_frame(frame)
            frame_idx["n"] += 1

    assert "ゴール検知: scorer=Charlie assist=None (許可リスト外のため記録対象外)" in caplog.text


def test_goal_detection_logs_always_recorded_in_all_mode(monkeypatch, caplog):
    """Issue #88: GOAL_RECORD_MODE=allの場合、許可リストに関係なく常に
    「記録対象」と表示することを確認する。
    """
    frame_idx = {"n": 0}

    monkeypatch.setattr(match_state_module, "is_goal_event", lambda frame: frame_idx["n"] < 3)
    monkeypatch.setattr(match_state_module, "confirm_goal_text", lambda frame: True)
    monkeypatch.setattr(match_state_module, "is_own_goal_event", lambda frame: False)
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

    machine = MatchStateMachine(now_fn=FakeClock(), goal_confirm_seconds=2)
    frame = np.zeros((10, 10, 3), dtype=np.uint8)

    with caplog.at_level("INFO", logger="nss_tracker.state"):
        for _ in range(4):
            machine.process_frame(frame)
            frame_idx["n"] += 1

    assert "ゴール検知: scorer=Charlie assist=None (記録対象)" in caplog.text


def test_goal_detection_logs_partial_redact_in_redact_mode(monkeypatch, caplog):
    """Issue #88: GOAL_RECORD_MODE=allowlist_redactで、得点者のみ許可リスト外の
    場合に「一部redactして記録対象」と表示することを確認する。
    """
    frame_idx = {"n": 0}

    monkeypatch.setattr(match_state_module, "is_goal_event", lambda frame: frame_idx["n"] < 3)
    monkeypatch.setattr(match_state_module, "confirm_goal_text", lambda frame: True)
    monkeypatch.setattr(match_state_module, "is_own_goal_event", lambda frame: False)
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

    machine = MatchStateMachine(now_fn=FakeClock(), goal_confirm_seconds=2)
    frame = np.zeros((10, 10, 3), dtype=np.uint8)

    with caplog.at_level("INFO", logger="nss_tracker.state"):
        for _ in range(4):
            machine.process_frame(frame)
            frame_idx["n"] += 1

    assert "ゴール検知: scorer=たなか assist=ブルドッグ (一部redactして記録対象)" in caplog.text


def test_goal_detection_logs_full_record_in_redact_mode_when_both_allowed(monkeypatch, caplog):
    """allowlist_redactでも、両者とも許可リストにいればredactせず「記録対象」と表示する。"""
    frame_idx = {"n": 0}

    monkeypatch.setattr(match_state_module, "is_goal_event", lambda frame: frame_idx["n"] < 3)
    monkeypatch.setattr(match_state_module, "confirm_goal_text", lambda frame: True)
    monkeypatch.setattr(match_state_module, "is_own_goal_event", lambda frame: False)
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

    machine = MatchStateMachine(now_fn=FakeClock(), goal_confirm_seconds=2)
    frame = np.zeros((10, 10, 3), dtype=np.uint8)

    with caplog.at_level("INFO", logger="nss_tracker.state"):
        for _ in range(4):
            machine.process_frame(frame)
            frame_idx["n"] += 1

    assert "ゴール検知: scorer=Alice assist=Bob (記録対象)" in caplog.text


def test_goal_detection_logs_no_redact_when_assist_missing_in_redact_mode(monkeypatch, caplog):
    """allowlist_redactで、得点者が許可リストにいてアシストがそもそも存在しない
    (None)場合は「redactするものが無い」ため「記録対象」と表示する。
    """
    frame_idx = {"n": 0}

    monkeypatch.setattr(match_state_module, "is_goal_event", lambda frame: frame_idx["n"] < 3)
    monkeypatch.setattr(match_state_module, "confirm_goal_text", lambda frame: True)
    monkeypatch.setattr(match_state_module, "is_own_goal_event", lambda frame: False)
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

    machine = MatchStateMachine(now_fn=FakeClock(), goal_confirm_seconds=2)
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
    monkeypatch.setattr(match_state_module, "is_league_change_screen", lambda frame: False)
    monkeypatch.setattr(match_state_module, "is_demotion_label_candidate", lambda frame: False)

    machine = MatchStateMachine(
        now_fn=FakeClock(),
        banner_confirm_seconds=2,
        banner_absence_confirm_seconds=2,
        goal_confirm_seconds=2,
        league_change_grace_seconds=1,
        rank_recheck_interval_seconds=1,
        rank_stability_monitor=StabilityMonitor(roi=(0, 0, 5, 5), stable_frames_required=1),
    )

    # Issue #229: 試合の区切りをVS画面確定に一本化したため、このテストの
    # 関心事(VS画面確定より後の挙動)を検証するには、VS画面を確認済みとして扱う
    machine._vs_confirmed_this_match = True
    # Issue #235: VS画面でランクを検知した(=ランクを賭けた)試合として扱うための
    # ショートカット(_vs_confirmed_this_matchと同じ理由でVS画面確定の全過程は再現しない)
    machine._pending_vs_mine_ranks = [SlotRank("∞", 1)]
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


def test_rank_read_failure_still_reports_confirmed_demotion_label(monkeypatch):
    """Issue #374: ランクバッジが最後まで読み取れない(rank_after=None)試合でも、
    降格ラベル自体は独立して検知できていれば、MatchResult.league_change_label_detected
    に"down"として持ち回ることを確認する(実際に7試合目で起きたケース: バッジ完全
    未読のまま降格ラベルだけ検知され、その情報が失われて次の試合のrank_beforeが
    1帯ズレて記録された。database.db側の対応(#374実装)はこのフィールドを前提にする)。
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
    monkeypatch.setattr(match_state_module, "is_league_change_screen", lambda frame: False)
    monkeypatch.setattr(match_state_module, "is_demotion_label_candidate", lambda frame: True)
    monkeypatch.setattr(match_state_module, "confirm_demotion_label_text", lambda frame: True)

    machine = MatchStateMachine(
        now_fn=FakeClock(),
        banner_confirm_seconds=2,
        banner_absence_confirm_seconds=2,
        goal_confirm_seconds=2,
        league_change_grace_seconds=1,
        rank_recheck_interval_seconds=1,
        demotion_label_confirm_seconds=1,
        rank_stability_monitor=StabilityMonitor(roi=(0, 0, 5, 5), stable_frames_required=1),
    )

    machine._vs_confirmed_this_match = True
    machine._pending_vs_mine_ranks = [SlotRank("∞", 1)]
    frame = np.zeros((10, 10, 3), dtype=np.uint8)
    result = None
    for _ in range(30):
        r = machine.process_frame(frame)
        if r is not None:
            result = r
            break

    assert result is not None, "MatchResultが確定しなかった"
    assert result.rank_before is None
    assert result.rank_after is None
    assert result.league_change_label_detected == "down"


def test_read_rank_before_prefers_vs_screen_tier_over_conflicting_ocr(monkeypatch, caplog):
    """Issue #283: 結果バナー確定時点の帯番号OCRがVS画面の読み取りと食い違う場合、
    VS画面側(精度が高い)を優先して採用することを確認する(実機で41→0と誤読した
    事象の回帰防止)。ゲージの溜まり具合(小数部)は従来通りOCR側の値をそのまま使う。
    """
    monkeypatch.setattr(match_state_module, "is_goal_event", lambda frame: False)
    monkeypatch.setattr(match_state_module, "is_vs_screen", lambda frame: False)
    monkeypatch.setattr(match_state_module, "is_match_end_screen", lambda frame: False)
    monkeypatch.setattr(match_state_module, "is_full_blackout", lambda frame: False)
    monkeypatch.setattr(match_state_module, "classify_banner", lambda frame: "lose")
    # 帯番号を0と誤読(実機事象の再現)。小数部0.98は正しく読めている想定
    monkeypatch.setattr(match_state_module, "read_precise_rank", lambda frame, gauge_roi, rank_number_roi: (0, 0.98))
    monkeypatch.setattr(match_state_module, "read_rank_gauge_fill", lambda frame, roi: 0.98)
    monkeypatch.setattr(match_state_module, "is_league_change_screen", lambda frame: False)
    monkeypatch.setattr(match_state_module, "is_demotion_label_candidate", lambda frame: False)

    machine = MatchStateMachine(
        now_fn=FakeClock(),
        banner_confirm_seconds=2,
        rank_stability_monitor=StabilityMonitor(roi=(0, 0, 5, 5), stable_frames_required=1),
    )
    machine._vs_confirmed_this_match = True
    # VS画面は正しく∞41と読めていた想定
    machine._pending_vs_mine_ranks = [SlotRank("∞", 41)]

    frame = np.zeros((10, 10, 3), dtype=np.uint8)
    with caplog.at_level("WARNING", logger="nss_tracker.state"):
        for _ in range(5):
            machine.process_frame(frame)
            if machine._pending_rank_before_tier is not None:
                break

    assert machine._pending_rank_before_tier == 41, "帯番号はVS画面側(41)を採用するはず"
    assert machine._pending_rank_before == pytest.approx(41.98), (
        "小数部(ゲージの溜まり具合)はOCR側の値をそのまま使うはず"
    )
    assert "帯番号OCR(0)がVS画面の読み取り(41)と食い違っています" in caplog.text


def test_read_rank_before_falls_back_to_ocr_when_vs_screen_tier_unavailable(monkeypatch):
    """Issue #283: VS画面で自分のランクを検知できていない(S/A帯、または未確認)場合は、
    従来通り結果バナー確定時点のOCR結果をそのまま使うことを確認する。
    """
    monkeypatch.setattr(match_state_module, "is_goal_event", lambda frame: False)
    monkeypatch.setattr(match_state_module, "is_vs_screen", lambda frame: False)
    monkeypatch.setattr(match_state_module, "is_match_end_screen", lambda frame: False)
    monkeypatch.setattr(match_state_module, "is_full_blackout", lambda frame: False)
    monkeypatch.setattr(match_state_module, "classify_banner", lambda frame: "lose")
    monkeypatch.setattr(match_state_module, "read_precise_rank", lambda frame, gauge_roi, rank_number_roi: (12, 12.5))
    monkeypatch.setattr(match_state_module, "read_rank_gauge_fill", lambda frame, roi: 0.5)
    monkeypatch.setattr(match_state_module, "is_league_change_screen", lambda frame: False)
    monkeypatch.setattr(match_state_module, "is_demotion_label_candidate", lambda frame: False)

    machine = MatchStateMachine(
        now_fn=FakeClock(),
        banner_confirm_seconds=2,
        rank_stability_monitor=StabilityMonitor(roi=(0, 0, 5, 5), stable_frames_required=1),
    )
    machine._vs_confirmed_this_match = True
    # S帯はrank_before/afterの追跡対象外のため、OCR結果にフォールバックするはず
    machine._pending_vs_mine_ranks = [SlotRank("S", 9)]

    frame = np.zeros((10, 10, 3), dtype=np.uint8)
    for _ in range(5):
        machine.process_frame(frame)
        if machine._pending_rank_before_tier is not None:
            break

    assert machine._pending_rank_before_tier == 12
    assert machine._pending_rank_before == pytest.approx(12.5)


def test_read_rank_before_returns_none_when_ocr_completely_fails_even_with_vs_screen_tier(monkeypatch, caplog):
    """Issue #283: バッジ自体が読み取れない(compact/enlargedどちらのOCRも失敗する)
    場合は、VS画面側の帯番号があっても値を捏造せずNoneのまま返すことを確認する
    (「バッジが無い」ことと「帯番号だけ誤読した」ことを混同しないため)。
    """
    calls = {"n": 0}

    def fake_classify_banner(frame):
        n = calls["n"]
        calls["n"] += 1
        # banner_confirm_frames分は"lose"を返して確定させ、以降はTRACKING_RANK中に
        # バナーが消えている状態(banner_absence_confirm_frames)を再現する
        return "lose" if n < 3 else None

    monkeypatch.setattr(match_state_module, "is_goal_event", lambda frame: False)
    monkeypatch.setattr(match_state_module, "is_vs_screen", lambda frame: False)
    monkeypatch.setattr(match_state_module, "is_match_end_screen", lambda frame: False)
    monkeypatch.setattr(match_state_module, "is_full_blackout", lambda frame: False)
    monkeypatch.setattr(match_state_module, "classify_banner", fake_classify_banner)
    monkeypatch.setattr(match_state_module, "read_precise_rank", lambda frame, gauge_roi, rank_number_roi: None)
    monkeypatch.setattr(match_state_module, "read_rank_gauge_fill", lambda frame, roi: None)
    monkeypatch.setattr(match_state_module, "is_league_change_screen", lambda frame: False)
    monkeypatch.setattr(match_state_module, "is_demotion_label_candidate", lambda frame: False)

    machine = MatchStateMachine(
        now_fn=FakeClock(),
        banner_confirm_seconds=2,
        banner_absence_confirm_seconds=2,
        league_change_grace_seconds=1,
        rank_recheck_interval_seconds=1,
        rank_stability_monitor=StabilityMonitor(roi=(0, 0, 5, 5), stable_frames_required=1),
    )
    machine._vs_confirmed_this_match = True
    machine._pending_vs_mine_ranks = [SlotRank("∞", 41)]

    frame = np.zeros((10, 10, 3), dtype=np.uint8)
    result = None
    with caplog.at_level("INFO", logger="nss_tracker.state"):
        for _ in range(30):
            r = machine.process_frame(frame)
            if r is not None:
                result = r
                break

    assert result is not None, "MatchResultが確定しなかった"
    assert result.rank_before is None, "OCRが完全に失敗した場合はVS画面側があっても値を捏造しないはず"
    assert "結果バナー確定時点でランクバッジを読み取れませんでした" in caplog.text


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
    monkeypatch.setattr(match_state_module, "is_league_change_screen", lambda frame: False)
    monkeypatch.setattr(match_state_module, "is_demotion_label_candidate", lambda frame: False)
    monkeypatch.setattr(match_state_module, "is_goal_event", lambda frame: False)
    monkeypatch.setattr(match_state_module, "is_vs_screen", lambda frame: False)
    monkeypatch.setattr(match_state_module, "is_match_end_screen", lambda frame: False)
    monkeypatch.setattr(match_state_module, "is_full_blackout", lambda frame: False)

    machine = MatchStateMachine(
        now_fn=FakeClock(),
        banner_confirm_seconds=2,
        league_change_grace_seconds=10,
        rank_recheck_interval_seconds=3,
        rank_stability_monitor=StabilityMonitor(roi=(0, 0, 5, 5), stable_frames_required=2),
    )

    # Issue #229: 試合の区切りをVS画面確定に一本化したため、このテストの
    # 関心事(VS画面確定より後の挙動)を検証するには、VS画面を確認済みとして扱う
    machine._vs_confirmed_this_match = True
    # Issue #235: VS画面でランクを検知した(=ランクを賭けた)試合として扱うための
    # ショートカット(_vs_confirmed_this_matchと同じ理由でVS画面確定の全過程は再現しない)
    machine._pending_vs_mine_ranks = [SlotRank("∞", 40)]
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


def test_track_rank_grace_debounces_transition_spike_and_fade_noise(monkeypatch):
    """Issue #235: 結果バナー消灯直後のワイプ演出による一瞬の急騰や、ランクバッジが
    画面外へ消えて暗転へフェードしていく過程での急落など、遷移演出由来のノイズが
    _latest_gauge_fillへ混入しないことを確認する。

    2026-08-05実機テストセッション・3試合目の回帰: 0.10前後で安定していた値が、
    ワイプ演出で0.86まで急騰した後フェードで0.0まで急落し、暗転即確定パスが
    その0.0(バッジが画面から消えただけの無意味な値)を誤って採用していた。
    """
    fill_sequence = [0.10] * 20 + [0.40, 0.75, 0.86, 0.70, 0.30, 0.0]
    state = {"fill_idx": 0, "last_fill": None}

    def fake_read_rank_gauge_fill(frame, gauge_roi):
        idx = min(state["fill_idx"], len(fill_sequence) - 1)
        state["fill_idx"] += 1
        state["last_fill"] = fill_sequence[idx]
        return state["last_fill"]

    def fake_is_full_blackout(frame):
        # ノイズの最後(0.0)が読まれた直後のフレームで暗転が来る想定。
        # 0.0がrank_recheck_interval_frames分連続する前に確定させることで、
        # このノイズ自体が確定値になってしまわないかを検証する
        return state["last_fill"] == 0.0

    monkeypatch.setattr(match_state_module, "classify_banner", lambda frame: "lose")
    monkeypatch.setattr(
        match_state_module, "read_precise_rank", lambda frame, gauge_roi, rank_number_roi: (39, 39.10)
    )
    monkeypatch.setattr(match_state_module, "read_rank_gauge_fill", fake_read_rank_gauge_fill)
    monkeypatch.setattr(match_state_module, "is_league_change_screen", lambda frame: False)
    monkeypatch.setattr(match_state_module, "is_demotion_label_candidate", lambda frame: False)
    monkeypatch.setattr(match_state_module, "is_goal_event", lambda frame: False)
    monkeypatch.setattr(match_state_module, "is_vs_screen", lambda frame: False)
    monkeypatch.setattr(match_state_module, "is_match_end_screen", lambda frame: False)
    monkeypatch.setattr(match_state_module, "is_full_blackout", fake_is_full_blackout)

    machine = MatchStateMachine(
        now_fn=FakeClock(),
        banner_confirm_seconds=2,
        league_change_grace_seconds=1000,
        rank_recheck_interval_seconds=10,
        rank_stability_monitor=StabilityMonitor(roi=(0, 0, 5, 5), stable_frames_required=2),
    )
    machine._vs_confirmed_this_match = True
    machine._pending_vs_mine_ranks = [SlotRank("∞", 39)]
    frame = np.zeros((10, 10, 3), dtype=np.uint8)
    result = None
    for _ in range(60):
        result = machine.process_frame(frame)
        if result is not None:
            break

    assert result is not None, "MatchResultが確定しなかった"
    assert result.rank_after == pytest.approx(39.10), (
        f"遷移演出のノイズ(急騰・急落)を確定値として拾ってしまっている: {result.rank_after}"
    )


def test_track_rank_grace_logs_gauge_value_every_frame_at_debug_level(monkeypatch, caplog):
    """Issue #235: GRACE中、ランクゲージの塗りつぶし値をDEBUGレベルで毎フレーム
    ログに出すことを確認する(実機デバッグ時に確定ロジックの挙動を追えるようにする目的)。
    """
    monkeypatch.setattr(match_state_module, "classify_banner", lambda frame: "lose")
    monkeypatch.setattr(
        match_state_module, "read_precise_rank", lambda frame, gauge_roi, rank_number_roi: (39, 39.10)
    )
    monkeypatch.setattr(match_state_module, "read_rank_gauge_fill", lambda frame, roi: 0.10)
    monkeypatch.setattr(match_state_module, "is_league_change_screen", lambda frame: False)
    monkeypatch.setattr(match_state_module, "is_demotion_label_candidate", lambda frame: False)
    monkeypatch.setattr(match_state_module, "is_goal_event", lambda frame: False)
    monkeypatch.setattr(match_state_module, "is_vs_screen", lambda frame: False)
    monkeypatch.setattr(match_state_module, "is_match_end_screen", lambda frame: False)
    monkeypatch.setattr(match_state_module, "is_full_blackout", lambda frame: False)

    machine = MatchStateMachine(
        now_fn=FakeClock(),
        banner_confirm_seconds=2,
        league_change_grace_seconds=1000,
        rank_recheck_interval_seconds=10,
        rank_stability_monitor=StabilityMonitor(roi=(0, 0, 5, 5), stable_frames_required=2),
    )
    machine._vs_confirmed_this_match = True
    machine._pending_vs_mine_ranks = [SlotRank("∞", 39)]
    frame = np.zeros((10, 10, 3), dtype=np.uint8)
    with caplog.at_level("DEBUG", logger="nss_tracker.state"):
        for _ in range(10):
            machine.process_frame(frame)

    assert "0試合目 ランクゲージ: tier=39 fill=0.100" in caplog.text


def test_check_for_vs_screen_no_longer_logs_hsv_debug(monkeypatch, caplog):
    """Issue #235: VS_ROIの生HSV値を毎フレームDEBUGログに出す処理は不要になった
    ため削除した(実機デバッグでもう使っていなかったため)。DEBUGレベルでも
    このログが出ないことを確認する。
    """
    monkeypatch.setattr(match_state_module, "is_vs_screen", lambda frame: False)
    monkeypatch.setattr(match_state_module, "is_goal_event", lambda frame: False)
    monkeypatch.setattr(match_state_module, "classify_banner", lambda frame: None)
    monkeypatch.setattr(match_state_module, "is_match_end_screen", lambda frame: False)

    machine = MatchStateMachine(now_fn=FakeClock())
    frame = np.zeros((10, 10, 3), dtype=np.uint8)
    with caplog.at_level("DEBUG", logger="nss_tracker.state"):
        for _ in range(5):
            machine.process_frame(frame)

    assert "VS_ROI HSV" not in caplog.text


def test_win_without_promotion_keeps_tier_and_takes_gauge_fraction_only(monkeypatch):
    """勝ちだが昇格演出を確認できていない試合は、帯番号を変えずゲージ小数部だけを
    採用することを確認する(Issue #136 / #396)。

    Issue #136時点は「帯番号OCRが不自然な値を返し、再スキャンしても直らない場合の
    フォールバック」だったが、Issue #396でGRACE中の帯番号OCRを廃止したため、
    この規則が帯番号を決める唯一の方法になった(_infer_tier_after参照)。
    """
    read_calls = {"n": 0}
    raw_calls = {"n": 0}

    def fake_read_precise_rank(frame, gauge_roi, rank_number_roi):
        raw_calls["n"] += 1
        if raw_calls["n"] == 2:
            # Issue #222: _apply_rank_before_ocr()の拡大ROI側フォールバック呼び出し。
            # このテストは結果バナー確定時点をコンパクト表示想定にしているため失敗させる
            return None
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
        now_fn=FakeClock(),
        banner_confirm_seconds=2,
        league_change_grace_seconds=3,
        rank_recheck_interval_seconds=1000,
        rank_stability_monitor=StabilityMonitor(roi=(0, 0, 5, 5), stable_frames_required=2),
    )

    # Issue #229: 試合の区切りをVS画面確定に一本化したため、このテストの
    # 関心事(VS画面確定より後の挙動)を検証するには、VS画面を確認済みとして扱う
    machine._vs_confirmed_this_match = True
    # Issue #235: VS画面でランクを検知した(=ランクを賭けた)試合として扱うための
    # ショートカット(_vs_confirmed_this_matchと同じ理由でVS画面確定の全過程は再現しない)
    machine._pending_vs_mine_ranks = [SlotRank("∞", 38)]
    frame = np.zeros((10, 10, 3), dtype=np.uint8)
    result = None
    for _ in range(60):
        result = machine.process_frame(frame)
        if result is not None:
            break

    assert result is not None, "MatchResultが確定しなかった"
    assert result.rank_after == pytest.approx(38.4)
    assert result.league_changed is None


def test_lose_with_gauge_wraparound_infers_demotion(monkeypatch):
    """負け試合でゲージ小数部が0を割り込んで大きく増えて見える(0.2→0.9)場合は
    降格と推測し、帯番号を1つ下げて記録することを確認する(Issue #136 / #396)。

    降格ラベル(独立信号)を確認できなかった場合のフォールバック経路。
    """
    read_calls = {"n": 0}
    raw_calls = {"n": 0}

    def fake_read_precise_rank(frame, gauge_roi, rank_number_roi):
        raw_calls["n"] += 1
        if raw_calls["n"] == 2:
            # Issue #222: _apply_rank_before_ocr()の拡大ROI側フォールバック呼び出し。
            # このテストは結果バナー確定時点をコンパクト表示想定にしているため失敗させる
            return None
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
        now_fn=FakeClock(),
        banner_confirm_seconds=2,
        league_change_grace_seconds=3,
        rank_recheck_interval_seconds=1000,
        rank_stability_monitor=StabilityMonitor(roi=(0, 0, 5, 5), stable_frames_required=2),
    )

    # Issue #229: 試合の区切りをVS画面確定に一本化したため、このテストの
    # 関心事(VS画面確定より後の挙動)を検証するには、VS画面を確認済みとして扱う
    machine._vs_confirmed_this_match = True
    # Issue #235: VS画面でランクを検知した(=ランクを賭けた)試合として扱うための
    # ショートカット(_vs_confirmed_this_matchと同じ理由でVS画面確定の全過程は再現しない)
    machine._pending_vs_mine_ranks = [SlotRank("∞", 38)]
    frame = np.zeros((10, 10, 3), dtype=np.uint8)
    result = None
    for _ in range(60):
        result = machine.process_frame(frame)
        if result is not None:
            break

    assert result is not None, "MatchResultが確定しなかった"
    assert result.rank_after == pytest.approx(37.9)
    assert result.league_changed == "down"


def test_draw_always_keeps_tier_unchanged(monkeypatch):
    """引き分け試合はゲージが全く動かない仕様のため、常に試合前の帯番号を
    据え置くことを確認する(Issue #136 / #396)。
    """
    read_calls = {"n": 0}
    raw_calls = {"n": 0}

    def fake_read_precise_rank(frame, gauge_roi, rank_number_roi):
        raw_calls["n"] += 1
        if raw_calls["n"] == 2:
            # Issue #222: _apply_rank_before_ocr()の拡大ROI側フォールバック呼び出し。
            # このテストは結果バナー確定時点をコンパクト表示想定にしているため失敗させる
            return None
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
        now_fn=FakeClock(),
        banner_confirm_seconds=2,
        league_change_grace_seconds=3,
        rank_recheck_interval_seconds=1000,
        rank_stability_monitor=StabilityMonitor(roi=(0, 0, 5, 5), stable_frames_required=2),
    )

    # Issue #229: 試合の区切りをVS画面確定に一本化したため、このテストの
    # 関心事(VS画面確定より後の挙動)を検証するには、VS画面を確認済みとして扱う
    machine._vs_confirmed_this_match = True
    # Issue #235: VS画面でランクを検知した(=ランクを賭けた)試合として扱うための
    # ショートカット(_vs_confirmed_this_matchと同じ理由でVS画面確定の全過程は再現しない)
    machine._pending_vs_mine_ranks = [SlotRank("∞", 38)]
    frame = np.zeros((10, 10, 3), dtype=np.uint8)
    result = None
    for _ in range(60):
        result = machine.process_frame(frame)
        if result is not None:
            break

    assert result is not None, "MatchResultが確定しなかった"
    assert result.league_changed is None
    assert result.rank_after == pytest.approx(38.9)


def test_demotion_label_wins_over_small_gauge_magnitude(monkeypatch):
    """Issue #176: 降格ラベルを独立信号として確認できた場合、ゲージ小数部の
    増加幅がRANK_TIER_WRAP_MIN_MAGNITUDE未満(ゲージの連続性だけでは降格と
    判断できない)であっても、帯番号を1つ下げて記録することを確認する。
    """
    read_calls = {"n": 0}
    raw_calls = {"n": 0}

    def fake_read_precise_rank(frame, gauge_roi, rank_number_roi):
        raw_calls["n"] += 1
        if raw_calls["n"] == 2:
            # Issue #222: _apply_rank_before_ocr()の拡大ROI側フォールバック呼び出し。
            # このテストは結果バナー確定時点をコンパクト表示想定にしているため失敗させる
            return None
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
        now_fn=FakeClock(),
        banner_confirm_seconds=2,
        league_change_grace_seconds=3,
        rank_recheck_interval_seconds=1000,
        demotion_label_confirm_seconds=2,
        rank_stability_monitor=StabilityMonitor(roi=(0, 0, 5, 5), stable_frames_required=2),
    )

    # Issue #229: 試合の区切りをVS画面確定に一本化したため、このテストの
    # 関心事(VS画面確定より後の挙動)を検証するには、VS画面を確認済みとして扱う
    machine._vs_confirmed_this_match = True
    # Issue #235: VS画面でランクを検知した(=ランクを賭けた)試合として扱うための
    # ショートカット(_vs_confirmed_this_matchと同じ理由でVS画面確定の全過程は再現しない)
    machine._pending_vs_mine_ranks = [SlotRank("∞", 38)]
    frame = np.zeros((10, 10, 3), dtype=np.uint8)
    result = None
    for _ in range(60):
        result = machine.process_frame(frame)
        if result is not None:
            break

    assert result is not None, "MatchResultが確定しなかった"
    assert result.rank_after == pytest.approx(37.3)
    assert result.league_changed == "down"


class _RecordingExecutor:
    """submitされた関数を記録し、その場で同期実行して完了済みFutureを返すフェイク。

    Issue #397: 重いOCRが「メインループから直接呼ばれていない(=Executor経由で
    別プロセスへ渡せる形になっている)」ことを検証するために使う。
    """

    def __init__(self) -> None:
        self.submitted: list = []

    def submit(self, fn, *args, **kwargs):
        self.submitted.append(fn)
        future: concurrent.futures.Future = concurrent.futures.Future()
        future.set_result(fn(*args, **kwargs))
        return future


def test_obs_switch_uses_blackout_observation_when_frame_itself_is_not_black(monkeypatch):
    """Issue #398: process_frame()に渡されたBlackoutObservationが暗転を報告して
    いれば、そのフレーム自体が暗転していなくても暗転検知として扱うことを確認する。

    検知ループが重い処理で止まっている間にキャプチャ側だけが観測した暗転
    (0.40秒しかなく、read()が返す「最新フレーム」には既に写っていない)を
    取りこぼさないための経路。
    """
    frame_idx = {"n": 0}

    monkeypatch.setattr(match_state_module, "is_vs_screen", lambda frame: frame_idx["n"] < 3)
    monkeypatch.setattr(match_state_module, "read_vs_screen_ranks", lambda frame: ([], []))
    monkeypatch.setattr(
        match_state_module, "is_match_end_screen", lambda frame: 3 <= frame_idx["n"] < 5
    )
    monkeypatch.setattr(match_state_module, "confirm_match_end_text", lambda frame: True)
    monkeypatch.setattr(match_state_module, "is_goal_event", lambda frame: False)
    monkeypatch.setattr(match_state_module, "classify_banner", lambda frame: None)
    monkeypatch.setattr(match_state_module, "read_rank_gauge_fill", lambda frame, roi: 0.0)
    monkeypatch.setattr(match_state_module, "is_league_change_screen", lambda frame: False)
    monkeypatch.setattr(match_state_module, "is_demotion_label_candidate", lambda frame: False)
    # フレーム単体では絶対に暗転と判定されない状態にしておく
    monkeypatch.setattr(match_state_module, "is_full_blackout", lambda frame: False)

    machine = MatchStateMachine(
        now_fn=FakeClock(),
        vs_screen_confirm_seconds=2,
        match_end_confirm_seconds=1,
        obs_switch_delay_after_blackout_seconds=3,
        obs_switch_timeout_seconds=1000,
        rank_stability_monitor=StabilityMonitor(roi=(0, 0, 5, 5), stable_frames_required=1),
    )

    frame = np.zeros((10, 10, 3), dtype=np.uint8)
    not_black = BlackoutObservation(blackout=False, min_mean=120.0, min_std=40.0)
    for _ in range(5):
        machine.process_frame(frame, not_black)
        frame_idx["n"] += 1
    assert machine.in_match is True, "VS画面確定〜試合終了直後はTrueのはず"

    for _ in range(5):
        machine.process_frame(frame, not_black)
        frame_idx["n"] += 1
    assert machine.in_match is True, "暗転を観測していない間はTrueのまま維持されるはず"

    # メインループが止まっている間にキャプチャ側だけが暗転を観測した
    saw_blackout = BlackoutObservation(blackout=True, min_mean=0.0, min_std=0.5)
    machine.process_frame(frame, saw_blackout)  # 暗転検知1フレーム目(elapsed=0)
    for _ in range(3):
        machine.process_frame(frame, not_black)

    assert machine.in_match is False, (
        "キャプチャ側の観測で暗転を検知し、delay経過後にFalseへ戻るはず"
    )


def test_vs_screen_and_rank_before_ocr_go_through_the_executor(monkeypatch):
    """Issue #397: VS画面ランクOCRと結果バナー確定時のランクバッジ読み取りが、
    どちらもメインループから直接ではなくExecutor経由で実行されることを確認する。

    本番ではこのExecutorがProcessPoolExecutorになり、PaddleOCR推論の間も
    GILが解放されるため、FfmpegFrameReaderの読み取りスレッドが止まらなくなる
    (#383の「検知ループの盲区」対策、モジュールdocstring参照)。
    """
    frame_idx = {"n": 0}

    monkeypatch.setattr(match_state_module, "is_vs_screen", lambda frame: frame_idx["n"] < 3)
    monkeypatch.setattr(
        match_state_module, "read_vs_screen_ranks", lambda frame: ([SlotRank("∞", 38)], [])
    )
    monkeypatch.setattr(match_state_module, "read_team_colors", lambda frame: ("#111111", "#222222"))
    monkeypatch.setattr(
        match_state_module,
        "read_precise_rank",
        lambda frame, gauge_roi, rank_number_roi: (38, 38.2)
        if rank_number_roi == RANK_NUMBER_ROI_COMPACT
        else None,
    )
    monkeypatch.setattr(match_state_module, "read_rank_gauge_fill", lambda frame, roi: 0.2)
    monkeypatch.setattr(match_state_module, "classify_banner", lambda frame: "win" if frame_idx["n"] >= 4 else None)
    monkeypatch.setattr(match_state_module, "is_goal_event", lambda frame: False)
    monkeypatch.setattr(match_state_module, "is_match_end_screen", lambda frame: False)
    monkeypatch.setattr(match_state_module, "is_league_change_screen", lambda frame: False)
    monkeypatch.setattr(match_state_module, "is_demotion_label_candidate", lambda frame: False)
    monkeypatch.setattr(match_state_module, "is_full_blackout", lambda frame: False)

    executor = _RecordingExecutor()
    machine = MatchStateMachine(
        now_fn=FakeClock(),
        vs_screen_confirm_seconds=2,
        banner_confirm_seconds=2,
        rank_ocr_executor=executor,
        rank_stability_monitor=StabilityMonitor(roi=(0, 0, 5, 5), stable_frames_required=1),
    )

    frame = np.zeros((10, 10, 3), dtype=np.uint8)
    for _ in range(10):
        machine.process_frame(frame)
        frame_idx["n"] += 1

    assert executor.submitted == [_run_vs_screen_ocr, _run_rank_before_ocr], (
        f"Executor経由で実行されたOCRが想定と違う: {executor.submitted}"
    )
    # Executor経由でもVS画面の読み取り結果はきちんと反映される
    assert machine._pending_vs_mine_ranks == [SlotRank("∞", 38)]
    assert machine._pending_mine_team_color == "#111111"


def test_grace_never_calls_tier_ocr_and_seeds_tier_from_rank_before(monkeypatch):
    """Issue #396: TRACKING_RANK(GRACE)中は帯番号OCRを一切呼ばず、帯番号は
    結果バナー確定時に読み取った試合前の値を起点にすることを確認する。

    read_precise_rank()が呼ばれるのは_apply_rank_before_ocr()(結果バナー確定時、
    コンパクト/拡大の2回)だけで、GRACE中は軽量なread_rank_gauge_fill()しか
    呼ばれない。この2つが#383の「検知ループの盲区」の主因(1回1.5〜1.7秒の
    ブロック)だったため、呼ばれないこと自体が本Issueの成果物になる。
    """
    precise_rois = []
    gauge_calls = {"n": 0}

    def fake_read_precise_rank(frame, gauge_roi, rank_number_roi):
        precise_rois.append(rank_number_roi)
        # コンパクト側だけ成功させる(拡大側は_apply_rank_before_ocr()のフォールバック)
        return (38, 38.2) if rank_number_roi == RANK_NUMBER_ROI_COMPACT else None

    def fake_read_rank_gauge_fill(frame, roi):
        gauge_calls["n"] += 1
        return 0.5

    monkeypatch.setattr(match_state_module, "classify_banner", lambda frame: "win")
    monkeypatch.setattr(match_state_module, "read_precise_rank", fake_read_precise_rank)
    monkeypatch.setattr(match_state_module, "read_rank_gauge_fill", fake_read_rank_gauge_fill)
    monkeypatch.setattr(match_state_module, "is_league_change_screen", lambda frame: False)
    monkeypatch.setattr(match_state_module, "is_demotion_label_candidate", lambda frame: False)
    monkeypatch.setattr(match_state_module, "is_goal_event", lambda frame: False)
    monkeypatch.setattr(match_state_module, "is_vs_screen", lambda frame: False)
    monkeypatch.setattr(match_state_module, "is_match_end_screen", lambda frame: False)
    monkeypatch.setattr(match_state_module, "is_full_blackout", lambda frame: False)

    machine = MatchStateMachine(
        now_fn=FakeClock(),
        banner_confirm_seconds=2,
        league_change_grace_seconds=3,
        rank_recheck_interval_seconds=1,
        rank_stability_monitor=StabilityMonitor(roi=(0, 0, 5, 5), stable_frames_required=2),
    )
    machine._vs_confirmed_this_match = True
    machine._pending_vs_mine_ranks = [SlotRank("∞", 38)]

    frame = np.zeros((10, 10, 3), dtype=np.uint8)
    result = None
    for _ in range(60):
        result = machine.process_frame(frame)
        if result is not None:
            break

    assert result is not None, "MatchResultが確定しなかった"
    assert len(precise_rois) == 2, (
        f"read_precise_rank()は結果バナー確定時の2回だけのはず(実際: {len(precise_rois)}回)"
    )
    assert precise_rois == [RANK_NUMBER_ROI_COMPACT, RANK_NUMBER_ROI_ENLARGED]
    assert gauge_calls["n"] > 2, "GRACE中はread_rank_gauge_fill()で追跡し続けるはず"
    # 昇格演出も降格ラベルも無いため帯番号は試合前(38)のまま、小数部はゲージの値
    assert result.rank_after == pytest.approx(38.5)
    assert result.league_changed is None


def test_demotion_confirmed_but_tier_ocr_reads_unchanged_still_records_demotion(monkeypatch):
    """Issue #202/#396: 降格ラベル(独立信号)を確認できていれば、帯番号を1つ下げて
    記録することを確認する。

    Issue #202時点は「帯番号OCRが変化なしを返し続けても再スキャン経路に合流して
    降格を記録する」という形のテストだったが、Issue #396でGRACE中の帯番号OCR自体を
    廃止したため、帯番号は常に試合前の帯を起点に独立信号だけで±1する
    (_infer_tier_after参照)。確認したい挙動(降格ラベルを確認できた負け試合は
    1帯下がって記録される)は変わっていない。
    """
    read_calls = {"n": 0}
    raw_calls = {"n": 0}

    def fake_read_precise_rank(frame, gauge_roi, rank_number_roi):
        raw_calls["n"] += 1
        if raw_calls["n"] == 2:
            # Issue #222: _apply_rank_before_ocr()の拡大ROI側フォールバック呼び出し。
            # このテストは結果バナー確定時点をコンパクト表示想定にしているため失敗させる
            return None
        read_calls["n"] += 1
        if read_calls["n"] == 1:
            return (38, 38.2)  # before(小数部0.2)
        if read_calls["n"] == 2:
            return (38, 38.3)  # GRACE突入直後(帯番号は変化なしのまま)
        return (38, 38.4)  # (Issue #396以降、GRACE中はこの経路自体が呼ばれない)

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
        now_fn=FakeClock(),
        banner_confirm_seconds=2,
        league_change_grace_seconds=3,
        rank_recheck_interval_seconds=1000,
        demotion_label_confirm_seconds=2,
        rank_stability_monitor=StabilityMonitor(roi=(0, 0, 5, 5), stable_frames_required=2),
    )

    # Issue #229: 試合の区切りをVS画面確定に一本化したため、このテストの
    # 関心事(VS画面確定より後の挙動)を検証するには、VS画面を確認済みとして扱う
    machine._vs_confirmed_this_match = True
    # Issue #235: VS画面でランクを検知した(=ランクを賭けた)試合として扱うための
    # ショートカット(_vs_confirmed_this_matchと同じ理由でVS画面確定の全過程は再現しない)
    machine._pending_vs_mine_ranks = [SlotRank("∞", 38)]
    frame = np.zeros((10, 10, 3), dtype=np.uint8)
    result = None
    for _ in range(60):
        result = machine.process_frame(frame)
        if result is not None:
            break

    assert result is not None, "MatchResultが確定しなかった"
    # 帯番号は38-1=37、小数部はGRACE中に追跡したゲージの値(0.3)
    assert result.rank_after == pytest.approx(37.3)
    assert result.league_changed == "down"


def test_unchanged_tier_stays_plausible_without_demotion_confirmation(monkeypatch):
    """Issue #202の修正が通常ケースを壊していないことを確認する。降格ラベルを
    確認できていない(通常の)試合では、帯番号が変化なしと読めた場合は
    再スキャンを挟まず素直に確定することを確認する。
    """
    read_calls = {"n": 0}
    raw_calls = {"n": 0}

    def fake_read_precise_rank(frame, gauge_roi, rank_number_roi):
        raw_calls["n"] += 1
        if raw_calls["n"] == 2:
            # Issue #222: _apply_rank_before_ocr()の拡大ROI側フォールバック呼び出し。
            # このテストは結果バナー確定時点をコンパクト表示想定にしているため失敗させる
            return None
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
        now_fn=FakeClock(),
        banner_confirm_seconds=2,
        league_change_grace_seconds=3,
        rank_recheck_interval_seconds=1000,
        rank_stability_monitor=StabilityMonitor(roi=(0, 0, 5, 5), stable_frames_required=2),
    )

    # Issue #229: 試合の区切りをVS画面確定に一本化したため、このテストの
    # 関心事(VS画面確定より後の挙動)を検証するには、VS画面を確認済みとして扱う
    machine._vs_confirmed_this_match = True
    # Issue #235: VS画面でランクを検知した(=ランクを賭けた)試合として扱うための
    # ショートカット(_vs_confirmed_this_matchと同じ理由でVS画面確定の全過程は再現しない)
    machine._pending_vs_mine_ranks = [SlotRank("∞", 38)]
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
    raw_calls = {"n": 0}

    def fake_read_precise_rank(frame, gauge_roi, rank_number_roi):
        raw_calls["n"] += 1
        if raw_calls["n"] == 2:
            # Issue #222: _apply_rank_before_ocr()の拡大ROI側フォールバック呼び出し。
            # このテストは結果バナー確定時点をコンパクト表示想定にしているため失敗させる
            return None
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
        now_fn=FakeClock(),
        banner_confirm_seconds=2,
        league_change_grace_seconds=3,
        rank_recheck_interval_seconds=1000,
        demotion_label_confirm_seconds=2,
        rank_stability_monitor=StabilityMonitor(roi=(0, 0, 5, 5), stable_frames_required=2),
    )

    # Issue #229: 試合の区切りをVS画面確定に一本化したため、このテストの
    # 関心事(VS画面確定より後の挙動)を検証するには、VS画面を確認済みとして扱う
    machine._vs_confirmed_this_match = True
    # Issue #235: VS画面でランクを検知した(=ランクを賭けた)試合として扱うための
    # ショートカット(_vs_confirmed_this_matchと同じ理由でVS画面確定の全過程は再現しない)
    machine._pending_vs_mine_ranks = [SlotRank("∞", 38)]
    frame = np.zeros((10, 10, 3), dtype=np.uint8)
    result = None
    for _ in range(60):
        result = machine.process_frame(frame)
        if result is not None:
            break

    assert result is not None, "MatchResultが確定しなかった"
    assert result.league_changed is None
    assert result.rank_after == pytest.approx(38.3)


def test_promotion_during_grace_period_is_caught(monkeypatch):
    """Issue #209/#235: ゲージが帯の上限付近のままバナーが消えても早期確定せず
    待ち続け、その後実際に昇格演出(is_league_change_screen)が始まれば正しく
    帯を+1して記録することを確認する。

    fixtures/videos/30・42番の回帰(昇格演出が始まる前にゲージの踊り場+バナー消灯が
    重なって誤って早期確定していた)を再現するテスト。Issue #178で追加された
    「バナー消灯+ゲージ変化なし」による早期確定パス自体は、遷移演出のノイズを
    誤って安定値と誤認する別の不具合(Issue #235)が実データで見つかったため
    廃止済み(near_tier_capガード等の部分的な対策では防ぎきれなかった)。
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
        # 呼び出し1・2回目はbanner確定時の(before、Issue #222対応でコンパクト/
        # 拡大両方を試すため2回になる。同じ値を返すため食い違いは起きない)、
        # 3回目はGRACE突入時(昇格演出が始まる前)の読み取りで、いずれも
        # 帯の上限付近の値を返す。4回目以降(演出後の再度のGRACE突入時)から
        # 昇格後の値を返す
        if precise_calls["n"] <= 3:
            return (37, 37.98)  # 昇格直前、帯の上限付近で踊り場になっている状態
        return (38, 38.06)  # 昇格後

    def fake_read_rank_gauge_fill(frame, roi):
        # Issue #396: GRACE中は帯番号OCRを行わなくなったため、演出の前後は
        # read_precise_rankの呼び出し回数ではなく昇格演出の到達で切り替える
        return 0.98 if league_change_calls["n"] < PROMOTION_AT_CALL else 0.06

    banner_call_count = {"n": 0}

    def fake_classify_banner(frame):
        banner_call_count["n"] += 1
        # Issue #388: banner_confirm_seconds=2(FakeClock step=1.0)を確定させるには
        # 経過2.0秒(=3回分のスパン)必要。以降はTRACKING_RANK中にバナーのテキストが
        # 一時的に(または最後まで)消えている状態を再現する
        return "win" if banner_call_count["n"] <= 3 else None

    monkeypatch.setattr(match_state_module, "classify_banner", fake_classify_banner)
    monkeypatch.setattr(match_state_module, "read_precise_rank", fake_read_precise_rank)
    monkeypatch.setattr(match_state_module, "read_rank_gauge_fill", fake_read_rank_gauge_fill)
    monkeypatch.setattr(match_state_module, "is_league_change_screen", fake_is_league_change_screen)
    monkeypatch.setattr(match_state_module, "is_demotion_label_candidate", lambda frame: False)
    monkeypatch.setattr(match_state_module, "is_goal_event", lambda frame: False)
    monkeypatch.setattr(match_state_module, "is_vs_screen", lambda frame: False)
    monkeypatch.setattr(match_state_module, "is_match_end_screen", lambda frame: False)
    monkeypatch.setattr(match_state_module, "is_full_blackout", lambda frame: False)

    machine = MatchStateMachine(
        now_fn=FakeClock(),
        banner_confirm_seconds=2,
        league_change_grace_seconds=200,
        rank_recheck_interval_seconds=3,
        rank_stability_monitor=StabilityMonitor(roi=(0, 0, 5, 5), stable_frames_required=2),
    )

    # Issue #229: 試合の区切りをVS画面確定に一本化したため、このテストの
    # 関心事(VS画面確定より後の挙動)を検証するには、VS画面を確認済みとして扱う
    machine._vs_confirmed_this_match = True
    # Issue #235: VS画面でランクを検知した(=ランクを賭けた)試合として扱うための
    # ショートカット(_vs_confirmed_this_matchと同じ理由でVS画面確定の全過程は再現しない)
    machine._pending_vs_mine_ranks = [SlotRank("∞", 37)]
    frame = np.zeros((10, 10, 3), dtype=np.uint8)
    result = None
    for _ in range(320):
        result = machine.process_frame(frame)
        if result is not None:
            break

    assert result is not None, "MatchResultが確定しなかった"
    assert result.rank_after == pytest.approx(38.06)
    assert result.league_changed == "up", (
        "昇格演出が始まる前に早期確定してしまい、昇格を見逃した(Issue #209の回帰)"
    )


def test_no_promotion_during_grace_period_still_finalizes_after_full_timeout(monkeypatch):
    """Issue #209/#235: ゲージが帯の上限付近でバナーが消えても、実際には昇格演出が
    一度も来ない場合は、league_change_grace_frames(通常のタイムアウト)満了時点で
    正しく確定することを確認する(帯番号は変化なしのまま)。Issue #235で早期確定
    パス自体を廃止したため、確定手段は暗転即確定かこの猶予満了のいずれかのみになった。
    """
    banner_call_count = {"n": 0}

    def fake_classify_banner(frame):
        banner_call_count["n"] += 1
        # Issue #388: banner_confirm_seconds=2(FakeClock step=1.0)を確定させるには
        # 経過2.0秒(=3回分のスパン)必要
        return "win" if banner_call_count["n"] <= 3 else None

    monkeypatch.setattr(match_state_module, "classify_banner", fake_classify_banner)
    monkeypatch.setattr(match_state_module, "read_precise_rank", lambda frame, gauge_roi, rank_number_roi: (37, 37.98))
    monkeypatch.setattr(match_state_module, "read_rank_gauge_fill", lambda frame, roi: 0.98)
    monkeypatch.setattr(match_state_module, "is_league_change_screen", lambda frame: False)
    monkeypatch.setattr(match_state_module, "is_demotion_label_candidate", lambda frame: False)
    monkeypatch.setattr(match_state_module, "is_goal_event", lambda frame: False)
    monkeypatch.setattr(match_state_module, "is_vs_screen", lambda frame: False)
    monkeypatch.setattr(match_state_module, "is_match_end_screen", lambda frame: False)
    monkeypatch.setattr(match_state_module, "is_full_blackout", lambda frame: False)

    machine = MatchStateMachine(
        now_fn=FakeClock(),
        banner_confirm_seconds=2,
        league_change_grace_seconds=10,
        rank_recheck_interval_seconds=3,
        rank_stability_monitor=StabilityMonitor(roi=(0, 0, 5, 5), stable_frames_required=2),
    )

    # Issue #229: 試合の区切りをVS画面確定に一本化したため、このテストの
    # 関心事(VS画面確定より後の挙動)を検証するには、VS画面を確認済みとして扱う
    machine._vs_confirmed_this_match = True
    # Issue #235: VS画面でランクを検知した(=ランクを賭けた)試合として扱うための
    # ショートカット(_vs_confirmed_this_matchと同じ理由でVS画面確定の全過程は再現しない)
    machine._pending_vs_mine_ranks = [SlotRank("∞", 37)]
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
    """Issue #209: 暗転(is_full_blackout)を検知したら、grace_counterの状態に
    関わらず直ちに確定することを確認する。

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
    monkeypatch.setattr(match_state_module, "is_league_change_screen", lambda frame: False)
    monkeypatch.setattr(match_state_module, "is_demotion_label_candidate", lambda frame: False)
    monkeypatch.setattr(match_state_module, "is_goal_event", lambda frame: False)
    monkeypatch.setattr(match_state_module, "is_vs_screen", lambda frame: False)
    monkeypatch.setattr(match_state_module, "is_match_end_screen", lambda frame: False)
    monkeypatch.setattr(match_state_module, "is_full_blackout", fake_is_full_blackout)

    machine = MatchStateMachine(
        now_fn=FakeClock(),
        banner_confirm_seconds=2,
        league_change_grace_seconds=10_000,
        rank_recheck_interval_seconds=3,
        rank_stability_monitor=StabilityMonitor(roi=(0, 0, 5, 5), stable_frames_required=2),
    )

    # Issue #229: 試合の区切りをVS画面確定に一本化したため、このテストの
    # 関心事(VS画面確定より後の挙動)を検証するには、VS画面を確認済みとして扱う
    machine._vs_confirmed_this_match = True
    # Issue #235: VS画面でランクを検知した(=ランクを賭けた)試合として扱うための
    # ショートカット(_vs_confirmed_this_matchと同じ理由でVS画面確定の全過程は再現しない)
    machine._pending_vs_mine_ranks = [SlotRank("∞", 37)]
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
    monkeypatch.setattr(match_state_module, "is_own_goal_event", lambda frame: False)
    monkeypatch.setattr(match_state_module, "read_scorer_name", lambda frame: ("Alice", 0.95))
    monkeypatch.setattr(match_state_module, "read_assist_name", lambda frame: None)
    monkeypatch.setattr(match_state_module, "classify_banner", lambda frame: None)
    monkeypatch.setattr(match_state_module, "is_vs_screen", lambda frame: False)
    monkeypatch.setattr(match_state_module, "is_match_end_screen", lambda frame: False)
    monkeypatch.setattr(match_state_module, "is_full_blackout", lambda frame: False)
    monkeypatch.setenv("GOAL_RECORD_MODE", "allowlist")

    machine = MatchStateMachine(now_fn=FakeClock(), goal_confirm_seconds=2)
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

    machine = MatchStateMachine(now_fn=FakeClock(), goal_confirm_seconds=2)
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
        now_fn=FakeClock(),
        banner_confirm_seconds=100,
        banner_confirm_seconds_after_match_end=3,
        match_end_confirm_seconds=1,
        league_change_grace_seconds=1,
        rank_stability_monitor=StabilityMonitor(roi=(0, 0, 5, 5), stable_frames_required=1),
    )

    # Issue #229: 試合の区切りをVS画面確定に一本化したため、このテストの
    # 関心事(VS画面確定より後の挙動)を検証するには、VS画面を確認済みとして扱う
    machine._vs_confirmed_this_match = True
    # Issue #235: VS画面でランクを検知した(=ランクを賭けた)試合として扱うための
    # ショートカット(_vs_confirmed_this_matchと同じ理由でVS画面確定の全過程は再現しない)
    machine._pending_vs_mine_ranks = [SlotRank("∞", 10)]
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
        now_fn=FakeClock(),
        banner_confirm_seconds=5,
        banner_confirm_seconds_after_match_end=1,
        match_end_confirm_seconds=1,
        league_change_grace_seconds=1,
        rank_stability_monitor=StabilityMonitor(roi=(0, 0, 5, 5), stable_frames_required=1),
    )

    # Issue #229: 試合の区切りをVS画面確定に一本化したため、このテストの
    # 関心事(VS画面確定より後の挙動)を検証するには、VS画面を確認済みとして扱う
    machine._vs_confirmed_this_match = True
    # Issue #235: VS画面でランクを検知した(=ランクを賭けた)試合として扱うための
    # ショートカット(_vs_confirmed_this_matchと同じ理由でVS画面確定の全過程は再現しない)
    machine._pending_vs_mine_ranks = [SlotRank("∞", 10)]
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
        return frame_idx["n"] < 3

    def fake_is_match_end_screen(frame):
        return 6 <= frame_idx["n"] < 9

    def fake_classify_banner(frame):
        return "win" if frame_idx["n"] >= 12 else None

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
        now_fn=FakeClock(),
        vs_screen_confirm_seconds=2,
        match_end_confirm_seconds=2,
        banner_confirm_seconds_after_match_end=2,
        banner_confirm_seconds=100,
        league_change_grace_seconds=1,
        rank_stability_monitor=StabilityMonitor(roi=(0, 0, 5, 5), stable_frames_required=1),
    )

    frame = np.zeros((10, 10, 3), dtype=np.uint8)
    result = None
    with caplog.at_level("INFO", logger="nss_tracker.state"):
        for _ in range(30):
            result = machine.process_frame(frame)
            frame_idx["n"] += 1
            if result is not None:
                break

    assert result is not None, "MatchResultが確定しなかった"
    assert "1試合目開始" in caplog.text
    assert "1試合目 試合終了" in caplog.text
    assert "1試合目の結果: 勝ち (ランク(試合前): 10.5)" in caplog.text


def test_unranked_match_finalizes_immediately_at_banner_confirm(monkeypatch):
    """Issue #235: VS画面の自チームスロット0(自分自身)でランクを検知できなかった
    試合は「ランクを賭けない試合」とみなし、ランク変動アニメーションの安定待ち
    (TRACKING_RANK)を経由せず、結果バナー確定と同じフレームでMatchResultが
    確定する(rank_before/rank_after/league_changedともNone)ことを確認する。
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
        lambda frame: ([SlotRank(None, None)] * 4, [SlotRank(None, None)] * 4),
    )
    monkeypatch.setattr(match_state_module, "is_goal_event", lambda frame: False)
    monkeypatch.setattr(match_state_module, "classify_banner", fake_classify_banner)
    # ランクを賭けない試合は結果バナーにもバッジ自体が表示されない(CLAUDE.md参照)ため、
    # rank_beforeの読み取りも失敗する想定
    monkeypatch.setattr(match_state_module, "read_precise_rank", lambda frame, gauge_roi, rank_number_roi: None)
    monkeypatch.setattr(match_state_module, "is_match_end_screen", lambda frame: False)
    monkeypatch.setattr(match_state_module, "is_full_blackout", lambda frame: False)

    machine = MatchStateMachine(
        now_fn=FakeClock(),
        banner_confirm_seconds=2,
        vs_screen_confirm_seconds=2,
    )

    frame = np.zeros((10, 10, 3), dtype=np.uint8)
    result = None
    for _ in range(15):
        result = machine.process_frame(frame)
        if result is not None:
            break

    assert result is not None, "MatchResultが確定しなかった"
    assert result.rank_before is None
    assert result.rank_after is None
    assert result.league_changed is None
    assert machine.current_state == "cooldown", "TRACKING_RANKを経由せず直接COOLDOWNへ遷移するはず"


def test_ranked_match_still_waits_for_rank_tracking_after_banner_confirm(monkeypatch):
    """Issue #235: 対照として、VS画面の自チームスロット0でランクを検知できた試合は
    従来どおりTRACKING_RANK(ランク変動アニメーションの安定待ち)を経由し、
    結果バナー確定と同じフレームでは確定しないことを確認する。
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
        lambda frame: ([SlotRank("∞", 39), SlotRank(None, None), SlotRank(None, None), SlotRank(None, None)], []),
    )
    monkeypatch.setattr(match_state_module, "is_goal_event", lambda frame: False)
    monkeypatch.setattr(match_state_module, "classify_banner", fake_classify_banner)
    monkeypatch.setattr(match_state_module, "read_precise_rank", lambda frame, gauge_roi, rank_number_roi: (10, 10.0))
    monkeypatch.setattr(match_state_module, "is_match_end_screen", lambda frame: False)
    monkeypatch.setattr(match_state_module, "is_full_blackout", lambda frame: False)

    machine = MatchStateMachine(
        now_fn=FakeClock(),
        banner_confirm_seconds=2,
        vs_screen_confirm_seconds=2,
    )

    frame = np.zeros((10, 10, 3), dtype=np.uint8)
    result = None
    # vs_screen_confirm_seconds=2でVS画面確定、banner_confirm_seconds=2でバナー確定する
    # 組み合わせ(Issue #388: FakeClock step=1.0では閾値2.0秒に達するまで3フレーム分の
    # スパンが必要)。バナー確定した瞬間で止め、その先のTRACKING_RANK内部の挙動
    # (_track_rank)までは踏み込まない
    for _ in range(8):
        result = machine.process_frame(frame)

    assert result is None, "ランクを賭けた試合はバナー確定と同じフレームでは確定しないはず"
    assert machine.current_state == "tracking_rank"


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
    # Issue #283: 結果バナー確定時点のrank_beforeはVS画面のスロット0(38)を優先して
    # 採用するため、ここでのOCRモックもそれに合わせておく(不一致自体は別テストで検証)
    monkeypatch.setattr(match_state_module, "read_precise_rank", lambda frame, gauge_roi, rank_number_roi: (38, 38.0))
    monkeypatch.setattr(match_state_module, "read_rank_gauge_fill", lambda frame, roi: 0.0)
    monkeypatch.setattr(match_state_module, "is_league_change_screen", lambda frame: False)
    monkeypatch.setattr(match_state_module, "is_demotion_label_candidate", lambda frame: False)
    monkeypatch.setattr(match_state_module, "is_match_end_screen", lambda frame: False)
    monkeypatch.setattr(match_state_module, "is_full_blackout", lambda frame: False)

    machine = MatchStateMachine(
        now_fn=FakeClock(),
        banner_confirm_seconds=2,
        vs_screen_confirm_seconds=2,
        league_change_grace_seconds=1,
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
    # Issue #283: 結果バナー確定時点のrank_beforeはVS画面のスロット0(38)を優先して
    # 採用するため、ここでのOCRモックもそれに合わせておく(不一致自体は別テストで検証)
    monkeypatch.setattr(match_state_module, "read_precise_rank", lambda frame, gauge_roi, rank_number_roi: (38, 38.0))
    monkeypatch.setattr(match_state_module, "read_rank_gauge_fill", lambda frame, roi: 0.0)
    monkeypatch.setattr(match_state_module, "is_league_change_screen", lambda frame: False)
    monkeypatch.setattr(match_state_module, "is_demotion_label_candidate", lambda frame: False)
    monkeypatch.setattr(match_state_module, "is_match_end_screen", lambda frame: False)
    monkeypatch.setattr(match_state_module, "is_full_blackout", lambda frame: False)

    machine = MatchStateMachine(
        now_fn=FakeClock(),
        banner_confirm_seconds=2,
        vs_screen_confirm_seconds=2,
        league_change_grace_seconds=1,
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


def test_vs_screen_not_confirmed_causes_result_banner_to_be_rejected(monkeypatch, caplog):
    """Issue #229: 試合の区切りをVS画面確定に一本化したため、VS画面を一度も
    確認できていない状態で結果バナーが確定しても、新しい試合としては記録
    しない(MatchResultを返さずWATCHING状態のまま留まる)ことを確認する。
    直前の試合の残像を誤って結果バナーとして拾ったとみなし、INFOログに
    残すだけで済ませる。
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
        now_fn=FakeClock(),
        banner_confirm_seconds=2,
        league_change_grace_seconds=1,
        rank_stability_monitor=StabilityMonitor(roi=(0, 0, 5, 5), stable_frames_required=1),
    )

    frame = np.zeros((10, 10, 3), dtype=np.uint8)
    result = None
    with caplog.at_level("INFO", logger="nss_tracker.state"):
        for _ in range(15):
            result = machine.process_frame(frame)

    assert result is None, "VS画面未確認の結果バナーはMatchResultを作らないはず"
    assert "VS画面を確認できないまま結果バナー" in caplog.text
    assert machine.current_state == "watching", "WATCHING状態のまま留まるはず"


def test_vs_screen_not_confirmed_discards_buffered_goals(monkeypatch):
    """Issue #229: VS画面未確認で結果バナーを棄却する際、その誤検知バナーに
    紐づいてバッファされていた可能性のあるゴール検知も、次の本物の試合に
    誤って持ち越さないよう破棄することを確認する。

    is_goal_event/classify_bannerとも最初の数フレームだけ真になるようにし
    (現実の検知に近い単発イベント)、結果バナーが棄却されるちょうどその
    フレームでループを止めることで、その後の挙動(次のバナー確定サイクル)
    と混ざらないようにする。
    """

    def fake_is_goal_event(frame):
        # Issue #388: goal_confirm_seconds=1(FakeClock step=1.0)を確定させるには
        # 経過1.0秒(=2フレーム分のスパン)必要
        return frame_idx["n"] < 2

    def fake_classify_banner(frame):
        return "win" if frame_idx["n"] >= 10 else None

    frame_idx = {"n": 0}
    monkeypatch.setenv("GOAL_RECORD_MODE", "allowlist")
    monkeypatch.setattr(match_state_module, "is_vs_screen", lambda frame: False)
    monkeypatch.setattr(match_state_module, "is_goal_event", fake_is_goal_event)
    monkeypatch.setattr(match_state_module, "confirm_goal_text", lambda frame: True)
    monkeypatch.setattr(match_state_module, "is_own_goal_event", lambda frame: False)
    monkeypatch.setattr(match_state_module, "read_scorer_name", lambda frame: ("Alice", 0.95))
    monkeypatch.setattr(match_state_module, "read_assist_name", lambda frame: None)
    monkeypatch.setattr(match_state_module, "classify_banner", fake_classify_banner)
    monkeypatch.setattr(match_state_module, "is_match_end_screen", lambda frame: False)
    monkeypatch.setattr(match_state_module, "is_full_blackout", lambda frame: False)

    machine = MatchStateMachine(now_fn=FakeClock(), banner_confirm_seconds=2, goal_confirm_seconds=1)
    frame = np.zeros((10, 10, 3), dtype=np.uint8)

    # frame_idx 0〜9: ゴールが1件バッファされるが、バナーはまだ確定しない
    for _ in range(10):
        machine.process_frame(frame)
        frame_idx["n"] += 1
    assert len(machine._pending_goals) == 1, "テスト前提: 棄却前にゴールが1件バッファされているはず"

    # frame_idx 10〜12: バナー("win")が閾値2.0秒(Issue #388、FakeClock step=1.0では
    # 3フレーム分のスパン)に達し、VS未確認のため棄却される
    for _ in range(3):
        machine.process_frame(frame)
        frame_idx["n"] += 1

    assert machine._pending_goals == [], "棄却された結果バナーに紐づくゴールは破棄されるはず"


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

    machine = MatchStateMachine(now_fn=FakeClock(), vs_screen_confirm_seconds=2)
    frame = np.zeros((10, 10, 3), dtype=np.uint8)

    with caplog.at_level("INFO", logger="nss_tracker.state"):
        for _ in range(3):
            machine.process_frame(frame)
        # Issue #189/#397: VS画面ランクOCRは別プロセス(テストではThreadPoolExecutor)で
        # 実行されるため、結果の取り込み(ログ出力)を待ってから検証する
        machine._poll_vs_ocr(wait=True)

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

    machine = MatchStateMachine(now_fn=FakeClock(), vs_screen_confirm_seconds=2)
    frame = np.zeros((10, 10, 3), dtype=np.uint8)

    assert machine.pop_vs_screen_event() is None, "確定前はNoneのはず"

    # Issue #388: vs_screen_confirm_seconds=2(FakeClock step=1.0)を確定させるには
    # 経過2.0秒(=3フレーム分のスパン)必要
    machine.process_frame(frame)  # 1フレーム目: streak=1、まだ未確定
    assert machine.pop_vs_screen_event() is None

    machine.process_frame(frame)  # 2フレーム目: streak=2、経過1.0秒でまだ未確定
    assert machine.pop_vs_screen_event() is None

    machine.process_frame(frame)  # 3フレーム目で経過2.0秒に到達し確定
    # Issue #189/#397: VS画面ランクOCRは別プロセス(テストではThreadPoolExecutor)で
    # 実行されるため、VsScreenEventが書き込まれるのを待ってからpopする
    machine._poll_vs_ocr(wait=True)
    event = machine.pop_vs_screen_event()

    assert event is not None
    assert event.mine_ranks == [SlotRank("∞", 38)]
    assert event.opponent_ranks == [SlotRank("∞", 10)]
    assert event.mine_team_color == "#64bde2"
    assert event.opponent_team_color == "#f87abe"
    # popすると消費されるため、同じ確定を指すイベントを2度は取得できない
    assert machine.pop_vs_screen_event() is None

    machine.process_frame(frame)  # 同じVS画面がまだ表示され続けている4フレーム目
    assert machine.pop_vs_screen_event() is None, "同じVS画面が続いている間は再度発火しない"


def test_in_match_true_after_vs_screen_confirmed_and_false_after_match_end(monkeypatch):
    """Issue #83: OBSシーン自動切替のトリガーであるin_matchが、VS画面確定でTrueになり、
    試合が終わると暗転検知から一定時間後にFalseに戻ることを確認する(Issue #224で、
    Falseに戻るタイミングを暗転検知基準に変更した)。
    Issue #190対応後は「試合終了」バナーをOCR確認できた試合のみFalseに戻るため、
    ここでは確認できたケースとしてis_match_end_screen/confirm_match_end_textを
    Trueにする。テスト用フレームは全黒(np.zeros)のため、is_full_blackout()は
    毎フレームTrueを返す(暗転検知のタイミング自体はこのテストの関心事ではない)。

    Issue #371: 暗転待ちの起点が_finalize()から「試合終了」OCR確認へ前倒しされたため、
    in_matchがFalseに戻るのはMatchResultの確定より前になりうる(このテストのように
    毎フレーム暗転扱いになる条件下では実際にそうなる)。したがって「確定直後もまだ
    True」という以前の期待は成り立たなくなった。ここでは、切替がMatchResultの
    確定タイミングに依存しないことそのものを検証する。
    """
    frame_idx = {"n": 0}

    def fake_is_vs_screen(frame):
        # Issue #388: vs_screen_confirm_seconds=2(FakeClock step=1.0)を確定させるには
        # 経過2.0秒(=3フレーム分のスパン)必要
        return frame_idx["n"] < 3

    def fake_is_match_end_screen(frame):
        return 3 <= frame_idx["n"] < 5

    def fake_classify_banner(frame):
        return "win" if frame_idx["n"] >= 6 else None

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
        now_fn=FakeClock(),
        vs_screen_confirm_seconds=2,
        banner_confirm_seconds=2,
        banner_confirm_seconds_after_match_end=2,
        match_end_confirm_seconds=1,
        league_change_grace_seconds=1,
        obs_switch_delay_after_blackout_seconds=2,
        rank_stability_monitor=StabilityMonitor(roi=(0, 0, 5, 5), stable_frames_required=1),
    )

    frame = np.zeros((10, 10, 3), dtype=np.uint8)
    assert machine.in_match is False, "初期状態はFalse(試合間)のはず"

    result = None
    in_match_became_true_frame = None
    in_match_became_false_frame = None
    for _ in range(30):
        result = machine.process_frame(frame)
        if in_match_became_true_frame is None and machine.in_match:
            in_match_became_true_frame = frame_idx["n"]
        elif in_match_became_true_frame is not None and in_match_became_false_frame is None and not machine.in_match:
            in_match_became_false_frame = frame_idx["n"]
        frame_idx["n"] += 1
        if result is not None:
            break

    assert in_match_became_true_frame is not None, "VS画面確定後にin_matchがTrueにならなかった"
    assert result is not None, "MatchResultが確定しなかった"
    assert in_match_became_false_frame is not None, (
        "「試合終了」を確認できた試合は、暗転検知から一定時間後にin_matchがFalseに戻るはず"
    )
    # Issue #371: 「試合終了」確認(frame_idx=2)を起点に、暗転(全黒フレームのため
    # 即座にTrue)からobs_switch_delay_after_blackout_frames(=2)経過して戻る
    assert in_match_became_false_frame > in_match_became_true_frame, (
        "in_matchはTrueになった後にFalseへ戻るはず"
    )
    assert machine.in_match is False, "試合終了後はin_matchがFalseのままのはず"


def test_obs_switch_waits_for_blackout_after_finalize(monkeypatch):
    """Issue #224: 試合結果が確定(_finalize())した直後はまだ暗転を検知していない場合、
    in_matchはTrueのまま維持され、暗転を検知してからobs_switch_delay_after_blackout_frames
    経過して初めてFalseに戻ることを確認する。ランク値の確定タイミングとOBSシーン
    切替タイミングが分離されたことの検証(#223: 結果画面から離脱するフェード演出と
    確定タイミングが重なる問題の対策として、切替を暗転基準に統一した)。
    """
    frame_idx = {"n": 0}
    blackout = {"active": False}

    def fake_is_vs_screen(frame):
        # Issue #388: vs_screen_confirm_seconds=2(FakeClock step=1.0)を確定させるには
        # 経過2.0秒(=3フレーム分のスパン)必要
        return frame_idx["n"] < 3

    def fake_is_match_end_screen(frame):
        return 3 <= frame_idx["n"] < 5

    def fake_classify_banner(frame):
        return "win" if frame_idx["n"] >= 6 else None

    def fake_is_full_blackout(frame):
        return blackout["active"]

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
    monkeypatch.setattr(match_state_module, "is_full_blackout", fake_is_full_blackout)

    machine = MatchStateMachine(
        now_fn=FakeClock(),
        vs_screen_confirm_seconds=2,
        banner_confirm_seconds=2,
        banner_confirm_seconds_after_match_end=2,
        match_end_confirm_seconds=1,
        league_change_grace_seconds=1,
        obs_switch_delay_after_blackout_seconds=3,
        # Issue #395: このテストは「暗転が来るまで何フレーム進めてもTrueのまま」を
        # 確認するのが目的のため、暗転取りこぼし時のタイムアウトは発火させない
        obs_switch_timeout_seconds=1000,
        rank_stability_monitor=StabilityMonitor(roi=(0, 0, 5, 5), stable_frames_required=1),
    )

    frame = np.zeros((10, 10, 3), dtype=np.uint8)

    result = None
    for _ in range(30):
        result = machine.process_frame(frame)
        frame_idx["n"] += 1
        if result is not None:
            break

    assert result is not None, "MatchResultが確定しなかった"
    assert machine.in_match is True, "確定直後はまだ暗転未検知のためTrueのはず"

    # 暗転がまだ来ていない間は、何フレーム進めてもTrueのまま維持される
    # (フェード演出等でrank確定と暗転検知のタイミングがずれても誤って早く
    # 切り替わらないことの検証)
    for _ in range(10):
        machine.process_frame(frame)
    assert machine.in_match is True, "暗転を検知するまではin_matchがTrueのまま維持されるはず"

    # Issue #388: 経過秒数(now-started_at)ベースになったため、delay=3秒に到達する
    # には暗転検知フレーム自身を含め4回分のスパンが必要(1回目はelapsed=0)
    blackout["active"] = True
    machine.process_frame(frame)  # 暗転検知1フレーム目(elapsed=0)
    assert machine.in_match is True, "暗転検知直後、delay未経過ではまだTrueのはず"
    machine.process_frame(frame)  # 2フレーム目(elapsed=1)
    assert machine.in_match is True, "delay(=3)未経過ではまだTrueのはず"
    machine.process_frame(frame)  # 3フレーム目(elapsed=2)
    assert machine.in_match is True, "delay(=3)未経過ではまだTrueのはず"
    machine.process_frame(frame)  # 4フレーム目(elapsed=3)でdelay到達
    assert machine.in_match is False, "暗転検知からdelay分経過後にFalseへ戻るはず"


def _make_machine_for_obs_timeout(monkeypatch, blackout_flag, frame_idx, **kwargs):
    """Issue #395: 暗転タイムアウトのテスト用に、VS画面確定〜「試合終了」確認まで
    進む最小構成の状態機械を組み立てる。
    """

    def fake_is_vs_screen(frame):
        return frame_idx["n"] < 3

    def fake_is_match_end_screen(frame):
        return 3 <= frame_idx["n"] < 5

    monkeypatch.setattr(match_state_module, "is_vs_screen", fake_is_vs_screen)
    monkeypatch.setattr(match_state_module, "read_vs_screen_ranks", lambda frame: ([], []))
    monkeypatch.setattr(match_state_module, "is_match_end_screen", fake_is_match_end_screen)
    monkeypatch.setattr(match_state_module, "confirm_match_end_text", lambda frame: True)
    monkeypatch.setattr(match_state_module, "is_goal_event", lambda frame: False)
    monkeypatch.setattr(match_state_module, "classify_banner", lambda frame: None)
    monkeypatch.setattr(match_state_module, "read_rank_gauge_fill", lambda frame, roi: 0.0)
    monkeypatch.setattr(match_state_module, "is_league_change_screen", lambda frame: False)
    monkeypatch.setattr(match_state_module, "is_demotion_label_candidate", lambda frame: False)
    monkeypatch.setattr(match_state_module, "is_full_blackout", lambda frame: blackout_flag["active"])

    return MatchStateMachine(
        now_fn=FakeClock(),
        vs_screen_confirm_seconds=2,
        match_end_confirm_seconds=1,
        obs_switch_delay_after_blackout_seconds=3,
        rank_stability_monitor=StabilityMonitor(roi=(0, 0, 5, 5), stable_frames_required=1),
        **kwargs,
    )


def test_obs_switch_times_out_when_blackout_is_never_detected(monkeypatch, caplog):
    """Issue #395: 暗転を一度も検知できないまま obs_switch_timeout_seconds が
    経過したら、暗転を待たずにin_matchをFalseへ戻すことを確認する(#383の
    取りこぼしが残っている間の安全網)。あわせて、この経路を通ったことが
    WARNINGログに残ることも確認する(安全網が症状を隠した回数を後から
    数えられるようにするため、Issueの必須要件)。
    """
    frame_idx = {"n": 0}
    blackout = {"active": False}  # 暗転は最後まで来ない
    machine = _make_machine_for_obs_timeout(
        monkeypatch, blackout, frame_idx, obs_switch_timeout_seconds=10
    )

    frame = np.zeros((10, 10, 3), dtype=np.uint8)

    # 「試合終了」確認(frame_idx=3〜4)まで進める。FakeClockはprocess_frame
    # 1回につき1秒進む
    with caplog.at_level(logging.WARNING, logger="nss_tracker.state"):
        for _ in range(5):
            machine.process_frame(frame)
            frame_idx["n"] += 1
        assert machine.in_match is True, "VS画面確定〜試合終了直後はTrueのはず"

        # タイムアウト(10秒)未満の間はTrueのまま維持される
        for _ in range(8):
            machine.process_frame(frame)
            frame_idx["n"] += 1
        assert machine.in_match is True, "タイムアウト未満ではTrueのまま維持されるはず"

        for _ in range(5):
            machine.process_frame(frame)
            frame_idx["n"] += 1

    assert machine.in_match is False, "タイムアウト経過後はFalseへ戻るはず"
    assert "タイムアウトでOBSシーンを切り替えます" in caplog.text


def test_obs_switch_timeout_does_not_preempt_normal_blackout_path(monkeypatch, caplog):
    """Issue #395: タイムアウトより前に暗転を検知できた場合は従来どおりの経路
    (暗転検知 + obs_switch_delay_after_blackout_seconds)で切り替わり、
    タイムアウトのWARNINGは出ないことを確認する。
    """
    frame_idx = {"n": 0}
    blackout = {"active": False}
    machine = _make_machine_for_obs_timeout(
        monkeypatch, blackout, frame_idx, obs_switch_timeout_seconds=30
    )

    frame = np.zeros((10, 10, 3), dtype=np.uint8)

    with caplog.at_level(logging.WARNING, logger="nss_tracker.state"):
        for _ in range(5):
            machine.process_frame(frame)
            frame_idx["n"] += 1
        assert machine.in_match is True

        blackout["active"] = True
        # 暗転検知フレーム自身はelapsed=0のため、delay(=3)到達には4回分必要
        for _ in range(4):
            machine.process_frame(frame)
            frame_idx["n"] += 1

    assert machine.in_match is False, "暗転検知からdelay経過後にFalseへ戻るはず"
    assert "タイムアウト" not in caplog.text, "通常経路ではタイムアウトのWARNINGは出ないはず"


def test_obs_switch_uses_first_blackout_when_finalize_itself_triggered_by_blackout(monkeypatch):
    """Issue #224(追加調査): このゲームは試合終了後「ランク変更→暗転1→別画面→
    暗転2→マッチング画面」の順で暗転が2回現れる。_finalize()がIssue #209の
    「暗転を検知したら即確定」パス自身によって呼ばれた場合(=暗転1のフレーム
    そのものがfinalizeのトリガー)でも、in_matchはこの暗転1を起点に
    delay分待ってからFalseに戻ることを確認する(誤って暗転2を起点にしてしまう
    と、別画面を挟んだ分だけ切替が遅れる・タイミングがずれる回帰を防ぐ)。
    """
    frame_idx = {"n": 0}

    def fake_is_vs_screen(frame):
        # Issue #388: vs_screen_confirm_seconds=2(FakeClock step=1.0)を確定させるには
        # 経過2.0秒(=3フレーム分のスパン)必要
        return frame_idx["n"] < 3

    def fake_is_match_end_screen(frame):
        return 3 <= frame_idx["n"] < 6

    def fake_classify_banner(frame):
        return "win" if 6 <= frame_idx["n"] < 10 else None

    BLACKOUT1_FRAME = 15
    BLACKOUT2_FRAME = 50  # 別画面を挟んだ十分後ろ(暗転1を正しく捕まえていれば無関係のはず)

    def fake_is_full_blackout(frame):
        n = frame_idx["n"]
        return n == BLACKOUT1_FRAME or n >= BLACKOUT2_FRAME

    monkeypatch.setattr(match_state_module, "is_vs_screen", fake_is_vs_screen)
    # Issue #235: 暗転即確定パス(ランク監視中の挙動)を検証するテストのため、
    # VS画面でランクを検知した(ランクを賭けた)試合として扱う必要がある。
    # Issue #283: 帯番号は結果バナー確定時点のOCR(下のread_precise_rank、tier=10)と
    # 揃えておく(不一致時の挙動は別テストで検証するため、ここでは無関係にしたい)
    monkeypatch.setattr(match_state_module, "read_vs_screen_ranks", lambda frame: ([SlotRank("∞", 10)], []))
    monkeypatch.setattr(match_state_module, "is_match_end_screen", fake_is_match_end_screen)
    monkeypatch.setattr(match_state_module, "confirm_match_end_text", lambda frame: True)
    monkeypatch.setattr(match_state_module, "is_goal_event", lambda frame: False)
    monkeypatch.setattr(match_state_module, "classify_banner", fake_classify_banner)
    monkeypatch.setattr(match_state_module, "read_precise_rank", lambda frame, gauge_roi, rank_number_roi: (10, 10.0))
    monkeypatch.setattr(match_state_module, "read_rank_gauge_fill", lambda frame, roi: 0.0)
    monkeypatch.setattr(match_state_module, "is_league_change_screen", lambda frame: False)
    monkeypatch.setattr(match_state_module, "is_demotion_label_candidate", lambda frame: False)
    monkeypatch.setattr(match_state_module, "is_full_blackout", fake_is_full_blackout)

    machine = MatchStateMachine(
        now_fn=FakeClock(),
        vs_screen_confirm_seconds=2,
        banner_confirm_seconds=2,
        banner_confirm_seconds_after_match_end=2,
        match_end_confirm_seconds=1,
        # grace期間満了・periodic recheckいずれの経路でも確定させず、暗転即時確定
        # パスのみで確定させるため、これらのフレーム数閾値を到底届かない大きさにする
        league_change_grace_seconds=10_000,
        rank_recheck_interval_seconds=10_000,
        obs_switch_delay_after_blackout_seconds=5,
        rank_stability_monitor=StabilityMonitor(roi=(0, 0, 5, 5), stable_frames_required=1),
    )

    frame = np.zeros((10, 10, 3), dtype=np.uint8)

    result = None
    finalize_frame = None
    for _ in range(BLACKOUT1_FRAME + 5):
        result = machine.process_frame(frame)
        if result is not None and finalize_frame is None:
            finalize_frame = frame_idx["n"]
        frame_idx["n"] += 1
        if result is not None:
            break

    assert result is not None, "MatchResultが確定しなかった"
    assert finalize_frame == BLACKOUT1_FRAME, "暗転1のフレーム自体がfinalizeのトリガーになっているはず"
    assert machine.in_match is True, "確定直後、delay未経過ではまだTrueのはず"

    in_match_became_false_frame = None
    for _ in range(20):
        machine.process_frame(frame)
        if in_match_became_false_frame is None and machine.in_match is False:
            in_match_became_false_frame = frame_idx["n"]
            break
        frame_idx["n"] += 1

    assert in_match_became_false_frame is not None, "in_matchがFalseに戻らなかった"
    assert in_match_became_false_frame < BLACKOUT2_FRAME, (
        "暗転2(別画面を挟んだ2回目)を待たず、暗転1を起点にFalseへ戻っているはず"
    )
    # Issue #388: 経過秒数(now-started_at)ベースになったため、delay(=5)到達は
    # 暗転1検知フレーム自身から数えてdelay分後(暗転1フレームのelapsedは0のため)
    assert in_match_became_false_frame == BLACKOUT1_FRAME + 5, (
        "暗転1からobs_switch_delay_after_blackout_seconds(=5)秒経過後にFalseへ戻るはず"
    )


def test_obs_switch_uses_first_blackout_even_when_finalize_is_delayed(monkeypatch):
    """Issue #371: ランク確定(_finalize())が大きく遅れた試合でも、OBSシーン切替は
    「試合終了」OCR確認を起点とした最初の暗転(暗転1)で行われることを確認する。

    実配信(2026-08-14)では、ランクバッジを最後まで読み取れなかった試合で
    GRACEフェーズが長引き、_finalize()が暗転1どころか暗転2も過ぎた後に呼ばれた
    結果、OBSシーン切替が2〜4分遅延した。ここではその状況を、
    read_precise_rank()が常にNoneを返す(バッジが読めない)ことで再現する:
    _grace_candidate_rank_tierがNoneのままになるため、Issue #209の
    「暗転を検知したら即確定」パスが働かず、確定はgrace期間の満了まで遅れる。

    修正前は_pending_obs_switchが_finalize()でしか立たなかったため、この状況では
    暗転1・暗転2とも取りこぼしていた。修正後は暗転1を起点にFalseへ戻る。
    """
    frame_idx = {"n": 0}

    def fake_is_vs_screen(frame):
        # Issue #388: vs_screen_confirm_seconds=2(FakeClock step=1.0)を確定させるには
        # 経過2.0秒(=3フレーム分のスパン)必要
        return frame_idx["n"] < 3

    def fake_is_match_end_screen(frame):
        return 3 <= frame_idx["n"] < 6

    def fake_classify_banner(frame):
        return "lose" if 6 <= frame_idx["n"] < 10 else None

    BLACKOUT1_FRAME = 15
    BLACKOUT2_FRAME = 25
    GRACE_FRAMES = 40  # 暗転1・暗転2のどちらよりも後にfinalizeが来るようにする
    OBS_SWITCH_DELAY = 5

    def fake_is_full_blackout(frame):
        n = frame_idx["n"]
        return n == BLACKOUT1_FRAME or n == BLACKOUT2_FRAME

    monkeypatch.setattr(match_state_module, "is_vs_screen", fake_is_vs_screen)
    # ランクを賭けた試合(VS画面では自分のランクを検知できている)だが、
    # 結果画面ではバッジを読み取れない、という実配信で起きた状況を再現する
    monkeypatch.setattr(match_state_module, "read_vs_screen_ranks", lambda frame: ([SlotRank("∞", 10)], []))
    monkeypatch.setattr(match_state_module, "is_match_end_screen", fake_is_match_end_screen)
    monkeypatch.setattr(match_state_module, "confirm_match_end_text", lambda frame: True)
    monkeypatch.setattr(match_state_module, "is_goal_event", lambda frame: False)
    monkeypatch.setattr(match_state_module, "classify_banner", fake_classify_banner)
    monkeypatch.setattr(match_state_module, "read_precise_rank", lambda frame, gauge_roi, rank_number_roi: None)
    monkeypatch.setattr(match_state_module, "read_rank_gauge_fill", lambda frame, roi: None)
    monkeypatch.setattr(match_state_module, "is_league_change_screen", lambda frame: False)
    monkeypatch.setattr(match_state_module, "is_demotion_label_candidate", lambda frame: False)
    monkeypatch.setattr(match_state_module, "is_full_blackout", fake_is_full_blackout)

    machine = MatchStateMachine(
        now_fn=FakeClock(),
        vs_screen_confirm_seconds=2,
        banner_confirm_seconds=2,
        banner_confirm_seconds_after_match_end=2,
        match_end_confirm_seconds=1,
        league_change_grace_seconds=GRACE_FRAMES,
        rank_recheck_interval_seconds=10_000,
        obs_switch_delay_after_blackout_seconds=OBS_SWITCH_DELAY,
        rank_stability_monitor=StabilityMonitor(roi=(0, 0, 5, 5), stable_frames_required=1),
    )

    frame = np.zeros((10, 10, 3), dtype=np.uint8)

    in_match_became_false_frame = None
    finalize_frame = None
    for _ in range(BLACKOUT2_FRAME + GRACE_FRAMES + 20):
        result = machine.process_frame(frame)
        if result is not None and finalize_frame is None:
            finalize_frame = frame_idx["n"]
        if in_match_became_false_frame is None and machine.in_match is False and frame_idx["n"] > 2:
            in_match_became_false_frame = frame_idx["n"]
        frame_idx["n"] += 1
        if finalize_frame is not None:
            break

    assert finalize_frame is not None, "MatchResultが確定しなかった"
    assert in_match_became_false_frame is not None, "in_matchがFalseに戻らなかった"
    # Issue #388: 経過秒数(now-started_at)ベースになったため、delay到達は
    # 暗転1検知フレーム自身から数えてdelay分後(暗転1フレームのelapsedは0のため)
    assert in_match_became_false_frame == BLACKOUT1_FRAME + OBS_SWITCH_DELAY, (
        "「試合終了」確認を起点に、暗転1からdelay分経過した時点でFalseへ戻るはず"
    )
    assert in_match_became_false_frame < finalize_frame, (
        "OBSシーン切替がランク確定(_finalize())の完了を待たずに行われているはず"
        "(修正前は_finalize()でしかフラグが立たず、暗転1・暗転2とも取りこぼしていた)"
    )


def test_in_match_stays_true_after_finalize_without_match_end_confirmation(monkeypatch):
    """Issue #190: 「試合終了」バナーをOCR確認できないまま試合結果が確定した場合
    (実プレイ中の背景誤検知がbanner_confirm_framesを突破した可能性を否定できない
    ケース)、OBSシーン切替の誤爆を防ぐためin_matchはTrueのまま維持され、
    (視聴者体験としては)試合中シーンに留まることを確認する。MatchResult自体は
    従来どおり記録される。
    """
    frame_idx = {"n": 0}

    def fake_is_vs_screen(frame):
        # Issue #388: vs_screen_confirm_seconds=2(FakeClock step=1.0)を確定させるには
        # 経過2.0秒(=3フレーム分のスパン)必要
        return frame_idx["n"] < 3

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
        now_fn=FakeClock(),
        vs_screen_confirm_seconds=2,
        banner_confirm_seconds=2,
        league_change_grace_seconds=1,
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

    machine = MatchStateMachine(now_fn=FakeClock(), vs_screen_confirm_seconds=2)
    frame = np.zeros((10, 10, 3), dtype=np.uint8)
    for _ in range(10):
        machine.process_frame(frame)
    # Issue #189/#397: VS画面ランクOCRは別プロセス(テストではThreadPoolExecutor)で
    # 実行されるため、呼び出し回数を確認する前に完了を待つ
    machine._poll_vs_ocr(wait=True)

    assert read_calls["n"] == 1


def test_vs_screen_flicker_after_confirm_does_not_double_count_match(monkeypatch):
    """Issue #234: 同じVS画面が表示され続けている間に、演出等でis_vs_screenが
    1フレームだけFalseを返しても、確定直後のロック期間中は再度の試合開始として
    数えないことを確認する(実機で見つかった二重カウント不具合の再現)。
    """
    # frame_idx 3(確定に必要なstreakを満たした直後)だけFalseにして、
    # 実データで見られたキック演出中の一瞬の判定揺れを再現する
    flicker_frame_idx = 3

    def fake_is_vs_screen(frame):
        return frame_idx["n"] != flicker_frame_idx

    frame_idx = {"n": 0}
    monkeypatch.setattr(match_state_module, "is_vs_screen", fake_is_vs_screen)
    monkeypatch.setattr(match_state_module, "read_vs_screen_ranks", lambda frame: ([], []))
    monkeypatch.setattr(match_state_module, "read_team_colors", lambda frame: (None, None))
    monkeypatch.setattr(match_state_module, "is_goal_event", lambda frame: False)
    monkeypatch.setattr(match_state_module, "classify_banner", lambda frame: None)
    monkeypatch.setattr(match_state_module, "is_match_end_screen", lambda frame: False)
    monkeypatch.setattr(match_state_module, "is_full_blackout", lambda frame: False)

    # vs_screen_lockout_framesはテスト全体のフレーム数より大きくしておき、
    # ロックが途中で切れて2回目の確定に成功してしまわないようにする
    machine = MatchStateMachine(now_fn=FakeClock(), vs_screen_confirm_seconds=2, vs_screen_lockout_seconds=100)
    frame = np.zeros((10, 10, 3), dtype=np.uint8)
    for _ in range(20):
        machine.process_frame(frame)
        frame_idx["n"] += 1

    assert machine._session_match_no == 1, "同じVS画面表示中のフリッカーで試合開始が二重に数えられてはいけない"


def test_vs_screen_lockout_expires_before_next_real_match(monkeypatch):
    """Issue #234: ロック期間が終わった後に本当に新しいVS画面が表示された場合は、
    正しく2試合目として数えられることを確認する(ロックが以降ずっと新しい試合の
    検知を妨げ続けないことの確認)。
    """

    def fake_is_vs_screen(frame):
        n = frame_idx["n"]
        # 0-2: 1試合目のVS画面, 3-9: ロック期間+試合中(VS画面ではない),
        # 10-: 2試合目の本物のVS画面
        return n < 3 or n >= 10

    frame_idx = {"n": 0}
    monkeypatch.setattr(match_state_module, "is_vs_screen", fake_is_vs_screen)
    monkeypatch.setattr(match_state_module, "read_vs_screen_ranks", lambda frame: ([], []))
    monkeypatch.setattr(match_state_module, "read_team_colors", lambda frame: (None, None))
    monkeypatch.setattr(match_state_module, "is_goal_event", lambda frame: False)
    monkeypatch.setattr(match_state_module, "classify_banner", lambda frame: None)
    monkeypatch.setattr(match_state_module, "is_match_end_screen", lambda frame: False)
    monkeypatch.setattr(match_state_module, "is_full_blackout", lambda frame: False)

    machine = MatchStateMachine(now_fn=FakeClock(), vs_screen_confirm_seconds=2, vs_screen_lockout_seconds=5)
    frame = np.zeros((10, 10, 3), dtype=np.uint8)
    for _ in range(20):
        machine.process_frame(frame)
        frame_idx["n"] += 1

    assert machine._session_match_no == 2, "ロック解除後の本物のVS画面は2試合目として数えられるはず"


def test_vs_screen_confirmed_again_before_previous_match_finalized_logs_info(monkeypatch, caplog):
    """Issue #243: 前の試合が結果画面確定(_finalize())前に残ったまま次のVS画面が
    確定した場合、前の試合のゴールが新しい試合に持ち越されることをINFOログで
    可視化することを確認する。挙動(持ち越されること自体)は変更しない。
    """

    def fake_is_vs_screen(frame):
        n = frame_idx["n"]
        # Issue #388: vs_screen_confirm_seconds=2(FakeClock step=1.0)を確定させるには
        # 経過2.0秒(=3フレーム分のスパン)必要。0-2: 1試合目のVS画面確定,
        # 3-8: 結果画面が来ないまま試合中(ロック期間), 9-: 1試合目を確定させないまま
        # 2試合目の本物のVS画面が現れる
        return n < 3 or n >= 9

    def fake_is_goal_event(frame):
        # Issue #388: goal_confirm_seconds=1(FakeClock step=1.0)を確定させるには
        # 経過1.0秒(=2フレーム分のスパン)必要
        return frame_idx["n"] in (3, 4)

    frame_idx = {"n": 0}
    monkeypatch.setenv("GOAL_RECORD_MODE", "allowlist")
    monkeypatch.setattr(match_state_module, "is_vs_screen", fake_is_vs_screen)
    monkeypatch.setattr(match_state_module, "read_vs_screen_ranks", lambda frame: ([], []))
    monkeypatch.setattr(match_state_module, "read_team_colors", lambda frame: (None, None))
    monkeypatch.setattr(match_state_module, "is_goal_event", fake_is_goal_event)
    monkeypatch.setattr(match_state_module, "confirm_goal_text", lambda frame: True)
    monkeypatch.setattr(match_state_module, "is_own_goal_event", lambda frame: False)
    monkeypatch.setattr(match_state_module, "read_scorer_name", lambda frame: ("Alice", 0.95))
    monkeypatch.setattr(match_state_module, "read_assist_name", lambda frame: None)
    monkeypatch.setattr(match_state_module, "classify_banner", lambda frame: None)
    monkeypatch.setattr(match_state_module, "is_match_end_screen", lambda frame: False)
    monkeypatch.setattr(match_state_module, "is_full_blackout", lambda frame: False)

    machine = MatchStateMachine(now_fn=FakeClock(), vs_screen_confirm_seconds=2, goal_confirm_seconds=1, vs_screen_lockout_seconds=5)
    frame = np.zeros((10, 10, 3), dtype=np.uint8)
    with caplog.at_level("INFO", logger="nss_tracker.state"):
        for _ in range(30):
            machine.process_frame(frame)
            frame_idx["n"] += 1

    assert machine._session_match_no == 2
    assert len(machine._pending_goals) == 1, "テスト前提: 1試合目のゴールが持ち越されているはず"
    assert (
        "1試合目: 前の試合が結果画面確定前に次のVS画面を検知しました。"
        "前の試合のゴール(1件)は今回の試合の記録に持ち越されます" in caplog.text
    )


def test_banner_confirm_survives_severely_degraded_effective_fps(monkeypatch):
    """Issue #388の回帰テスト。

    #383/#387で実際に観測されたとおり、検知ループの実効fpsは処理内容次第で
    大きく変動しうる(暗転待ち区間だけ60fps→約28fpsに半減する等)。以前の
    フレーム数ベースの実装であれば、banner_confirm_seconds(既定2.0秒、
    main.pyが渡すproduction値)は60fps想定でbanner_confirm_frames=120フレームに
    換算されており、実効fpsがこのテストのように5fps相当(FakeClock step=0.2秒)まで
    落ち込んだ場合、120フレーム分の観測に実時間24秒かかっていたはずで、
    実際には2秒しか表示されない結果バナーの確定を取りこぼしていた
    (#387で解析した「勝ち」バナー未記録の実害と同じ構造)。

    新実装(実時間ベース)では、実効fpsがどれだけ低くても「実際に画面上で
    2秒間持続したか」だけを見るため、正しく約2.0秒(FakeClock step=0.25秒で
    9サンプル)で確定することを確認する。step=0.25は2進数で誤差なく表現できる値
    (0.2だと浮動小数点の累積誤差でelapsedが2.0にわずかに届かず1サンプル余分に
    必要になることがあったため)。
    """
    monkeypatch.setattr(match_state_module, "is_vs_screen", lambda frame: False)
    monkeypatch.setattr(match_state_module, "is_goal_event", lambda frame: False)
    monkeypatch.setattr(match_state_module, "is_match_end_screen", lambda frame: False)
    monkeypatch.setattr(match_state_module, "classify_banner", lambda frame: "win")
    monkeypatch.setattr(match_state_module, "read_precise_rank", lambda frame, gauge_roi, rank_number_roi: None)

    # banner_confirm_secondsは明示的に渡さず、production(main.py)と同じ既定値
    # (config/detection.tomlのBANNER_CONFIRM_SECONDS=2.0)をそのまま使う
    machine = MatchStateMachine(now_fn=FakeClock(step=0.25))
    # このテストの関心事(banner確定の実時間耐性)にVS画面確定の全過程は無関係なため、
    # 他のテストと同じショートカットでVS画面確認済みとして扱う。
    # _pending_vs_mine_ranksが空のままなので「ランクを賭けない試合」経路に乗り、
    # バナー確定と同じフレームでMatchResultが即座に確定する(_watch_for_banner参照)
    machine._vs_confirmed_this_match = True

    frame = np.zeros((10, 10, 3), dtype=np.uint8)
    confirmed_at = None
    for i in range(20):
        result = machine.process_frame(frame)
        if result is not None:
            confirmed_at = i + 1
            break

    assert confirmed_at is not None, "実効fpsが低くても、実経過2.0秒でbanner確定するはず"
    assert confirmed_at == 9, (
        f"確定までに{confirmed_at}サンプルかかった(期待は実経過2.0秒分=9サンプル)。"
        "フレーム数ベースの挙動に逆行していないか確認すること"
    )


class _ManualExecutor:
    """submitされた関数を保留し、テスト側がresolve()を呼ぶまで完了させないフェイク(Issue #430)。"""

    def __init__(self) -> None:
        self.pending: list = []

    def submit(self, fn, *args, **kwargs):
        future: concurrent.futures.Future = concurrent.futures.Future()
        self.pending.append((future, fn, args, kwargs))
        return future

    def resolve(self) -> None:
        for future, fn, args, kwargs in self.pending:
            future.set_result(fn(*args, **kwargs))
        self.pending = []


class _LazyFuture(concurrent.futures.Future):
    """result()で待たれた時点で初めて完了するFuture(Issue #430)。

    done()は待たれるまでFalseを返すため、「非ブロッキングの取り込みでは
    まだ届いていない」「完了を待つ経路でだけ値が揃う」状況をスレッド無しで
    決定的に作れる。
    """

    def __init__(self, fn, args) -> None:
        super().__init__()
        self._fn = fn
        self._args = args

    def result(self, timeout=None):
        if not super().done():
            self.set_result(self._fn(*self._args))
        return super().result(timeout)


class _LazyExecutor:
    def submit(self, fn, *args, **kwargs):
        return _LazyFuture(fn, args)


def _patch_for_rank_before_pending_tests(monkeypatch, gauge_fill_fn):
    monkeypatch.setattr(match_state_module, "is_goal_event", lambda frame: False)
    monkeypatch.setattr(match_state_module, "is_vs_screen", lambda frame: False)
    monkeypatch.setattr(match_state_module, "is_match_end_screen", lambda frame: False)
    monkeypatch.setattr(match_state_module, "is_full_blackout", lambda frame: False)
    monkeypatch.setattr(match_state_module, "classify_banner", lambda frame: "win")
    monkeypatch.setattr(
        match_state_module,
        "read_precise_rank",
        lambda frame, gauge_roi, rank_number_roi: (41, 41.2) if rank_number_roi == RANK_NUMBER_ROI_COMPACT else None,
    )
    monkeypatch.setattr(match_state_module, "read_rank_gauge_fill", gauge_fill_fn)
    monkeypatch.setattr(match_state_module, "is_league_change_screen", lambda frame: False)
    monkeypatch.setattr(match_state_module, "is_demotion_label_candidate", lambda frame: False)


def _ranked_machine_waiting_for_banner(executor) -> MatchStateMachine:
    machine = MatchStateMachine(
        now_fn=FakeClock(),
        banner_confirm_seconds=2,
        # GRACE満了では確定させない(暗転・読み取りの取り込みだけを見るため)
        league_change_grace_seconds=100,
        rank_ocr_executor=executor,
        rank_stability_monitor=StabilityMonitor(roi=(0, 0, 5, 5), stable_frames_required=1),
    )
    machine._vs_confirmed_this_match = True
    machine._pending_vs_mine_ranks = [SlotRank("∞", 41)]
    return machine


def test_ranked_match_tracks_gauge_while_rank_before_ocr_is_pending(monkeypatch):
    """Issue #430: ランクを賭けた試合では、結果バナー確定時の試合前ランク読み取りの
    完了を待たずにTRACKING_RANKへ進み、待っている間もゲージを追跡することを確認する。

    以前はここで完了を待っており(実測2.4〜4.0秒)、その間にランク変動アニメーションが
    終わってしまうため、ゲージ追跡・手動入力用クリップのどちらも取りこぼしていた。
    """
    gauge_calls = {"n": 0}

    def fake_gauge_fill(frame, roi):
        gauge_calls["n"] += 1
        return 0.4

    _patch_for_rank_before_pending_tests(monkeypatch, fake_gauge_fill)
    executor = _ManualExecutor()
    machine = _ranked_machine_waiting_for_banner(executor)

    frame = np.zeros((10, 10, 3), dtype=np.uint8)
    for _ in range(5):
        machine.process_frame(frame)
        if machine.current_state == "tracking_rank":
            break
    assert machine.current_state == "tracking_rank", "読み取りの完了を待たずにTRACKING_RANKへ進むはず"
    assert len(executor.pending) == 1, "試合前ランクの読み取りは投げたまま(未完了)のはず"
    assert machine._pending_rank_before is None

    for _ in range(5):
        assert machine.process_frame(frame) is None
    assert gauge_calls["n"] > 0, "読み取りを待っている間もゲージを追跡するはず"
    assert machine._pending_rank_before is None, "完了していない間は試合前ランクは空のまま"

    executor.resolve()
    machine.process_frame(frame)
    assert machine._pending_rank_before_tier == 41
    assert machine._pending_rank_before == pytest.approx(41.2)
    assert machine._grace_candidate_rank_tier == 41, "取り込んだ時点で帯番号の起点も埋まるはず"


def test_blackout_while_rank_before_ocr_is_pending_waits_and_finalizes(monkeypatch):
    """Issue #430: 試合前ランクの読み取りが届く前に暗転が来た場合、その場で完了を
    待ってから確定することを確認する(待たずに素通りすると帯番号の起点が無いまま
    GRACE満了まで確定が延びる)。
    """
    _patch_for_rank_before_pending_tests(monkeypatch, lambda frame, roi: 0.4)
    machine = _ranked_machine_waiting_for_banner(_LazyExecutor())

    frame = np.zeros((10, 10, 3), dtype=np.uint8)
    not_black = BlackoutObservation(blackout=False, min_mean=120.0, min_std=40.0)
    for _ in range(5):
        machine.process_frame(frame, not_black)
        if machine.current_state == "tracking_rank":
            break
    assert machine.current_state == "tracking_rank"

    for _ in range(3):
        assert machine.process_frame(frame, not_black) is None
    assert machine._rank_before_future is not None, "暗転が来るまでは読み取りの完了を待たないはず"

    result = machine.process_frame(frame, BlackoutObservation(blackout=True, min_mean=0.0, min_std=0.5))
    assert result is not None, "暗転の時点で読み取りの完了を待って確定するはず"
    assert result.rank_before == pytest.approx(41.2)
