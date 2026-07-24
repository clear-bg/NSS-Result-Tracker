"""試合結果・ゴールのSQLiteへの保存。

DBファイルの保存先は`.env`の`DB_PATH`で指定する(必須、未設定時は
`connect()`呼び出し時に`ConfigError`。config.get_db_path参照)。

日時カラムは2種類ある:
- detected_at: 試合結果/ゴールを検知した実時刻(ドメイン上の日時)。期間で
  絞り込むグラフ集計等はこちらを使う
- created_at / updated_at: レコード自体の作成・更新時刻(監査用の一般的な
  カラム)。今後追加するテーブルにも同様に持たせる想定

いずれもJST(日本標準時、timeutil.now_jst参照)で保存する。個人利用(日本国内)
のみを想定しており、UTCで保存すると目視確認時に9時間ズレて分かりにくいため。

goalsテーブルはmatches.idをmatch_idとして参照する(matchesテーブル自体には
先回りして将来項目を持たせない、という既存方針どおり)。得点者名がOCRで
全く読み取れなかった場合(scorer_nameがNone)、save_goal()は何も挿入せず
Noneを返す(このケースは.envのGOAL_RECORD_MODEに関わらず一律)。

GOAL_RECORD_MODE(Issue #88)は許可リスト(config.is_allowed_player())の
扱いを3種類から選べる:
- "all": 許可リストに関係なく得点者・アシストを両方そのまま記録する
- "allowlist"(従来の挙動): 得点者・アシストのどちらか一方でも許可リストに
  あれば、両方の名前をそのまま記録する。どちらも許可リストに無い場合は
  何も挿入せずNoneを返す(ローカル運用のみのため、許可リスト外の実名が
  DBに残ること自体は許容する、という判断)
- "allowlist_redact": 得点者・アシストのどちらか一方でも許可リストにあれば
  記録するが、DBに載る名前は許可リストにあるものだけにする(許可リスト外の
  名前はNULLで保存する)。このためgoals.scorer_nameはNOT NULL制約を持たない
  (_migrate_goals_scorer_name_nullable参照)

save_goal()が保存しなかった場合、その理由(得点者名が読み取れなかった/
許可リスト外)をログに残すが、許可リスト外の実名自体はログにも出さない
(ログファイルはgit管理外とはいえ、記録しないと決めた実名を別の場所に
書き出さないため)。

vs_slot_ranksテーブルは、VS画面(マッチング完了)確定時点の両チーム最大4人分の
ランクバッジを1行=1スロット(最大1試合あたり8行)で保存する。goalsテーブルと
同じくmatches.idをmatch_idとして参照する。名前を持たない(ランクの数値のみを
記録する、個人の識別情報は持たない設計。CLAUDE.md・Issue #39参照)ため、goals
のような許可リストによるフィルタリングは行わない。rank_tierは∞/S/A帯内の数値、
rank_tier_labelは'∞'/'S'/'A'のいずれか(Issue #40)。B/C/D/Eバッジ・ランク非表示・
読み取り失敗はいずれも区別せずrank_tier/rank_tier_labelともNULLのまま全スロット分
そのまま保存する(detection.vs_rank側もポリシーを持たず見えたものをそのまま報告
する設計と対応させている)。

sessionsテーブルは「配信セッション」(Issue #93)を表す。1セッション=main.pyの
プロセス起動1回に対応する(手動起動のみを想定する現状の運用と一致させるため、
CLAUDE.md「常駐アプリとしての起動方法」参照)。main.py起動直後にcreate_session()
で1行作成し、正常終了時(main()のfinally節)にend_session()でended_atを記録する。
異常終了(プロセスkillなど)の場合はended_atがNULLのまま残るが、次回起動時に
新しいセッション行が作られるだけなので実害はない。matches.session_idはこの
sessionsテーブルを参照する(NULL許容、Issue #93より前に記録された既存matches行は
NULLのまま)。Webサーバーは検知・記録処理と別スレッド/別コネクションで動くため
(モジュールdocstring内、web/server.py参照)、直接session_idを共有できない。
そのためfetch_current_session_id()はsessionsテーブルの最新行(MAX(id))を
「現在実行中(または直近)のセッション」とみなして返す。プロセスは起動ごとに
1回しかセッション行を作らないため、この最新行判定だけで常に一意に定まる。
"""

import logging
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Optional

from nss_tracker.config import get_db_path, get_goal_record_mode, is_allowed_player
from nss_tracker.detection.vs_rank import SlotRank
from nss_tracker.state.match_state import MatchResult
from nss_tracker.timeutil import now_jst

