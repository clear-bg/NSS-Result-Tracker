import sqlite3
from datetime import datetime, timezone

from nss_tracker.database.db import (
    connect,
    create_session,
    end_session,
    fetch_all_goals,
    fetch_all_matches,
    fetch_current_session_id,
    fetch_goals_for_session,
    fetch_matches_for_session,
    fetch_recent_matches,
    fetch_vs_slot_ranks,
    save_goal,
    save_match_result,
    save_vs_slot_ranks,
)
from nss_tracker.detection.vs_rank import SlotRank
from nss_tracker.state.match_state import MatchResult


def test_connect_creates_matches_table():
    conn = connect(":memory:")
    tables = conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    table_names = {row["name"] for row in tables}
    assert "matches" in table_names
    assert "goals" in table_names
    assert "vs_slot_ranks" in table_names
    assert "sessions" in table_names


def test_create_session_inserts_row_with_null_ended_at():
    conn = connect(":memory:")

    session_id = create_session(conn)

    row = conn.execute("SELECT * FROM sessions WHERE id = ?", (session_id,)).fetchone()
    assert row["started_at"] is not None
    assert row["ended_at"] is None
    assert row["created_at"] == row["updated_at"]


def test_end_session_sets_ended_at():
    conn = connect(":memory:")
    session_id = create_session(conn)

    end_session(conn, session_id)

    row = conn.execute("SELECT * FROM sessions WHERE id = ?", (session_id,)).fetchone()
    assert row["ended_at"] is not None


def test_fetch_current_session_id_returns_latest_session():
    conn = connect(":memory:")
    create_session(conn)
    latest_id = create_session(conn)

    assert fetch_current_session_id(conn) == latest_id


def test_fetch_current_session_id_returns_none_when_no_sessions():
    conn = connect(":memory:")

    assert fetch_current_session_id(conn) is None


def test_save_match_result_stores_session_id():
    conn = connect(":memory:")
    session_id = create_session(conn)
    match = MatchResult(
        result="win",
        rank_before=1,
        rank_after=2,
        league_changed=None,
        detected_at=datetime.now(timezone.utc),
    )

    match_id = save_match_result(conn, match, session_id=session_id)

    row = conn.execute("SELECT * FROM matches WHERE id = ?", (match_id,)).fetchone()
    assert row["session_id"] == session_id


def test_save_match_result_without_session_id_leaves_it_null():
    conn = connect(":memory:")
    match = MatchResult(
        result="win",
        rank_before=1,
        rank_after=2,
        league_changed=None,
        detected_at=datetime.now(timezone.utc),
    )

    match_id = save_match_result(conn, match)

    row = conn.execute("SELECT * FROM matches WHERE id = ?", (match_id,)).fetchone()
    assert row["session_id"] is None


def test_connect_migrates_legacy_matches_without_session_id(tmp_path):
    """Issue #93: session_id列が無い移行前のDBファイルに対しても、connect()を
    呼ぶだけで列が追加され、既存データを保持したまま新しいmatchesを
    session_id付きで挿入できるようになることを確認する。
    """
    db_path = tmp_path / "legacy.db"
    legacy_conn = sqlite3.connect(db_path)
    legacy_conn.executescript(
        """
        CREATE TABLE matches (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            detected_at TEXT NOT NULL,
            result TEXT NOT NULL,
            rank_before REAL,
            rank_after REAL,
            league_changed TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        INSERT INTO matches (detected_at, result, created_at, updated_at)
            VALUES ('2026-07-01T00:00:00+09:00', 'win', '2026-07-01T00:00:00+09:00', '2026-07-01T00:00:00+09:00');
        """
    )
    legacy_conn.commit()
    legacy_conn.close()

    conn = connect(db_path)

    columns = conn.execute("PRAGMA table_info(matches)").fetchall()
    session_id_column = next(c for c in columns if c["name"] == "session_id")
    assert session_id_column["notnull"] == 0

    rows = fetch_all_matches(conn)
    assert len(rows) == 1
    assert rows[0]["session_id"] is None

    session_id = create_session(conn)
    match = MatchResult(
        result="lose",
        rank_before=5,
        rank_after=4,
        league_changed=None,
        detected_at=datetime.now(timezone.utc),
    )
    save_match_result(conn, match, session_id=session_id)

    rows = fetch_all_matches(conn)
    assert len(rows) == 2
    assert rows[1]["session_id"] == session_id


