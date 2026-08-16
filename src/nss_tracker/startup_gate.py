"""起動時の設定確認ゲート(Issue #379)。

自分の性格上、システムを起動できたこと自体に満足してしまい、専用部屋で遊ぶ日でも
`room_type`を`random`のまま起動しっぱなしにしてしまう、といった設定忘れが実際に
起きていた。これを防ぐため、起動直後は`/admin`・`/rank-entry`のWebダッシュボード
だけを開き(main.pyの`webbrowser.open()`まで)、OBS Virtual Camera・OBS
(obs-websocket)・YouTube連携への実際の接続は、`/admin`で「確認完了」ボタンを
押すまで一切行わない。main.pyは`wait_for_confirmation()`でこれらの接続を始める
直前をブロックする(タイムアウトは設けない。配信を始めるまでいくら待たされても
実害が無いため)。

対象は`config.get_room_type()`(Issue #358)と`OBS_SCENE_SWITCHING_ENABLED`
(`_EDITABLE_ENV_KEYS`の一括フォーム、`web/server.py`の`admin_update`)の2項目。
どちらも起動のたびに「未選択」から始まり(`web/templates/admin.html`側で
初期表示を空欄にする)、`/admin`で明示的に選択・送信されるまで「確認完了」
ボタン自体をdisabledにする(機械的に押してしまうリスクを下げるため、エラー
表示で弾く方式は採らない、ユーザーとの相談で決定)。`confirm_start()`側にも
同じ条件のチェックを持たせているのは、disabled属性をバイパスして直接POSTされた
場合の防御(defense in depth)。

`match_transition.py`・`youtube_chat.py`の`DiveTimeState`と同じ「DBを経由しない
一過性のインメモリ状態」パターン(main.pyのプロセス起動ごとに0からリセットされる)。
"""

import threading

from nss_tracker.config import ConfigError, get_room_type

_lock = threading.Lock()
_obs_scene_switching_confirmed = False
_confirmed_event = threading.Event()


def mark_obs_scene_switching_confirmed() -> None:
    """OBS_SCENE_SWITCHING_ENABLEDが/adminの一括フォーム経由で明示的に送信されたことを記録する。

    フォームの<select>は初期表示が空欄(プレースホルダー)のため、送信できた
    時点でユーザーが明示的にtrue/falseを選んだことを意味する(web/server.pyの
    admin_update参照)。
    """
    global _obs_scene_switching_confirmed
    with _lock:
        _obs_scene_switching_confirmed = True


def is_obs_scene_switching_confirmed() -> bool:
    with _lock:
        return _obs_scene_switching_confirmed


def can_confirm_start() -> bool:
    """「確認完了」ボタンを有効化してよいかどうかを返す。

    room_type・OBS_SCENE_SWITCHING_ENABLEDの両方が今回の起動で明示的に
    選択済みであることが条件。
    """
    return get_room_type() is not None and is_obs_scene_switching_confirmed()


def confirm_start() -> None:
    """「確認完了」ボタン押下を反映し、main.py側のwait_for_confirmation()のブロックを解除する。

    can_confirm_start()がFalseの場合はConfigErrorを送出する(disabled属性を
    バイパスして直接POSTされた場合の防御)。既に確認済みの場合は何もしない(冪等)。
    """
    if _confirmed_event.is_set():
        return
    if not can_confirm_start():
        raise ConfigError("room_typeとOBS_SCENE_SWITCHING_ENABLEDの両方を選択してから確認してください")
    _confirmed_event.set()


def is_confirmed() -> bool:
    return _confirmed_event.is_set()


def wait_for_confirmation() -> None:
    """確認完了まで無期限にブロックする(main.pyがOBS/YouTube接続を始める直前に呼ぶ)。"""
    _confirmed_event.wait()