logger = logging.getLogger("nss_tracker.database")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at TEXT NOT NULL,       -- 配信セッション開始時刻(main.py起動時刻、ISO8601, JST)
    ended_at TEXT,                  -- 配信セッション終了時刻。正常終了時のみ設定、異常終了時はNULLのまま
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS matches (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id INTEGER REFERENCES sessions(id), -- 検知時点の配信セッション。Issue #93より前の既存行はNULL
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
    scorer_name TEXT,               -- GOAL_RECORD_MODE=allowlist_redactでNULLになりうる
    assist_name TEXT,               -- アシスト無し、または上記redactの場合NULL
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS vs_slot_ranks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    match_id INTEGER NOT NULL REFERENCES matches(id),
    side TEXT NOT NULL,             -- 'mine' / 'opponent'
    slot_index INTEGER NOT NULL,    -- 0(カメラに最も近い位置)〜3(最も奥)
    rank_tier INTEGER,              -- 帯内の数値。未識別・B~E・非表示はNULL
    rank_tier_label TEXT,           -- '∞'/'S'/'A'。未識別・B~E・非表示はNULL
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
"""


def _migrate_goals_scorer_name_nullable(conn: sqlite3.Connection) -> None:
    """Issue #88: goals.scorer_nameのNOT NULL制約を外す既存DBファイルの移行。

    _SCHEMAは新規DBでは最初からNOT NULL無しでgoalsテーブルを作るため、
    この関数は「_SCHEMA変更前に作られた既存のnss_tracker.dbファイル」を
    開いたときにだけ実際に移行処理を行う(該当しなければ即returnで無害)。
    SQLiteは列のNOT NULL制約を直接変更するALTER TABLEをサポートしないため、
    新しいスキーマでテーブルを作り直し、データを移してから差し替える。
    """
    columns = conn.execute("PRAGMA table_info(goals)").fetchall()
    scorer_column = next((c for c in columns if c["name"] == "scorer_name"), None)
    if scorer_column is None or scorer_column["notnull"] == 0:
        return

    logger.info("goalsテーブルのscorer_name列のNOT NULL制約を移行しています")
    conn.executescript(
        """
        CREATE TABLE goals_new (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            match_id INTEGER NOT NULL REFERENCES matches(id),
            detected_at TEXT NOT NULL,
            scorer_name TEXT,
            assist_name TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        INSERT INTO goals_new SELECT * FROM goals;
        DROP TABLE goals;
        ALTER TABLE goals_new RENAME TO goals;
        """
    )
    conn.commit()


def _migrate_matches_add_session_id(conn: sqlite3.Connection) -> None:
    """Issue #93: 既存DBファイルのmatchesテーブルにsession_id列を追加する。

    NOT NULL制約が絡まない列追加のため、goalsのケース(_migrate_goals_scorer_name_nullable、
    テーブルの作り直しが必要)と異なりALTER TABLE ADD COLUMNだけで済む。新規DBでは
    _SCHEMA自体に既にsession_id列を含むため、この関数は無害にreturnする。
    """
    columns = conn.execute("PRAGMA table_info(matches)").fetchall()
    if any(c["name"] == "session_id" for c in columns):
        return

    logger.info("matchesテーブルにsession_id列を追加しています")
    conn.execute("ALTER TABLE matches ADD COLUMN session_id INTEGER REFERENCES sessions(id)")
    conn.commit()


def connect(db_path: Optional[Path] = None) -> sqlite3.Connection:
    """DBに接続し、テーブルが無ければ作成して返す。

    db_pathを省略した場合はconfig.get_db_path()を呼び出し時に評価する
    (モジュールインポート時にDB_PATHを要求してしまうと、DB接続を伴わない
    テスト・スクリプトの実行までDB_PATH未設定でConfigErrorになってしまうため)。
    """
    if db_path is None:
        db_path = get_db_path()
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.executescript(_SCHEMA)
    conn.commit()
    _migrate_goals_scorer_name_nullable(conn)
    _migrate_matches_add_session_id(conn)
    logger.info("DBに接続しました: %s", Path(db_path).resolve())
    return conn


def create_session(conn: sqlite3.Connection) -> int:
    """新しい配信セッションをsessionsテーブルに1行作成し、挿入したレコードのidを返す。

    main.py起動ごとに1回だけ呼ばれる想定(モジュールdocstring参照)。
    """
    now = now_jst().isoformat()
    cursor = conn.execute(
        "INSERT INTO sessions (started_at, created_at, updated_at) VALUES (?, ?, ?)",
        (now, now, now),
    )
    conn.commit()
    return cursor.lastrowid


def end_session(conn: sqlite3.Connection, session_id: int) -> None:
    """指定した配信セッションにended_atを記録する(main.py正常終了時に1回呼ばれる想定)。"""
    now = now_jst().isoformat()
    conn.execute(
        "UPDATE sessions SET ended_at = ?, updated_at = ? WHERE id = ?",
        (now, now, session_id),
    )
    conn.commit()


def fetch_current_session_id(conn: sqlite3.Connection) -> Optional[int]:
    """現在実行中(または直近)とみなす配信セッションのidを返す。

    sessionsテーブルの最新行(MAX(id))を返す(モジュールdocstring参照)。
    セッションが1件も無ければNoneを返す。
    """
    row = conn.execute("SELECT id FROM sessions ORDER BY id DESC LIMIT 1").fetchone()
    return row["id"] if row is not None else None


def save_match_result(conn: sqlite3.Connection, match: MatchResult, session_id: Optional[int] = None) -> int:
    """MatchResultを1件matchesテーブルに保存し、挿入したレコードのidを返す。

    session_idを省略した場合はNULLのまま保存する(セッション機構導入前と同じ挙動を
    前提にするテスト・スクリプト向け。main.py本体は必ず実際のsession_idを渡す)。
    """
    now = now_jst().isoformat()
    cursor = conn.execute(
        "INSERT INTO matches "
        "(session_id, detected_at, result, rank_before, rank_after, league_changed, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (
            session_id,
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

    scorer_nameがNoneの場合(OCR失敗等)は.envのGOAL_RECORD_MODEに関わらず
    保存できずNoneを返す。scorer_nameが存在する場合の挙動はGOAL_RECORD_MODE
    (config.get_goal_record_mode()、モジュールdocstring参照)によって変わる:
    "all"は常にそのまま保存、"allowlist"はどちらか一方が許可リストにあれば
    両方そのまま保存(どちらも無ければNoneを返す)、"allowlist_redact"は
    どちらか一方が許可リストにあれば保存するが、許可リストに無い方の名前は
    NULLにして保存する。
    """
    if not scorer_name:
        logger.info("ゴールを検知しましたが得点者名を読み取れなかったため記録しません: match_id=%d", match_id)
        return None

    mode = get_goal_record_mode()
    scorer_allowed = is_allowed_player(scorer_name)
    assist_allowed = bool(assist_name) and is_allowed_player(assist_name)

    if mode != "all" and not scorer_allowed and not assist_allowed:
        logger.info(
            "ゴールを検知しましたが得点者・アシスト者とも許可リストに無いため記録しません: match_id=%d",
            match_id,
        )
        return None

    if mode == "allowlist_redact":
        if not scorer_allowed:
            scorer_name = None
        if not assist_allowed:
            assist_name = None

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


def save_vs_slot_ranks(
    conn: sqlite3.Connection,
    match_id: int,
    mine_ranks: list[SlotRank],
    opponent_ranks: list[SlotRank],
) -> list[int]:
    """VS画面確定時点の両チーム最大4人分のランクバッジをvs_slot_ranksテーブルに保存する。

    mine_ranks/opponent_ranksはそれぞれスロット0(カメラに最も近い位置、
    自チーム側は自分自身)〜3(最も奥)の順(detection.vs_rank.read_vs_screen_ranks
    の戻り値と対応させること)。読み取れなかったスロット(SlotRank(None, None))も
    rank_tier/rank_tier_labelがNULLの行としてそのまま保存する(goalsと異なり
    許可リストによるフィルタリングは行わない、モジュールdocstring参照)。
    挿入した各レコードのidをリストで返す。
    """
    now = now_jst().isoformat()
    inserted_ids = []
    for side, ranks in (("mine", mine_ranks), ("opponent", opponent_ranks)):
        for slot_index, slot_rank in enumerate(ranks):
            cursor = conn.execute(
                "INSERT INTO vs_slot_ranks "
                "(match_id, side, slot_index, rank_tier, rank_tier_label, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (match_id, side, slot_index, slot_rank.value, slot_rank.tier, now, now),
            )
            inserted_ids.append(cursor.lastrowid)
    conn.commit()
    return inserted_ids


def fetch_vs_slot_ranks(conn: sqlite3.Connection, match_id: int) -> list[sqlite3.Row]:
    """指定した試合のVSスロットランクをside, slot_index順ですべて取得する。"""
    return conn.execute(
        "SELECT * FROM vs_slot_ranks WHERE match_id = ? ORDER BY side, slot_index",
        (match_id,),
    ).fetchall()
