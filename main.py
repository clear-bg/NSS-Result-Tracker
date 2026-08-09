"""アプリケーションのエントリーポイント。

capture(FfmpegFrameReader) → state(MatchStateMachine) → database(db)を
つなぎ、常時稼働する検知ループとして実行する。ログ設定もここで一箇所に
まとめる(CLAUDE.md記載のログ方針: コンソール+ファイルの両方に出力、
レベルは環境変数NSS_TRACKER_LOG_LEVELで切り替え)。ログの
タイムスタンプはOSのタイムゾーン設定によらず常にJST表記で出力する
(DB側もtimeutil.now_jstで統一済み、本ファイルの_jst_converter参照)。

ログファイルはIssue #71対応でセッション(プロセス起動)ごとに分けている
(`logs/tracker_{起動時刻(JST)}.log`、_generate_log_file_path参照)。以前は
単一ファイルをサイズでローテーションする方式だったが、複数回に分けて
実プレイした際にセッション単位で見返しやすいよう変更した。

`uv run python main.py` で実行するとOBS Virtual Camera(dshow)からの
実キャプチャを試みる。OBS/Switchをまだ用意していない段階でも配線全体を
確認できるよう、`uv run python main.py --video path/to/file.mp4` を渡すと
fixtures/videos等の動画ファイルを入力に差し替えて同じループを試せる。
"""

import argparse
import logging
import sqlite3
import sys
import time
import webbrowser
from concurrent.futures import ProcessPoolExecutor
from datetime import datetime
from pathlib import Path
from typing import Optional

import cv2
import numpy as np

from nss_tracker.capture.ffmpeg_capture import FfmpegFrameReader
from nss_tracker.config import (
    ConfigError,
    get_capture_device_name,
    get_capture_fps,
    get_capture_resolution,
    get_db_path,
    get_frame_read_timeout_seconds,
    get_goal_record_mode,
    get_log_level,
    get_obs_browser_source_names,
    get_obs_scene_between_matches,
    get_obs_scene_in_match,
    get_obs_scene_switching_enabled,
    get_obs_websocket_host,
    get_obs_websocket_password,
    get_obs_websocket_port,
    get_rank_delta_distribution_scope,
    get_rank_graph_match_limit,
    get_web_host,
    get_web_port,
    get_youtube_chat_dive_time_enabled,
)
from nss_tracker.database import db
from nss_tracker.detection.goal import _get_name_reader
from nss_tracker.detection.motion import StabilityMonitor, is_full_blackout
from nss_tracker.detection.rank_ocr import RANK_NUMBER_ROI_ENLARGED, RANK_ROI, _get_reader, read_rank
from nss_tracker.detection.vs_rank import _get_reader as _get_vs_rank_reader
from nss_tracker.obs_control import ObsSceneController
from nss_tracker.rank_entry_clips import DEFAULT_CLIPS_DIR, RankEntryClipRecorder
from nss_tracker.state.match_state import (
    VS_SCREEN_LOCKOUT_SECONDS,
    MatchResult,
    MatchStateMachine,
    VsScreenEvent,
)
from nss_tracker.timeutil import JST, now_jst
from nss_tracker.web.runner import start_web_server_thread
from nss_tracker.web.server import create_app
from nss_tracker.youtube_chat import DiveTimeWatcher

LOG_DIR = Path("logs")
# Issue #255: 実キャプチャ時のfpsは.envのCAPTURE_FPSを使う(get_capture_fps参照)。
# これは--video指定時、動画ファイルからのfps自動検出(_detect_fps)が失敗した
# 場合にのみ使うフォールバック値
DEFAULT_CAPTURE_FPS = 30.0

logger = logging.getLogger("nss_tracker")


_JST_OFFSET_SECONDS = JST.utcoffset(None).total_seconds()


def _jst_converter(secs: float) -> time.struct_time:
    """ログのタイムスタンプ変換を、OSのタイムゾーン設定に依存せず常にJSTにする。

    logging.Formatter.converterのデフォルト(time.localtime)はOS設定に従うため、
    OS側がJST以外の場合にログとDB(timeutil.now_jst参照、常にJST)の表記が
    ズレてしまう。gmtime基準にtimeutil.JSTのオフセット分ずらすことで、OS設定に
    よらず常にJSTのカレンダー時刻を計算する(zoneinfo等のタイムゾーンDBは不要)。
    """
    return time.gmtime(secs + _JST_OFFSET_SECONDS)


