"""他競技をプレイしている間、検知処理を手動で一時停止するための、DBを経由しないインメモリ状態(Issue #440)。

Issue #433(テニスのVS画面をサッカーの試合開始と誤検知)への対応として、検知ロジック側に
「サッカーかどうか」を判定する仕組みを増やすのではなく、配信者が他競技に切り替えている間は
`/admin`から検知処理そのものを手動で止められるようにする(ユーザーとの相談で決定)。

`room_type`(Issue #358)と同じく`.env`へは永続化せず、プロセス起動のたびに必ずFalse
(一時停止していない)にリセットされる。ただし`room_type`と異なり起動確認ゲート
(`startup_gate.py`)の対象には含めない。配信中に何度もサッカー⇄他競技を行き来する想定のため、
起動時に1回選べば足りる`room_type`とは性質が違う(いつでも即座に切り替えられる必要がある)。

`main.py`はメインループで`is_paused()`がTrueの間、`machine.process_frame(...)`以下
(記録・クリップ録画・OBSシーン切替)を丸ごとスキップする。`FfmpegFrameReader.read()`自体は
止めない(ffmpeg側のパイプが詰まらないよう、読み続けて捨てるだけ)。

再開時に`MatchStateMachine`を作り直すことはしない。作り直すと`_make_match_state_machine()`が
呼び出しごとに新しい`ProcessPoolExecutor`を2つ生成しOCRのコールドスタート(実測3.8〜7秒)を
再度払う上、古いExecutorを明示的にshutdownしないとリークする。加えて`session_match_no`
(「n試合目」のカウンタ)は`MatchStateMachine`内部のprivate変数で外から引き継げず、作り直すと
試合番号が1試合目からリセットされてしまう。これらの副作用を避けようとすると
`MatchStateMachine`側への変更が必要になり、「検知ロジックを増やしたくない」という今回の目的から
外れるため、再開時は何もせずそのまま使い続ける方針にした(ユーザーとの相談で決定)。
`MatchStateMachine`の内部状態はすべて時間ベースのデバウンス/ロックアウトのみで構成されている
ため、最悪でも「VS画面ロックアウト(既定30秒)が一時停止直前の残り時間だけ余分に効く」程度の
自己修復するズレに留まり、記録の誤りには繋がらない。

`youtube_chat.py`の`DiveTimeState`・`match_transition.py`と同じ「DBを経由しない
一過性のインメモリ状態」パターン。`main.py`側の読み取り(メインループ)と
`web/server.py`側の書き込み(uvicornのスレッドプール)が別スレッドのため、
`match_transition.py`と同じくロックで保護する。
"""

import threading

_lock = threading.Lock()
_paused = False


def is_paused() -> bool:
    """検知処理が一時停止中かどうかを返す(プロセス起動直後は常にFalse)。"""
    with _lock:
        return _paused


def set_paused(value: bool) -> None:
    """`/admin`からの一時停止トグル切り替えを反映する。"""
    global _paused
    with _lock:
        _paused = value