def test_connect_matches_migration_is_idempotent_for_already_migrated_schema(tmp_path):
    """新規DB(最初からsession_id列あり)にconnect()を複数回呼んでもエラーにならないこと。"""
    db_path = tmp_path / "fresh.db"
    connect(db_path).close()
    conn = connect(db_path)
    columns = conn.execute("PRAGMA table_info(matches)").fetchall()
    session_id_column = next(c for c in columns if c["name"] == "session_id")
    assert session_id_column["notnull"] == 0


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


def test_fetch_recent_matches_returns_last_n_in_ascending_order():
    conn = connect(":memory:")
    for i in range(5):
        save_match_result(
            conn,
            MatchResult(
                result="win",
                rank_before=i,
                rank_after=i + 1,
                league_changed=None,
                detected_at=datetime.now(timezone.utc),
            ),
        )

    rows = fetch_recent_matches(conn, limit=3)

    assert [row["rank_before"] for row in rows] == [2, 3, 4]


def test_fetch_recent_matches_returns_all_when_fewer_than_limit():
    conn = connect(":memory:")
    save_match_result(
        conn,
        MatchResult(result="win", rank_before=1, rank_after=2, league_changed=None, detected_at=datetime.now(timezone.utc)),
    )

    rows = fetch_recent_matches(conn, limit=10)

    assert len(rows) == 1


def test_fetch_recent_matches_returns_empty_list_when_no_matches():
    conn = connect(":memory:")

    assert fetch_recent_matches(conn, limit=10) == []


def test_fetch_goals_for_session_only_returns_goals_from_that_session(monkeypatch):
    monkeypatch.setenv("ALLOWED_PLAYERS", "Alice,Bob")
    monkeypatch.setenv("GOAL_RECORD_MODE", "all")
    conn = connect(":memory:")

    first_session_id = create_session(conn)
    first_match_id = save_match_result(
        conn,
        MatchResult(result="win", rank_before=1, rank_after=1, league_changed=None, detected_at=datetime.now(timezone.utc)),
        session_id=first_session_id,
    )
    save_goal(conn, first_match_id, "Alice", None, datetime.now(timezone.utc))

    second_session_id = create_session(conn)
    second_match_id = save_match_result(
        conn,
        MatchResult(result="win", rank_before=1, rank_after=1, league_changed=None, detected_at=datetime.now(timezone.utc)),
        session_id=second_session_id,
    )
    save_goal(conn, second_match_id, "Bob", None, datetime.now(timezone.utc))

    rows = fetch_goals_for_session(conn, second_session_id)

    assert len(rows) == 1
    assert rows[0]["scorer_name"] == "Bob"


def test_fetch_goals_for_session_returns_empty_list_when_no_goals(monkeypatch):
    conn = connect(":memory:")
    session_id = create_session(conn)

    assert fetch_goals_for_session(conn, session_id) == []


def test_fetch_matches_for_session_only_returns_matches_from_that_session():
    conn = connect(":memory:")
    first_session_id = create_session(conn)
    save_match_result(
        conn,
        MatchResult(result="win", rank_before=1, rank_after=2, league_changed=None, detected_at=datetime.now(timezone.utc)),
        session_id=first_session_id,
    )
    second_session_id = create_session(conn)
    save_match_result(
        conn,
        MatchResult(result="lose", rank_before=5, rank_after=4, league_changed=None, detected_at=datetime.now(timezone.utc)),
        session_id=second_session_id,
    )

    rows = fetch_matches_for_session(conn, second_session_id)

    assert len(rows) == 1
    assert rows[0]["result"] == "lose"


def test_fetch_matches_for_session_returns_empty_list_when_no_matches():
    conn = connect(":memory:")
    session_id = create_session(conn)

    assert fetch_matches_for_session(conn, session_id) == []


def test_save_goal_for_allowed_player(monkeypatch):
    monkeypatch.setenv("ALLOWED_PLAYERS", "Alice")
    monkeypatch.setenv("GOAL_RECORD_MODE", "allowlist")
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
    monkeypatch.setenv("GOAL_RECORD_MODE", "allowlist")
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
    monkeypatch.setenv("GOAL_RECORD_MODE", "allowlist")
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
    monkeypatch.setenv("GOAL_RECORD_MODE", "allowlist")
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
    monkeypatch.setenv("GOAL_RECORD_MODE", "allowlist")
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
    monkeypatch.setenv("GOAL_RECORD_MODE", "allowlist")
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
    monkeypatch.setenv("GOAL_RECORD_MODE", "allowlist")
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


