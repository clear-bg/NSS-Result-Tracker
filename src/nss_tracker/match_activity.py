"""試合の処理中かどうかを、検知ループからWebサーバーへ伝えるインメモリ状態(Issue #489)。

記録漏れ試合の手動追加(`/match-entry`)は、挿入位置以降の試合IDを1つずつ後ろへずらす。
検知ループはその間も試合IDをメモリ上に持ったまま処理を続けることがあるため、
試合の処理中にIDをずらすと、ずれた後の別の試合にクリップ等が紐付いてしまう。
具体的には、試合結果をDBへ保存してから暗転でクリップの録画を終えるまでの間
(`main.py`の`pending_clip_match_id`)と、録画を終えてからバックグラウンドで
`{match_id}.mp4`を書き出し終えるまでの間がこれに当たる。

そこで`main.py`のメインループが毎フレーム「処理中か」を書き込み、`/match-entry`は
処理中の間は追加を受け付けない(ユーザーとの相談で決定:「試合中は実行させない」)。
処理中とみなすのは次のいずれかの間:

- `MatchStateMachine.in_match`がTrue(VS画面確定〜試合間シーンに戻るまで)
- ランク入力用クリップを録画中、またはその書き出し中

`main.py`を起動せずWebサーバーだけを動かしている場合(`scripts/run_web_dashboard.py`)は
常にFalseのまま(検知ループが無いため、IDをずらしても困る相手がいない)。

`detection_pause.py`・`virtual_camera_status.py`と同じ「DBを経由しない一過性の
インメモリ状態」パターン。書き込み(メインループ)と読み取り(uvicornのスレッドプール)が
別スレッドのため、ロックで保護する。
"""

import threading

_lock = threading.Lock()
_in_progress = False


def set_in_progress(value: bool) -> None:
    """試合の処理中かどうかを書き込む(main.pyのメインループが毎フレーム呼ぶ)。"""
    global _in_progress
    with _lock:
        _in_progress = value


def is_in_progress() -> bool:
    """試合の処理中ならTrue(プロセス起動直後・検知ループが無い場合はFalse)。"""
    with _lock:
        return _in_progress
