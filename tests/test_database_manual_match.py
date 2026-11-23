"""記録漏れ試合の手動追加(Issue #489)のDB書き込み(db.insert_manual_match)のテスト。"""

from datetime import datetime
from pathlib import Path

import pytest

from nss_tracker.database.db import (
    backup_database,
    connect,
    create_session,
    fetch_match,
    fetch_rank_warning_acks,
    fetch_vs_slot_ranks,
    insert_manual_match,
    plan_manual_match_insert,
    save_rank_warning_ack,
)
from nss_tracker.manual_match import ManualMatchEntry, ManualMatchError, VsSlotEntry
from nss_tracker.timeutil import JST


def _at(day: int, hour: int, minute: int) -> datetime:
    return datetime(2026, 11, day, hour, minute, tzinfo=JST)


def _insert(conn, session_id, detected_at, rank_before, rank_after, result="win", staked=1, excluded=0) -> int:
    now = detected_at.isoformat()
    cursor = conn.execute(
        "INSERT INTO matches (session_id, detected_at, result, rank_before, rank_after, rank_staked, "
        "excluded, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (session_id, detected_at.isoformat(), result, rank_before, rank_after, staked, excluded, now, now),
    )
    conn.commit()
    return cursor.lastrowid


@pytest.fixture()
def conn(tmp_path: Path):
    connection = connect(tmp_path / "test.db")
    yield connection
    connection.close()


@pytest.fixture()
def two_sessions(conn):
    """前回の配信(2試合)と、1試合目の前を記録し損ねた今回の配信(2試合)。"""
    previous_session = create_session(conn)
    current_session = create_session(conn)
    ids = [
        _insert(conn, previous_session, _at(20, 21, 0), 42.90, 43.10),
        _insert(conn, previous_session, _at(20, 21, 10), 43.10, 43.27, result="win"),
        _insert(conn, current_session, _at(22, 21, 9), 43.27, 43.78),
        _insert(conn, current_session, _at(22, 21, 15), 43.78, None, result="lose"),
    ]
    return previous_session, current_session, ids


def _entry(**overrides) -> ManualMatchEntry:
    values = {"detected_at": _at(22, 21, 4), "result": "win", "rank_after": 43.51}
    values.update(overrides)
    return ManualMatchEntry(**values)


def test_plan_describes_position_chain_and_shifted_matches(conn, two_sessions):
    _, current_session, ids = two_sessions
    plan = plan_manual_match_insert(conn, _entry(), current_session)
    assert plan.position.new_id == ids[2]
    assert plan.position.session_id == current_session
    assert plan.previous_staked_id == ids[1]
    assert plan.rank_before == 43.27
    assert plan.league_changed is None
    assert plan.shifted_match_ids == (ids[2], ids[3])
    assert plan.next_staked_id == ids[2]
    assert plan.next_staked_old_rank_before == 43.27
    assert plan.next_staked_rank_after == 43.78


def test_insert_shifts_following_ids_and_relinks_the_chain(conn, two_sessions):
    _, current_session, ids = two_sessions
    new_id, _ = insert_manual_match(conn, _entry(), current_session)

    assert new_id == ids[2]
    added = fetch_match(conn, new_id)
    assert added["session_id"] == current_session
    assert added["result"] == "win"
    assert added["rank_before"] == 43.27
    assert added["rank_after"] == 43.51
    assert added["rank_staked"] == 1
    assert added["room_type"] == "random"
    assert added["rank_before_ocr"] is None
    assert added["rank_after_ocr"] is None

    # 元のid=3,4は4,5へずれ、直後の試合の試合前ランクは追加した試合のrank_afterになる
    moved = fetch_match(conn, ids[2] + 1)
    assert moved["detected_at"] == _at(22, 21, 9).isoformat()
    assert moved["rank_before"] == 43.51
    assert moved["rank_after"] == 43.78
    assert fetch_match(conn, ids[3] + 1)["rank_before"] == 43.78
    assert [row["id"] for row in conn.execute("SELECT id FROM matches ORDER BY id")] == [1, 2, 3, 4, 5]


def test_insert_moves_child_rows_together(conn, two_sessions):
    _, current_session, ids = two_sessions
    now = _at(22, 21, 9).isoformat()
    conn.execute(
        "INSERT INTO goals (match_id, detected_at, scorer_name, created_at, updated_at) VALUES (?, ?, 'a', ?, ?)",
        (ids[2], now, now, now),
    )
    conn.execute(
        "INSERT INTO vs_slot_ranks (match_id, side, slot_index, rank_tier, rank_tier_label, created_at, updated_at) "
        "VALUES (?, 'mine', 0, 43, '∞', ?, ?)",
        (ids[2], now, now),
    )
    conn.commit()
    save_rank_warning_ack(conn, ids[2], "J")
    save_rank_warning_ack(conn, ids[3], "J")

    insert_manual_match(conn, _entry(), current_session)

    assert conn.execute("SELECT match_id FROM goals").fetchone()["match_id"] == ids[2] + 1
    assert [r["rank_tier"] for r in fetch_vs_slot_ranks(conn, ids[2] + 1)] == [43]
    assert fetch_vs_slot_ranks(conn, ids[2]) == []
    assert fetch_rank_warning_acks(conn, ids[2]) == frozenset()
    assert fetch_rank_warning_acks(conn, ids[2] + 1) == frozenset({"J"})
    assert fetch_rank_warning_acks(conn, ids[3] + 1) == frozenset({"J"})


