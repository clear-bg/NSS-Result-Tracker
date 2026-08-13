"""試合終了→試合間シーンへの突入を知らせる、DBを経由しないインメモリ信号(Issue #361)。

main.pyのmachine.in_matchがTrue→Falseに変わった瞬間(試合が終わり、OBSが試合中
シーンから試合間シーンへ切り替わるのとまったく同じタイミング、obs_control.
ObsSceneController.set_in_match()を呼んでいるのと同じ箇所)にnotify_between_matches()
を呼び、モジュールレベルのエポックカウンタを1つ進める。

/overlay/rank-graphのようなSVGウィジェットは、レンダリングのたびに
get_between_matches_epoch()の値をHTMLへ埋め込む。ブラウザ側
(static/overlay-refresh.js)は定期ポーリングのたびにこの値を前回値と比較し、
変化していれば「ちょうど試合間に入った直後」とみなして登場アニメーションを
再生する(overlay-refresh.jsのdata-animate-on-change="signal"モード参照)。
値自体に意味は無く、「前回ポーリング時から変化したかどうか」だけが重要なため、
単純にインクリメントするだけの整数にしている。

OBSのブラウザソースはシーン切り替わり自体をページへ通知する仕組みを持たない
(ページは常時裏で動き続けてポーリングしているだけ、Issue #104)ため、
「実際の切り替わり」と「ページがそれに気づく瞬間」の間には最大でもポーリング
間隔分のずれが生じる。これを縮めるため、/overlay/rank-graphのポーリング間隔は
他ウィジェットの既定5秒より短い500msを使う(web/server.pyの
_RANK_GRAPH_REFRESH_INTERVAL_MS参照。OBSの実ブラウザソースに
window.obsstudioが注入されるか確認したが、実機で`undefined`だったため
利用できないことを確認済み)。

youtube_chat.pyのDiveTimeStateと同じ「DBを経由しない一過性のインメモリ状態」
パターン(main.pyのプロセス起動ごとに0からリセットされる)。main.py側の
書き込みスレッドとweb/server.py側の読み取りスレッド(uvicornのスレッドプール)が
別スレッドのため、DiveTimeStateと同じくロックで保護する。

信号自体はランク推移グラフ専用にはせず、将来他のウィジェット(得点/アシスト・
勝率、直近試合結果ログ、勝敗別ランク増減分布等)が同じ仕組みに乗れるよう汎用に
保つ(Issue #361のユーザー確認済み決定事項)。
"""

import threading

_lock = threading.Lock()
_between_matches_epoch = 0


def notify_between_matches() -> None:
    """試合が終わり試合間に入ったことを通知する(in_matchがTrue→Falseになった瞬間に呼ぶ)。"""
    global _between_matches_epoch
    with _lock:
        _between_matches_epoch += 1


def get_between_matches_epoch() -> int:
    """現在のエポック値を返す。試合間に入るたびに1ずつ増える、値自体に意味の無い通し番号。"""
    with _lock:
        return _between_matches_epoch
