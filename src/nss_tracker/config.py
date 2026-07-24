"""プレイヤー許可リスト・キャプチャ設定の読み込み。

得点・アシストの記録は、`.env`(git管理外。他プレイヤーの実名を含むため)の
`ALLOWED_PLAYERS`にカンマ区切りで書かれたプレイヤーが得点者・アシスト者の
どちらか一方でも含まれていれば対象とする(database.db.save_goal参照)。
テンプレートは`.env.example`(git管理対象)を参照すること。`ALLOWED_PLAYERS`は
未設定時に「空リスト(=誰のゴールも記録しない)」という安全側の状態になる
ため、他の設定項目と異なりフォールバック値を持つ(2026-07-22、Issue #89の
議論を経てユーザーと確認済み)。

`ALLOWED_PLAYERS`以外の設定項目(`CAPTURE_DEVICE_NAME`・`CAPTURE_WIDTH`・
`CAPTURE_HEIGHT`・`DB_PATH`・`FRAME_READ_TIMEOUT_SECONDS`・`NSS_TRACKER_LOG_LEVEL`
・`WEB_HOST`・`WEB_PORT`・`GOAL_RECORD_MODE`)は、Python側にフォールバック用のデフォルト値を
一切持たない。`.env`に値が設定されていることを前提に動作し、未設定または
不正な値の場合は起動時に`ConfigError`を送出して明示的に失敗する(2026-07-22、
Issue #89の決め事。以前は一部の項目に「未設定でも動作に支障が無い値だから」
という理由でフォールバック値を持たせていたが、暗黙のデフォルトに気づかない
まま運用してしまうことを避けるため、`ALLOWED_PLAYERS`を除き全項目で統一した)。
`.env.example`側には各項目の実際の初期値をコメントアウトせずに記載してあるため、
`.env.example`をコピーするだけでそのまま動く。値を変更したい場合や、`.env`から
行ごと削除してしまった場合にのみ`ConfigError`に遭遇する。

`RANK_GRAPH_MATCH_LIMIT`(Issue #95)は、`ALLOWED_PLAYERS`に次ぐ2つ目の例外として
空文字列を許容する。ランク推移グラフの対象を「直近何試合分にするか」を指定する値
だが、空欄(未設定)の場合は「全期間を表示する」という安全側のデフォルト動作に
なるため(`.env.example`側もこの空欄をデフォルト値として記載する、ユーザーとの
相談で決定、2026-07-24)。数値を指定した場合はその値のint化を試み、数値化できない
値・0以下の値はConfigErrorを送出する。
"""

import logging
import os
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv

load_dotenv()

_VALID_LOG_LEVEL_NAMES = ("DEBUG", "INFO", "WARNING", "ERROR")
_VALID_GOAL_RECORD_MODES = ("all", "allowlist", "allowlist_redact")


class ConfigError(RuntimeError):
    """.envに必須の設定値が不足している、または値が不正な場合に送出する。"""


def _require_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise ConfigError(f"{name}が.envに設定されていません。.env.exampleを参考に設定してください。")
    return value


def get_allowed_players() -> frozenset[str]:
    """許可リスト(ALLOWED_PLAYERS)を取得する。呼び出しのたびに.envから再読み込みする。

    Issue #96: 許可リストが1名だけの場合、その人物=配信者本人であることが
    自明なため、ダッシュボード上でプレイヤー名自体を表示しない簡略表示に
    切り替える判定(呼び出し側でlen()==1を見る)にも使う。
    """
    raw = os.environ.get("ALLOWED_PLAYERS", "")
    return frozenset(name.strip() for name in raw.split(",") if name.strip())


def is_allowed_player(name: str) -> bool:
    """得点・アシストを記録してよいプレイヤーかどうかを判定する。

    ALLOWED_PLAYERSは呼び出しのたびに.envから再読み込みする
    (テストや運用中の設定変更を反映しやすくするため)。
    """
    return name in get_allowed_players()


def get_capture_device_name() -> str:
    """dshowから読み取るキャプチャデバイス名を取得する。未設定時はConfigErrorを送出する。"""
    return _require_env("CAPTURE_DEVICE_NAME")


def get_capture_resolution() -> tuple[int, int]:
    """キャプチャ解像度(width, height)を取得する。未設定時はConfigErrorを送出する。"""
    width_raw = _require_env("CAPTURE_WIDTH")
    height_raw = _require_env("CAPTURE_HEIGHT")
    return int(width_raw), int(height_raw)


def get_db_path() -> Path:
    """DBファイルの保存先を取得する。未設定時はConfigErrorを送出する。"""
    return Path(_require_env("DB_PATH"))


def get_frame_read_timeout_seconds() -> float:
    """フレーム取得のタイムアウト秒数を取得する。未設定時はConfigErrorを送出する。"""
    return float(_require_env("FRAME_READ_TIMEOUT_SECONDS"))


def get_log_level_name() -> str:
    """ログレベル名を取得する。未設定・不正な値の場合はConfigErrorを送出する。"""
    value = _require_env("NSS_TRACKER_LOG_LEVEL").upper()
    if value not in _VALID_LOG_LEVEL_NAMES:
        raise ConfigError(
            f"NSS_TRACKER_LOG_LEVELの値が不正です: {value}"
            f"({'/'.join(_VALID_LOG_LEVEL_NAMES)}のいずれかを指定してください)"
        )
    return value


def get_log_level() -> int:
    """loggingモジュールのログレベル定数を取得する。"""
    return logging.getLevelName(get_log_level_name())


def get_web_host() -> str:
    """Webダッシュボードのバインド先ホストを取得する。未設定時はConfigErrorを送出する。"""
    return _require_env("WEB_HOST")


def get_web_port() -> int:
    """Webダッシュボードのポート番号を取得する。未設定時はConfigErrorを送出する。"""
    return int(_require_env("WEB_PORT"))


def get_rank_graph_match_limit() -> Optional[int]:
    """ランク推移グラフの対象範囲(直近何試合分か)を取得する。

    未設定・空文字列の場合はNone(全期間を表示する)を返す(モジュールdocstring
    参照、ALLOWED_PLAYERSに次ぐ2つ目の「空文字列を許容する」例外)。数値化できない
    値、または0以下の値はConfigErrorを送出する。
    """
    raw = os.environ.get("RANK_GRAPH_MATCH_LIMIT", "").strip()
    if not raw:
        return None
    try:
        value = int(raw)
    except ValueError:
        raise ConfigError(
            f"RANK_GRAPH_MATCH_LIMITの値が不正です: {raw}(空欄で全期間、または正の整数を指定してください)"
        )
    if value <= 0:
        raise ConfigError(f"RANK_GRAPH_MATCH_LIMITの値が不正です: {raw}(正の整数を指定してください)")
    return value


def get_goal_record_mode() -> str:
    """ゴール/アシストをDBに記録する際の許可リストの扱いモードを取得する(Issue #88)。

    "all"(許可リストに関係なく全員記録)/"allowlist"(どちらかが許可リストに
    いれば両方そのまま記録)/"allowlist_redact"(どちらかが許可リストにいれば
    記録するが、許可リスト外の名前はNULLにする)のいずれか。未設定・不正な
    値の場合はConfigErrorを送出する(database.db.save_goal参照)。
    """
    value = _require_env("GOAL_RECORD_MODE")
    if value not in _VALID_GOAL_RECORD_MODES:
        raise ConfigError(
            f"GOAL_RECORD_MODEの値が不正です: {value}({'/'.join(_VALID_GOAL_RECORD_MODES)}のいずれかを指定してください)"
        )
    return value
