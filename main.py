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
import os
import sqlite3
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

import cv2

from nss_tracker.capture.ffmpeg_capture import FfmpegFrameReader
from nss_tracker.config import (
    ConfigError,
    get_capture_device_name,
    get_capture_resolution,
    get_db_path,
    get_frame_read_timeout_seconds,
    get_web_host,
    get_web_port,
)
from nss_tracker.database import db
from nss_tracker.detection.goal import _get_name_reader
from nss_tracker.detection.motion import StabilityMonitor
from nss_tracker.detection.rank_ocr import RANK_ROI, _get_reader
from nss_tracker.detection.vs_rank import _get_reader as _get_vs_rank_reader
from nss_tracker.state.match_state import MatchResult, MatchStateMachine
from nss_tracker.timeutil import JST
from nss_tracker.web.runner import start_web_server_thread
from nss_tracker.web.server import create_app

LOG_DIR = Path("logs")
# OBS Virtual Cameraのキャプチャは30fps想定(CLAUDE.md)。--videoでの動作確認時は
# 実ファイルのfpsを自動検出するため、これは実キャプチャ時のみ使うデフォルト値
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
    level_name = os.environ.get("NSS_TRACKER_LOG_LEVEL", "INFO").upper()
    level = getattr(logging, level_name, logging.INFO)

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
    """
    confirm_frames = round(fps * 1.0)
    # Issue #67: 通常プレイ中の背景誤検知(実測1.3秒程度持続)がデバウンス(1秒)を
    # すり抜けて結果バナーの誤検知が発生したため、banner_confirm_framesのみ2秒に延長。
    # Issue #76: 「試合終了」バナーを確認できていれば、Issue #67修正前と同じ1秒
    # (confirm_framesと同じ)に短縮する(state/match_state.pyのモジュールdocstring参照)
    banner_confirm_frames = round(fps * 2.0)
    # 「試合終了」バナーは実測最短7フレーム(60fps)程度しか綺麗に表示されないことが
    # あるため、他のconfirm系より短いデバウンスにする(state/match_state.py参照)
    match_end_confirm_frames = round(fps * 0.1)
    return MatchStateMachine(
        banner_confirm_frames=banner_confirm_frames,
        banner_confirm_frames_after_match_end=confirm_frames,
        banner_absence_confirm_frames=confirm_frames,
        goal_confirm_frames=confirm_frames,
        vs_screen_confirm_frames=confirm_frames,
        match_end_confirm_frames=match_end_confirm_frames,
        league_change_grace_frames=round(fps * 5.0),
        rank_recheck_interval_frames=round(fps * 0.25),
        rank_stability_monitor=StabilityMonitor(roi=RANK_ROI, stable_frames_required=round(fps * 0.5)),
    )


def _record_match_result(conn: sqlite3.Connection, result: MatchResult) -> None:
    match_id = db.save_match_result(conn, result)
    logger.info(
        "試合結果を記録しました: id=%d result=%s rank=%s->%s league_changed=%s goals=%d",
        match_id,
        result.result,
        result.rank_before,
        result.rank_after,
        result.league_changed,
        len(result.goals),
    )
    for goal in result.goals:
        goal_id = db.save_goal(conn, match_id, goal.scorer_name, goal.assist_name, goal.detected_at)
        if goal_id is not None:
            logger.info(
                "ゴールを記録しました: match_id=%d scorer=%s assist=%s", match_id, goal.scorer_name, goal.assist_name
            )

    if result.vs_mine_ranks or result.vs_opponent_ranks:
        db.save_vs_slot_ranks(conn, match_id, result.vs_mine_ranks, result.vs_opponent_ranks)
        logger.info(
            "VS画面のランクを記録しました: match_id=%d mine=%s opponent=%s",
            match_id,
            result.vs_mine_ranks,
            result.vs_opponent_ranks,
        )
    else:
        logger.info("VS画面を検知できなかったため、VSスロットランクは記録しません: match_id=%d", match_id)


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


def run(reader: FfmpegFrameReader, machine: MatchStateMachine, conn: sqlite3.Connection) -> None:
    prev_state = machine.current_state
    frame_read_timeout_seconds = get_frame_read_timeout_seconds()
    # Issue #71: Ctrl+C受信時にセッションサマリを出すための内訳カウンタ
    session_results = {"win": 0, "lose": 0, "draw": 0}

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

            if machine.current_state != prev_state:
                logger.info("状態遷移: %s -> %s", prev_state, machine.current_state)
                prev_state = machine.current_state

            if result is not None:
                _record_match_result(conn, result)
                session_results[result.result] += 1
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
        "--video指定時は未指定ならファイルから自動検出、実キャプチャ時は未指定なら30fps想定",
    )
    args = parser.parse_args()

    log_file = _setup_logging()
    logger.info("ログファイル: %s", log_file)
    fps = args.fps
    if fps is None:
        fps = _detect_fps(args.video) if args.video is not None else DEFAULT_CAPTURE_FPS
    logger.info("fps=%.2fとして状態機械の閾値をスケーリングします", fps)

    try:
        reader = _make_reader(args.video)
    except ConfigError as exc:
        logger.error("設定エラー: %s", exc)
        sys.exit(1)
    if args.video is None:
        logger.info(
            "キャプチャ設定: device=%s resolution=%dx%d",
            get_capture_device_name(),
            *get_capture_resolution(),
        )
    else:
        logger.info("動画ファイルを入力として使用します: %s", args.video)
    machine = _make_match_state_machine(fps)
    db_path = get_db_path()
    conn = db.connect(db_path)
    web_host = get_web_host()
    web_port = get_web_port()
    web_handle = start_web_server_thread(create_app(db_path), host=web_host, port=web_port)
    logger.info("Webダッシュボードを起動しました: http://%s:%d/", web_host, web_port)
    try:
        run(reader, machine, conn)
    finally:
        web_handle.stop()
        conn.close()


if __name__ == "__main__":
    main()
