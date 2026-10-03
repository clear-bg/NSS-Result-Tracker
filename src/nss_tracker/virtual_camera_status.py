"""OBS Virtual Cameraから実際の映像が届いているかの監視と、その状態のインメモリ保持(Issue #470)。

OBSの「仮想カメラ開始」を押し忘れると、試合が1つも記録されないまま配信が終わってしまう。
停止中の仮想カメラはプレースホルダー画像を流し続けるため(`detection/virtual_camera.py`参照)、
ffmpegもメインループも普通に動き続け、ターミナルにも`/admin`にも何も出なかった。

`main.py`のメインループが毎フレーム`VirtualCameraMonitor.observe()`を呼び、
プレースホルダー画像が`inactive_after_seconds`続いたら「映像が来ていない」とみなして
WARNINGを出す。試合を記録していない間はログがほとんど流れないため、その時点の
ターミナルの最下行にこの警告が残る。ただしDEBUGレベルで動かしているとハートビート等で
流れてしまうため、映像が来ない間は`repeat_warning_seconds`ごとに出し直す。
実際の映像が届いたらINFOで知らせ、状態を戻す。

検知の一時停止(Issue #440)中も監視は続ける(映像の有無は検知を止めているかどうかと無関係なため)。

状態は`/admin`にも表示する(`web/server.py`の`/api/virtual-camera-status`)。
Issue #379では「接続結果は`/admin`に表示せず、ターミナルのログだけで確認する」と
決めていたが、仮想カメラの押し忘れはこの項目に限ってブラウザ側でも分かるようにしたい
というユーザーの要望で、この項目についてだけ覆した。

`detection_pause.py`・`match_transition.py`と同じ「DBを経由しない一過性のインメモリ状態」
パターン。`main.py`側の書き込み(メインループ)と`web/server.py`側の読み取り
(uvicornのスレッドプール)が別スレッドのため、ロックで保護する。
"""

import logging
import threading
from typing import Literal, Optional

import numpy as np

from nss_tracker.detection.virtual_camera import is_virtual_camera_placeholder

logger = logging.getLogger("nss_tracker.virtual_camera")

# waiting: まだ判定できていない(起動直後・プレースホルダーが続いてまだ間もない)
# active: 実際の映像が届いている
# inactive: プレースホルダー画像が一定時間続いている(仮想カメラが停止中)
VirtualCameraStatus = Literal["waiting", "active", "inactive"]

# プレースホルダー画像がこの秒数続いたら「映像が来ていない」とみなす。
# 一瞬だけ似た画面が出ても警告しないためのデバウンス
DEFAULT_INACTIVE_AFTER_SECONDS = 5.0
# 映像が来ない間、警告を出し直す間隔(秒)。DEBUGログが流れていても
# ターミナルの最下行付近に警告が残るようにするため
DEFAULT_REPEAT_WARNING_SECONDS = 30.0

_lock = threading.Lock()
_status: VirtualCameraStatus = "waiting"


def get_status() -> VirtualCameraStatus:
    """現在の仮想カメラの状態を返す(プロセス起動直後は常に"waiting")。"""
    with _lock:
        return _status


def _set_status(value: VirtualCameraStatus) -> None:
    global _status
    with _lock:
        _status = value


def reset_status() -> None:
    """状態を起動直後("waiting")に戻す(テスト用)。"""
    _set_status("waiting")


class VirtualCameraMonitor:
    """フレームを見て仮想カメラの状態を更新し、変化をログに出す。"""

    def __init__(
        self,
        inactive_after_seconds: float = DEFAULT_INACTIVE_AFTER_SECONDS,
        repeat_warning_seconds: float = DEFAULT_REPEAT_WARNING_SECONDS,
    ) -> None:
        self._inactive_after_seconds = inactive_after_seconds
        self._repeat_warning_seconds = repeat_warning_seconds
        # プレースホルダー画像が連続し始めた時刻。実際の映像を見たらNoneに戻す
        self._placeholder_since: Optional[float] = None
        self._last_warned_at: Optional[float] = None

    def observe(self, frame: np.ndarray, now: float) -> None:
        if not is_virtual_camera_placeholder(frame):
            self._placeholder_since = None
            self._last_warned_at = None
            if get_status() != "active":
                _set_status("active")
                logger.info("OBS Virtual Cameraからの映像を受信しています")
            return

        if self._placeholder_since is None:
            self._placeholder_since = now
        elapsed = now - self._placeholder_since
        if elapsed < self._inactive_after_seconds:
            return
        _set_status("inactive")
        if self._last_warned_at is None or now - self._last_warned_at >= self._repeat_warning_seconds:
            self._last_warned_at = now
            logger.warning(
                "OBS Virtual Cameraから映像が来ていません(%.0f秒間)。"
                "OBSの「仮想カメラ開始」を押し忘れていないか確認してください"
                "(開始すれば再起動しなくても自動で検知を始めます)",
                elapsed,
            )