def test_save_match_result_created_at_is_jst():
    conn = connect(":memory:")
    match = MatchResult(
        result="win",
        rank_before=1,
        rank_after=1,
        league_changed=None,
        detected_at=datetime.now(timezone.utc),
    )

    save_match_result(conn, match)

    row = fetch_all_matches(conn)[0]
    assert row["created_at"].endswith("+09:00")
    assert row["updated_at"].endswith("+09:00")


def test_save_goal_created_at_is_jst(monkeypatch):
    monkeypatch.setenv("ALLOWED_PLAYERS", "Alice")
    monkeypatch.setenv("GOAL_RECORD_MODE", "allowlist")
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

    save_goal(conn, match_id=match_id, scorer_name="Alice", assist_name=None, detected_at=datetime.now(timezone.utc))

    row = fetch_all_goals(conn)[0]
    assert row["created_at"].endswith("+09:00")
    assert row["updated_at"].endswith("+09:00")


def test_save_goal_logs_reason_without_leaking_disallowed_names(monkeypatch, caplog):
    monkeypatch.setenv("ALLOWED_PLAYERS", "Alice")
    monkeypatch.setenv("GOAL_RECORD_MODE", "allowlist")
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

    with caplog.at_level("INFO", logger="nss_tracker.database"):
        save_goal(
            conn,
            match_id=match_id,
            scorer_name="Stranger",
            assist_name="OtherStranger",
            detected_at=datetime.now(timezone.utc),
        )

    assert "許可リストに無い" in caplog.text
    assert "Stranger" not in caplog.text
    assert "OtherStranger" not in caplog.text


def test_save_goal_logs_reason_when_scorer_name_missing(monkeypatch, caplog):
    monkeypatch.setenv("ALLOWED_PLAYERS", "Alice")
    monkeypatch.setenv("GOAL_RECORD_MODE", "allowlist")
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

    with caplog.at_level("INFO", logger="nss_tracker.database"):
        save_goal(conn, match_id=match_id, scorer_name=None, assist_name=None, detected_at=datetime.now(timezone.utc))

    assert "読み取れなかった" in caplog.text


def test_save_goal_all_mode_records_regardless_of_allowlist(monkeypatch):
    monkeypatch.setenv("ALLOWED_PLAYERS", "")
    monkeypatch.setenv("GOAL_RECORD_MODE", "all")
    conn = connect(":memory:")
    match_id = _make_match(conn)

    goal_id = save_goal(
        conn,
        match_id=match_id,
        scorer_name="Stranger",
        assist_name="OtherStranger",
        detected_at=datetime.now(timezone.utc),
    )

    assert goal_id is not None
    row = fetch_all_goals(conn)[0]
    assert row["scorer_name"] == "Stranger"
    assert row["assist_name"] == "OtherStranger"


def test_save_goal_all_mode_still_skips_when_scorer_name_missing(monkeypatch):
    monkeypatch.setenv("ALLOWED_PLAYERS", "")
    monkeypatch.setenv("GOAL_RECORD_MODE", "all")
    conn = connect(":memory:")
    match_id = _make_match(conn)

    goal_id = save_goal(
        conn, match_id=match_id, scorer_name=None, assist_name="OtherStranger", detected_at=datetime.now(timezone.utc)
    )

    assert goal_id is None
    assert fetch_all_goals(conn) == []


def test_save_goal_redact_mode_nulls_out_disallowed_scorer(monkeypatch):
    """Issue #88の例そのもの: 許可リストに「ブルドッグ」がいて、
    得点者=たなか(許可リスト外)・アシスト=ブルドッグ(許可リスト内)の場合、
    得点者名はNULLでアシスト名はそのまま記録される。
    """
    monkeypatch.setenv("ALLOWED_PLAYERS", "ブルドッグ")
    monkeypatch.setenv("GOAL_RECORD_MODE", "allowlist_redact")
    conn = connect(":memory:")
    match_id = _make_match(conn)

    goal_id = save_goal(
        conn,
        match_id=match_id,
        scorer_name="たなか",
        assist_name="ブルドッグ",
        detected_at=datetime.now(timezone.utc),
    )

    assert goal_id is not None
    row = fetch_all_goals(conn)[0]
    assert row["scorer_name"] is None
    assert row["assist_name"] == "ブルドッグ"


