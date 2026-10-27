"""`/admin`からのシステム終了要求を、Webサーバーのスレッドから検知ループ(main.py)へ伝えるインメモリ状態(Issue #485)。

これまでシステムを止める方法はターミナルでのCtrl+Cしかなかったが、配信中はブラウザ(`/admin`)で
操作していることが多いため、ブラウザから終了できるようにした。

Webサーバー(uvicorn)は別スレッドで動いているため、リクエストハンドラ(`web/server.py`の
`admin_shutdown`)はここで終了を要求するだけで、プロセスを直接落とさない。実際の後始末
(クリップの破棄・`reader.stop()`・ProcessPoolExecutorの停止・OBS/YouTube連携の停止・
Webサーバーの停止)は、Ctrl+Cのときと同じ経路でメインスレッドが行う。main.pyは
検知ループの毎フレームと、起動確認ゲート(`startup_gate.wait_for_confirmation()`)の
待機中の両方でこの要求を確認する。

`detection_pause.py`・`startup_gate.py`と同じ「DBを経由しない一過性のインメモリ状態」
パターン(プロセス起動ごとに未要求から始まる)。一度要求したら取り消せない。
"""

import threading

_requested_event = threading.Event()


def request_shutdown() -> None:
    """終了を要求する。既に要求済みの場合は何もしない(冪等)。"""
    _requested_event.set()


def is_requested() -> bool:
    return _requested_event.is_set()
