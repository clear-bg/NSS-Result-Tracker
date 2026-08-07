"""プレイヤー許可リスト・キャプチャ設定の読み込み。

得点・アシストの記録は、`.env`(git管理外。他プレイヤーの実名を含むため)の
`ALLOWED_PLAYERS`にカンマ区切りで書かれたプレイヤーが得点者・アシスト者の
どちらか一方でも含まれていれば対象とする(database.db.save_goal参照)。
テンプレートは`.env.example`(git管理対象)を参照すること。`ALLOWED_PLAYERS`は
未設定時に「空リスト(=誰のゴールも記録しない)」という安全側の状態になる
ため、他の設定項目と異なりフォールバック値を持つ。

`ALLOWED_PLAYERS`以外の設定項目(`CAPTURE_DEVICE_NAME`・`CAPTURE_WIDTH`・
`CAPTURE_HEIGHT`・`CAPTURE_FPS`・`DB_PATH`・`FRAME_READ_TIMEOUT_SECONDS`・
`NSS_TRACKER_LOG_LEVEL`
・`WEB_HOST`・`WEB_PORT`・`GOAL_RECORD_MODE`・`RANK_DELTA_DISTRIBUTION_SCOPE`・
`RANK_GRAPH_MATCH_LIMIT`・`OBS_WEBSOCKET_HOST`・
`OBS_WEBSOCKET_PORT`・`OBS_WEBSOCKET_PASSWORD`・`OBS_SCENE_IN_MATCH`・
`OBS_SCENE_BETWEEN_MATCHES`・`OBS_BROWSER_SOURCE_NAMES`・
`OBS_SCENE_SWITCHING_ENABLED`)は、
Python側にフォールバック用のデフォルト値を一切持たない。`.env`に値が設定されて
いることを前提に動作し、未設定または不正な値の場合は起動時に`ConfigError`を
送出して明示的に失敗する(暗黙のデフォルトに気づかないまま運用してしまうことを
避けるため)。`.env.example`側には各項目の実際の初期値をコメントアウトせずに
記載してあるため、`.env.example`をコピーするだけでそのまま動く。値を変更したい
場合や、`.env`から行ごと削除してしまった場合にのみ`ConfigError`に遭遇する。

`RANK_GRAPH_MATCH_LIMIT`・`OBS_WEBSOCKET_PASSWORD`は以前、空文字列を「全期間表示」
「認証無効」を意味する値として許容していたが、この「空欄=意図した設定」という
状態はIssue #89がそもそも避けたかった「未設定に気づかないまま運用してしまう」
ケースと見分けがつかないため、Issue #126で廃止した。現在は`ALLOWED_PLAYERS`のみが
唯一の例外であり、それ以外は空文字列を含め値が不正な場合ConfigErrorを送出する。

`RANK_GRAPH_MATCH_LIMIT`はランク推移グラフの対象を「直近何試合分にするか」を
指定する値。文字列`"all"`(全期間を表示する)、または正の整数の文字列を受け付ける。
それ以外の値(空文字列含む)はConfigErrorを送出する。

`OBS_WEBSOCKET_PASSWORD`はobs-websocketの接続パスワード。OBS側で認証を無効化して
いる場合は文字列`"none"`を指定する(空文字列のパスワードとして扱われる)。
それ以外の値(空文字列含む)は実際のパスワードとしてそのまま使う。

`OBS_BROWSER_SOURCE_NAMES`(Issue #247)は起動時に再読み込みするOBSブラウザ
ソース名。使わない場合は`OBS_WEBSOCKET_PASSWORD`と同じく文字列`"none"`を指定する
(空リストとして扱われる)。それ以外はカンマ区切りのソース名一覧として解釈する
(`ALLOWED_PLAYERS`と同じ形式)。

`OBS_SCENE_SWITCHING_ENABLED`(Issue #248)は`"true"`/`"false"`の文字列で、
OBSシーン自動切替(`obs_control.ObsSceneController.set_in_match`)を実際に
行うかどうかを制御する。無効化してもOBSへの接続自体(Issue #247のブラウザ
ソース再読み込み等)は維持したまま、シーン切替の呼び出しだけをスキップする
(配信によっては手動でシーンを操作したい場合があるための切り替え手段。
既定値は`"true"`で、これまでの「常に自動切替」という挙動を変えない)。

`ALLOWED_PLAYERS`・`GOAL_RECORD_MODE`・`RANK_GRAPH_MATCH_LIMIT`・
`RANK_DELTA_DISTRIBUTION_SCOPE`・`OBS_SCENE_SWITCHING_ENABLED`の5項目のみ、
Webダッシュボードの管理画面(`/admin`、`web/server.py`参照)からGUIで更新できる
(Issue #129、`OBS_SCENE_SWITCHING_ENABLED`はIssue #248で追加)。この5項目は
検知ループを再起動しなくても次に参照されたタイミングから即座に反映される
(get_allowed_players/get_goal_record_mode/get_rank_graph_match_limit/
get_rank_delta_distribution_scope/get_obs_scene_switching_enabledがいずれも
呼び出しのたびにos.environを読み直す実装のため)。`update_editable_settings`は
os.environと`.env`ファイルの両方を更新する(`.env`側も更新するのは、次回起動時
にも同じ値を引き継ぐため)。この5項目を選んだ理由は、配信中に調整したくなり得る値
(出演者・記録方針・グラフの表示範囲・シーン自動切替の要否)に絞ったため。
キャプチャ設定やOBS接続情報等は配信開始前に一度決めれば十分なため対象外とした。
"""

