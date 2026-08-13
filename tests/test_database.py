import sqlite3
from datetime import datetime, timezone

import pytest

from nss_tracker.database.db import (
    connect,
    create_session,
    end_session,
    fetch_all_goals,
    fetch_all_matches,
    fetch_current_session_id,
    fetch_goals_for_session,
    fetch_latest_vs_rank_snapshot,
    fetch_matches_for_session,
    fetch_oldest_pending_manual_rank_match,
    fetch_pending_manual_rank_match_count,
    fetch_recent_matches,
    fetch_vs_rank_snapshot_slots,
    fetch_vs_slot_ranks,
    save_goal,
    save_manual_rank_after,
    save_match_result,
    save_vs_rank_snapshot,
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
    assert "vs_rank_snapshots" in table_names
    assert "vs_rank_snapshot_slots" in table_names


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


def test_save_match_result_uses_current_room_type_for_unranked_match(monkeypatch):
    """Issue #358: ランクを賭けない試合(rank_before/rank_afterどちらもNone)は、
    config.get_room_type()(/adminからの現在設定)がそのままroom_typeとして
    保存されることを確認する。
    """
    monkeypatch.setattr("nss_tracker.config._current_room_type", "private")
    conn = connect(":memory:")
    match = MatchResult(
        result="win",
        rank_before=None,
        rank_after=None,
        league_changed=None,
        detected_at=datetime.now(timezone.utc),
    )

    match_id = save_match_result(conn, match)

    row = conn.execute("SELECT * FROM matches WHERE id = ?", (match_id,)).fetchone()
    assert row["room_type"] == "private"


def test_save_match_result_defaults_room_type_to_random_when_unranked(monkeypatch):
    monkeypatch.setattr("nss_tracker.config._current_room_type", "random")
    conn = connect(":memory:")
    match = MatchResult(
        result="win",
        rank_before=None,
        rank_after=None,
        league_changed=None,
        detected_at=datetime.now(timezone.utc),
    )

    match_id = save_match_result(conn, match)

    row = conn.execute("SELECT * FROM matches WHERE id = ?", (match_id,)).fetchone()
    assert row["room_type"] == "random"


@pytest.mark.parametrize(
    "rank_before,rank_after",
    [(38.5, None), (None, 39.0), (38.5, 39.0)],
)
def test_save_match_result_forces_random_room_type_for_ranked_match(monkeypatch, rank_before, rank_after):
    """Issue #358: ランクが検出された試合(rank_before/rank_afterのいずれかが非None)は、
    現在の野良/専用部屋設定が'private'であっても、切り替え忘れ対策として必ず
    'random'に上書きされることを確認する(ランクを賭けた対戦は仕組み上野良でしか
    成立しないため)。
    """
    monkeypatch.setattr("nss_tracker.config._current_room_type", "private")
    conn = connect(":memory:")
    match = MatchResult(
        result="win",
        rank_before=rank_before,
        rank_after=rank_after,
        league_changed=None,
        detected_at=datetime.now(timezone.utc),
    )

    match_id = save_match_result(conn, match)

    row = conn.execute("SELECT * FROM matches WHERE id = ?", (match_id,)).fetchone()
    assert row["room_type"] == "random"


def test_save_match_result_corrects_previous_rank_after_with_small_diff(caplog, monkeypatch):
    """Issue #179: 直前の試合のrank_afterと今回のrank_beforeに差があれば、
    続いている試合とみなして直前の試合のrank_afterを補正することを確認する。
    """
    monkeypatch.setenv("RANK_AFTER_CORRECTION_ENABLED", "true")
    conn = connect(":memory:")
    first = MatchResult(
        result="lose",
        rank_before=38.62,
        rank_after=38.60,  # #178修正後もなお残る読み取り誤差を想定した値
        league_changed=None,
        detected_at=datetime.now(timezone.utc),
    )
    first_id = save_match_result(conn, first)

    second = MatchResult(
        result="win",
        rank_before=38.41,  # 真の値(精度の高いrank_before)
        rank_after=38.63,
        league_changed=None,
        detected_at=datetime.now(timezone.utc),
    )
    with caplog.at_level("INFO", logger="nss_tracker.database"):
        save_match_result(conn, second)

    row = conn.execute("SELECT * FROM matches WHERE id = ?", (first_id,)).fetchone()
    assert row["rank_after"] == 38.41
    assert f"matches.id={first_id}" in caplog.text
    assert "補完しました" in caplog.text


def test_save_match_result_corrects_previous_rank_after_even_when_diff_is_large(caplog, monkeypatch):
    """Issue #253: 差分が大きくても、試合間でランクは変動しないという前提のもと
    常に直前の試合のrank_afterを補正することを確認する(閾値による補正スキップは撤廃済み)。
    """
    monkeypatch.setenv("RANK_AFTER_CORRECTION_ENABLED", "true")
    conn = connect(":memory:")
    first = MatchResult(
        result="lose",
        rank_before=38.62,
        rank_after=38.60,
        league_changed=None,
        detected_at=datetime.now(timezone.utc),
    )
    first_id = save_match_result(conn, first)

    second = MatchResult(
        result="win",
        rank_before=39.50,  # 1試合分の通常変動幅を大きく超える差でも補正する
        rank_after=39.70,
        league_changed=None,
        detected_at=datetime.now(timezone.utc),
    )
    with caplog.at_level("INFO", logger="nss_tracker.database"):
        save_match_result(conn, second)

    row = conn.execute("SELECT * FROM matches WHERE id = ?", (first_id,)).fetchone()
    assert row["rank_after"] == 39.50
    assert f"matches.id={first_id}" in caplog.text
    assert "補完しました" in caplog.text


def test_save_match_result_backfills_when_previous_rank_after_is_none(caplog, monkeypatch):
    """Issue #285: 直前の試合のrank_afterがNone(バッジ読み取り失敗等)の場合でも、
    今回の試合のrank_beforeで補完することを確認する(以前はNoneの場合スキップして
    永久に欠損が残るバグがあった)。
    """
    monkeypatch.setenv("RANK_AFTER_CORRECTION_ENABLED", "true")
    conn = connect(":memory:")
    first = MatchResult(
        result="lose",
        rank_before=38.62,
        rank_after=None,
        league_changed=None,
        detected_at=datetime.now(timezone.utc),
    )
    first_id = save_match_result(conn, first)

    second = MatchResult(
        result="win",
        rank_before=38.62,
        rank_after=38.80,
        league_changed=None,
        detected_at=datetime.now(timezone.utc),
    )
    with caplog.at_level("INFO", logger="nss_tracker.database"):
        save_match_result(conn, second)

    row = conn.execute("SELECT * FROM matches WHERE id = ?", (first_id,)).fetchone()
    assert row["rank_after"] == 38.62
    assert f"matches.id={first_id}" in caplog.text
    assert "補完しました" in caplog.text


def test_save_match_result_does_not_correct_when_current_rank_before_is_none(monkeypatch):
    """今回の試合のrank_beforeがNoneの場合は補正の基準にできないため
    何もしないことを確認する。

    Issue #306: save_match_result()はrank_afterを書き込まなくなった(手動入力
    専用、rank_after_ocrへ移動)ため、補正が起きなければfirstのrank_afterは
    常にNULLのまま(補正はまだ一度も起きていない状態)。
    """
    monkeypatch.setenv("RANK_AFTER_CORRECTION_ENABLED", "true")
    conn = connect(":memory:")
    first = MatchResult(
        result="lose",
        rank_before=38.62,
        rank_after=38.60,
        league_changed=None,
        detected_at=datetime.now(timezone.utc),
    )
    first_id = save_match_result(conn, first)

    second = MatchResult(
        result="win",
        rank_before=None,
        rank_after=38.80,
        league_changed=None,
        detected_at=datetime.now(timezone.utc),
    )
    save_match_result(conn, second)

    row = conn.execute("SELECT * FROM matches WHERE id = ?", (first_id,)).fetchone()
    assert row["rank_after"] is None
    assert row["rank_after_ocr"] == 38.60


def test_save_match_result_does_not_correct_first_match_in_db(monkeypatch):
    """DB内に1件も試合が無い状態で最初の試合を保存する場合、直前の試合が
    存在しないため補正処理自体が何もしないことを確認する(エラーにならない)。

    Issue #306: rank_afterは手動入力専用になったため、match.rank_after(OCR推定値)は
    rank_after_ocr列に保存され、rank_after自体はNULLのまま(手動入力待ち)になる。
    """
    monkeypatch.setenv("RANK_AFTER_CORRECTION_ENABLED", "true")
    conn = connect(":memory:")
    match = MatchResult(
        result="win",
        rank_before=38.62,
        rank_after=38.80,
        league_changed=None,
        detected_at=datetime.now(timezone.utc),
    )

    match_id = save_match_result(conn, match)

    row = conn.execute("SELECT * FROM matches WHERE id = ?", (match_id,)).fetchone()
    assert row["rank_after"] is None
    assert row["rank_after_ocr"] == 38.80


def test_save_match_result_skips_correction_when_disabled(monkeypatch):
    """Issue #295: RANK_AFTER_CORRECTION_ENABLED=falseの場合、直前の試合の
    rank_afterを一切書き換えないことを確認する(仕組み自体は残しつつ、実機データで
    見つかった副作用のため一時的に無効化できるようにした)。

    Issue #306: rank_afterは手動入力専用になったため、firstのrank_afterはもともと
    NULL(手動入力待ち)であり、補正が無効化されている間はその状態のまま変わらない。
    """
    monkeypatch.setenv("RANK_AFTER_CORRECTION_ENABLED", "false")
    conn = connect(":memory:")
    first = MatchResult(
        result="lose",
        rank_before=38.62,
        rank_after=38.60,
        league_changed=None,
        detected_at=datetime.now(timezone.utc),
    )
    first_id = save_match_result(conn, first)

    second = MatchResult(
        result="win",
        rank_before=39.50,
        rank_after=39.70,
        league_changed=None,
        detected_at=datetime.now(timezone.utc),
    )
    save_match_result(conn, second)

    row = conn.execute("SELECT * FROM matches WHERE id = ?", (first_id,)).fetchone()
    assert row["rank_after"] is None, "無効化されている間は直前の試合のrank_afterを書き換えないはず"


def test_maybe_correct_previous_match_rank_after_does_not_overwrite_manual_value(monkeypatch):
    """Issue #306: 手動確定済み(rank_afterが非NULL)のrank_afterは、
    RANK_AFTER_CORRECTION_ENABLED=trueであっても補正(上書き)されないことを確認する。
    """
    monkeypatch.setenv("RANK_AFTER_CORRECTION_ENABLED", "true")
    conn = connect(":memory:")
    first = MatchResult(
        result="lose",
        rank_before=38.62,
        rank_after=38.60,
        league_changed=None,
        detected_at=datetime.now(timezone.utc),
    )
    first_id = save_match_result(conn, first)
    save_manual_rank_after(conn, first_id, 38.55)

    second = MatchResult(
        result="win",
        rank_before=39.50,  # firstのrank_afterと大きく異なる値でも上書きされないはず
        rank_after=39.70,
        league_changed=None,
        detected_at=datetime.now(timezone.utc),
    )
    save_match_result(conn, second)

    row = conn.execute("SELECT * FROM matches WHERE id = ?", (first_id,)).fetchone()
    assert row["rank_after"] == 38.55


def test_save_manual_rank_after_computes_league_changed_up():
    conn = connect(":memory:")
    match = MatchResult(
        result="win",
        rank_before=38.62,
        rank_after=None,
        league_changed=None,
        detected_at=datetime.now(timezone.utc),
    )
    match_id = save_match_result(conn, match)

    save_manual_rank_after(conn, match_id, 39.10)

    row = conn.execute("SELECT * FROM matches WHERE id = ?", (match_id,)).fetchone()
    assert row["rank_after"] == 39.10
    assert row["league_changed"] == "up"


def test_save_manual_rank_after_computes_league_changed_down():
    conn = connect(":memory:")
    match = MatchResult(
        result="lose",
        rank_before=38.62,
        rank_after=None,
        league_changed=None,
        detected_at=datetime.now(timezone.utc),
    )
    match_id = save_match_result(conn, match)

    save_manual_rank_after(conn, match_id, 37.90)

    row = conn.execute("SELECT * FROM matches WHERE id = ?", (match_id,)).fetchone()
    assert row["rank_after"] == 37.90
    assert row["league_changed"] == "down"


def test_save_manual_rank_after_computes_league_changed_none_when_tier_unchanged():
    conn = connect(":memory:")
    match = MatchResult(
        result="win",
        rank_before=38.62,
        rank_after=None,
        league_changed=None,
        detected_at=datetime.now(timezone.utc),
    )
    match_id = save_match_result(conn, match)

    save_manual_rank_after(conn, match_id, 38.95)

    row = conn.execute("SELECT * FROM matches WHERE id = ?", (match_id,)).fetchone()
    assert row["rank_after"] == 38.95
    assert row["league_changed"] is None


def test_save_manual_rank_after_raises_for_missing_match():
    conn = connect(":memory:")

    with pytest.raises(ValueError):
        save_manual_rank_after(conn, 999, 40.0)


def test_save_manual_rank_after_raises_for_unranked_match():
    conn = connect(":memory:")
    match = MatchResult(
        result="win",
        rank_before=None,
        rank_after=None,
        league_changed=None,
        detected_at=datetime.now(timezone.utc),
    )
    match_id = save_match_result(conn, match)

    with pytest.raises(ValueError):
        save_manual_rank_after(conn, match_id, 40.0)


def test_fetch_oldest_pending_manual_rank_match_returns_oldest_unconfirmed():
    conn = connect(":memory:")
    unranked = MatchResult(
        result="win", rank_before=None, rank_after=None, league_changed=None, detected_at=datetime.now(timezone.utc)
    )
    save_match_result(conn, unranked)
    oldest_pending = MatchResult(
        result="lose", rank_before=38.62, rank_after=38.50, league_changed=None, detected_at=datetime.now(timezone.utc)
    )
    oldest_pending_id = save_match_result(conn, oldest_pending)
    newer_pending = MatchResult(
        result="win", rank_before=38.55, rank_after=39.00, league_changed=None, detected_at=datetime.now(timezone.utc)
    )
    save_match_result(conn, newer_pending)

    row = fetch_oldest_pending_manual_rank_match(conn)

    assert row["id"] == oldest_pending_id


def test_fetch_oldest_pending_manual_rank_match_excludes_confirmed_matches():
    conn = connect(":memory:")
    confirmed = MatchResult(
        result="win", rank_before=38.62, rank_after=None, league_changed=None, detected_at=datetime.now(timezone.utc)
    )
    confirmed_id = save_match_result(conn, confirmed)
    save_manual_rank_after(conn, confirmed_id, 39.00)
    pending = MatchResult(
        result="lose", rank_before=39.00, rank_after=38.80, league_changed=None, detected_at=datetime.now(timezone.utc)
    )
    pending_id = save_match_result(conn, pending)

    row = fetch_oldest_pending_manual_rank_match(conn)

    assert row["id"] == pending_id


def test_fetch_oldest_pending_manual_rank_match_returns_none_when_none_pending():
    conn = connect(":memory:")
    unranked = MatchResult(
        result="draw", rank_before=None, rank_after=None, league_changed=None, detected_at=datetime.now(timezone.utc)
    )
    save_match_result(conn, unranked)

    assert fetch_oldest_pending_manual_rank_match(conn) is None


def test_save_match_result_rounds_rank_before_ocr_and_rank_after_ocr():
    """Issue #305系の会話で決定: rank_before_ocr/rank_after_ocrは小数第2位までに
    丸めて保存する(小数第3位を四捨五入)。
    """
    conn = connect(":memory:")
    match = MatchResult(
        result="win",
        rank_before=38.626,
        rank_after=39.124,
        league_changed=None,
        detected_at=datetime.now(timezone.utc),
    )

    match_id = save_match_result(conn, match)

    row = conn.execute("SELECT * FROM matches WHERE id = ?", (match_id,)).fetchone()
    assert row["rank_before_ocr"] == 38.63
    assert row["rank_after_ocr"] == 39.12


def test_save_match_result_rounds_bootstrap_rank_before():
    """前例が無いフォールバック(_resolve_rank_before)経由のrank_beforeも丸められることを確認する。"""
    conn = connect(":memory:")
    match = MatchResult(
        result="win", rank_before=38.626, rank_after=None, league_changed=None, detected_at=datetime.now(timezone.utc)
    )

    match_id = save_match_result(conn, match)

    row = conn.execute("SELECT * FROM matches WHERE id = ?", (match_id,)).fetchone()
    assert row["rank_before"] == 38.63


def test_save_manual_rank_after_rounds_value():
    conn = connect(":memory:")
    match = MatchResult(
        result="win", rank_before=38.62, rank_after=None, league_changed=None, detected_at=datetime.now(timezone.utc)
    )
    match_id = save_match_result(conn, match)

    save_manual_rank_after(conn, match_id, 39.127)

    row = conn.execute("SELECT * FROM matches WHERE id = ?", (match_id,)).fetchone()
    assert row["rank_after"] == 39.13


def test_matches_check_constraint_rejects_unrounded_rank_after(monkeypatch):
    """Issue #305系の会話で決定: db.pyの丸め処理をすり抜けて(バグ等で)小数第3位まで
    ある値を直接書き込もうとした場合、CHECK制約でIntegrityErrorになることを
    保険として確認する(db.py関数を経由しない、生のSQLでの検証)。
    """
    conn = connect(":memory:")
    match = MatchResult(
        result="win", rank_before=38.62, rank_after=None, league_changed=None, detected_at=datetime.now(timezone.utc)
    )
    match_id = save_match_result(conn, match)

    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("UPDATE matches SET rank_after = 39.127 WHERE id = ?", (match_id,))


def test_save_match_result_resolves_rank_before_from_ocr_when_no_prior_ranked_match():
    """Issue #308: DB内に直近のランクを賭けた試合が無い(初回)場合、
    rank_beforeはこの試合自身のOCR実測値(rank_before_ocr)をそのまま使う。
    """
    conn = connect(":memory:")
    match = MatchResult(
        result="win", rank_before=38.62, rank_after=None, league_changed=None, detected_at=datetime.now(timezone.utc)
    )

    match_id = save_match_result(conn, match)

    row = conn.execute("SELECT * FROM matches WHERE id = ?", (match_id,)).fetchone()
    assert row["rank_before"] == 38.62
    assert row["rank_before_ocr"] == 38.62


def test_save_match_result_chains_rank_before_from_confirmed_previous_rank_after():
    """Issue #308: 直近のランクを賭けた試合のrank_afterが確定済みなら、
    その値をそのまま今回のrank_beforeとして引き継ぐ(OCR実測値は無視する)。
    """
    conn = connect(":memory:")
    first = MatchResult(
        result="win", rank_before=38.62, rank_after=None, league_changed=None, detected_at=datetime.now(timezone.utc)
    )
    first_id = save_match_result(conn, first)
    save_manual_rank_after(conn, first_id, 39.10)

    second = MatchResult(
        result="lose",
        rank_before=38.90,  # OCR実測値は多少ズレているが、チェーンではこちらは使われない
        rank_after=None,
        league_changed=None,
        detected_at=datetime.now(timezone.utc),
    )
    second_id = save_match_result(conn, second)

    row = conn.execute("SELECT * FROM matches WHERE id = ?", (second_id,)).fetchone()
    assert row["rank_before"] == 39.10
    assert row["rank_before_ocr"] == 38.90


def test_save_match_result_leaves_rank_before_none_when_previous_rank_after_unconfirmed():
    """Issue #308: 直近のランクを賭けた試合のrank_afterがまだ未確定の場合、
    今回のrank_beforeもNoneのまま(手動入力待ち)にする(チェーンが詰まる)。
    """
    conn = connect(":memory:")
    first = MatchResult(
        result="win", rank_before=38.62, rank_after=None, league_changed=None, detected_at=datetime.now(timezone.utc)
    )
    save_match_result(conn, first)

    second = MatchResult(
        result="lose", rank_before=38.90, rank_after=None, league_changed=None, detected_at=datetime.now(timezone.utc)
    )
    second_id = save_match_result(conn, second)

    row = conn.execute("SELECT * FROM matches WHERE id = ?", (second_id,)).fetchone()
    assert row["rank_before"] is None
    assert row["rank_before_ocr"] == 38.90


def test_save_match_result_skips_rank_before_chain_for_unranked_match():
    """ランクを賭けない試合(rank_before_ocrがNone)は、直近の試合の確定状況に
    関わらずrank_beforeも常にNoneのままであることを確認する。
    """
    conn = connect(":memory:")
    first = MatchResult(
        result="win", rank_before=38.62, rank_after=None, league_changed=None, detected_at=datetime.now(timezone.utc)
    )
    first_id = save_match_result(conn, first)
    save_manual_rank_after(conn, first_id, 39.10)

    unranked = MatchResult(
        result="draw", rank_before=None, rank_after=None, league_changed=None, detected_at=datetime.now(timezone.utc)
    )
    unranked_id = save_match_result(conn, unranked)

    row = conn.execute("SELECT * FROM matches WHERE id = ?", (unranked_id,)).fetchone()
    assert row["rank_before"] is None
    assert row["rank_before_ocr"] is None


def test_save_manual_rank_after_backfills_next_blocked_rank_before():
    """Issue #308: 未確定だった試合のrank_afterを確定すると、それを引き継ぎ元として
    待っていた次の試合のrank_beforeが自動的に埋まることを確認する。
    """
    conn = connect(":memory:")
    first = MatchResult(
        result="win", rank_before=38.62, rank_after=None, league_changed=None, detected_at=datetime.now(timezone.utc)
    )
    first_id = save_match_result(conn, first)
    second = MatchResult(
        result="lose", rank_before=38.90, rank_after=None, league_changed=None, detected_at=datetime.now(timezone.utc)
    )
    second_id = save_match_result(conn, second)
    row = conn.execute("SELECT rank_before FROM matches WHERE id = ?", (second_id,)).fetchone()
    assert row["rank_before"] is None, "前提: この時点ではまだチェーンが詰まっているはず"

    save_manual_rank_after(conn, first_id, 39.10)

    row = conn.execute("SELECT rank_before FROM matches WHERE id = ?", (second_id,)).fetchone()
    assert row["rank_before"] == 39.10


def test_save_manual_rank_after_cascades_backfill_across_multiple_blocked_matches():
    """Issue #308: 2件以上まとめて未確定のまま溜まっていた場合でも、古い順に
    確定させるたびにチェーンが1件ずつ連鎖的に解決されることを確認する。
    """
    conn = connect(":memory:")
    ids = []
    for i, rank_before_ocr in enumerate([38.62, 38.90, 39.20]):
        match = MatchResult(
            result="win",
            rank_before=rank_before_ocr,
            rank_after=None,
            league_changed=None,
            detected_at=datetime.now(timezone.utc),
        )
        ids.append(save_match_result(conn, match))

    def get_rank_before(match_id):
        return conn.execute("SELECT rank_before FROM matches WHERE id = ?", (match_id,)).fetchone()["rank_before"]

    assert get_rank_before(ids[1]) is None
    assert get_rank_before(ids[2]) is None

    save_manual_rank_after(conn, ids[0], 39.00)
    assert get_rank_before(ids[1]) == 39.00
    assert get_rank_before(ids[2]) is None, "ids[1]自体がまだ未確定のため、ids[2]まではまだ連鎖しないはず"

    save_manual_rank_after(conn, ids[1], 38.50)
    assert get_rank_before(ids[2]) == 38.50


def test_save_manual_rank_after_correction_updates_value_and_league_changed():
    """Issue #338: 既に確定済みの試合に対してsave_manual_rank_after()を再度呼ぶと
    (=修正)、エラーにならずrank_after・league_changedが新しい値で上書きされる
    ことを確認する。
    """
    conn = connect(":memory:")
    match = MatchResult(
        result="win", rank_before=38.62, rank_after=None, league_changed=None, detected_at=datetime.now(timezone.utc)
    )
    match_id = save_match_result(conn, match)
    save_manual_rank_after(conn, match_id, 38.90)

    save_manual_rank_after(conn, match_id, 39.10)

    row = conn.execute("SELECT rank_after, league_changed FROM matches WHERE id = ?", (match_id,)).fetchone()
    assert row["rank_after"] == 39.10
    assert row["league_changed"] == "up"


def test_save_manual_rank_after_correction_force_overwrites_next_pending_rank_before():
    """Issue #338: 確定済みの試合を修正すると、次の試合(まだ未確定)のrank_beforeが
    既にチェーンで埋まっていても、修正後の値で連動更新されることを確認する。
    """
    conn = connect(":memory:")
    first = MatchResult(
        result="win", rank_before=38.62, rank_after=None, league_changed=None, detected_at=datetime.now(timezone.utc)
    )
    first_id = save_match_result(conn, first)
    save_manual_rank_after(conn, first_id, 39.10)

    second = MatchResult(
        result="lose", rank_before=38.90, rank_after=None, league_changed=None, detected_at=datetime.now(timezone.utc)
    )
    second_id = save_match_result(conn, second)
    assert conn.execute(
        "SELECT rank_before FROM matches WHERE id = ?", (second_id,)
    ).fetchone()["rank_before"] == 39.10

    save_manual_rank_after(conn, first_id, 39.50)  # 修正

    row = conn.execute("SELECT rank_before FROM matches WHERE id = ?", (second_id,)).fetchone()
    assert row["rank_before"] == 39.50


def test_save_manual_rank_after_correction_force_overwrites_next_confirmed_rank_before_and_warns(caplog):
    """Issue #338: 修正した試合の次の試合が既に確定済み(別途rank_afterも手動確定
    済み)だった場合でも、rank_beforeは無条件で連動更新する(ユーザー確認済みの
    仕様)。ただしその試合自身のrank_after・league_changedには手を付けず、
    不整合の可能性をWARNINGログで可視化することを確認する。
    """
    conn = connect(":memory:")
    first = MatchResult(
        result="win", rank_before=38.62, rank_after=None, league_changed=None, detected_at=datetime.now(timezone.utc)
    )
    first_id = save_match_result(conn, first)
    save_manual_rank_after(conn, first_id, 39.10)

    second = MatchResult(
        result="win", rank_before=39.10, rank_after=None, league_changed=None, detected_at=datetime.now(timezone.utc)
    )
    second_id = save_match_result(conn, second)
    save_manual_rank_after(conn, second_id, 39.30)  # 次の試合自身も別途確定済み

    with caplog.at_level("WARNING", logger="nss_tracker.database"):
        save_manual_rank_after(conn, first_id, 39.50)  # 修正

    row = conn.execute(
        "SELECT rank_before, rank_after, league_changed FROM matches WHERE id = ?", (second_id,)
    ).fetchone()
    assert row["rank_before"] == 39.50, "rank_beforeは無条件で連動更新される"
    assert row["rank_after"] == 39.30, "次の試合自身が確定した値は変更しない"
    assert f"matches.id={second_id}" in caplog.text
    assert "要手動確認" in caplog.text


def test_save_manual_rank_after_correction_does_not_cascade_beyond_immediate_next_match():
    """Issue #338: 連動更新は直後の1件のみが対象で、さらにその先の試合までは
    連鎖しないことを確認する(その先の試合のrank_beforeは、直後の試合自身が
    確定した実際のrank_afterに基づいたままでよいため)。
    """
    conn = connect(":memory:")
    ids = []
    prev_rank_after = None
    for i, rank_before_ocr in enumerate([38.62, 38.90, 39.20]):
        match = MatchResult(
            result="win",
            rank_before=rank_before_ocr,
            rank_after=None,
            league_changed=None,
            detected_at=datetime.now(timezone.utc),
        )
        match_id = save_match_result(conn, match)
        ids.append(match_id)
        save_manual_rank_after(conn, match_id, rank_before_ocr + 0.20)

    third_rank_before_original = conn.execute(
        "SELECT rank_before FROM matches WHERE id = ?", (ids[2],)
    ).fetchone()["rank_before"]

    save_manual_rank_after(conn, ids[0], 40.00)  # 修正

    second_row = conn.execute("SELECT rank_before FROM matches WHERE id = ?", (ids[1],)).fetchone()
    third_row = conn.execute("SELECT rank_before FROM matches WHERE id = ?", (ids[2],)).fetchone()
    assert second_row["rank_before"] == 40.00
    assert third_row["rank_before"] == third_rank_before_original


def test_save_manual_rank_after_raises_for_chain_blocked_match():
    """rank_beforeがまだチェーンで解決できていない(直前の試合が未確定の)試合に
    対してsave_manual_rank_after()を呼ぶとValueErrorになることを確認する
    (fetch_oldest_pending_manual_rank_match()は通常この状態の試合を返さない
    はずだが、防御的チェックとして確認する)。
    """
    conn = connect(":memory:")
    first = MatchResult(
        result="win", rank_before=38.62, rank_after=None, league_changed=None, detected_at=datetime.now(timezone.utc)
    )
    save_match_result(conn, first)
    second = MatchResult(
        result="lose", rank_before=38.90, rank_after=None, league_changed=None, detected_at=datetime.now(timezone.utc)
    )
    second_id = save_match_result(conn, second)

    with pytest.raises(ValueError):
        save_manual_rank_after(conn, second_id, 39.00)


def test_fetch_pending_manual_rank_match_count():
    conn = connect(":memory:")
    unranked = MatchResult(
        result="draw", rank_before=None, rank_after=None, league_changed=None, detected_at=datetime.now(timezone.utc)
    )
    save_match_result(conn, unranked)
    for _ in range(3):
        pending = MatchResult(
            result="win", rank_before=38.62, rank_after=38.50, league_changed=None, detected_at=datetime.now(timezone.utc)
        )
        save_match_result(conn, pending)

    assert fetch_pending_manual_rank_match_count(conn) == 3


def test_connect_migrates_legacy_matches_without_rank_before_ocr(tmp_path):
    """Issue #308: rank_before_ocr列が無い移行前のDBファイル(#306より前、または
    #306直後で#308より前のDB)に対しても、connect()を呼ぶだけで列が追加され、
    既存のrank_beforeの値がrank_before_ocrへコピーされることを確認する。
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
            rank_after_ocr REAL,
            league_changed TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        INSERT INTO matches (detected_at, result, rank_before, created_at, updated_at)
            VALUES ('2026-07-01T00:00:00+09:00', 'win', 38.62, '2026-07-01T00:00:00+09:00', '2026-07-01T00:00:00+09:00');
        INSERT INTO matches (detected_at, result, created_at, updated_at)
            VALUES ('2026-07-01T00:01:00+09:00', 'draw', '2026-07-01T00:01:00+09:00', '2026-07-01T00:01:00+09:00');
        """
    )
    legacy_conn.commit()
    legacy_conn.close()

    conn = connect(db_path)

    rows = fetch_all_matches(conn)
    assert len(rows) == 2
    assert rows[0]["rank_before_ocr"] == 38.62  # 既存rank_beforeからコピーされる
    assert rows[1]["rank_before_ocr"] is None  # 元々rank_beforeがNULLの行はNULLのまま