def _generate_log_file_path(now: Optional[datetime] = None) -> Path:
    """セッション(プロセス起動)ごとに固有のログファイルパスを生成する。

    Issue #71: 単一ファイルをサイズでローテーションする方式から、
    起動時刻(JST)をファイル名に埋め込んでセッションごとに分ける方式に変更した
    (複数回に分けて実プレイした際、ログをセッション単位で見返しやすくするため)。
    nowを省略した場合はJSTの現在時刻を使う(テスト用に注入できるようにしている)。
    """
    if now is None:
        now = datetime.now(JST)
    return LOG_DIR / f"tracker_{now.strftime('%Y%m%d_%H%M%S')}.log"


def _setup_logging() -> Path:
    level = get_log_level()

    LOG_DIR.mkdir(exist_ok=True)
    log_file = _generate_log_file_path()
    formatter = logging.Formatter("%(asctime)s JST [%(levelname)s] %(name)s: %(message)s")
    formatter.converter = _jst_converter

    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)

    file_handler = logging.FileHandler(log_file, encoding="utf-8")
    file_handler.setFormatter(formatter)

    logger.handlers.clear()
    logger.setLevel(level)
    logger.addHandler(console_handler)
    logger.addHandler(file_handler)
    # 依存ライブラリ(torch等)がroot loggerに独自のhandlerを設定していることがあり、
    # propagateしたままだと同じメッセージが二重・三重に出力されてしまう
    logger.propagate = False
    return log_file


def _make_reader(video_path: Optional[Path]) -> FfmpegFrameReader:
    if video_path is None:
        width, height = get_capture_resolution()
        return FfmpegFrameReader(device_name=get_capture_device_name(), width=width, height=height)
    # -re: 動画をファイルの本来のfpsで(実時間と同じ速さで)読み込む。
    # 付けない場合ffmpegはデコードできる限り高速に全フレームを吐き出してしまい、
    # FfmpegFrameReaderの「追いつかない間の古いフレームは破棄する」設計と組み合わさると
    # 実際にはほとんどのフレームが読み飛ばされてしまい、実キャプチャの動作を再現できない
    return FfmpegFrameReader(input_args=["-re", "-i", str(video_path)])


def _detect_fps(video_path: Path) -> float:
    cap = cv2.VideoCapture(str(video_path))
    try:
        fps = cap.get(cv2.CAP_PROP_FPS)
    finally:
        cap.release()
    return fps if fps and fps > 0 else DEFAULT_CAPTURE_FPS


