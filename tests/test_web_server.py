from pathlib import Path

import httpx
from fastapi.testclient import TestClient

from nss_tracker.database import db
from nss_tracker.state.match_state import MatchResult
from nss_tracker.timeutil import now_jst
from nss_tracker.web.runner import start_web_server_thread
from nss_tracker.web.server import create_app


def test_health_endpoint(tmp_path: Path):
    app = create_app(tmp_path / "test.db")
    client = TestClient(app)

    response = client.get("/api/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_matches_count_reflects_db_contents(tmp_path: Path):
    db_path = tmp_path / "test.db"
    conn = db.connect(db_path)
    for result in ["win", "win", "lose", "draw"]:
        db.save_match_result(
            conn,
            MatchResult(result=result, rank_before=1, rank_after=1, league_changed=None, detected_at=now_jst()),
        )
    conn.close()

    client = TestClient(create_app(db_path))

    response = client.get("/api/matches/count")

    assert response.status_code == 200
    assert response.json() == {"total": 4, "win": 2, "lose": 1, "draw": 1}


def test_matches_count_with_no_matches(tmp_path: Path):
    db_path = tmp_path / "test.db"
    db.connect(db_path).close()

    client = TestClient(create_app(db_path))

    response = client.get("/api/matches/count")

    assert response.json() == {"total": 0, "win": 0, "lose": 0, "draw": 0}


def test_index_page_shows_match_counts(tmp_path: Path):
    db_path = tmp_path / "test.db"
    conn = db.connect(db_path)
    for result in ["win", "win", "lose"]:
        db.save_match_result(
            conn,
            MatchResult(result=result, rank_before=1, rank_after=1, league_changed=None, detected_at=now_jst()),
        )
    conn.close()

    client = TestClient(create_app(db_path))

    response = client.get("/")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert "3" in response.text
    assert "win: 2" in response.text
    assert "lose: 1" in response.text
    assert "draw: 0" in response.text


def test_start_web_server_thread_serves_requests_and_stops_cleanly(tmp_path: Path):
    """Issue #80のPoC: 別スレッドで起動したuvicornが実際にHTTPリクエストに
    応答し、stop()でスレッドごと正常終了できることを確認する
    (検知ループと同一プロセス内で共存できるかの技術検証)。
    """
    db_path = tmp_path / "test.db"
    db.connect(db_path).close()

    handle = start_web_server_thread(create_app(db_path), host="127.0.0.1", port=8766)
    try:
        response = httpx.get("http://127.0.0.1:8766/api/health", timeout=2.0)
        assert response.status_code == 200
        assert response.json() == {"status": "ok"}
    finally:
        handle.stop()

    assert not handle.thread.is_alive()
