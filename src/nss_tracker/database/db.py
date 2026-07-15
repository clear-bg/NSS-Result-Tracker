"""試合結果のSQLiteへの保存。

CLAUDE.md記載の方針どおり、現在のスコープは勝敗・ランクの記録のみ
(ゴール・アシストの記録は将来段階で未実装)。将来ゴール・アシスト等を
記録する際は、matches.id を match_id として参照する別テーブル(例: goals)を
追加する形で拡張する想定のため、matchesテーブル自体には将来項目を
先回りして持たせていない。
"""

import sqlite3
from pathlib import Path

from nss_tracker.state.match_state import MatchResult

DEFAULT_DB_PATH = Path("nss_tracker.db")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS matches (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    detected_at TEXT NOT NULL,      -- 結果バナー検知時刻(ISO8601)
    result TEXT NOT NULL,           -- 'win' or 'lose'
    rank_before INTEGER,            -- 結果バナー表示時点のランク値
    rank_after INTEGER,             -- ランク変動確定後の値
    league_changed TEXT             -- 'up' / 'down' / NULL
);
"""


def connect(db_path: Path = DEFAULT_DB_PATH) -> sqlite3.Connection:
    """DBに接続し、matchesテーブルが無ければ作成して返す。"""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute(_SCHEMA)
    conn.commit()
    return conn


def save_match_result(conn: sqlite3.Connection, match: MatchResult) -> int:
    """MatchResultを1件matchesテーブルに保存し、挿入したレコードのidを返す。"""
    cursor = conn.execute(
        "INSERT INTO matches (detected_at, result, rank_before, rank_after, league_changed) "
        "VALUES (?, ?, ?, ?, ?)",
        (
            match.detected_at.isoformat(),
            match.result,
            match.rank_before,
            match.rank_after,
            match.league_changed,
        ),
    )
    conn.commit()
    return cursor.lastrowid


def fetch_all_matches(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    """保存済みの試合結果を記録順(id昇順)ですべて取得する。"""
    return conn.execute("SELECT * FROM matches ORDER BY id").fetchall()
