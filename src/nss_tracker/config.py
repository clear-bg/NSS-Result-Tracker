"""プレイヤー許可リスト・キャプチャ設定の読み込み。

得点・アシストの記録は、`.env`(git管理外。他プレイヤーの実名を含むため)の
`ALLOWED_PLAYERS`にカンマ区切りで書かれたプレイヤーのみを対象とする。
リストに無いプレイヤーの得点は記録すらしない(database.db.save_goal参照)。
テンプレートは`.env.example`(git管理対象)を参照すること。

キャプチャデバイス名・解像度(`CAPTURE_DEVICE_NAME`/`CAPTURE_WIDTH`/`CAPTURE_HEIGHT`)も
同様に`.env`から読み込む。フォールバック用のデフォルト値は持たず、未設定の場合は
ConfigErrorを送出する(`.env.example`を必ずコピーして値を埋めてもらう運用)。

`DB_PATH`・`FRAME_READ_TIMEOUT_SECONDS`は上記と異なり、未設定でも動作に支障が
無い値のため、フォールバック用のデフォルト値を持つ(ConfigErrorは送出しない)。
"""

import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()


class ConfigError(RuntimeError):
    """.envに必須の設定値が不足している場合に送出する。"""


def _load_allowed_players() -> frozenset[str]:
    raw = os.environ.get("ALLOWED_PLAYERS", "")
    return frozenset(name.strip() for name in raw.split(",") if name.strip())


def is_allowed_player(name: str) -> bool:
    """得点・アシストを記録してよいプレイヤーかどうかを判定する。

    ALLOWED_PLAYERSは呼び出しのたびに.envから再読み込みする
    (テストや運用中の設定変更を反映しやすくするため)。
    """
    return name in _load_allowed_players()


def get_capture_device_name() -> str:
    """dshowから読み取るキャプチャデバイス名を取得する。未設定時はConfigErrorを送出する。"""
    value = os.environ.get("CAPTURE_DEVICE_NAME")
    if not value:
        raise ConfigError("CAPTURE_DEVICE_NAMEが.envに設定されていません。.env.exampleを参考に設定してください。")
    return value


def get_capture_resolution() -> tuple[int, int]:
    """キャプチャ解像度(width, height)を取得する。未設定時はConfigErrorを送出する。"""
    width_raw = os.environ.get("CAPTURE_WIDTH")
    if not width_raw:
        raise ConfigError("CAPTURE_WIDTHが.envに設定されていません。.env.exampleを参考に設定してください。")
    height_raw = os.environ.get("CAPTURE_HEIGHT")
    if not height_raw:
        raise ConfigError("CAPTURE_HEIGHTが.envに設定されていません。.env.exampleを参考に設定してください。")
    return int(width_raw), int(height_raw)


def get_db_path() -> Path:
    """DBファイルの保存先を取得する。未設定時はカレントディレクトリのnss_tracker.dbにフォールバックする。"""
    value = os.environ.get("DB_PATH")
    return Path(value) if value else Path("nss_tracker.db")


def get_frame_read_timeout_seconds() -> float:
    """フレーム取得のタイムアウト秒数を取得する。未設定時は5.0にフォールバックする。"""
    value = os.environ.get("FRAME_READ_TIMEOUT_SECONDS")
    return float(value) if value else 5.0
