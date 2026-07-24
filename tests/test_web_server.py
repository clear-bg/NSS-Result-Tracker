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


def test_winrate_with_no_sessions_returns_empty_session_counts(tmp_path: Path):
    db_path = tmp_path / "test.db"
    conn = db.connect(db_path)
    db.save_match_result(
        conn,
        MatchResult(result="win", rank_before=1, rank_after=1, league_changed=None, detected_at=now_jst()),
    )
    conn.close()

    client = TestClient(create_app(db_path))

    response = client.get("/api/winrate")

    assert response.status_code == 200
    assert response.json() == {
        "session": {"total": 0, "win": 0, "lose": 0, "draw": 0},
        "cumulative": {"total": 1, "win": 1, "lose": 0, "draw": 0},
    }


def test_winrate_splits_session_and_cumulative_counts(tmp_path: Path):
    db_path = tmp_path / "test.db"
    conn = db.connect(db_path)
    first_session_id = db.create_session(conn)
    db.save_match_result(
        conn,
        MatchResult(result="lose", rank_before=1, rank_after=1, league_changed=None, detected_at=now_jst()),
        session_id=first_session_id,
    )
    second_session_id = db.create_session(conn)
    for result in ["win", "win"]:
        db.save_match_result(
            conn,
            MatchResult(result=result, rank_before=1, rank_after=1, league_changed=None, detected_at=now_jst()),
            session_id=second_session_id,
        )
    conn.close()

    client = TestClient(create_app(db_path))

    response = client.get("/api/winrate")

    assert response.status_code == 200
    assert response.json() == {
        "session": {"total": 2, "win": 2, "lose": 0, "draw": 0},
        "cumulative": {"total": 3, "win": 2, "lose": 1, "draw": 0},
    }


def test_overlay_winrate_page_shows_readable_summary(tmp_path: Path):
    db_path = tmp_path / "test.db"
    conn = db.connect(db_path)
    session_id = db.create_session(conn)
    for result in ["win", "win", "lose"]:
        db.save_match_result(
            conn,
            MatchResult(result=result, rank_before=1, rank_after=1, league_changed=None, detected_at=now_jst()),
            session_id=session_id,
        )
    conn.close()

    client = TestClient(create_app(db_path))

    response = client.get("/overlay/winrate")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert "配信セッション: 3試合" in response.text
    assert "win 2 / lose 1 / draw 0" in response.text
    assert "勝率 66.7%" in response.text
    assert "累計: 3試合" in response.text


def test_overlay_winrate_page_links_transparent_background_stylesheet(tmp_path: Path):
    """OBSのブラウザソースに重ねて配置する想定のため、文字の無い部分が
    背後の他の部品を隠さないよう背景を明示的に透過にしていることを確認する。
    """
    db_path = tmp_path / "test.db"
    db.connect(db_path).close()

    client = TestClient(create_app(db_path))

    page_response = client.get("/overlay/winrate")
    assert '<link rel="stylesheet" href="/static/overlay.css">' in page_response.text

    css_response = client.get("/static/overlay.css")
    assert css_response.status_code == 200
    assert "background: transparent" in css_response.text


def test_overlay_winrate_page_shows_dash_when_no_matches(tmp_path: Path):
    db_path = tmp_path / "test.db"
    db.connect(db_path).close()

    client = TestClient(create_app(db_path))

    response = client.get("/overlay/winrate")

    assert response.status_code == 200
    assert "勝率 -" in response.text


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
