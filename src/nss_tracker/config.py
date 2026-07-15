"""プレイヤー許可リストの読み込み。

得点・アシストの記録は、`.env`(git管理外。他プレイヤーの実名を含むため)の
`ALLOWED_PLAYERS`にカンマ区切りで書かれたプレイヤーのみを対象とする。
リストに無いプレイヤーの得点は記録すらしない(database.db.save_goal参照)。
テンプレートは`.env.example`(git管理対象)を参照すること。
"""

import os

from dotenv import load_dotenv

load_dotenv()


def _load_allowed_players() -> frozenset[str]:
    raw = os.environ.get("ALLOWED_PLAYERS", "")
    return frozenset(name.strip() for name in raw.split(",") if name.strip())


def is_allowed_player(name: str) -> bool:
    """得点・アシストを記録してよいプレイヤーかどうかを判定する。

    ALLOWED_PLAYERSは呼び出しのたびに.envから再読み込みする
    (テストや運用中の設定変更を反映しやすくするため)。
    """
    return name in _load_allowed_players()