import logging
import os
from pathlib import Path
from typing import Optional

from dotenv import find_dotenv, load_dotenv, set_key

load_dotenv()

_VALID_LOG_LEVEL_NAMES = ("DEBUG", "INFO", "WARNING", "ERROR")
_VALID_GOAL_RECORD_MODES = ("all", "allowlist", "allowlist_redact")
_VALID_RANK_DELTA_DISTRIBUTION_SCOPES = ("session", "all")
_VALID_BOOL_STRINGS = ("true", "false")


class ConfigError(RuntimeError):
    """.envに必須の設定値が不足している、または値が不正な場合に送出する。"""


def _require_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise ConfigError(f"{name}が.envに設定されていません。.env.exampleを参考に設定してください。")
    return value


def get_allowed_players() -> frozenset[str]:
    """許可リスト(ALLOWED_PLAYERS)を取得する。呼び出しのたびに.envから再読み込みする。

    許可リストが1名だけの場合、その人物=配信者本人であることが自明なため、
    ダッシュボード上でプレイヤー名自体を表示しない簡略表示に切り替える判定
    (呼び出し側でlen()==1を見る)にも使う。
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


def get_capture_fps() -> float:
    """実キャプチャのfps(状態機械の閾値スケーリングに使用)を取得する。

    未設定時はConfigErrorを送出する(Issue #255)。OBS Virtual Cameraの実際の
    出力fpsは環境ごとに異なりうる(30fps想定で運用していたが実測60fpsだった
    ケースがあり、フレーム数ベースの各種デバウンス閾値が想定の半分の実時間で
    条件を満たしてしまう不具合の原因になった)ため、.envで明示させる。
    main.py側で`--fps`未指定時のみこの値を使う(`--fps`指定時はそちらを優先)。
    """
    return float(_require_env("CAPTURE_FPS"))


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


def _validate_rank_graph_match_limit(raw: str) -> Optional[int]:
    raw = raw.strip()
    if raw == "all":
        return None
    try:
        value = int(raw)
    except ValueError:
        raise ConfigError(
            f"RANK_GRAPH_MATCH_LIMITの値が不正です: {raw}(allまたは正の整数を指定してください)"
        )
    if value <= 0:
        raise ConfigError(f"RANK_GRAPH_MATCH_LIMITの値が不正です: {raw}(正の整数を指定してください)")
    return value


def get_rank_graph_match_limit() -> Optional[int]:
    """ランク推移グラフの対象範囲(直近何試合分か)を取得する。

    値が`"all"`の場合はNone(全期間を表示する)を返す。それ以外は正の整数として
    解釈を試み、数値化できない値・0以下の値・空文字列はConfigErrorを送出する
    (Issue #126、モジュールdocstring参照)。
    """
    return _validate_rank_graph_match_limit(_require_env("RANK_GRAPH_MATCH_LIMIT"))


def _validate_rank_delta_distribution_scope(value: str) -> str:
    if value not in _VALID_RANK_DELTA_DISTRIBUTION_SCOPES:
        raise ConfigError(
            f"RANK_DELTA_DISTRIBUTION_SCOPEの値が不正です: {value}"
            f"({'/'.join(_VALID_RANK_DELTA_DISTRIBUTION_SCOPES)}のいずれかを指定してください)"
        )
    return value


def get_rank_delta_distribution_scope() -> str:
    """勝敗別ランク増減分布(箱ひげ図)の集計対象を取得する。

    "session"(現在の配信セッションのみ)/"all"(累計・全期間)のいずれか。
    未設定・不正な値の場合はConfigErrorを送出する(GOAL_RECORD_MODEと同じ扱い、
    空文字列は許容しない)。
    """
    return _validate_rank_delta_distribution_scope(_require_env("RANK_DELTA_DISTRIBUTION_SCOPE"))


def get_obs_websocket_host() -> str:
    """obs-websocketの接続先ホストを取得する。未設定時はConfigErrorを送出する。"""
    return _require_env("OBS_WEBSOCKET_HOST")


def get_obs_websocket_port() -> int:
    """obs-websocketの接続先ポートを取得する。未設定時はConfigErrorを送出する。"""
    return int(_require_env("OBS_WEBSOCKET_PORT"))


def get_obs_websocket_password() -> str:
    """obs-websocketの接続パスワードを取得する。

    値が`"none"`の場合は空文字列(認証無効)を返す。それ以外は実際のパスワードと
    してそのまま返す。未設定・空文字列はConfigErrorを送出する(Issue #126、
    モジュールdocstring参照)。
    """
    raw = _require_env("OBS_WEBSOCKET_PASSWORD")
    if raw == "none":
        return ""
    return raw


def get_obs_scene_in_match() -> str:
    """試合中(VS画面確定〜試合結果確定)に切り替えるOBSシーン名を取得する。未設定時はConfigErrorを送出する。"""
    return _require_env("OBS_SCENE_IN_MATCH")


def get_obs_scene_between_matches() -> str:
    """試合と試合の間に切り替えるOBSシーン名を取得する。未設定時はConfigErrorを送出する。"""
    return _require_env("OBS_SCENE_BETWEEN_MATCHES")


def get_obs_browser_source_names() -> tuple[str, ...]:
    """起動時(OBS接続成功直後)に再読み込みするOBSブラウザソース名を取得する(Issue #247)。

    値が`"none"`の場合は再読み込み対象なし(空タプル)として扱う
    (OBS_WEBSOCKET_PASSWORDと同じパターン)。それ以外はカンマ区切りのソース名一覧
    として解釈する(ALLOWED_PLAYERSと同じ、前後の空白は除去し空要素は無視する)。
    未設定・空文字列はConfigErrorを送出する。
    """
    raw = _require_env("OBS_BROWSER_SOURCE_NAMES")
    if raw == "none":
        return ()
    return tuple(name.strip() for name in raw.split(",") if name.strip())


def _validate_obs_scene_switching_enabled(value: str) -> str:
    if value not in _VALID_BOOL_STRINGS:
        raise ConfigError(
            f"OBS_SCENE_SWITCHING_ENABLEDの値が不正です: {value}({'/'.join(_VALID_BOOL_STRINGS)}のいずれかを指定してください)"
        )
    return value


def get_obs_scene_switching_enabled() -> bool:
    """OBSシーン自動切替(ObsSceneController.set_in_match)を実際に行うかどうかを取得する(Issue #248)。

    無効化してもOBSへの接続自体(ブラウザソース再読み込み等)は維持したまま、
    シーン切替の呼び出しだけをスキップする。呼び出しのたびに.envから
    再読み込みする(ALLOWED_PLAYERS等と同じ、/adminからの変更を検知ループの
    再起動なしに反映するため)。未設定・不正な値の場合はConfigErrorを送出する。
    """
    return _validate_obs_scene_switching_enabled(_require_env("OBS_SCENE_SWITCHING_ENABLED")) == "true"


def _validate_goal_record_mode(value: str) -> str:
    if value not in _VALID_GOAL_RECORD_MODES:
        raise ConfigError(
            f"GOAL_RECORD_MODEの値が不正です: {value}({'/'.join(_VALID_GOAL_RECORD_MODES)}のいずれかを指定してください)"
        )
    return value


def get_goal_record_mode() -> str:
    """ゴール/アシストをDBに記録する際の許可リストの扱いモードを取得する。

    "all"(許可リストに関係なく全員記録)/"allowlist"(どちらかが許可リストに
    いれば両方そのまま記録)/"allowlist_redact"(どちらかが許可リストにいれば
    記録するが、許可リスト外の名前はNULLにする)のいずれか。未設定・不正な
    値の場合はConfigErrorを送出する(database.db.save_goal参照)。
    """
    return _validate_goal_record_mode(_require_env("GOAL_RECORD_MODE"))


_EDITABLE_ENV_KEYS = (
    "ALLOWED_PLAYERS",
    "GOAL_RECORD_MODE",
    "RANK_GRAPH_MATCH_LIMIT",
    "RANK_DELTA_DISTRIBUTION_SCOPE",
    "OBS_SCENE_SWITCHING_ENABLED",
)


def get_editable_settings() -> dict[str, str]:
    """管理画面(/admin)で表示・編集する対象5項目の現在値を返す。"""
    return {key: os.environ.get(key, "") for key in _EDITABLE_ENV_KEYS}


def update_editable_settings(values: dict[str, str]) -> None:
    """管理画面(/admin)からの更新をos.environ・.envの両方に反映する。

    キーは_EDITABLE_ENV_KEYSの5つ全てが必須。ALLOWED_PLAYERS以外は各get_xxx()と
    同じバリデーション関数を通し、いずれか1つでも不正ならConfigErrorを送出して
    何も更新しない(部分適用を避けるため、書き込み前に全項目を検証する)。
    ALLOWED_PLAYERSはカンマ区切りの自由記述でフォーマット上の制約が無いため
    検証しない(空文字列も「誰も記録しない」という有効な値、モジュールdocstring参照)。
    """
    _validate_goal_record_mode(values["GOAL_RECORD_MODE"])
    _validate_rank_graph_match_limit(values["RANK_GRAPH_MATCH_LIMIT"])
    _validate_rank_delta_distribution_scope(values["RANK_DELTA_DISTRIBUTION_SCOPE"])
    _validate_obs_scene_switching_enabled(values["OBS_SCENE_SWITCHING_ENABLED"])

    dotenv_path = find_dotenv()
    for key in _EDITABLE_ENV_KEYS:
        os.environ[key] = values[key]
        set_key(dotenv_path, key, values[key])