def _make_match_state_machine(fps: float) -> MatchStateMachine:
    """fpsに応じてスケーリングした閾値でMatchStateMachineを構築する。

    state/match_state.pyのdocstring・クラスのデフォルト値は30fps想定のため、
    60fps等の入力ではここで呼び出し側からスケーリングする必要がある
    (CLAUDE.md・tests/test_match_state.pyの_run_state_machineと同じ考え方)。

    Issue #303: 帯番号の定期再チェック(PaddleOCR)用に、ここでProcessPoolExecutorを
    明示的に渡す。MatchStateMachine側のデフォルト(ThreadPoolExecutor)はGILの制約で
    CPUバウンドなPaddleOCR推論の間メインループを道連れでブロックしてしまうため、
    本番実行では別プロセスで真に並列実行する必要がある(state/match_state.pyの
    モジュールdocstring参照)。ワーカープロセスは`MatchStateMachine`のライフタイム
    (=アプリのセッション全体)を通じて使い回されるため、モデル読み込みのコールド
    スタートは実質的に最初の1回だけで済む。
    """
    confirm_frames = round(fps * 1.0)
    # Issue #67: 通常プレイ中の背景誤検知(実測1.3秒程度持続)がデバウンス(1秒)を
    # すり抜けて結果バナーの誤検知が発生したため、banner_confirm_framesのみ2秒に延長。
    # Issue #76: 「試合終了」バナーを確認できていれば、Issue #67修正前と同じ1秒
    # (confirm_framesと同じ)に短縮する(state/match_state.pyのモジュールdocstring参照)
    banner_confirm_frames = round(fps * 2.0)
    # Issue #190: このデバウンスは安全マージンとして機能しておらず(真偽の判定は
    # OCR文字一致confirm_match_end_textが担う)、「試合終了」バナーは実測最短
    # 7フレーム(60fps)程度しか綺麗に表示されないことがあるため、色候補判定を
    # 満たした最初のフレームで即OCR確認する(state/match_state.py参照)
    match_end_confirm_frames = 1
    tier_recheck_executor = ProcessPoolExecutor(max_workers=1)
    # Issue #303: ワーカープロセス側のPaddleOCRモデル読み込み(コールドスタート、
    # 実測3.8〜7秒程度)を、実際に必要になる前(=最初のランクを賭けた試合の
    # GRACEフェーズ)に前倒しで済ませておく。結果は使わず投げっぱなしでよい
    # (_warmup_ocr_engines()と同じ狙いだが、こちらは別プロセスで実行されるため
    # メインプロセス側のフレーム取得を一切妨げない)
    tier_recheck_executor.submit(read_rank, np.zeros((1080, 1920, 3), dtype=np.uint8), RANK_NUMBER_ROI_ENLARGED)
    return MatchStateMachine(
        banner_confirm_frames=banner_confirm_frames,
        banner_confirm_frames_after_match_end=confirm_frames,
        banner_absence_confirm_frames=confirm_frames,
        goal_confirm_frames=confirm_frames,
        vs_screen_confirm_frames=confirm_frames,
        # Issue #234: VS_SCREEN_LOCKOUT_SECONDS(config/detection.tomlの
        # [match_state]で上書き可能)をfpsに応じてフレーム数換算する
        vs_screen_lockout_frames=round(fps * VS_SCREEN_LOCKOUT_SECONDS),
        match_end_confirm_frames=match_end_confirm_frames,
        demotion_label_confirm_frames=confirm_frames,
        tier_recheck_executor=tier_recheck_executor,
        league_change_grace_frames=round(fps * 5.0),
        rank_recheck_interval_frames=round(fps * 0.25),
        rank_tier_rescan_wait_frames=round(fps / 6),
        # Issue #224: 試合終了時のOBSシーン切替は、暗転を最初に検知してから1秒後
        obs_switch_delay_after_blackout_frames=round(fps * 1.0),
        rank_stability_monitor=StabilityMonitor(roi=RANK_ROI, stable_frames_required=round(fps * 0.5)),
    )


def _record_match_result(conn: sqlite3.Connection, session_id: Optional[int], result: MatchResult) -> int:
    match_id = db.save_match_result(conn, result, session_id=session_id)
    # Issue #306: rank_after/league_changedは手動入力が確定させるまでDBにはNULLの
    # ままのため、ここでのresult.rank_after/league_changedはあくまで自動検知(OCR)の
    # 参考値であることが分かるようにログの文言を変える(rank_after_ocr参照)
    logger.info(
        "%d試合目 試合結果を記録しました: id=%d result=%s rank_before=%s rank_after_ocr=%s(参考、正の値は手動入力待ち) "
        "goals=%d team_color=%s->%s",
        result.session_match_no,
        match_id,
        result.result,
        result.rank_before,
        result.rank_after,
        len(result.goals),
        result.mine_team_color,
        result.opponent_team_color,
    )
    for goal in result.goals:
        goal_id = db.save_goal(
            conn, match_id, goal.scorer_name, goal.assist_name, goal.detected_at, is_own_goal=goal.is_own_goal
        )
        if goal_id is not None:
            logger.info(
                "%d試合目 ゴールを記録しました: match_id=%d scorer=%s assist=%s",
                result.session_match_no,
                match_id,
                goal.scorer_name,
                goal.assist_name,
            )

    if result.vs_mine_ranks or result.vs_opponent_ranks:
        db.save_vs_slot_ranks(conn, match_id, result.vs_mine_ranks, result.vs_opponent_ranks)
        # Issue #236: rankの移り変わりは「試合結果を記録しました」ログにも出ているが、
        # VS画面のランク(mine/opponent)とあわせて見比べやすいようこちらにも出す
        logger.info(
            "%d試合目 VS画面のランクを記録しました: match_id=%d mine=%s opponent=%s rank=%s->%s",
            result.session_match_no,
            match_id,
            result.vs_mine_ranks,
            result.vs_opponent_ranks,
            result.rank_before,
            result.rank_after,
        )
    else:
        logger.info(
            "%d試合目 VS画面を検知できなかったため、VSスロットランクは記録しません: match_id=%d",
            result.session_match_no,
            match_id,
        )
        # Issue #145: VS画面を見逃した試合が終わった際は、対戦相手ランク比較ウィジェットの
        # 表示を前の試合の値のまま残さず、none/noneにリセットする(db._recordの
        # モジュールdocstring参照)
        db.save_vs_rank_snapshot(conn, session_id, [], [], None, None, result.detected_at)

    return match_id