def test_save_goal_redact_mode_nulls_out_disallowed_assist(monkeypatch):
    monkeypatch.setenv("ALLOWED_PLAYERS", "Alice")
    monkeypatch.setenv("GOAL_RECORD_MODE", "allowlist_redact")
    conn = connect(":memory:")
    match_id = _make_match(conn)

    save_goal(
        conn,
        match_id=match_id,
        scorer_name="Alice",
        assist_name="Stranger",
        detected_at=datetime.now(timezone.utc),
    )

    row = fetch_all_goals(conn)[0]
    assert row["scorer_name"] == "Alice"
    assert row["assist_name"] is None


def test_save_goal_redact_mode_keeps_both_when_both_allowed(monkeypatch):
    monkeypatch.setenv("ALLOWED_PLAYERS", "Alice,Bob")
    monkeypatch.setenv("GOAL_RECORD_MODE", "allowlist_redact")
    conn = connect(":memory:")
    match_id = _make_match(conn)

    save_goal(
        conn, match_id=match_id, scorer_name="Alice", assist_name="Bob", detected_at=datetime.now(timezone.utc)
    )

    row = fetch_all_goals(conn)[0]
    assert row["scorer_name"] == "Alice"
    assert row["assist_name"] == "Bob"


def test_save_goal_redact_mode_skips_when_neither_allowed(monkeypatch):
    monkeypatch.setenv("ALLOWED_PLAYERS", "Alice")
    monkeypatch.setenv("GOAL_RECORD_MODE", "allowlist_redact")
    conn = connect(":memory:")
    match_id = _make_match(conn)

    goal_id = save_goal(
        conn,
        match_id=match_id,
        scorer_name="Stranger",
        assist_name="OtherStranger",
        detected_at=datetime.now(timezone.utc),
    )

    assert goal_id is None
    assert fetch_all_goals(conn) == []


def test_connect_migrates_legacy_not_null_scorer_name_schema(tmp_path):
    """Issue #88: scorer_nameがNOT NULLだった移行前のDBファイルに対しても、
    connect()を呼ぶだけでNOT NULL制約が外れ、既存データを保持したまま
    NULLの得点者名を挿入できるようになることを確認する。
    """
    db_path = tmp_path / "legacy.db"
    legacy_conn = sqlite3.connect(db_path)
    legacy_conn.executescript(
        """
        CREATE TABLE matches (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            detected_at TEXT NOT NULL,
            result TEXT NOT NULL,
            rank_before REAL,
            rank_after REAL,
            league_changed TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE TABLE goals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            match_id INTEGER NOT NULL REFERENCES matches(id),
            detected_at TEXT NOT NULL,
            scorer_name TEXT NOT NULL,
            assist_name TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        INSERT INTO matches (detected_at, result, created_at, updated_at)
            VALUES ('2026-07-01T00:00:00+09:00', 'win', '2026-07-01T00:00:00+09:00', '2026-07-01T00:00:00+09:00');
        INSERT INTO goals (match_id, detected_at, scorer_name, assist_name, created_at, updated_at)
            VALUES (1, '2026-07-01T00:00:00+09:00', 'ExistingScorer', 'ExistingAssist',
                    '2026-07-01T00:00:00+09:00', '2026-07-01T00:00:00+09:00');
        """
    )
    legacy_conn.commit()
    legacy_conn.close()

    conn = connect(db_path)

    columns = conn.execute("PRAGMA table_info(goals)").fetchall()
    scorer_column = next(c for c in columns if c["name"] == "scorer_name")
    assert scorer_column["notnull"] == 0

    rows = fetch_all_goals(conn)
    assert len(rows) == 1
    assert rows[0]["scorer_name"] == "ExistingScorer"
    assert rows[0]["assist_name"] == "ExistingAssist"

    conn.execute(
        "INSERT INTO goals (match_id, detected_at, scorer_name, assist_name, created_at, updated_at) "
        "VALUES (1, '2026-07-02T00:00:00+09:00', NULL, 'RedactedTest', '2026-07-02T00:00:00+09:00', "
        "'2026-07-02T00:00:00+09:00')"
    )
    conn.commit()
    assert fetch_all_goals(conn)[1]["scorer_name"] is None


