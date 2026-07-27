"""main.pyの配線(capture -> state -> database)がつながっていることを確認する統合テスト。

detection/の各判定ロジック自体の精度はtest_banner.py・test_rank_ocr.py等で
既に検証済みのため、ここでは「実際にrun()を実行したときにDBへ1件正しく
記録されるか」という配線そのものだけを見る。動画ファイルは-re(実時間速度)で
読み込むため、動画の長さ分だけ実行に時間がかかる(このテストが遅い理由)。
"""

import sqlite3
import sys
import webbrowser
from datetime import datetime

import pytest

from conftest import requires_video_fixtures
from nss_tracker.database import db
from nss_tracker.timeutil import JST

import main

VIDEO_NAME = "29_lose_blue_hdr_off.mp4"


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
    monkeypatch.setattr(main, "run", lambda reader, machine, conn, session_id, obs_controller: None)

    db_path = tmp_path / "test.db"
    monkeypatch.setenv("DB_PATH", str(db_path))
    monkeypatch.setenv("WEB_HOST", "127.0.0.1")
    monkeypatch.setenv("WEB_PORT", "8768")
    monkeypatch.setenv("NSS_TRACKER_LOG_LEVEL", "INFO")
    monkeypatch.setenv("GOAL_RECORD_MODE", "allowlist")
    monkeypatch.setenv("RANK_DELTA_DISTRIBUTION_SCOPE", "session")
    monkeypatch.setenv("RANK_GRAPH_MATCH_LIMIT", "all")
    # OBS未起動でもmain()が異常終了しないことを兼ねて確認する(Issue #83、接続失敗はWARNINGログのみ)
    monkeypatch.setenv("OBS_WEBSOCKET_HOST", "127.0.0.1")
    monkeypatch.setenv("OBS_WEBSOCKET_PORT", "48765")
    monkeypatch.setenv("OBS_WEBSOCKET_PASSWORD", "none")
    monkeypatch.setenv("OBS_SCENE_IN_MATCH", "InMatch")
    monkeypatch.setenv("OBS_SCENE_BETWEEN_MATCHES", "BetweenMatches")
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
    opened_urls = []
    monkeypatch.setattr(main.webbrowser, "open", lambda url: opened_urls.append(url))

    main.main()

    assert captured["host"] == "127.0.0.1"
    assert captured["port"] == 8768
    assert not captured["handle"].thread.is_alive()
    assert db_path.exists()
    # Issue #129: 設定画面(/admin)を起動時に自動的に開くことを確認する
    assert opened_urls == ["http://127.0.0.1:8768/admin"]


def test_main_continues_when_browser_cannot_be_opened(monkeypatch, tmp_path):
    """Issue #129: ブラウザが無い環境等で設定画面の自動起動に失敗しても、アプリ全体は止めない。"""
    monkeypatch.setattr(main, "LOG_DIR", tmp_path / "logs")
    monkeypatch.setattr(main, "run", lambda reader, machine, conn, session_id, obs_controller: None)

    db_path = tmp_path / "test.db"
    monkeypatch.setenv("DB_PATH", str(db_path))
    monkeypatch.setenv("WEB_HOST", "127.0.0.1")
    monkeypatch.setenv("WEB_PORT", "8769")
    monkeypatch.setenv("NSS_TRACKER_LOG_LEVEL", "INFO")
    monkeypatch.setenv("GOAL_RECORD_MODE", "allowlist")
    monkeypatch.setenv("RANK_DELTA_DISTRIBUTION_SCOPE", "session")
    monkeypatch.setenv("RANK_GRAPH_MATCH_LIMIT", "all")
    monkeypatch.setenv("OBS_WEBSOCKET_HOST", "127.0.0.1")
    monkeypatch.setenv("OBS_WEBSOCKET_PORT", "48766")
    monkeypatch.setenv("OBS_WEBSOCKET_PASSWORD", "none")
    monkeypatch.setenv("OBS_SCENE_IN_MATCH", "InMatch")
    monkeypatch.setenv("OBS_SCENE_BETWEEN_MATCHES", "BetweenMatches")
    monkeypatch.setattr(sys, "argv", ["main.py", "--video", "dummy.mp4"])

    def raise_error(url):
        raise webbrowser.Error("no browser")

    monkeypatch.setattr(main.webbrowser, "open", raise_error)

    main.main()  # 例外を送出せず正常終了することを確認する

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

        class _NoOpObsController:
            def set_in_match(self, in_match: bool) -> None:
                pass

        main.run(reader, machine, conn, session_id, _NoOpObsController())

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