def _record_vs_screen_event(conn: sqlite3.Connection, session_id: Optional[int], event: VsScreenEvent) -> None:
    """VS画面確定を検知した瞬間に、対戦相手ランク比較ウィジェット用のスナップショットを即書き込む(Issue #145)。

    試合結果確定(_record_match_result)を待たないため、ウィジェットの表示が
    1試合分遅れる問題を解消する。VS画面検知自体はできたがランク読み取り自体が
    失敗した場合(mine_ranks/opponent_ranksが両方空)は何も書かない(このフレーム
    時点では単なる読み取り失敗か本当にVS画面自体を見逃したか区別できないため、
    最終的な扱いは試合終了時の_record_match_resultに委ねる)。
    """
    if not event.mine_ranks and not event.opponent_ranks:
        return
    db.save_vs_rank_snapshot(
        conn,
        session_id,
        event.mine_ranks,
        event.opponent_ranks,
        event.mine_team_color,
        event.opponent_team_color,
        now_jst(),
    )
    logger.info(
        "%d試合目 VS画面のランクを即時反映しました: mine=%s opponent=%s team_color=%s->%s",
        event.session_match_no,
        event.mine_ranks,
        event.opponent_ranks,
        event.mine_team_color,
        event.opponent_team_color,
    )


def _warmup_ocr_engines() -> None:
    """OCRエンジン(EasyOCR・PaddleOCR)を事前に構築しておく。

    実測: モデル読み込みを伴う初回構築は約3.8秒かかる(2回目以降は0.2秒未満)。
    ループ中に初めて呼び出すと、その数秒間フレーム取得側が実時間で進み続け、
    FfmpegFrameReaderの「追いつかない間は古いフレームを破棄する」設計と
    組み合わさって、ちょうどランクバッジの安定待ちが始まる直後というもっとも
    重要な数秒間分のフレームを丸ごと読み飛ばしてしまう。ループ開始前に
    済ませておくことでこれを避ける。
    """
    logger.info("OCRエンジンを初期化しています(数秒かかります)")
    _get_reader()
    _get_name_reader()
    _get_vs_rank_reader()
    logger.info("OCRエンジンの初期化が完了しました")


