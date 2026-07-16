"""main.pyの配線(capture -> state -> database)がつながっていることを確認する統合テスト。

detection/の各判定ロジック自体の精度はtest_banner.py・test_rank_ocr.py等で
既に検証済みのため、ここでは「実際にrun()を実行したときにDBへ1件正しく
記録されるか」という配線そのものだけを見る。動画ファイルは-re(実時間速度)で
読み込むため、動画の長さ分だけ実行に時間がかかる(このテストが遅い理由)。
"""

import sqlite3

import pytest

from conftest import requires_video_fixtures
from nss_tracker.database import db

import main

VIDEO_NAME = "02_lose_red_1-2.mp4"


@pytest.mark.slow
@requires_video_fixtures
def test_run_wires_capture_state_and_database(videos_dir, monkeypatch):
    # 得点者名は実名を含みうるため、許可リストを空にしてgoalsへの記録内容を
    # テストの関心から外す(配線確認のみが目的。名前は検証しない)
    monkeypatch.setenv("ALLOWED_PLAYERS", "")

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

        main.run(reader, machine, conn)

        rows = db.fetch_all_matches(conn)
        assert len(rows) == 1, f"記録された試合数が{len(rows)}件(期待は1件)"
        row = rows[0]
        assert row["result"] == "lose"
        assert row["rank_before"] is not None
        assert row["rank_after"] is not None
        assert row["created_at"] is not None
        assert row["updated_at"] is not None
    finally:
        conn.close()
