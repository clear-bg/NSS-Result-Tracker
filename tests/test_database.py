from datetime import datetime, timezone

from nss_tracker.database.db import connect, fetch_all_goals, fetch_all_matches, save_goal, save_match_result
from nss_tracker.state.match_state import MatchResult


def test_connect_creates_matches_table():
    conn = connect(":memory:")
    tables = conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    table_names = {row["name"] for row in tables}
    assert "matches" in table_names
    assert "goals" in table_names


def test_save_and_fetch_match_result():
    conn = connect(":memory:")
    match = MatchResult(
        result="win",
        rank_before=39,
        rank_after=40,
        league_changed="up",
        detected_at=datetime(2026, 7, 16, 12, 0, 0, tzinfo=timezone.utc),
    )

    inserted_id = save_match_result(conn, match)
    assert inserted_id == 1

    rows = fetch_all_matches(conn)
    assert len(rows) == 1
    row = rows[0]
    assert row["id"] == 1
    assert row["detected_at"] == "2026-07-16T12:00:00+00:00"
    assert row["result"] == "win"
    assert row["rank_before"] == 39.0
    assert row["rank_after"] == 40.0
    assert row["league_changed"] == "up"
    assert row["created_at"] is not None
    assert row["updated_at"] is not None
    assert row["created_at"] == row["updated_at"]


def test_save_match_result_with_none_fields():
    conn = connect(":memory:")
    match = MatchResult(
        result="lose",
        rank_before=None,
        rank_after=None,
        league_changed=None,
        detected_at=datetime(2026, 7, 16, 12, 30, 0, tzinfo=timezone.utc),
    )

    save_match_result(conn, match)

    row = fetch_all_matches(conn)[0]
    assert row["rank_before"] is None
    assert row["rank_after"] is None
    assert row["league_changed"] is None


def test_save_match_result_draw():
    conn = connect(":memory:")
    match = MatchResult(
        result="draw",
        rank_before=39,
        rank_after=39,
        league_changed=None,
        detected_at=datetime.now(timezone.utc),
    )

    save_match_result(conn, match)

    row = fetch_all_matches(conn)[0]
    assert row["result"] == "draw"


def test_fetch_all_matches_orders_by_id():
    conn = connect(":memory:")
    for result in ["win", "lose", "win"]:
        save_match_result(
            conn,
            MatchResult(
                result=result,
                rank_before=1,
                rank_after=1,
                league_changed=None,
                detected_at=datetime.now(timezone.utc),
            ),
        )

    rows = fetch_all_matches(conn)
    assert [row["id"] for row in rows] == [1, 2, 3]
    assert [row["result"] for row in rows] == ["win", "lose", "win"]


def test_save_goal_for_allowed_player(monkeypatch):
    monkeypatch.setenv("ALLOWED_PLAYERS", "Alice")
    conn = connect(":memory:")
    match_id = save_match_result(
        conn,
        MatchResult(
            result="win",
            rank_before=1,
            rank_after=1,
            league_changed=None,
            detected_at=datetime.now(timezone.utc),
        ),
    )

    goal_id = save_goal(
        conn,
        match_id=match_id,
        scorer_name="Alice",
        assist_name="Bob",
        detected_at=datetime(2026, 7, 16, 12, 5, 0, tzinfo=timezone.utc),
    )

    assert goal_id == 1
    rows = fetch_all_goals(conn)
    assert len(rows) == 1
    row = rows[0]
    assert row["match_id"] == match_id
    assert row["scorer_name"] == "Alice"
    assert row["assist_name"] == "Bob"
    assert row["detected_at"] == "2026-07-16T12:05:00+00:00"
    assert row["created_at"] is not None
    assert row["updated_at"] is not None