def run(
    reader: FfmpegFrameReader,
    machine: MatchStateMachine,
    conn: sqlite3.Connection,
    session_id: Optional[int],
    obs_controller: ObsSceneController,
    fps: float,
    clip_recorder: RankEntryClipRecorder,
) -> None:
    prev_state = machine.current_state
    prev_in_match = machine.in_match
    frame_read_timeout_seconds = get_frame_read_timeout_seconds()
    # Issue #71: Ctrl+C受信時にセッションサマリを出すための内訳カウンタ
    session_results = {"win": 0, "lose": 0, "draw": 0}
    # Issue #307: 録画中の区間に対応する試合のID。試合結果確定(_record_match_result)
    # の時点で判明するまではNone(録画自体はそれより前、tracking_rank突入時点で
    # 始まっているため)。clip_recorder.is_recordingがFalseの間は常にNone
    pending_clip_match_id: Optional[int] = None

    _warmup_ocr_engines()

    logger.info("フレーム取得を開始します")
    reader.start()
    try:
        while True:
            frame = reader.read(timeout=frame_read_timeout_seconds)
            if frame is None:
                if reader.is_running:
                    logger.warning("フレーム取得が%.0f秒以内に来ませんでした。継続します", frame_read_timeout_seconds)
                    # 入力終了直後はread()が待機せず即座にNoneを返し続けることがあるため、
                    # is_running(プロセスの終了検知)が追いつくまでの間ビジーループしないよう
                    # 一呼吸置く
                    time.sleep(0.1)
                    continue
                if reader.error is not None:
                    logger.error("ffmpegの読み取りでエラーが発生しました: %s", reader.error)
                else:
                    logger.error("ffmpegプロセスが終了し、フレームが取得できなくなりました")
                break

            result = machine.process_frame(frame)

            vs_screen_event = machine.pop_vs_screen_event()
            if vs_screen_event is not None:
                _record_vs_screen_event(conn, session_id, vs_screen_event)

            if machine.current_state != prev_state:
                # Issue #307: ランクを賭けた試合の結果バナー確定〜GRACEフェーズ突入の
                # 瞬間(モジュールdocstring参照、ランクを賭けない試合はこの遷移自体が
                # 起こらないため自然に録画対象外になる)
                if prev_state == "watching" and machine.current_state == "tracking_rank":
                    clip_recorder.start(fps)
                    pending_clip_match_id = None
                # 処理落ち(フレーム抜け)の実測用。この累計値を状態遷移のたびに出すことで、
                # 例えばtracking_rank突入〜離脱の間の差分から、ランク確定処理中に
                # どれだけフレームを読み捨てたかを事後に追えるようにする
                logger.info(
                    "状態遷移: %s -> %s (frames_produced=%d frames_consumed=%d)",
                    prev_state,
                    machine.current_state,
                    reader.frames_produced,
                    reader.frames_consumed,
                )
                prev_state = machine.current_state

            if machine.in_match != prev_in_match:
                obs_controller.set_in_match(machine.in_match)
                prev_in_match = machine.in_match

            if result is not None:
                match_id = _record_match_result(conn, session_id, result)
                session_results[result.result] += 1
                if clip_recorder.is_recording:
                    pending_clip_match_id = match_id

            if clip_recorder.is_recording:
                # Issue #307: 暗転を検知した時点、または録画が異常に長引いた場合の
                # 安全策(MAX_DURATION_SECONDS)のどちらかで区間を終える
                duration_exceeded = clip_recorder.add_frame(frame)
                if pending_clip_match_id is not None and (duration_exceeded or is_full_blackout(frame)):
                    clip_recorder.finish(pending_clip_match_id)
                    pending_clip_match_id = None
                elif duration_exceeded:
                    # match_id判明前に上限に達した(通常は起こらないはずの異常系)。
                    # クリップを生成せず録画をリセットして無限に溜め込まないようにする
                    logger.warning("動画クリップの録画がmatch_id判明前に上限時間へ達したため、リセットします")
                    clip_recorder.start(fps)
    except KeyboardInterrupt:
        total = sum(session_results.values())
        logger.info(
            "Ctrl+Cを受け取ったため終了します(このセッションで記録した試合数: %d件 win=%d lose=%d draw=%d)",
            total,
            session_results["win"],
            session_results["lose"],
            session_results["draw"],
        )
    finally:
        reader.stop()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--video",
        type=Path,
        default=None,
        help="OBS Virtual Cameraの代わりに読み込む動画ファイル(配線確認用。未指定時は実キャプチャ)",
    )
    parser.add_argument(
        "--fps",
        type=float,
        default=None,
        help="キャプチャのfps(状態機械の閾値スケーリングに使用)。"
        "--video指定時は未指定ならファイルから自動検出、実キャプチャ時は未指定なら.envのCAPTURE_FPSを使用",
    )
    args = parser.parse_args()

    try:
        log_file = _setup_logging()
    except ConfigError as exc:
        # ロガーが未設定のため標準エラー出力に直接書く
        print(f"設定エラー: {exc}", file=sys.stderr)
        sys.exit(1)
    logger.info("ログファイル: %s", log_file)

    try:
        reader = _make_reader(args.video)
        fps = args.fps
        if fps is None:
            # Issue #255: 実キャプチャ時は.envのCAPTURE_FPSを使う(OBS Virtual Cameraの
            # 実際の出力fpsは環境ごとに異なりうるため、他の実キャプチャ設定と同様に
            # 未設定ならConfigErrorで明示的に失敗させる)
            fps = _detect_fps(args.video) if args.video is not None else get_capture_fps()
        db_path = get_db_path()
        web_host = get_web_host()
        web_port = get_web_port()
        obs_host = get_obs_websocket_host()
        obs_port = get_obs_websocket_port()
        obs_password = get_obs_websocket_password()
        obs_scene_in_match = get_obs_scene_in_match()
        obs_scene_between_matches = get_obs_scene_between_matches()
        obs_browser_source_names = get_obs_browser_source_names()
        get_goal_record_mode()  # 値自体はdb.py/match_state.pyが都度参照するため、ここでは早期に検証するだけ
        get_rank_graph_match_limit()  # 値自体はweb/server.pyが都度参照するため、ここでは早期に検証するだけ
        get_rank_delta_distribution_scope()  # 値自体はweb/server.pyが都度参照するため、ここでは早期に検証するだけ
        get_obs_scene_switching_enabled()  # 値自体はobs_control.pyが都度参照するため、ここでは早期に検証するだけ
        youtube_chat_dive_time_enabled = get_youtube_chat_dive_time_enabled()
    except ConfigError as exc:
        logger.error("設定エラー: %s", exc)
        sys.exit(1)
    logger.info("fps=%.2fとして状態機械の閾値をスケーリングします", fps)
    if args.video is None:
        logger.info(
            "キャプチャ設定: device=%s resolution=%dx%d",
            get_capture_device_name(),
            *get_capture_resolution(),
        )
    else:
        logger.info("動画ファイルを入力として使用します: %s", args.video)
    machine = _make_match_state_machine(fps)
    conn = db.connect(db_path)
    session_id = db.create_session(conn)
    logger.info("配信セッションを開始しました: session_id=%d", session_id)
    web_handle = start_web_server_thread(create_app(db_path), host=web_host, port=web_port)
    logger.info("Webダッシュボードを起動しました: http://%s:%d/", web_host, web_port)
    admin_url = f"http://{web_host}:{web_port}/admin"
    # Issue #129: 配信中に調整したくなり得る設定項目(ALLOWED_PLAYERS等)をGUIで
    # 編集できる管理画面を起動時に自動的に開く。失敗してもアプリ全体を止める
    # 理由にはならない(ブラウザが無い環境等)ため、WARNINGログのみで継続する
    try:
        webbrowser.open(admin_url)
    except Exception:
        logger.warning("設定画面を自動的に開けませんでした。手動で開いてください: %s", admin_url)
    # Issue #306: ランク確定値(rank_after)の手動入力ページも、/adminと同様に
    # 起動時に自動的に別ブラウザで開く(いずれ同一画面に統合する可能性はあるが、
    # 現段階では別ページのまま、ユーザー確認済み)
    rank_entry_url = f"http://{web_host}:{web_port}/rank-entry"
    try:
        webbrowser.open(rank_entry_url)
    except Exception:
        logger.warning("ランク手動入力画面を自動的に開けませんでした。手動で開いてください: %s", rank_entry_url)
    obs_controller = ObsSceneController(
        host=obs_host,
        port=obs_port,
        password=obs_password,
        scene_in_match=obs_scene_in_match,
        scene_between_matches=obs_scene_between_matches,
        browser_source_names=obs_browser_source_names,
    )
    dive_time_watcher: Optional[DiveTimeWatcher] = None
    if youtube_chat_dive_time_enabled:
        dive_time_watcher = DiveTimeWatcher()
        dive_time_watcher.start()
    clip_recorder = RankEntryClipRecorder(output_dir=DEFAULT_CLIPS_DIR)
    try:
        run(reader, machine, conn, session_id, obs_controller, fps, clip_recorder)
    finally:
        if dive_time_watcher is not None:
            dive_time_watcher.stop()
        obs_controller.close()
        web_handle.stop()
        db.end_session(conn, session_id)
        conn.close()


if __name__ == "__main__":
    main()