def test_next_autoincrement_id_does_not_collide_after_shift(conn, two_sessions):
    _, current_session, ids = two_sessions
    insert_manual_match(conn, _entry(), current_session)
    later = _insert(conn, current_session, _at(22, 21, 30), None, None)
    assert later == ids[3] + 2


def test_insert_saves_vs_ranks_for_all_slots_when_any_is_known(conn, two_sessions):
    _, current_session, _ = two_sessions
    unknown = VsSlotEntry()
    entry = _entry(
        vs_mine=(VsSlotEntry("∞", 43), VsSlotEntry("S", 7), unknown, unknown),
        vs_opponent=(VsSlotEntry("∞", 45), unknown, unknown, unknown),
    )
    new_id, _ = insert_manual_match(conn, entry, current_session)
    rows = fetch_vs_slot_ranks(conn, new_id)
    assert len(rows) == 8
    assert [(r["side"], r["slot_index"], r["rank_tier_label"], r["rank_tier"]) for r in rows[:2]] == [
        ("mine", 0, "∞", 43),
        ("mine", 1, "S", 7),
    ]
    assert rows[2]["rank_tier"] is None and rows[2]["rank_tier_label"] is None


def test_insert_saves_no_vs_ranks_when_all_unknown(conn, two_sessions):
    _, current_session, _ = two_sessions
    new_id, _ = insert_manual_match(conn, _entry(), current_session)
    assert fetch_vs_slot_ranks(conn, new_id) == []


def test_insert_records_league_change_and_label(conn, two_sessions):
    """昇格した試合はleague_changedと同じ値を昇格演出の検知結果にも入れる(ルールCを出さない)。"""
    _, current_session, ids = two_sessions
    conn.execute("UPDATE matches SET rank_after = 43.95 WHERE id = ?", (ids[1],))
    conn.commit()
    new_id, _ = insert_manual_match(conn, _entry(rank_after=44.12), current_session)
    added = fetch_match(conn, new_id)
    assert added["league_changed"] == "up"
    assert added["league_change_label_detected"] == "up"


def test_insert_appends_when_current_session_has_no_matches(conn, two_sessions):
    _, _, ids = two_sessions
    conn.execute("UPDATE matches SET rank_after = 43.60 WHERE id = ?", (ids[3],))
    conn.commit()
    new_session = create_session(conn)
    new_id, plan = insert_manual_match(conn, _entry(detected_at=_at(23, 20, 0), rank_after=43.80), new_session)
    assert new_id == ids[3] + 1
    assert plan.shifted_match_ids == ()
    added = fetch_match(conn, new_id)
    assert added["session_id"] == new_session
    assert added["rank_before"] == 43.60


def test_insert_is_rejected_when_previous_ranked_match_is_unconfirmed(conn, two_sessions):
    _, _, ids = two_sessions
    new_session = create_session(conn)
    with pytest.raises(ManualMatchError) as exc:
        insert_manual_match(conn, _entry(detected_at=_at(23, 20, 0)), new_session)
    assert f"id={ids[3]}" in str(exc.value)
    assert conn.execute("SELECT COUNT(*) AS n FROM matches").fetchone()["n"] == 4


def test_insert_is_rejected_for_impossible_rank_change(conn, two_sessions):
    _, current_session, _ = two_sessions
    with pytest.raises(ManualMatchError) as exc:
        insert_manual_match(conn, _entry(result="lose", rank_after=43.51), current_session)
    assert exc.value.field == "rank_after"
    assert conn.execute("SELECT COUNT(*) AS n FROM matches").fetchone()["n"] == 4


def test_insert_skips_excluded_matches_when_choosing_the_chain_source(conn, two_sessions):
    _, current_session, ids = two_sessions
    conn.execute("UPDATE matches SET excluded = 1 WHERE id = ?", (ids[1],))
    conn.commit()
    plan = plan_manual_match_insert(conn, _entry(), current_session)
    assert plan.previous_staked_id == ids[0]
    assert plan.rank_before == 43.10


def test_before_commit_failure_rolls_back_everything(conn, two_sessions):
    _, current_session, ids = two_sessions

    def fail(boundary):
        raise RuntimeError("rename failed")

    with pytest.raises(RuntimeError):
        insert_manual_match(conn, _entry(), current_session, before_commit=fail)
    assert [row["id"] for row in conn.execute("SELECT id FROM matches ORDER BY id")] == ids
    assert fetch_match(conn, ids[2])["rank_before"] == 43.27


def test_before_commit_receives_insert_position(conn, two_sessions):
    _, current_session, ids = two_sessions
    calls = []

    def rename(boundary):
        calls.append(("rename", boundary))
        return lambda: calls.append(("undo", boundary))

    insert_manual_match(conn, _entry(), current_session, before_commit=rename)
    assert calls == [("rename", ids[2])]


def test_backup_database_copies_current_contents(conn, two_sessions, tmp_path):
    dest = backup_database(conn, tmp_path / "backups" / "copy.db")
    copied = connect(dest)
    try:
        assert copied.execute("SELECT COUNT(*) AS n FROM matches").fetchone()["n"] == 4
    finally:
        copied.close()