def test_save_goal_saves_when_scorer_disallowed_but_assist_allowed(monkeypatch):
    monkeypatch.setenv("ALLOWED_PLAYERS", "Alice")
    conn = connect(":memory:")
    match_id = save_match_result(
        conn,
        MatchResult(
            result="win",
            rank_before=1,
            rank_after=1,
            league_changed=None,
            detected_at=datetime.now(timezone.utc),
        ),
    )

    goal_id = save_goal(
        conn,
        match_id=match_id,
        scorer_name="Stranger",
        assist_name="Alice",
        detected_at=datetime.now(timezone.utc),
    )

    assert goal_id is not None
    row = fetch_all_goals(conn)[0]
    assert row["scorer_name"] == "Stranger"
    assert row["assist_name"] == "Alice"


def test_save_goal_skips_when_neither_scorer_nor_assist_allowed(monkeypatch):
    monkeypatch.setenv("ALLOWED_PLAYERS", "Alice")
    conn = connect(":memory:")
    match_id = save_match_result(
        conn,
        MatchResult(
            result="win",
            rank_before=1,
            rank_after=1,
            league_changed=None,
            detected_at=datetime.now(timezone.utc),
        ),
    )

    goal_id = save_goal(
        conn,
        match_id=match_id,
        scorer_name="Stranger",
        assist_name="OtherStranger",
        detected_at=datetime.now(timezone.utc),
    )

    assert goal_id is None
    assert fetch_all_goals(conn) == []


def test_save_goal_skips_when_scorer_disallowed_and_assist_none(monkeypatch):
    monkeypatch.setenv("ALLOWED_PLAYERS", "Alice")
    conn = connect(":memory:")
    match_id = save_match_result(
        conn,
        MatchResult(
            result="win",
            rank_before=1,
            rank_after=1,
            league_changed=None,
            detected_at=datetime.now(timezone.utc),
        ),
    )

    goal_id = save_goal(
        conn,
        match_id=match_id,
        scorer_name="Stranger",
        assist_name=None,
        detected_at=datetime.now(timezone.utc),
    )

    assert goal_id is None
    assert fetch_all_goals(conn) == []


def test_save_goal_keeps_assist_name_when_assist_not_allowed(monkeypatch):
    monkeypatch.setenv("ALLOWED_PLAYERS", "Alice")
    conn = connect(":memory:")
    match_id = save_match_result(
        conn,
        MatchResult(
            result="win",
            rank_before=1,
            rank_after=1,
            league_changed=None,
            detected_at=datetime.now(timezone.utc),
        ),
    )

    save_goal(
        conn,
        match_id=match_id,
        scorer_name="Alice",
        assist_name="Stranger",
        detected_at=datetime.now(timezone.utc),
    )

    row = fetch_all_goals(conn)[0]
    assert row["scorer_name"] == "Alice"
    assert row["assist_name"] == "Stranger"


def test_save_goal_without_scorer_name_is_skipped(monkeypatch):
    monkeypatch.setenv("ALLOWED_PLAYERS", "Alice")
    conn = connect(":memory:")
    match_id = save_match_result(
        conn,
        MatchResult(
            result="win",
            rank_before=1,
            rank_after=1,
            league_changed=None,
            detected_at=datetime.now(timezone.utc),
        ),
    )

    goal_id = save_goal(conn, match_id=match_id, scorer_name=None, assist_name=None, detected_at=datetime.now(timezone.utc))

    assert goal_id is None
    assert fetch_all_goals(conn) == []


def test_save_goal_without_scorer_name_is_skipped_even_when_assist_allowed(monkeypatch):
    monkeypatch.setenv("ALLOWED_PLAYERS", "Alice")
    conn = connect(":memory:")
    match_id = save_match_result(
        conn,
        MatchResult(
            result="win",
            rank_before=1,
            rank_after=1,
            league_changed=None,
            detected_at=datetime.now(timezone.utc),
        ),
    )

    goal_id = save_goal(
        conn, match_id=match_id, scorer_name=None, assist_name="Alice", detected_at=datetime.now(timezone.utc)
    )

    assert goal_id is None
    assert fetch_all_goals(conn) == []
