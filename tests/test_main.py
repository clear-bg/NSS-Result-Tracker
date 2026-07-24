"""main.pyの配線(capture -> state -> database)がつながっていることを確認する統合テスト。

detection/の各判定ロジック自体の精度はtest_banner.py・test_rank_ocr.py等で
既に検証済みのため、ここでは「実際にrun()を実行したときにDBへ1件正しく
記録されるか」という配線そのものだけを見る。動画ファイルは-re(実時間速度)で
読み込むため、動画の長さ分だけ実行に時間がかかる(このテストが遅い理由)。
"""

import sqlite3
import sys
from datetime import datetime

import pytest

from conftest import requires_video_fixtures
from nss_tracker.database import db
from nss_tracker.timeutil import JST

import main

VIDEO_NAME = "02_lose_red_1-2.mp4"


def test_generate_log_file_path_uses_jst_timestamp():
    """Issue #71: ログファイルをセッション(起動時刻)ごとに分けるため、
    ファイル名にJSTの起動時刻を埋め込むことを確認する。
    """
    now = datetime(2026, 7, 20, 21, 5, 9, tzinfo=JST)

    path = main._generate_log_file_path(now)

    assert path == main.LOG_DIR / "tracker_20260720_210509.log"


def test_make_reader_without_video_uses_capture_env_config(monkeypatch):
    monkeypatch.setenv("CAPTURE_DEVICE_NAME", "Custom Capture Device")
    monkeypatch.setenv("CAPTURE_WIDTH", "1280")
    monkeypatch.setenv("CAPTURE_HEIGHT", "720")

    reader = main._make_reader(None)

    assert reader._width == 1280
    assert reader._height == 720
    assert reader._input_args == ["-f", "dshow", "-video_size", "1280x720", "-i", "video=Custom Capture Device"]


def test_main_starts_and_stops_web_server(monkeypatch, tmp_path):
    """Issue #81: main()がWebサーバーを起動し、終了時にstop()まで呼ぶことを確認する。

    検知ループ本体(run())やOCR初期化は重いため差し替え、main()の配線
    (get_db_path/get_web_host/get_web_portで得た値でstart_web_server_threadを
    呼び、finallyでweb_handle.stop()を呼ぶこと)だけを軽量に検証する。
    """
    monkeypatch.setattr(main, "LOG_DIR", tmp_path / "logs")
    monkeypatch.setattr(main, "run", lambda reader, machine, conn, session_id: None)

    db_path = tmp_path / "test.db"
    monkeypatch.setenv("DB_PATH", str(db_path))
    monkeypatch.setenv("WEB_HOST", "127.0.0.1")
    monkeypatch.setenv("WEB_PORT", "8768")
    monkeypatch.setenv("NSS_TRACKER_LOG_LEVEL", "INFO")
    monkeypatch.setenv("GOAL_RECORD_MODE", "allowlist")
    monkeypatch.setenv("RANK_DELTA_DISTRIBUTION_SCOPE", "session")
    monkeypatch.setattr(sys, "argv", ["main.py", "--video", "dummy.mp4"])

    original_start = main.start_web_server_thread
    captured = {}

    def spy_start(app, host, port):
        handle = original_start(app, host=host, port=port)
        captured["handle"] = handle
        captured["host"] = host
        captured["port"] = port
        return handle

    monkeypatch.setattr(main, "start_web_server_thread", spy_start)

    main.main()

    assert captured["host"] == "127.0.0.1"
    assert captured["port"] == 8768
    assert not captured["handle"].thread.is_alive()
    assert db_path.exists()


@pytest.mark.slow
@requires_video_fixtures
def test_run_wires_capture_state_and_database(videos_dir, monkeypatch):
    # 得点者名は実名を含みうるため、許可リストを空にしてgoalsへの記録内容を
    # テストの関心から外す(配線確認のみが目的。名前は検証しない)
    monkeypatch.setenv("ALLOWED_PLAYERS", "")
    monkeypatch.setenv("GOAL_RECORD_MODE", "allowlist")
    monkeypatch.setenv("FRAME_READ_TIMEOUT_SECONDS", "5.0")

    video_path = videos_dir / VIDEO_NAME
    assert video_path.is_file(), f"{VIDEO_NAME}がfixtures/videos/に見つからない"

    fps = main._detect_fps(video_path)
    reader = main._make_reader(video_path)
    machine = main._make_match_state_machine(fps)
    conn = sqlite3.connect(":memory:")
    try:
        conn.row_factory = sqlite3.Row
        conn.executescript(db._SCHEMA)
        conn.commit()
        session_id = db.create_session(conn)

        main.run(reader, machine, conn, session_id)

        rows = db.fetch_all_matches(conn)
        assert len(rows) == 1, f"記録された試合数が{len(rows)}件(期待は1件)"
        row = rows[0]
        assert row["result"] == "lose"
        assert row["rank_before"] is not None
        assert row["rank_after"] is not None
        assert row["created_at"] is not None
        assert row["updated_at"] is not None
        assert row["session_id"] == session_id
    finally:
        conn.close()