def test_connect_migrates_legacy_matches_without_rank_before_ocr_is_idempotent(tmp_path):
    db_path = tmp_path / "test.db"
    connect(db_path).close()

    connect(db_path).close()  # 2回目もエラーにならない


def test_connect_migrates_legacy_matches_without_room_type(tmp_path):
    """Issue #358: room_type列が無い移行前のDBファイルに対しても、connect()を
    呼ぶだけで列が追加され、既存行は全て'random'(野良)扱いになることを確認する。
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
    room_type_column = next(c for c in columns if c["name"] == "room_type")
    assert room_type_column["notnull"] == 1

    rows = fetch_all_matches(conn)
    assert len(rows) == 1
    assert rows[0]["room_type"] == "random"


def test_connect_migrates_legacy_matches_without_room_type_is_idempotent(tmp_path):
    db_path = tmp_path / "test.db"
    connect(db_path).close()

    connect(db_path).close()  # 2回目もエラーにならない


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


def test_connect_migrates_legacy_vs_slot_ranks_without_rank_tier_label(tmp_path):
    """Issue #119: rank_tier_label列が無い移行前のDBファイルに対しても、connect()を
    呼ぶだけで列が追加され、save_vs_slot_ranksが正常に動作するようになることを確認する。
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
        CREATE TABLE vs_slot_ranks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            match_id INTEGER NOT NULL REFERENCES matches(id),
            side TEXT NOT NULL,
            slot_index INTEGER NOT NULL,
            rank_tier INTEGER,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        """
    )
    legacy_conn.commit()
    legacy_conn.close()

    conn = connect(db_path)

    columns = conn.execute("PRAGMA table_info(vs_slot_ranks)").fetchall()
    rank_tier_label_column = next(c for c in columns if c["name"] == "rank_tier_label")
    assert rank_tier_label_column["notnull"] == 0

    match_id = _make_match(conn)
    save_vs_slot_ranks(
        conn,
        match_id=match_id,
        mine_ranks=[SlotRank("∞", 38), SlotRank(None, None), SlotRank(None, None), SlotRank(None, None)],
        opponent_ranks=[SlotRank(None, None)] * 4,
    )

    rows = fetch_vs_slot_ranks(conn, match_id)
    assert rows[0]["rank_tier_label"] == "∞"


def test_connect_vs_slot_ranks_migration_is_idempotent_for_already_migrated_schema(tmp_path):
    """新規DB(最初からrank_tier_label列あり)にconnect()を複数回呼んでもエラーにならないこと。"""
    db_path = tmp_path / "fresh.db"
    connect(db_path).close()
    conn = connect(db_path)
    columns = conn.execute("PRAGMA table_info(vs_slot_ranks)").fetchall()
    rank_tier_label_column = next(c for c in columns if c["name"] == "rank_tier_label")
    assert rank_tier_label_column["notnull"] == 0


def test_connect_migrates_legacy_matches_without_team_color(tmp_path):
    """Issue #113: mine_team_color/opponent_team_color列が無い移行前のDBファイルに
    対しても、connect()を呼ぶだけで列が追加され、既存データを保持したまま
    新しいmatchesをチームカラー付きで挿入できるようになることを確認する。
    """
    db_path = tmp_path / "legacy.db"
    legacy_conn = sqlite3.connect(db_path)
    legacy_conn.executescript(
        """
        CREATE TABLE matches (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id INTEGER,
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
    mine_color_column = next(c for c in columns if c["name"] == "mine_team_color")
    opponent_color_column = next(c for c in columns if c["name"] == "opponent_team_color")
    assert mine_color_column["notnull"] == 0
    assert opponent_color_column["notnull"] == 0

    rows = fetch_all_matches(conn)
    assert len(rows) == 1
    assert rows[0]["mine_team_color"] is None

    match = MatchResult(
        result="lose",
        rank_before=5,
        rank_after=4,
        league_changed=None,
        detected_at=datetime.now(timezone.utc),
        mine_team_color="#64bde2",
        opponent_team_color="#f87abe",
    )
    save_match_result(conn, match)

    rows = fetch_all_matches(conn)
    assert len(rows) == 2
    assert rows[1]["mine_team_color"] == "#64bde2"
    assert rows[1]["opponent_team_color"] == "#f87abe"


def test_connect_matches_team_color_migration_is_idempotent_for_already_migrated_schema(tmp_path):
    """新規DB(最初からteam_color列あり)にconnect()を複数回呼んでもエラーにならないこと。"""
    db_path = tmp_path / "fresh.db"
    connect(db_path).close()
    conn = connect(db_path)
    columns = conn.execute("PRAGMA table_info(matches)").fetchall()
    mine_color_column = next(c for c in columns if c["name"] == "mine_team_color")
    assert mine_color_column["notnull"] == 0


def test_save_and_fetch_match_result():
    """Issue #306: save_match_result()はrank_after/league_changedをもう書かない
    (手動入力専用に変更、rank_after_ocrへ移動)。match.rank_afterの値は
    rank_after_ocrへそのまま保存されることを確認する。
    """
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
    assert row["rank_after"] is None
    assert row["rank_after_ocr"] == 40.0
    assert row["league_changed"] is None
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


def test_save_match_result_with_team_color():
    conn = connect(":memory:")
    match = MatchResult(
        result="win",
        rank_before=39,
        rank_after=40,
        league_changed=None,
        detected_at=datetime.now(timezone.utc),
        mine_team_color="#64bde2",
        opponent_team_color="#f87abe",
    )

    save_match_result(conn, match)

    row = fetch_all_matches(conn)[0]
    assert row["mine_team_color"] == "#64bde2"
    assert row["opponent_team_color"] == "#f87abe"


def test_save_match_result_team_color_defaults_to_none():
    conn = connect(":memory:")
    match = MatchResult(
        result="win",
        rank_before=39,
        rank_after=40,
        league_changed=None,
        detected_at=datetime.now(timezone.utc),
    )

    save_match_result(conn, match)

    row = fetch_all_matches(conn)[0]
    assert row["mine_team_color"] is None
    assert row["opponent_team_color"] is None


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

    # Issue #308: rank_beforeはチェーン導出値になったため、順序検証にはmatchごとに
    # 一意な値のまま残るrank_before_ocr(自動検知値)を使う
    assert [row["rank_before_ocr"] for row in rows] == [2, 3, 4]


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


def test_save_goal_own_goal_all_mode_records_with_marker_scorer_name(monkeypatch):
    """Issue #217: オウンゴールはGOAL_RECORD_MODE=allの場合のみ、得点者名パネル
    自体が表示されない(scorer_name=None)にも関わらず固定文字列「オウンゴール」を
    scorer_nameとして記録する。
    """
    monkeypatch.setenv("ALLOWED_PLAYERS", "")
    monkeypatch.setenv("GOAL_RECORD_MODE", "all")
    conn = connect(":memory:")
    match_id = _make_match(conn)

    goal_id = save_goal(
        conn,
        match_id=match_id,
        scorer_name=None,
        assist_name=None,
        detected_at=datetime.now(timezone.utc),
        is_own_goal=True,
    )

    assert goal_id is not None
    row = fetch_all_goals(conn)[0]
    assert row["scorer_name"] == "オウンゴール"
    assert row["assist_name"] is None


def test_save_goal_own_goal_allowlist_mode_is_skipped(monkeypatch):
    """Issue #217: allowlistモードではオウンゴールに許可リストと照合できる実名が
    無いため記録しない(現状維持)。
    """
    monkeypatch.setenv("ALLOWED_PLAYERS", "")
    monkeypatch.setenv("GOAL_RECORD_MODE", "allowlist")
    conn = connect(":memory:")
    match_id = _make_match(conn)

    goal_id = save_goal(
        conn,
        match_id=match_id,
        scorer_name=None,
        assist_name=None,
        detected_at=datetime.now(timezone.utc),
        is_own_goal=True,
    )

    assert goal_id is None
    assert fetch_all_goals(conn) == []


def test_save_goal_own_goal_allowlist_redact_mode_is_skipped(monkeypatch):
    monkeypatch.setenv("ALLOWED_PLAYERS", "")
    monkeypatch.setenv("GOAL_RECORD_MODE", "allowlist_redact")
    conn = connect(":memory:")
    match_id = _make_match(conn)

    goal_id = save_goal(
        conn,
        match_id=match_id,
        scorer_name=None,
        assist_name=None,
        detected_at=datetime.now(timezone.utc),
        is_own_goal=True,
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


def test_save_and_fetch_vs_rank_snapshot():
    """Issue #145: 試合結果確定を待たずに書き込む「直近VS画面スナップショット」。"""
    conn = connect(":memory:")
    session_id = create_session(conn)

    snapshot_id = save_vs_rank_snapshot(
        conn,
        session_id=session_id,
        mine_ranks=[SlotRank("∞", 38), SlotRank("∞", 1), SlotRank("∞", 24), SlotRank("∞", 9)],
        opponent_ranks=[SlotRank("∞", 10), SlotRank("∞", 12), SlotRank("∞", 33), SlotRank("∞", 18)],
        mine_team_color="#64bde2",
        opponent_team_color="#f87abe",
        detected_at=datetime.now(timezone.utc),
    )

    header = fetch_latest_vs_rank_snapshot(conn)
    assert header["id"] == snapshot_id
    assert header["session_id"] == session_id
    assert header["mine_team_color"] == "#64bde2"
    assert header["opponent_team_color"] == "#f87abe"

    rows = fetch_vs_rank_snapshot_slots(conn, snapshot_id)
    assert len(rows) == 8
    assert [row["side"] for row in rows] == ["mine"] * 4 + ["opponent"] * 4
    assert [row["slot_index"] for row in rows] == [0, 1, 2, 3, 0, 1, 2, 3]
    assert [row["rank_tier"] for row in rows] == [38, 1, 24, 9, 10, 12, 33, 18]
    assert [row["rank_tier_label"] for row in rows] == ["∞"] * 8
    assert all(row["snapshot_id"] == snapshot_id for row in rows)


def test_fetch_latest_vs_rank_snapshot_returns_most_recently_saved_one():
    conn = connect(":memory:")
    save_vs_rank_snapshot(
        conn,
        session_id=None,
        mine_ranks=[SlotRank("∞", 1)] * 4,
        opponent_ranks=[SlotRank("∞", 1)] * 4,
        mine_team_color="#111111",
        opponent_team_color="#222222",
        detected_at=datetime.now(timezone.utc),
    )
    newest_id = save_vs_rank_snapshot(
        conn,
        session_id=None,
        mine_ranks=[SlotRank("∞", 40)] * 4,
        opponent_ranks=[SlotRank("∞", 10)] * 4,
        mine_team_color="#64bde2",
        opponent_team_color="#f87abe",
        detected_at=datetime.now(timezone.utc),
    )

    header = fetch_latest_vs_rank_snapshot(conn)

    assert header["id"] == newest_id
    assert header["mine_team_color"] == "#64bde2"


def test_save_vs_rank_snapshot_with_empty_ranks_creates_header_only():
    """VS画面を見逃した試合が終わった際、main.pyがリセット用に空スナップショットを書き込む想定。"""
    conn = connect(":memory:")

    snapshot_id = save_vs_rank_snapshot(
        conn,
        session_id=None,
        mine_ranks=[],
        opponent_ranks=[],
        mine_team_color=None,
        opponent_team_color=None,
        detected_at=datetime.now(timezone.utc),
    )

    header = fetch_latest_vs_rank_snapshot(conn)
    assert header["id"] == snapshot_id
    assert header["mine_team_color"] is None
    assert fetch_vs_rank_snapshot_slots(conn, snapshot_id) == []


def test_fetch_latest_vs_rank_snapshot_returns_none_when_no_snapshots():
    conn = connect(":memory:")
    assert fetch_latest_vs_rank_snapshot(conn) is None


def test_fetch_latest_vs_rank_snapshot_filters_by_session_id():
    """Issue #359: session_idを指定すると、そのセッションのスナップショットのみを対象にする。"""
    conn = connect(":memory:")
    old_session_id = create_session(conn)
    save_vs_rank_snapshot(
        conn,
        session_id=old_session_id,
        mine_ranks=[SlotRank("∞", 1)] * 4,
        opponent_ranks=[SlotRank("∞", 1)] * 4,
        mine_team_color="#111111",
        opponent_team_color="#222222",
        detected_at=datetime.now(timezone.utc),
    )
    current_session_id = create_session(conn)

    assert fetch_latest_vs_rank_snapshot(conn, session_id=current_session_id) is None

    newest_id = save_vs_rank_snapshot(
        conn,
        session_id=current_session_id,
        mine_ranks=[SlotRank("∞", 40)] * 4,
        opponent_ranks=[SlotRank("∞", 10)] * 4,
        mine_team_color="#64bde2",
        opponent_team_color="#f87abe",
        detected_at=datetime.now(timezone.utc),
    )

    header = fetch_latest_vs_rank_snapshot(conn, session_id=current_session_id)
    assert header["id"] == newest_id


def test_save_vs_rank_snapshot_created_at_is_jst():
    conn = connect(":memory:")

    snapshot_id = save_vs_rank_snapshot(
        conn,
        session_id=None,
        mine_ranks=[SlotRank("∞", 1), SlotRank(None, None), SlotRank(None, None), SlotRank(None, None)],
        opponent_ranks=[SlotRank(None, None)] * 4,
        mine_team_color="#64bde2",
        opponent_team_color="#f87abe",
        detected_at=datetime.now(timezone.utc),
    )

    header = fetch_latest_vs_rank_snapshot(conn)
    assert header["created_at"].endswith("+09:00")
    assert header["updated_at"].endswith("+09:00")

    row = fetch_vs_rank_snapshot_slots(conn, snapshot_id)[0]
    assert row["created_at"].endswith("+09:00")
    assert row["updated_at"].endswith("+09:00")
