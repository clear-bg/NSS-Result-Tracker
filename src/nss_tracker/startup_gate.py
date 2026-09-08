"""起動時の設定確認ゲート(Issue #379)。

自分の性格上、システムを起動できたこと自体に満足してしまい、専用部屋で遊ぶ日でも
`room_type`を`random`のまま起動しっぱなしにしてしまう、といった設定忘れが実際に
起きていた。これを防ぐため、起動直後は`/admin`・`/rank-entry`のWebダッシュボード
だけを開き(main.pyの`webbrowser.open()`まで)、OBS Virtual Camera・OBS
(obs-websocket)・YouTube連携への実際の接続は、`/admin`で「確認完了」ボタンを
押すまで一切行わない。main.pyは`wait_for_confirmation()`でこれらの接続を始める
直前をブロックする(タイムアウトは設けない。配信を始めるまでいくら待たされても
実害が無いため)。

対象は`config.get_room_type()`(Issue #358)と`OBS_SCENE_SWITCHING_ENABLED`の2項目。
どちらも起動のたびに「未選択」から始まり(`web/templates/admin.html`側で初期表示を
空欄にする)、`/admin`のフォーム(`web/server.py`の`admin_update`)で明示的に選択・
送信されるまでゲートを通過できない。

Issue #410: 当初は「確認完了」ボタン自体をdisabledにしていた(機械的に押して
しまうリスクを下げるため、エラー表示で弾く方式は採らない、という判断)。しかし
そのままだと起動のたびに3回フォームを送信する必要があり操作が煩わしかったため、
`/admin`のフォームを1つに統合し、ボタンは常に押せる・未選択の項目はその直下に
エラーを表示する方式へ変更した(ユーザーとの相談で決定)。`can_confirm_start()`は
`confirm_start()`側のチェックとして引き続き使い、未選択のまま接続が始まらない
ことを担保する(`web/server.py`側でも同じ条件を先に判定しているため、こちらは
フォームをバイパスして直接POSTされた場合の防御(defense in depth)にあたる)。

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

    can_confirm_start()がFalseの場合はConfigErrorを送出する(Issue #410以降は
    /adminのフォーム側で先に未選択を判定してエラー表示するため、こちらは
    フォームをバイパスして直接POSTされた場合の防御)。既に確認済みの場合は
    何もしない(冪等)。
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