def test_connect_is_idempotent_for_already_migrated_schema(tmp_path):
    """新規DB(最初からNOT NULL無し)にconnect()を複数回呼んでもエラーにならないこと。"""
    db_path = tmp_path / "fresh.db"
    connect(db_path).close()
    conn = connect(db_path)
    columns = conn.execute("PRAGMA table_info(goals)").fetchall()
    scorer_column = next(c for c in columns if c["name"] == "scorer_name")
    assert scorer_column["notnull"] == 0


def _make_match(conn) -> int:
    return save_match_result(
        conn,
        MatchResult(
            result="win",
            rank_before=1,
            rank_after=1,
            league_changed=None,
            detected_at=datetime.now(timezone.utc),
        ),
    )


def test_save_and_fetch_vs_slot_ranks():
    conn = connect(":memory:")
    match_id = _make_match(conn)

    inserted_ids = save_vs_slot_ranks(
        conn,
        match_id=match_id,
        mine_ranks=[SlotRank("∞", 38), SlotRank("∞", 1), SlotRank("∞", 24), SlotRank("∞", 9)],
        opponent_ranks=[SlotRank("∞", 10), SlotRank("∞", 12), SlotRank("∞", 33), SlotRank("∞", 18)],
    )

    assert inserted_ids == list(range(1, 9))
    rows = fetch_vs_slot_ranks(conn, match_id)
    assert len(rows) == 8
    assert [row["side"] for row in rows] == ["mine"] * 4 + ["opponent"] * 4
    assert [row["slot_index"] for row in rows] == [0, 1, 2, 3, 0, 1, 2, 3]
    assert [row["rank_tier"] for row in rows] == [38, 1, 24, 9, 10, 12, 33, 18]
    assert [row["rank_tier_label"] for row in rows] == ["∞"] * 8
    assert all(row["match_id"] == match_id for row in rows)
    assert all(row["created_at"] is not None for row in rows)


def test_save_vs_slot_ranks_keeps_none_slots_as_null_rows():
    """読み取れなかったスロット(B~E帯・ランク非表示等)も、
    goalsのような許可リストフィルタとは異なりスキップせずNULL行として保存する。
    """
    conn = connect(":memory:")
    match_id = _make_match(conn)

    save_vs_slot_ranks(
        conn,
        match_id=match_id,
        mine_ranks=[SlotRank("∞", 40), SlotRank("∞", 9), SlotRank("∞", 16), SlotRank(None, None)],
        opponent_ranks=[SlotRank(None, None)] * 4,
    )

    rows = fetch_vs_slot_ranks(conn, match_id)
    assert len(rows) == 8
    assert [row["rank_tier"] for row in rows] == [40, 9, 16, None, None, None, None, None]
    assert [row["rank_tier_label"] for row in rows] == ["∞", "∞", "∞", None, None, None, None, None]


def test_save_vs_slot_ranks_distinguishes_letter_tiers():
    """S/A帯は数値だけでなくrank_tier_labelでも∞と区別して保存できることを確認する
    (Issue #40)。
    """
    conn = connect(":memory:")
    match_id = _make_match(conn)

    save_vs_slot_ranks(
        conn,
        match_id=match_id,
        mine_ranks=[SlotRank("∞", 40), SlotRank("S", 3), SlotRank("A", 28), SlotRank(None, None)],
        opponent_ranks=[SlotRank(None, None)] * 4,
    )

    rows = fetch_vs_slot_ranks(conn, match_id)
    mine_rows = [row for row in rows if row["side"] == "mine"]
    assert [row["rank_tier_label"] for row in mine_rows] == ["∞", "S", "A", None]
    assert [row["rank_tier"] for row in mine_rows] == [40, 3, 28, None]


def test_save_vs_slot_ranks_created_at_is_jst():
    conn = connect(":memory:")
    match_id = _make_match(conn)

    save_vs_slot_ranks(
        conn,
        match_id=match_id,
        mine_ranks=[SlotRank("∞", 1), SlotRank(None, None), SlotRank(None, None), SlotRank(None, None)],
        opponent_ranks=[SlotRank(None, None)] * 4,
    )

    row = fetch_vs_slot_ranks(conn, match_id)[0]
    assert row["created_at"].endswith("+09:00")
    assert row["updated_at"].endswith("+09:00")
