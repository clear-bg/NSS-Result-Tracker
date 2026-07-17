"""試合結果・ゴールのSQLiteへの保存。

日時カラムは2種類ある:
- detected_at: 試合結果/ゴールを検知した実時刻(ドメイン上の日時)。期間で
  絞り込むグラフ集計等はこちらを使う
- created_at / updated_at: レコード自体の作成・更新時刻(監査用の一般的な
  カラム)。今後追加するテーブルにも同様に持たせる想定

いずれもJST(日本標準時、timeutil.now_jst参照)で保存する。個人利用(日本国内)
のみを想定しており、UTCで保存すると目視確認時に9時間ズレて分かりにくいため。

goalsテーブルはmatches.idをmatch_idとして参照する(matchesテーブル自体には
先回りして将来項目を持たせない、という既存方針どおり)。得点者・アシスト者の
どちらもconfig.is_allowed_player()で許可されていない場合、save_goal()は
何も挿入せずNoneを返す(記録すらしないというプライバシー方針)。どちらか一方でも
許可されていれば、もう一方が許可リストに無い名前でもそのまま保存する
(ローカル運用のみのため、許可リスト外の実名がDBに残ること自体は許容する)。
save_goal()が保存しなかった場合、その理由(得点者名が読み取れなかった/
許可リスト外)をログに残すが、許可リスト外の実名自体はログにも出さない
(ログファイルはgit管理外とはいえ、記録しないと決めた実名を別の場所に
書き出さないため)。
"""

import logging
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Optional

from nss_tracker.config import is_allowed_player
from nss_tracker.state.match_state import MatchResult
from nss_tracker.timeutil import now_jst

logger = logging.getLogger("nss_tracker.database")

DEFAULT_DB_PATH = Path("nss_tracker.db")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS matches (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    detected_at TEXT NOT NULL,      -- 結果バナー検知時刻(ISO8601, JST)
    result TEXT NOT NULL,           -- 'win' / 'lose' / 'draw'
    rank_before REAL,               -- 結果バナー表示時点のランク値
    rank_after REAL,                -- ランク変動確定後の値
    league_changed TEXT,            -- 'up' / 'down' / NULL
    created_at TEXT NOT NULL,       -- レコード作成時刻(ISO8601, JST)
    updated_at TEXT NOT NULL        -- レコード最終更新時刻(ISO8601, JST)
);

CREATE TABLE IF NOT EXISTS goals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    match_id INTEGER NOT NULL REFERENCES matches(id),
    detected_at TEXT NOT NULL,      -- ゴール検知時刻(ISO8601, JST)
    scorer_name TEXT NOT NULL,
    assist_name TEXT,               -- アシスト無しの場合NULL
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
"""


def connect(db_path: Path = DEFAULT_DB_PATH) -> sqlite3.Connection:
    """DBに接続し、テーブルが無ければ作成して返す。"""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.executescript(_SCHEMA)
    conn.commit()
    logger.info("DBに接続しました: %s", Path(db_path).resolve())
    return conn


def save_match_result(conn: sqlite3.Connection, match: MatchResult) -> int:
    """MatchResultを1件matchesテーブルに保存し、挿入したレコードのidを返す。"""
    now = now_jst().isoformat()
    cursor = conn.execute(
        "INSERT INTO matches "
        "(detected_at, result, rank_before, rank_after, league_changed, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (
            match.detected_at.isoformat(),
            match.result,
            match.rank_before,
            match.rank_after,
            match.league_changed,
            now,
            now,
        ),
    )
    conn.commit()
    return cursor.lastrowid


def fetch_all_matches(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    """保存済みの試合結果を記録順(id昇順)ですべて取得する。"""
    return conn.execute("SELECT * FROM matches ORDER BY id").fetchall()


def save_goal(
    conn: sqlite3.Connection,
    match_id: int,
    scorer_name: Optional[str],
    assist_name: Optional[str],
    detected_at: datetime,
) -> Optional[int]:
    """ゴールを1件goalsテーブルに保存する。

    scorer_nameがNoneの場合(OCR失敗等)はgoals.scorer_nameがNOT NULLのため
    保存できずNoneを返す。scorer_nameが存在していても、得点者・アシスト者の
    どちらもis_allowed_player()でFalseの場合は何も保存せずNoneを返す。
    どちらか一方でも許可されていれば、もう一方が許可リストに無い名前でも
    そのまま保存する。
    """
    if not scorer_name:
        logger.info("ゴールを検知しましたが得点者名を読み取れなかったため記録しません: match_id=%d", match_id)
        return None
    if not is_allowed_player(scorer_name) and not (assist_name and is_allowed_player(assist_name)):
        logger.info(
            "ゴールを検知しましたが得点者・アシスト者とも許可リストに無いため記録しません: match_id=%d",
            match_id,
        )
        return None

    now = now_jst().isoformat()
    cursor = conn.execute(
        "INSERT INTO goals (match_id, detected_at, scorer_name, assist_name, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (match_id, detected_at.isoformat(), scorer_name, assist_name, now, now),
    )
    conn.commit()
    return cursor.lastrowid


def fetch_all_goals(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    """保存済みのゴールを記録順(id昇順)ですべて取得する。"""
    return conn.execute("SELECT * FROM goals ORDER BY id").fetchall()
