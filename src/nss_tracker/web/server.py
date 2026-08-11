"""配信画面向けWebダッシュボードのFastAPIアプリ。

Issue #80(技術検証PoC)ではヘルスチェックとDB読み取りが動くことの確認用に
JSON APIのみを最小限で実装した。Issue #81ではこれに加えて、値が実際に
読めることをブラウザ(OBSのブラウザソース含む)で目視確認できる最小限の
HTMLページ(`/`)を追加した。

実際の表示ウィジェットはIssue #92(表示内容の確定)配下のsub-issueごとに
`/overlay/xxx`という個別のURLで追加していく想定(#83のOBSシーン切り替えで
ウィジェットごとにbrowser sourceを分けて配置できるようにするため)。現段階では
装飾はせず、読めれば良いレベルのプレーンテキストで実装し、必要に応じて後から
見た目を磨く(Issue #94、勝率ウィジェットが最初の例)。

HTML/CSSはPython文字列に埋め込まず、`web/templates/`(Jinja2、`FastAPI`標準の
`Jinja2Templates`)・`web/static/`(`StaticFiles`でマウント)に分離する(Issue #94、
当初はPython文字列埋め込みだったが、今後グラフ等JSが絡むウィジェットが増える前に
切り替えた)。ビジネスロジック(勝率計算等)はPython側に残し、テンプレート側は
値を並べるだけの薄いものにする。

`/overlay/xxx`は全てOBSの「ブラウザソース」として、ゲーム画面ワイプの
空きスペースに他の部品と重ねて配置される想定のため、`static/overlay.css`で
html/bodyの背景を明示的に`transparent`にする。CSSで背景色を何も指定しないと
レンダラーのデフォルト挙動に依存してしまい、狙って透過にしているわけではない
(OBSのブラウザソースはbrowser source自体に指定した幅×高さの矩形をまるごと
キャプチャするため、意図せず不透明になると文字の無い部分も含めてその矩形全体が
背後の他の部品を隠してしまう)。値確認用の`/`ページはOBSへの配置を想定していない
(通常のブラウザで見る用)ため、この透過スタイルは適用しない。

Issue #129: `/admin`は配信中に調整したくなり得る5項目(ALLOWED_PLAYERS・
GOAL_RECORD_MODE・RANK_GRAPH_MATCH_LIMIT・RANK_DELTA_DISTRIBUTION_SCOPE・
OBS_SCENE_SWITCHING_ENABLED(Issue #248)、`config.py`の`_EDITABLE_ENV_KEYS`参照)
をブラウザから編集する管理画面。`/`と同様にOBSへの配置を想定しないため
`overlay.css`の透過スタイルは使わない。
POST後は同じ`/admin`へのリダイレクト(PRGパターン)で結果(成功/エラー文言)を
クエリパラメータ経由で表示し、ブラウザの再読み込みで二重送信されないようにする。

Issue #257: `/admin`には上記設定フォームに加え、各`/overlay/xxx`ウィジェットへの
リンク一覧も表示する(OBSのブラウザソースごとに分かれているURLへ、動作確認等で
すぐアクセスできるようにするため)。リンク先はテンプレートにハードコードせず、
`_overlay_widget_links()`が実際に登録された`/overlay/xxx`ルート(`app.routes`)から
機械的に集める。表示ラベルのみ`_OVERLAY_WIDGET_LABELS`で個別に管理し、新しい
overlayルートを追加した際にラベルの追記を忘れるとアプリ起動時にRuntimeErrorで
気づける設計にした。

Issue #306: `matches.rank_after`(ランク変動確定後の値)は、GRACEフェーズの
自動OCR(`detection/rank_ocr.py`)の精度・パフォーマンス面の限界(Issue #287〜#303
参照)から、手動入力に切り替えた。`/rank-entry`は`/admin`とは別ページとして
用意し(`main.py`起動時に別ブラウザで自動的に開く想定)、未確定(`rank_after`が
NULL)の試合のうち最も古い1件を表示・入力させる(`database.db.
fetch_oldest_pending_manual_rank_match`)。`/admin`と同じPRGパターンで実装する。
未確定一覧のキュー表示・rank_beforeのチェーン解決等は範囲外(Issue #308で扱う)。
自動検知(OCR)自体は引き続きバックグラウンドで動き続け、読み取れた値は
比較用の`rank_after_ocr`列に保存される(`database.db.save_match_result`参照)。

エンドポイントごとに新規のsqlite3コネクションを開いて処理後すぐ閉じる。
sqlite3のコネクションはデフォルトでは開いたスレッド以外から使えず
(check_same_thread=True)、FastAPI/uvicornは同期defのエンドポイントを
スレッドプール上の任意のスレッドで実行するため、検知ループ側のコネクション
(database.db.connect())を使い回すことはできない。SQLiteは複数の読み取り専用
コネクションが同時に存在すること自体は問題ないため、リクエストごとに開閉する
方式で十分。

Issue #104: 全`/overlay/xxx`ページは`static/overlay-refresh.js`により自動更新される。
OBSのブラウザソースは一度読み込むと明示的にリロードしない限り表示が固定されるため、
ページ自身のURLを定期的にfetchし直し`<body>`だけを差し替える(ページ遷移を伴う
`<meta http-equiv="refresh">`は配信中に一瞬白塗き/点滅するリスクがあるため不採用、
ユーザーとの相談で決定)。検知ループ→Webサーバーへの直接イベント通知は行わず、
あくまでDB経由のポーリングのみで実現する(#83実装以降も含め、全ウィジェットで
更新方式を統一するため)。更新間隔は`_OVERLAY_REFRESH_INTERVAL_MS`(既定5秒)だが、
対戦相手ランク比較([#100](../issues/100))のみVS画面確定後できるだけ早く反映してほしい
という要望から`_VS_RANK_COMPARISON_REFRESH_INTERVAL_MS`(1秒)を使う。

Issue #259: 全`/overlay/xxx`ページは、クエリパラメータ`?debug_bg=1`(値は問わず有無のみ判定、
`_overlay_debug_bg_style()`参照)が付いている場合だけ`<body>`の背景を黒にする。OBSの
ブラウザソースが実際に使うURLにはこのパラメータを付けないため、配信時の見た目(透過)には
影響しない。`/admin`のoverlayリンク一覧(#257)はこのパラメータ付きのURLにすることで、
通常のブラウザで開いても白文字(overlay.cssのcolor: #fff)が読めるようにする。
"""

import logging
import math
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Optional
from urllib.parse import quote

from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from nss_tracker import youtube_chat
from nss_tracker.config import (
    ConfigError,
    get_allowed_players,
    get_editable_settings,
    get_rank_delta_distribution_scope,
    get_rank_graph_match_limit,
    is_allowed_player,
    update_editable_settings,
)
from nss_tracker.database.db import (
    fetch_all_matches,
    fetch_current_session_id,
    fetch_goals_for_session,
    fetch_latest_rank_after,
    fetch_latest_vs_rank_snapshot,
    fetch_match,
    fetch_matches_for_session,
    fetch_max_rank_after,
    fetch_oldest_pending_manual_rank_match,
    fetch_pending_manual_rank_match_count,
    fetch_recent_matches,
    fetch_vs_rank_snapshot_slots,
    save_manual_rank_after,
)
from nss_tracker.rank_entry_clips import DEFAULT_CLIPS_DIR, GAUGE_CLIPS_DIR

_WEB_DIR = Path(__file__).parent
_TEMPLATES = Jinja2Templates(directory=_WEB_DIR / "templates")
_logger = logging.getLogger("nss_tracker.web")

# Issue #104: 全overlay系ページ共通の自動更新間隔(ミリ秒)。static/overlay-refresh.js
# のdata-interval-ms属性に渡す。対戦相手ランク比較([#100](../issues/100))のみ、
# VS画面確定後できるだけ早く新しい試合の値に切り替わってほしいというユーザーの
# 要望から、他より短い間隔にしている(検知ループ→Webサーバーへの直接イベント
# 通知は行わずDB経由のポーリングのみで実現するため、間隔を短くすることで
# 「ほぼ即時」に近づける方針。#104のモジュールdocstring・issue参照)
_OVERLAY_REFRESH_INTERVAL_MS = 5000
_VS_RANK_COMPARISON_REFRESH_INTERVAL_MS = 1000


def _connect(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def _fetch_matches_count(db_path: Path, session_id: Optional[int] = None) -> dict:
    """試合数・勝ち数・負け数・引き分け数を集計する。

    session_idを指定すると、そのmatches.session_idに絞り込んだ集計になる
    (Issue #94の「配信セッション単位」の勝率表示用)。省略時は累計(全件)。
    """
    conn = _connect(db_path)
    try:
        query = (
            "SELECT COUNT(*) AS total, "
            "SUM(CASE WHEN result = 'win' THEN 1 ELSE 0 END) AS win, "
            "SUM(CASE WHEN result = 'lose' THEN 1 ELSE 0 END) AS lose, "
            "SUM(CASE WHEN result = 'draw' THEN 1 ELSE 0 END) AS draw "
            "FROM matches"
        )
        params: tuple = ()
        if session_id is not None:
            query += " WHERE session_id = ?"
            params = (session_id,)
        row = conn.execute(query, params).fetchone()
    finally:
        conn.close()
    return {
        "total": row["total"] or 0,
        "win": row["win"] or 0,
        "lose": row["lose"] or 0,
        "draw": row["draw"] or 0,
    }


_EMPTY_COUNTS = {"total": 0, "win": 0, "lose": 0, "draw": 0}


def _fetch_winrate(db_path: Path) -> dict:
    """配信セッション単位・累計それぞれの試合数・勝ち数・負け数・引き分け数を返す。

    「現在の配信セッション」はsessionsテーブルの最新行(db.fetch_current_session_id、
    main.pyのプロセス起動ごとに1行作られる前提)。セッションが1件も無い場合
    (main.py未起動でDBのみ閲覧している場合等)はsessionを空の集計として返す。
    """
    conn = _connect(db_path)
    try:
        current_session_id = fetch_current_session_id(conn)
    finally:
        conn.close()

    session_counts = (
        _fetch_matches_count(db_path, session_id=current_session_id)
        if current_session_id is not None
        else dict(_EMPTY_COUNTS)
    )
    cumulative_counts = _fetch_matches_count(db_path)
    return {"session": session_counts, "cumulative": cumulative_counts}


def _win_rate_percent(counts: dict) -> Optional[float]:
    """勝率(%)を計算する。試合数0の場合はNoneを返す(表示側で「-」等にする)。"""
    if counts["total"] == 0:
        return None
    return round(counts["win"] / counts["total"] * 100, 1)


def _format_win_rate_text(counts: dict) -> str:
    win_rate = _win_rate_percent(counts)
    return f"{win_rate}%" if win_rate is not None else "-"


# Issue #95: ランク推移グラフの対象範囲は「直近N試合」(配信セッションをまたぐ)。
# #94(勝率)・#96/#98(ゴール/アシスト・連勝連敗)は配信セッション単位に絞ったが、
# ランクは長期的な推移を見たい用途のため別の集計単位にした(ユーザーとの相談で決定)。
# 具体的な件数は.envのRANK_GRAPH_MATCH_LIMITで指定する(未設定/空欄なら全期間)


def _fetch_rank_history(db_path: Path) -> list[dict]:
    """ランク推移グラフ描画に必要な値だけを古い順で返す。

    対象範囲はconfig.get_rank_graph_match_limit()に従う(Noneなら全期間、
    数値ならその件数分の直近の試合のみ)。rank_afterがNULL(ランク読み取り失敗)の
    試合はグラフに描画しようがないため除外する。
    """
    limit = get_rank_graph_match_limit()
    conn = _connect(db_path)
    try:
        rows = fetch_all_matches(conn) if limit is None else fetch_recent_matches(conn, limit)
    finally:
        conn.close()
    return [
        {"rank_after": row["rank_after"], "league_changed": row["league_changed"]}
        for row in rows
        if row["rank_after"] is not None
    ]


def _fetch_rank_graph_summary(db_path: Path) -> Optional[dict]:
    """ランク推移グラフの統計欄(現在のランク・最高ランク・配信開始比)に必要な値を返す(Issue #313)。

    グラフ本体(_fetch_rank_history)と異なりRANK_GRAPH_MATCH_LIMITの影響は受けず、
    「現在のランク」「最高ランク」ともDB全体から集計する(ユーザー確認済み)。
    確定済みrank_afterが1件も無ければ、グラフ自体の「データがありません」表示と
    揃えてNoneを返す(呼び出し側は統計欄も出さない)。

    「配信開始時のランク」は、現在の配信セッションで最初にランクを賭けた試合の
    rank_before(Issue #308のチェーン設計により、配信開始前の最後に確定した
    rank_afterと一致する)。今のセッションでまだランクを賭けた試合が無い場合は、
    現在のランクをそのまま配信開始時のランクとみなし、増減を0として扱う
    (ユーザー確認済み)。
    """
    conn = _connect(db_path)
    try:
        current_rank = fetch_latest_rank_after(conn)
        if current_rank is None:
            return None
        max_rank = fetch_max_rank_after(conn)
        session_start_rank = current_rank
        session_id = fetch_current_session_id(conn)
        if session_id is not None:
            for row in fetch_matches_for_session(conn, session_id):
                if row["rank_before"] is not None:
                    session_start_rank = row["rank_before"]
                    break
    finally:
        conn.close()
    return {
        "current_rank": current_rank,
        "max_rank": max_rank,
        "session_start_rank": session_start_rank,
        "delta": current_rank - session_start_rank,
    }


# ランク推移グラフのSVG座標系(viewBox内の論理サイズ)。width/height="100%"で
# 実際の表示サイズ(OBSブラウザソースの矩形)まで引き伸ばす。左右上下でマージンを
# 分けているのは、左に縦軸のラベル・下に横軸のラベル分の余白が必要なため
_RANK_GRAPH_VIEWBOX_WIDTH = 786
# Issue #281で、縦軸の範囲(axis_range)が広いとき目盛り間隔をいきなり広げるより先に
# グラフ自体の縦幅を220px(通常運用)〜350pxの範囲で動的に伸ばす仕組みを追加したが、
# Issue #336で「配信画面で見たときにグラフの大きさが試合ごとに変わって落ち着かない」
# というフィードバックを受け撤廃し、常に350px固定に戻した(範囲が広い場合は従来通り
# 目盛り間隔(_rank_graph_y_tick_step)を広げて対応する、Issue #123の挙動を維持)
_RANK_GRAPH_VIEWBOX_HEIGHT = 350
_RANK_GRAPH_MARGIN_LEFT = 50
_RANK_GRAPH_MARGIN_RIGHT = 20
# タイトル分の余白を含む(_RANK_GRAPH_TITLE参照)。Issue #336でタイトルの文字サイズを
# 拡大したことに合わせて54→58に広げた
_RANK_GRAPH_MARGIN_TOP = 58
_RANK_GRAPH_MARGIN_BOTTOM = 30
_RANK_GRAPH_TITLE = "ランク推移"
# Issue #313: 統計タイル(現在のランク・最高ランク・配信開始比)を表示する場合に
# 追加で確保する高さ。グラフ本体の縦幅(_RANK_GRAPH_VIEWBOX_HEIGHT)とは別枠で
# 上乗せする(目盛り密度の計算に影響させないため)。Issue #336でタイトル・統計タイルの
# 間の余白とラベル文字サイズを拡大したことに合わせて56→66に広げ、さらに実配信画面での
# 確認を経てタイトルと統計タイルの間の余白をもう一段広げたい(隙間36→56)という要望を
# 受けて66→86に広げた
_RANK_GRAPH_SUMMARY_HEIGHT = 86
# 一番左の点がプロット領域の左端(枠)に接しないための余白(px)。右側は
# _rank_graph_x_axis_maxで軸自体の右端(試合番号の上限)を実際の試合数より
# 広げることで余白を作る(縦軸の_rank_graph_y_boundsと同じ考え方)ため、
# 左側だけピクセル単位の余白が別途必要になる(軸の下限を1より前には拡張できないため)
_RANK_GRAPH_LEFT_PADDING = 24
# 横軸(試合番号)の目盛りは、この値の倍数(5, 10, 15, ...)の位置に置く
# (均等に割った本数で置く方式だと間隔が5,5,4,5のようにガタつくため、固定間隔にした。
# ユーザーとの相談で決定)
_RANK_GRAPH_X_TICK_STEP = 5
# Issue #330: 試合数が多い(RANK_GRAPH_MATCH_LIMITを大きく/allにした)場合、5刻みのままだと
# 目盛りの本数が増えすぎて見づらくなるため、試合数がこの件数以上になったら目盛り間隔を
# 10刻みに広げる(ユーザーとの相談で決定)
_RANK_GRAPH_X_TICK_STEP_WIDE = 10
_RANK_GRAPH_X_TICK_STEP_WIDE_THRESHOLD = 70

# Issue #123: 縦軸(ランク値)の目盛り間隔は横軸と異なり固定値にできない。OCR誤読等の
# 外れ値がrank_afterに混ざると縦軸の範囲(_rank_graph_y_bounds)が数百に広がることがあり、
# 間隔1のままだと目盛り線・ラベルが密集して描画自体が崩れる(実データで確認済み、
# 縦軸が1〜412に広がり417本の目盛りが220の高さに詰め込まれた)。目盛りの本数が
# この値を超えないよう、1・2・5・10・20...の「きりの良い」間隔から動的に選ぶ
# (_rank_graph_y_tick_step参照)。通常運用の狭い範囲(数〜十数)では引き続き間隔1のまま。
# _RANK_GRAPH_Y_TICK_MAX_COUNTは_rank_graph_y_tick_stepのデフォルト値としても使うが、
# _render_rank_graph_svg内では実際の高さ(_RANK_GRAPH_VIEWBOX_HEIGHT)から計算した
# 本数を明示的に渡す
_RANK_GRAPH_Y_TICK_MAX_COUNT = 20
_RANK_GRAPH_Y_TICK_STEPS = (1, 2, 5, 10, 20, 50, 100, 200, 500, 1000, 2000, 5000, 10000)
# Issue #281で導入した「縦軸の目盛り本数(間隔1のまま)が10本に達したところでグラフの
# 縦幅が上限の350pxになる」密度をそのまま踏襲している(Issue #336で高さ自体は常に
# 350px固定になったため「上限」という位置づけではなくなったが、密度の基準値としては
# 変更する理由が無いため残す)。本数が増えるほど間隔1を保つための必要な高さも増えるため、
# 350px÷(10本-1間隔)を1目盛りあたりのpx数として使う
_RANK_GRAPH_Y_TICK_COUNT_AT_MAX_HEIGHT = 10
_RANK_GRAPH_Y_PX_PER_UNIT = (
    _RANK_GRAPH_VIEWBOX_HEIGHT - _RANK_GRAPH_MARGIN_TOP - _RANK_GRAPH_MARGIN_BOTTOM
) / (_RANK_GRAPH_Y_TICK_COUNT_AT_MAX_HEIGHT - 1)
# Issue #336: 縦軸の範囲(axis_range)が狭いとき、0.5刻みの補助線(rank-graph-gridline-minor、
# ラベル無し)だけでは見栄えが物足りないというフィードバックを受け、範囲がこの値以下のときは
# 0.5刻みも通常の目盛り(ラベル・短い目盛り線付き)として表示するようにした
# (_render_rank_graph_svg参照)。値が大きいほど0.5刻みラベル同士の間隔が狭まり窮屈になる
# ため(axis_range=6で約22px、7で約19pxとプレビューで確認し、7は詰まって見えると
# ユーザーが判断)、余裕を持って読める範囲としてユーザーと相談の上3に決定した
_RANK_GRAPH_Y_HALF_STEP_LABEL_MAX_RANGE = 3


def _rank_graph_y_tick_step(axis_range: int, max_tick_count: float = _RANK_GRAPH_Y_TICK_MAX_COUNT) -> int:
    """縦軸の目盛り間隔を、本数がmax_tick_countを超えないよう動的に決める。"""
    if axis_range <= 0:
        return 1
    raw_step = axis_range / max_tick_count
    for step in _RANK_GRAPH_Y_TICK_STEPS:
        if step >= raw_step:
            return step
    return _RANK_GRAPH_Y_TICK_STEPS[-1]


def _rank_graph_y_bounds(min_value: float, max_value: float) -> tuple[int, int]:
    """縦軸の下限・上限を整数で返す(ユーザーとの相談で決定)。

    一番上・一番下の点が軸の端に接しないよう、実際のデータの最小値より下限を
    必ず小さく、最大値より上限を必ず大きくする(例: 実データが30.3〜32.6なら
    下限30・上限33)。実データがちょうど整数と一致する場合(端に接してしまう)は
    その整数から1つ広げる。
    """
    lower = math.floor(min_value)
    if lower == min_value:
        lower -= 1
    upper = math.ceil(max_value)
    if upper == max_value:
        upper += 1
    return lower, upper


def _rank_graph_x_tick_step(point_count: int) -> int:
    """横軸(試合番号)の目盛り間隔を、試合数に応じて決める(Issue #330)。

    試合数がRANK_GRAPH_X_TICK_STEP_WIDE_THRESHOLD(70)以上になったら10刻みに
    広げ、それ未満は従来通り5刻みのまま。
    """
    if point_count >= _RANK_GRAPH_X_TICK_STEP_WIDE_THRESHOLD:
        return _RANK_GRAPH_X_TICK_STEP_WIDE
    return _RANK_GRAPH_X_TICK_STEP


def _rank_graph_x_axis_max(point_count: int, tick_step: int) -> int:
    """横軸(試合番号)の右端の値を返す(ユーザーとの相談で決定、縦軸のbounds拡張と同じ考え方)。

    実際の試合数(point_count)を上回る、tick_stepの倍数に切り上げる
    (例: tick_step=5で23試合なら25)。

    Issue #331: 以前はちょうど倍数の試合数(例: tick_step=5で20試合)でも
    一番右の点が軸の端に接してしまうことを避けるため、その場合さらに1段
    広げていたが、右側に丸々1目盛り分の空白ができて気になるというフィードバックを
    受けて撤廃した(ユーザー確認済み)。ちょうど倍数の試合数では一番右の点が
    枠の右端に接することを許容する。倍数でない試合数(例: 22試合)は、
    ceil()により従来通り次の倍数まで拡張されるため挙動は変わらない。
    """
    return math.ceil(point_count / tick_step) * tick_step


def _rank_graph_x_tick_values(axis_max: int, tick_step: int) -> list[int]:
    """横軸(試合番号)の目盛りとして表示する試合番号(1始まり)を返す。

    最初の試合(1)を必ず含み(ユーザーとの相談で決定)、そこにtick_step刻みの値を
    axis_max(_rank_graph_x_axis_max参照)まで加える。
    """
    values = {1}
    values.update(range(tick_step, axis_max + 1, tick_step))
    return sorted(values)


def _rank_graph_summary_svg(summary: dict, width: int) -> str:
    """統計タイル(現在のランク・最高ランク・配信開始時のランク)3つを横並びで描画する(Issue #313)。

    タイトルとグラフ本体の間、_RANK_GRAPH_SUMMARY_HEIGHT分の帯に収める。
    数値は常に小数第2位まで0埋めで表示する(例: 41.0 -> "41.00"、41.3 -> "41.30")。
    以前は`:g`で末尾の0を落としていたが、整数ぴったりの値が44のように桁数の
    異なる表示になり読みにくいというフィードバックを受けて統一した(ユーザー確認済み)。

    3つ目のタイルは、増減値(delta)だけを大きく出す案だと配信開始時点の
    ランク自体がどこにも残らず分かりにくいというフィードバックを受け、
    配信開始時のランクを他の2タイルと同じ大きさで常に表示し、その右に
    小さく増減値を添える形にした(同じ<text>内のtspanで連結し、text-anchor
    ="middle"がテキスト全体を1つの塊としてセンタリングする性質を利用して
    数値+増減値をまとめて中央寄せしている)。
    """
    current_text = f'{summary["current_rank"]:.2f}'
    max_rank = summary["max_rank"]
    max_text = f"{max_rank:.2f}" if max_rank is not None else "-"
    session_start_text = f'{summary["session_start_rank"]:.2f}'
    delta = summary["delta"]
    delta_text = f"+{delta:.2f}" if delta >= 0 else f"{delta:.2f}"
    if delta > 0:
        delta_class = "rank-graph-stat-delta-up"
    elif delta < 0:
        delta_class = "rank-graph-stat-delta-down"
    else:
        delta_class = "rank-graph-stat-delta-neutral"

    labels = ("現在のランク", "最高ランク", "配信開始時")
    values_svg = (
        f'<tspan class="rank-graph-stat-value">{current_text}</tspan>',
        f'<tspan class="rank-graph-stat-value">{max_text}</tspan>',
        f'<tspan class="rank-graph-stat-value">{session_start_text}</tspan>'
        f'<tspan class="rank-graph-stat-delta {delta_class}" dx="6">{delta_text}</tspan>',
    )
    tile_centers = (width / 6, width / 2, width * 5 / 6)
    parts = []
    for label, value_svg, cx in zip(labels, values_svg, tile_centers):
        parts.append(f'<text x="{cx:.1f}" y="94" text-anchor="middle" class="rank-graph-stat-label">{label}</text>')
        parts.append(f'<text x="{cx:.1f}" y="126" text-anchor="middle">{value_svg}</text>')
    for x in (width / 3, width * 2 / 3):
        parts.append(f'<line x1="{x:.1f}" y1="82" x2="{x:.1f}" y2="124" class="rank-graph-stat-divider" />')
    return "".join(parts)


def _render_rank_graph_svg(history: list[dict], summary: Optional[dict] = None) -> str:
    """ランク推移を、枠・縦横の目盛り付きの折れ線グラフとしてSVG文字列で描画する。

    JS・外部チャートライブラリは使わずサーバー側でSVGを組み立てる(配信環境の
    ネット接続が不安定でも表示が壊れないようにするための方針、ユーザーとの
    相談で決定)。昇格/降格(league_changed)による点の色分けはしない(全て白、
    ユーザーとの相談で決定)。

    Issue #180で「隣り合う試合同士が連続しているとみなせない箇所は点線でつなぐ」
    仕組みを一度導入したが、ユーザーとの相談で常に実線表示に戻した(2026-08-04)。

    Issue #313: summaryを渡すと、タイトルとグラフの間に統計タイル3つ(現在の
    ランク・最高ランク・配信開始比)を描画する(_rank_graph_summary_svg参照)。
    summaryがNone(_fetch_rank_graph_summaryが「データ無し」を返した)の場合は
    従来通り統計欄無しで描画する。データが無い(historyが空)場合でも、summary
    自体は別集計(DB全体からの現在/最高ランク)のため独立して出せることがあり、
    その場合は統計欄だけ表示し「データがありません」は据え置く。
    """
    width = _RANK_GRAPH_VIEWBOX_WIDTH
    summary_offset = _RANK_GRAPH_SUMMARY_HEIGHT if summary is not None else 0
    summary_svg = _rank_graph_summary_svg(summary, width) if summary is not None else ""

    if not history:
        height = _RANK_GRAPH_VIEWBOX_HEIGHT + summary_offset
        svg_open = f'<svg viewBox="0 0 {width} {height}" width="100%" height="100%" xmlns="http://www.w3.org/2000/svg">'
        panel_svg = f'<rect x="0" y="0" width="{width}" height="{height}" rx="10" class="rank-graph-panel" />'
        title_svg = (
            f'<text x="{width / 2}" y="38" text-anchor="middle" class="rank-graph-title">{_RANK_GRAPH_TITLE}</text>'
        )
        empty_y = summary_offset + _RANK_GRAPH_VIEWBOX_HEIGHT / 2
        return (
            f"{svg_open}{panel_svg}{title_svg}{summary_svg}"
            f'<text x="{width / 2}" y="{empty_y:.1f}" text-anchor="middle" class="rank-graph-empty">'
            "データがありません</text></svg>"
        )

    values = [point["rank_after"] for point in history]
    axis_min, axis_max = _rank_graph_y_bounds(min(values), max(values))
    axis_range = axis_max - axis_min

    # Issue #336: 縦幅は常に_RANK_GRAPH_VIEWBOX_HEIGHT固定(以前はaxis_rangeに応じて
    # 220px〜350pxの範囲で動的に決めていたが撤廃した、_RANK_GRAPH_VIEWBOX_HEIGHT参照)。
    # Issue #313: 統計タイル分(summary_offset)はこれとは別枠で上乗せする
    height = _RANK_GRAPH_VIEWBOX_HEIGHT + summary_offset
    svg_open = f'<svg viewBox="0 0 {width} {height}" width="100%" height="100%" xmlns="http://www.w3.org/2000/svg">'
    # 配信画面に重ねたときの視認性対策(Issue #113)。他の要素より先に描画することで
    # 一番背面に来るようにする
    panel_svg = f'<rect x="0" y="0" width="{width}" height="{height}" rx="10" class="rank-graph-panel" />'
    title_svg = f'<text x="{width / 2}" y="38" text-anchor="middle" class="rank-graph-title">{_RANK_GRAPH_TITLE}</text>'

    plot_left, plot_right = _RANK_GRAPH_MARGIN_LEFT, width - _RANK_GRAPH_MARGIN_RIGHT
    plot_top, plot_bottom = _RANK_GRAPH_MARGIN_TOP + summary_offset, height - _RANK_GRAPH_MARGIN_BOTTOM
    plot_width = plot_right - plot_left
    plot_height = plot_bottom - plot_top
    # 一番左の点がプロット領域の左端に接しないよう、内側に寄せた左端を使う
    # (枠・グリッド線自体はplot_leftのまま、軸自体の見た目は変えない)
    points_left = plot_left + _RANK_GRAPH_LEFT_PADDING

    x_tick_step = _rank_graph_x_tick_step(len(history))
    x_axis_max = _rank_graph_x_axis_max(len(history), x_tick_step)
    x_axis_max_index = x_axis_max - 1  # 試合番号(1始まり)を0始まりのインデックスに変換

    def x_at(index: int) -> float:
        return points_left + (plot_right - points_left) * index / x_axis_max_index

    def y_at(value: float) -> float:
        return plot_top + plot_height * (1 - (value - axis_min) / axis_range)

    # 縦軸目盛り: 横向きのグリッド線+左側にランク値のラベル+横軸と同様の短い目盛り線。
    # ラベル・目盛り自体は整数のみ・間隔は_rank_graph_y_tick_stepで動的に決める
    # (Issue #123、通常運用の狭い範囲では間隔1のまま)。軸の下限・上限自体を実データより
    # 広げてあるため(_rank_graph_y_bounds参照)、一番上・一番下の点は自然に軸の端から離れる。
    # デフォルトの_RANK_GRAPH_Y_TICK_MAX_COUNTではなく、実際のplot_heightから逆算した
    # 本数上限を渡す(Issue #281、summary_offsetの有無でplot_heightが変わるため)
    y_tick_step = _rank_graph_y_tick_step(axis_range, plot_height / _RANK_GRAPH_Y_PX_PER_UNIT)
    y_axis_svg = []
    for tick_value in range(axis_min, axis_max + 1, y_tick_step):
        y = y_at(tick_value)
        y_axis_svg.append(
            f'<line x1="{plot_left}" y1="{y:.1f}" x2="{plot_right}" y2="{y:.1f}" class="rank-graph-gridline" />'
        )
        y_axis_svg.append(
            f'<line x1="{plot_left - 5}" y1="{y:.1f}" x2="{plot_left}" y2="{y:.1f}" class="rank-graph-tick" />'
        )
        y_axis_svg.append(
            f'<text x="{plot_left - 8}" y="{y:.1f}" text-anchor="end" dominant-baseline="middle" '
            f'class="rank-graph-tick-label">{tick_value}</text>'
        )

    # Issue #146: 整数目盛りの間に0.5刻みの補助グリッド線を追加する(ラベル・目盛り線は
    # 増やさない)。目盛り間隔が1のとき(通常運用)だけ追加し、外れ値で間隔が2以上に
    # 広がった場合(_rank_graph_y_tick_step参照)は追加しない(ユーザーとの相談で決定。
    # #136対応後はこの手の外れ値自体が起こりにくくなる見込みのため、実運用上は
    # ほぼ常にこの分岐に入る)
    # Issue #336: さらに、縦軸の範囲が_RANK_GRAPH_Y_HALF_STEP_LABEL_MAX_RANGE以下と
    # 狭いときは、0.5刻みもラベル・短い目盛り線付きの通常の目盛りとして表示する
    # (見た目が物足りないというフィードバックを受けた対応。範囲が広いとラベル同士が
    # 詰まって見えるため、狭い範囲のみに限定している)
    if y_tick_step == 1:
        show_half_step_labels = axis_range <= _RANK_GRAPH_Y_HALF_STEP_LABEL_MAX_RANGE
        for major_value in range(axis_min, axis_max):
            half_value = major_value + 0.5
            y = y_at(half_value)
            if show_half_step_labels:
                y_axis_svg.append(
                    f'<line x1="{plot_left}" y1="{y:.1f}" x2="{plot_right}" y2="{y:.1f}" class="rank-graph-gridline" />'
                )
                y_axis_svg.append(
                    f'<line x1="{plot_left - 5}" y1="{y:.1f}" x2="{plot_left}" y2="{y:.1f}" class="rank-graph-tick" />'
                )
                y_axis_svg.append(
                    f'<text x="{plot_left - 8}" y="{y:.1f}" text-anchor="end" dominant-baseline="middle" '
                    f'class="rank-graph-tick-label">{half_value:g}</text>'
                )
            else:
                y_axis_svg.append(
                    f'<line x1="{plot_left}" y1="{y:.1f}" x2="{plot_right}" y2="{y:.1f}" '
                    'class="rank-graph-gridline-minor" />'
                )

    # 枠(プロット領域を囲む矩形)
    frame_svg = (
        f'<rect x="{plot_left}" y="{plot_top}" width="{plot_width}" height="{plot_height}" '
        'class="rank-graph-frame" />'
    )

    # 横軸目盛り: 縦向きの薄いグリッド線(縦軸のグリッド線と同様)+下側に短い目盛り線+
    # 試合番号のラベル。1試合目を必ず含み、実際の試合数を上回る位置(x_axis_max)まで
    # x_tick_step(通常5、試合数が多い場合は10、Issue #330)刻みで表示する
    x_axis_svg = []
    for match_number in _rank_graph_x_tick_values(x_axis_max, x_tick_step):
        x = x_at(match_number - 1)
        x_axis_svg.append(
            f'<line x1="{x:.1f}" y1="{plot_top}" x2="{x:.1f}" y2="{plot_bottom}" class="rank-graph-gridline" />'
        )
        x_axis_svg.append(
            f'<line x1="{x:.1f}" y1="{plot_bottom}" x2="{x:.1f}" y2="{plot_bottom + 5}" class="rank-graph-tick" />'
        )
        x_axis_svg.append(
            f'<text x="{x:.1f}" y="{plot_bottom + 18}" text-anchor="middle" class="rank-graph-tick-label">'
            f"{match_number}</text>"
        )

    coords = [(x_at(i), y_at(point["rank_after"])) for i, point in enumerate(history)]

    polyline_points = " ".join(f"{x:.1f},{y:.1f}" for x, y in coords)
    # Issue #313: エリアチャート化。折れ線と同じ座標列を使い、右端・左端からプロット
    # 領域の底辺まで下ろして閉じた多角形にする(線・点より先に描画し背面に敷く)
    area_points = f"{polyline_points} {coords[-1][0]:.1f},{plot_bottom:.1f} {coords[0][0]:.1f},{plot_bottom:.1f}"
    area_svg = [f'<polygon points="{area_points}" class="rank-graph-area" />']
    line_svg = [f'<polyline points="{polyline_points}" class="rank-graph-line" />']

    markers = [f'<circle cx="{x:.1f}" cy="{y:.1f}" r="4" class="rank-graph-point" />' for x, y in coords]

    return (
        f"{svg_open}{panel_svg}{title_svg}{summary_svg}"
        f"{''.join(y_axis_svg)}"
        f"{frame_svg}"
        f"{''.join(x_axis_svg)}"
        f"{''.join(area_svg)}"
        f"{''.join(line_svg)}"
        f"{''.join(markers)}"
        "</svg>"
    )


def _aggregate_goal_stats(rows: list[sqlite3.Row]) -> list[dict]:
    """ゴールの生データ(scorer_name/assist_name)から、許可リストプレイヤー別の
    得点数・アシスト数・関与数(得点+アシスト)を集計する(Issue #96)。

    許可リスト外の名前(GOAL_RECORD_MODE=all/allowlistで相手チームの名前等が
    そのまま保存されている場合がある)は集計対象から除外する(ユーザーとの
    相談で決定)。関与数の多い順(同数の場合は得点数の多い順、それも同じなら
    名前順)に並べて返す。

    Issue #271: 許可リストプレイヤーは`rows`に1件もゴールが無くても常に
    0件(得点0・アシスト0)として結果に含める。ゴールがまだ無い状態と
    「表示すべきデータが無い」状態(許可リストが未設定)を区別するため。
    """
    counts: dict[str, dict[str, int]] = {name: {"goals": 0, "assists": 0} for name in get_allowed_players()}
    for row in rows:
        scorer_name = row["scorer_name"]
        if scorer_name and is_allowed_player(scorer_name):
            counts[scorer_name]["goals"] += 1
        assist_name = row["assist_name"]
        if assist_name and is_allowed_player(assist_name):
            counts[assist_name]["assists"] += 1

    players = [
        {"name": name, "goals": c["goals"], "assists": c["assists"], "involvement": c["goals"] + c["assists"]}
        for name, c in counts.items()
    ]
    players.sort(key=lambda p: (-p["involvement"], -p["goals"], p["name"]))
    return players


def _fetch_goal_stats(db_path: Path) -> list[dict]:
    """現在の配信セッションのゴール/アシスト統計を、プレイヤー別に集計して返す。

    配信セッションが1件も無い場合(main.py未起動でDBのみ閲覧している場合等)も
    ゴール0件として扱い、許可リストプレイヤーを0件で返す(Issue #271、
    `_aggregate_goal_stats`参照)。許可リスト自体が空の場合のみ空リストになる。
    """
    conn = _connect(db_path)
    try:
        session_id = fetch_current_session_id(conn)
        rows = fetch_goals_for_session(conn, session_id) if session_id is not None else []
    finally:
        conn.close()
    return _aggregate_goal_stats(rows)


# Issue #100: ∞/S/A帯を跨いで比較できるよう、帯内の数値を統一スケールに換算する
# オフセット。ユーザーから聞いたゲーム内の実際の仕様(∞は0〜49、Sは0〜9、Aは
# 最大29で下限不明。各帯とも帯内の上限に到達すると次の帯へ自動的に昇格する)に
# 基づく。この換算により A29(-11) < S0(-10) < S9(-1) < ∞0(0) のように、
# 昇格の順序と換算後の数値の大小関係が矛盾なく一致する連続したスケールになる。
# B~E帯は範囲が未検証のため対象外のまま(detection/vs_rank.pyのdocstring参照)
_RANK_TIER_OFFSETS = {"∞": 0, "S": -10, "A": -40}


def _convert_rank_tier_to_unified_scale(tier_label: Optional[str], tier_value: Optional[int]) -> Optional[int]:
    """∞/S/A帯の帯内数値を、帯を跨いで比較できる統一スケールの数値に変換する。

    tier_labelが∞/S/Aのいずれでもない場合(B~E帯・読み取り失敗、rank_tier_label・
    rank_tierともNULL)、またはtier_valueがNoneの場合はNoneを返す(呼び出し側で
    「不明」として扱う)。
    """
    if tier_label not in _RANK_TIER_OFFSETS or tier_value is None:
        return None
    return tier_value + _RANK_TIER_OFFSETS[tier_label]


def _summarize_vs_slot_ranks(rows: list[sqlite3.Row]) -> dict:
    """1チーム分のvs_slot_ranks(最大4スロット)を、統一スケールでの合計値に集計する。

    合計に含められた人数(known_count)・含められなかった人数(unknown_count、
    B~E帯・読み取り失敗)も合わせて返す。全スロットが不明な場合、totalはNone。
    """
    converted_values = []
    unknown_count = 0
    for row in rows:
        value = _convert_rank_tier_to_unified_scale(row["rank_tier_label"], row["rank_tier"])
        if value is None:
            unknown_count += 1
        else:
            converted_values.append(value)
    return {
        "total": sum(converted_values) if converted_values else None,
        "known_count": len(converted_values),
        "unknown_count": unknown_count,
    }


_DEFAULT_TEAM_COLOR = "#666666"  # チームカラーが未検知の場合のフォールバック(ニュートラルな灰色)


def _fetch_vs_rank_comparison(db_path: Path) -> Optional[dict]:
    """直近VS画面スナップショットから、自チーム・相手チームそれぞれの統一スケール合計を算出する。

    Issue #145: 試合結果確定(matches/vs_slot_ranks)を待たず、VS画面確定を検知した
    瞬間に書き込まれるvs_rank_snapshots/vs_rank_snapshot_slots(state.match_state.
    MatchStateMachine.pop_vs_screen_event、main.py._record_vs_screen_event参照)を
    見る。スナップショットが1件も無い、またはスロット行が無い場合(直近の試合で
    VS画面を見逃した場合等、main.py._record_match_resultが空スナップショットを
    書き込む)はNoneを返す。チームカラー(Issue #113)もスナップショットに含まれる
    ため、あわせてここで返す。
    """
    conn = _connect(db_path)
    try:
        snapshot = fetch_latest_vs_rank_snapshot(conn)
        if snapshot is None:
            return None
        rows = fetch_vs_rank_snapshot_slots(conn, snapshot["id"])
    finally:
        conn.close()
    if not rows:
        return None
    mine_rows = [row for row in rows if row["side"] == "mine"]
    opponent_rows = [row for row in rows if row["side"] == "opponent"]
    return {
        "mine": _summarize_vs_slot_ranks(mine_rows),
        "opponent": _summarize_vs_slot_ranks(opponent_rows),
        "mine_team_color": snapshot["mine_team_color"] or _DEFAULT_TEAM_COLOR,
        "opponent_team_color": snapshot["opponent_team_color"] or _DEFAULT_TEAM_COLOR,
    }


def _format_vs_rank_value(summary: dict) -> str:
    """統一スケール合計を、数値のみの短いテキストに整形する(不明人数等は含めない)。

    このウィジェットは試合中(ゲーム画面が全画面の間)ゲーム映像に重ねて常時表示する
    想定のため、他のウィジェットと異なり文字数を極力減らす(ユーザーとの相談で決定、
    Issue #100)。値が無い場合(不明人数のみ等)は、直近スナップショット自体が無い
    場合の表示("-" VS "-")と表記を揃えるため"-"を返す(Issue #113、Issue #276で
    "none"から変更)。
    """
    return str(summary["total"]) if summary["total"] is not None else "-"


# Issue #99: 直近試合結果ログの対象範囲は#95(ランク推移グラフ)と同じく
# 「配信セッションをまたいだ直近N試合」。表示件数は固定値(ユーザーとの相談で決定、
# #95のRANK_GRAPH_MATCH_LIMITと異なり.env化はしない)
MATCH_LOG_LIMIT = 10
_MATCH_RESULT_LETTERS = {"win": "W", "lose": "L", "draw": "D"}
# Issue #262: 実際のサッカーの勝敗表示で見るような色分けバッジにする。dataviz skillの
# status palette(good/critical、`references/palette.md`参照)を流用し、win=good・
# lose=criticalに割り当てる。draw(引き分け)はstatus paletteに該当する状態が無いため、
# 同skillの「Muted (axis/labels)」トーン(勝敗どちらでもない中間色、ライト/ダーク共通)を使う
_MATCH_RESULT_BADGE_COLORS = {"win": "#0ca30c", "lose": "#d03b3b", "draw": "#898781"}
# Issue #262: 一番古い試合のバッジをこの不透明度から始め、新しい方の半分は
# フェードさせず常にopacity 1.0で表示する(古い方の半分だけ徐々に暗くする、
# ユーザーとの相談で決定。2026-08-07)。
_MATCH_LOG_OLDEST_OPACITY = 0.5


def _fetch_match_log(db_path: Path, limit: int = MATCH_LOG_LIMIT) -> list[str]:
    """直近limit件の試合結果('win'/'lose'/'draw')を古い順で返す。"""
    conn = _connect(db_path)
    try:
        rows = fetch_recent_matches(conn, limit)
    finally:
        conn.close()
    return [row["result"] for row in rows]


def _build_match_log_badges(results: list[str]) -> list[dict]:
    """試合結果のリストを、テンプレートで色分けバッジとして描画するための一覧に変換する。

    各要素は{"letter": "W", "color": "#0ca30c", "opacity": 1.0}のように、文字
    (_MATCH_RESULT_LETTERS)・背景色(_MATCH_RESULT_BADGE_COLORS)・不透明度をまとめたもの。
    色・不透明度とも固定のロジックからしか選ばれないため(任意の文字列をHTML/CSSに
    そのまま埋め込むインジェクションの心配はない)。

    不透明度は前半(古い方)と後半(新しい方)で扱いが異なる。件数(count)の
    前半count // 2件(fade_count)だけが対象で、一番古い(先頭)を
    _MATCH_LOG_OLDEST_OPACITYから始め、fade_countの終端(=後半の境界)で
    ちょうど1.0に達するよう線形に濃くする。後半(fade_count件目以降、最新側)は
    フェードさせず常にopacity 1.0で表示する(ユーザーとの相談で決定。
    「新しい方はくっきり、古い方だけ徐々に薄く」という要望から)。
    fade_countが1件のみの場合(全体が2〜3件程度の少数時)は線形補間のための
    2点目が無いため、その1件はそのまま_MATCH_LOG_OLDEST_OPACITYにする。
    """
    count = len(results)
    fade_count = count // 2
    badges = []
    for index, result in enumerate(results):
        if index >= fade_count:
            opacity = 1.0
        elif fade_count == 1:
            opacity = _MATCH_LOG_OLDEST_OPACITY
        else:
            opacity = _MATCH_LOG_OLDEST_OPACITY + (1.0 - _MATCH_LOG_OLDEST_OPACITY) * index / (fade_count - 1)
        badges.append(
            {
                "letter": _MATCH_RESULT_LETTERS[result],
                "color": _MATCH_RESULT_BADGE_COLORS[result],
                "opacity": round(opacity, 2),
            }
        )
    return badges


def _fetch_rank_delta_distribution(db_path: Path) -> dict:
    """勝ち試合のランク増加量・負け試合のランク減少量を集計する。

    集計対象は.envのRANK_DELTA_DISTRIBUTION_SCOPEで切り替える(Issue #101):
    "session"なら現在の配信セッションのみ、"all"なら累計(全期間)。
    Issue #101: 「勝ったのにあまり増えていない」「負けた時は勝った時より明らかに
    多く減っている」という非対称性を絶対値ベースで直接比較できるようにするため、
    負け試合の変化量は絶対値にして返す(ユーザーとの相談で決定。符号を揃えず
    そのまま返すと、勝ちが正・負けが負の値になり単純な大小比較がしづらいため)。
    draw(引き分け)、rank_before/rank_afterのいずれかがNULLの試合は対象外。
    "session"指定時に配信セッションが1件も無い場合は両方とも空リストを返す。
    """
    scope = get_rank_delta_distribution_scope()
    conn = _connect(db_path)
    try:
        if scope == "all":
            rows = fetch_all_matches(conn)
        else:
            session_id = fetch_current_session_id(conn)
            if session_id is None:
                return {"win": [], "lose": []}
            rows = fetch_matches_for_session(conn, session_id)
    finally:
        conn.close()

    win_deltas: list[float] = []
    lose_deltas: list[float] = []
    for row in rows:
        if row["rank_before"] is None or row["rank_after"] is None:
            continue
        delta = row["rank_after"] - row["rank_before"]
        if row["result"] == "win":
            win_deltas.append(delta)
        elif row["result"] == "lose":
            lose_deltas.append(abs(delta))
    return {"win": win_deltas, "lose": lose_deltas}


def _percentile(sorted_values: list[float], pct: float) -> float:
    """線形補間によるパーセンタイル値を返す(numpyのデフォルト'linear'法相当)。

    sorted_valuesは昇順ソート済み・1件以上であること。
    """
    if len(sorted_values) == 1:
        return sorted_values[0]
    rank = (len(sorted_values) - 1) * (pct / 100)
    lower_index, upper_index = math.floor(rank), math.ceil(rank)
    if lower_index == upper_index:
        return sorted_values[int(rank)]
    fraction = rank - lower_index
    return sorted_values[lower_index] + (sorted_values[upper_index] - sorted_values[lower_index]) * fraction


def _compute_box_stats(values: list[float]) -> Optional[dict]:
    """箱ひげ図に必要な統計値(最小・第1四分位・中央値・第3四分位・最大・平均)を返す。

    値が1件も無い場合はNoneを返す。
    """
    if not values:
        return None
    sorted_values = sorted(values)
    return {
        "min": sorted_values[0],
        "q1": _percentile(sorted_values, 25),
        "median": _percentile(sorted_values, 50),
        "q3": _percentile(sorted_values, 75),
        "max": sorted_values[-1],
        "mean": sum(values) / len(values),
    }


# 勝敗別ランク増減分布グラフのSVG座標系。数直線(横軸)を下側に描き、その上に
# 「勝ち」「負け」2段の横向き箱ひげ図を積む(縦に2段並べる)構成にする
# (ユーザーとの相談で決定)。原点は常に0で、正の方向(右)にのみ伸ばす
# (負け側もabsで正の値にしてあるため、軸は0〜最大値のみで足りる)
_BOX_PLOT_VIEWBOX_WIDTH = 779
_BOX_PLOT_VIEWBOX_HEIGHT = 160
# Issue #336: 「増加」「減少」ラベル(24px)とプロット領域の間が窮屈に見えるという
# フィードバックを受け、ラベルの右端からプロット領域左端までの間隔を8px→18pxに広げた
# (_BOX_PLOT_ROW_LABEL_GAP参照)。ラベル文字自体が左にはみ出さないよう、
# MARGIN_LEFTも60→70に広げている。さらに実配信画面での確認を経て、パネル背景の
# 左端からラベルまでの余白も窮屈というフィードバックを受け70→90に広げた
_BOX_PLOT_MARGIN_LEFT = 90
_BOX_PLOT_MARGIN_RIGHT = 30
# タイトル分の余白を含む(_BOX_PLOT_TITLE参照)。Issue #336でタイトルの文字サイズを
# 拡大したことに合わせて49→53に広げた
_BOX_PLOT_MARGIN_TOP = 53
_BOX_PLOT_MARGIN_BOTTOM = 30
_BOX_PLOT_TITLE = "ランク増減分布"
_BOX_PLOT_ROW_HEIGHT_RATIO = 0.5  # 各段の高さのうち箱ひげ図本体が占める割合
# Issue #278: 「増加」「減少」ラベルの文字サイズを上げたのに合わせ、win/lose
# 2段が詰まって見えないよう段の間に明示的な余白を設ける(ユーザーとの相談で決定)
_BOX_PLOT_ROW_GAP = 10
# Issue #336: 「増加」「減少」ラベルの右端からプロット領域左端(plot_left)までの間隔。
# 以前はY軸目盛りラベルと同じ8pxを流用していたが、ラベル文字が24pxと大きいため
# 窮屈に見えるというフィードバックを受け広げた
_BOX_PLOT_ROW_LABEL_GAP = 18
# 横軸(数直線)の目盛りは、この値の倍数(0.1, 0.2, 0.3, ...)の位置に固定間隔で置く
# (#99の横軸と同じ考え方。ユーザーとの相談で決定)
_BOX_PLOT_X_TICK_STEP = 0.1
_BOX_PLOT_ROWS = (("win", "増加"), ("lose", "減少"))


def _rank_delta_axis_max(stats_by_category: dict) -> float:
    """横軸(数直線)の右端の値を返す。

    実データの最大値を必ず上回る、_BOX_PLOT_X_TICK_STEPの倍数にする(一番右の
    値が軸の端に接しないようにするため、#95のランク推移グラフの軸拡張と同じ
    考え方)。データが1件も無い場合は_BOX_PLOT_X_TICK_STEPを返す(空のグラフ
    でも軸自体は描画できるようにする)。
    """
    max_values = [stats["max"] for stats in stats_by_category.values() if stats is not None]
    if not max_values:
        return _BOX_PLOT_X_TICK_STEP
    # 浮動小数点誤差を避けるため、目盛り単位の整数(何個分の0.1か)で計算してから戻す
    raw_max = max(max_values)
    steps = math.floor(raw_max / _BOX_PLOT_X_TICK_STEP + 1e-9)
    return round((steps + 1) * _BOX_PLOT_X_TICK_STEP, 10)


def _render_rank_delta_box_plot_svg(win_values: list[float], lose_values: list[float]) -> str:
    """勝ち試合の増加量・負け試合の減少量(絶対値)を、横向きの箱ひげ図2段でSVG描画する。

    JS・外部チャートライブラリは使わずサーバー側でSVGを組み立てる(#95と同じ方針)。
    """
    width, height = _BOX_PLOT_VIEWBOX_WIDTH, _BOX_PLOT_VIEWBOX_HEIGHT
    svg_open = f'<svg viewBox="0 0 {width} {height}" width="100%" height="100%" xmlns="http://www.w3.org/2000/svg">'
    # 配信画面に重ねたときの視認性対策(Issue #113)。他の要素より先に描画することで
    # 一番背面に来るようにする
    panel_svg = f'<rect x="0" y="0" width="{width}" height="{height}" rx="10" class="rank-delta-panel" />'
    title_svg = f'<text x="{width / 2}" y="36" text-anchor="middle" class="rank-delta-title">{_BOX_PLOT_TITLE}</text>'

    stats_by_category = {"win": _compute_box_stats(win_values), "lose": _compute_box_stats(lose_values)}
    if stats_by_category["win"] is None and stats_by_category["lose"] is None:
        return (
            f"{svg_open}{panel_svg}{title_svg}"
            f'<text x="{width / 2}" y="{height / 2}" text-anchor="middle" class="rank-delta-empty">'
            "データがありません</text></svg>"
        )

    plot_left, plot_right = _BOX_PLOT_MARGIN_LEFT, width - _BOX_PLOT_MARGIN_RIGHT
    plot_top, plot_bottom = _BOX_PLOT_MARGIN_TOP, height - _BOX_PLOT_MARGIN_BOTTOM
    axis_max = _rank_delta_axis_max(stats_by_category)

    def x_at(value: float) -> float:
        return plot_left + (plot_right - plot_left) * value / axis_max

    # 横軸目盛り: 縦向きの薄いグリッド線+下側に短い目盛り線+数値ラベル。
    # axis_maxは_BOX_PLOT_X_TICK_STEPの倍数になるよう構成済みなので、0から
    # axis_maxまで単純に刻んでいけば必ず右端がちょうど目盛りに乗る
    axis_svg = []
    tick_count = round(axis_max / _BOX_PLOT_X_TICK_STEP) + 1
    for i in range(tick_count):
        tick_value = round(i * _BOX_PLOT_X_TICK_STEP, 10)
        x = x_at(tick_value)
        axis_svg.append(
            f'<line x1="{x:.1f}" y1="{plot_top}" x2="{x:.1f}" y2="{plot_bottom}" class="rank-delta-gridline" />'
        )
        axis_svg.append(
            f'<line x1="{x:.1f}" y1="{plot_bottom}" x2="{x:.1f}" y2="{plot_bottom + 5}" class="rank-delta-tick" />'
        )
        axis_svg.append(
            f'<text x="{x:.1f}" y="{plot_bottom + 18}" text-anchor="middle" class="rank-delta-tick-label">'
            f"{tick_value:.1f}</text>"
        )

    total_row_gap = _BOX_PLOT_ROW_GAP * (len(_BOX_PLOT_ROWS) - 1)
    row_height = (plot_bottom - plot_top - total_row_gap) / len(_BOX_PLOT_ROWS)
    box_height = row_height * _BOX_PLOT_ROW_HEIGHT_RATIO

    rows_svg = []
    for index, (category, category_label) in enumerate(_BOX_PLOT_ROWS):
        row_top = plot_top + index * (row_height + _BOX_PLOT_ROW_GAP)
        row_center_y = row_top + row_height / 2
        rows_svg.append(
            f'<text x="{plot_left - _BOX_PLOT_ROW_LABEL_GAP}" y="{row_center_y:.1f}" text-anchor="end" '
            f'dominant-baseline="middle" class="rank-delta-row-label">{category_label}</text>'
        )
        stats = stats_by_category[category]
        if stats is None:
            continue
        box_top, box_bottom = row_center_y - box_height / 2, row_center_y + box_height / 2
        min_x, q1_x, median_x, q3_x, max_x, mean_x = (
            x_at(stats["min"]),
            x_at(stats["q1"]),
            x_at(stats["median"]),
            x_at(stats["q3"]),
            x_at(stats["max"]),
            x_at(stats["mean"]),
        )
        css_class = f"rank-delta-{category}"
        # ひげ(最小〜第1四分位、第3四分位〜最大)+ 両端のキャップ
        rows_svg.append(
            f'<line x1="{min_x:.1f}" y1="{row_center_y:.1f}" x2="{q1_x:.1f}" y2="{row_center_y:.1f}" '
            f'class="rank-delta-whisker {css_class}" />'
        )
        rows_svg.append(
            f'<line x1="{q3_x:.1f}" y1="{row_center_y:.1f}" x2="{max_x:.1f}" y2="{row_center_y:.1f}" '
            f'class="rank-delta-whisker {css_class}" />'
        )
        rows_svg.append(
            f'<line x1="{min_x:.1f}" y1="{box_top:.1f}" x2="{min_x:.1f}" y2="{box_bottom:.1f}" '
            f'class="rank-delta-cap {css_class}" />'
        )
        rows_svg.append(
            f'<line x1="{max_x:.1f}" y1="{box_top:.1f}" x2="{max_x:.1f}" y2="{box_bottom:.1f}" '
            f'class="rank-delta-cap {css_class}" />'
        )
        # 箱(第1四分位〜第3四分位)
        rows_svg.append(
            f'<rect x="{min(q1_x, q3_x):.1f}" y="{box_top:.1f}" width="{abs(q3_x - q1_x):.1f}" '
            f'height="{box_height:.1f}" class="rank-delta-box {css_class}" />'
        )
        # 中央値(箱の中の縦線)
        rows_svg.append(
            f'<line x1="{median_x:.1f}" y1="{box_top:.1f}" x2="{median_x:.1f}" y2="{box_bottom:.1f}" '
            'class="rank-delta-median" />'
        )
        # 平均(丸マーカー)
        rows_svg.append(
            f'<circle cx="{mean_x:.1f}" cy="{row_center_y:.1f}" r="4" class="rank-delta-mean" />'
        )

    return f"{svg_open}{panel_svg}{title_svg}{''.join(axis_svg)}{''.join(rows_svg)}</svg>"


# Issue #257: /adminページに表示する各overlayウィジェットへのリンク一覧の表示ラベル。
# キーは実際に登録されているoverlayルートのパス。リンク自体は_overlay_widget_links()が
# create_app()に実際に登録されたルート(app.routes)から機械的に集めるため、パスを
# ここやテンプレート側に別途書き写す必要は無い。表示ラベルのみここで個別に管理し、
# 新しいoverlayルートを追加した際にこの辞書への追記を忘れると、_overlay_widget_links()が
# 起動時(create_app呼び出し時)にRuntimeErrorで気づける設計にした
_OVERLAY_WIDGET_LABELS = {
    "/overlay/winrate": "勝率",
    "/overlay/rank-graph": "ランク推移グラフ",
    "/overlay/goal-stats": "ゴール/アシスト統計",
    "/overlay/match-log": "直近試合結果ログ",
    "/overlay/vs-rank-comparison": "対戦相手ランク比較",
    "/overlay/rank-delta-distribution": "ランク増減分布",
    "/overlay/dive-time": "次に潜る時間",
}


def _overlay_widget_links(app: FastAPI) -> list[dict]:
    """登録済みの/overlay/xxxルートから、/adminページ用のリンク一覧を組み立てる。

    パスはapp.routesから実際に登録されたものを集めるため、新しいoverlayルートを
    追加すれば自動的にリンク集に反映される(テンプレート側の追記は不要)。
    表示ラベルが_OVERLAY_WIDGET_LABELSに無い場合はRuntimeErrorで即座に失敗させ、
    ラベルの追記漏れに気づけるようにする(Issue #257)。
    """
    links = []
    for route in app.routes:
        path = getattr(route, "path", None)
        if path is None or not path.startswith("/overlay/"):
            continue
        if path not in _OVERLAY_WIDGET_LABELS:
            raise RuntimeError(
                f"overlayルート{path}の表示ラベルが_OVERLAY_WIDGET_LABELSに登録されていません"
            )
        links.append({"path": path, "label": _OVERLAY_WIDGET_LABELS[path]})
    return links


# Issue #259: /adminのリンク一覧など、通常のブラウザ(背景白)でoverlayページを開いた際に
# 白文字(static/overlay.cssのcolor: #fff)が読めなくなる問題の暫定策。クエリパラメータの
# 値そのものは受け取らず、有無だけを見て固定色に切り替える(任意の文字列をそのまま
# HTML/CSSに埋め込むインジェクションを避けるため)。OBSのブラウザソースが実際に使う
# URLにはこのパラメータを付けないため、配信時の見た目(透過)には一切影響しない
_OVERLAY_DEBUG_BG_QUERY_PARAM = "debug_bg"
_OVERLAY_DEBUG_BG_STYLE = ' style="background: #000;"'


def _overlay_debug_bg_style(request: Request) -> str:
    """クエリパラメータ(?debug_bg=1等、値は問わない)が付いている場合のみ、
    <body>に埋め込む背景色のinline style文字列を返す(無ければ空文字列)。

    overlay-refresh.js(Issue #104)は<body>のinnerHTMLしか差し替えないため、
    ここで<body>タグ自体に設定したstyle属性は自動更新後も保持される。
    """
    if _OVERLAY_DEBUG_BG_QUERY_PARAM in request.query_params:
        return _OVERLAY_DEBUG_BG_STYLE
    return ""


_MATCH_RESULT_LABELS = {"win": "勝ち", "lose": "負け", "draw": "引き分け"}


def _rank_entry_recency_label(index: int) -> str:
    """新しい順に並んだクリップ一覧内での位置から、選択ボタンの見出し文言を作る(Issue #307)。

    ユーザーとの相談で、単なる日時ラベルだけでなく「どれが最新か」を一目で
    分かるようにする要望があったため、「最新」「1つ前」「2つ前」の形にする。
    """
    return "最新" if index == 0 else f"{index}つ前"


def _build_rank_entry_clip_info(row: sqlite3.Row, index: int, has_clip: bool, has_gauge_clip: bool = False) -> dict:
    detected_at = datetime.fromisoformat(row["detected_at"])
    return {
        "match_id": row["id"],
        "recency_label": _rank_entry_recency_label(index),
        "detected_at_text": detected_at.strftime("%m/%d %H:%M"),
        "result_text": _MATCH_RESULT_LABELS.get(row["result"], row["result"]),
        "rank_before": row["rank_before"],
        "rank_after_ocr": row["rank_after_ocr"],
        "rank_after": row["rank_after"],
        "has_clip": has_clip,
        # Issue #312: ゲージクローズアップ動画(画面全体クリップとは別ファイル)が
        # 存在するかどうか。画面全体クリップより後から追加した機能のため、
        # 導入前に生成された試合や、まだエンコードが終わっていない試合ではFalseになりうる
        "has_gauge_clip": has_gauge_clip,
    }


def _build_rank_entry_context(db_path: Path) -> dict:
    """/rank-entryページ用に、直近の動画クリップ(最大3件、Issue #307)とそれぞれに
    対応する試合情報を新しい順に返す。

    テンプレート側は先頭(最新)をデフォルト選択として表示し、JS側で選択を
    切り替えるたびに同じ形のデータで左側の試合情報・入力フォームを差し替える
    (動画の切り替えと連動させたいというユーザー要望、Issue #307)。

    クリップが1件も無い場合(Issue #307導入前からの未確定分、またはまだ
    エンコードが完了していない直後等)は、Issue #306/#308までの表示に
    フォールバックし、最古の未確定試合を動画無し(has_clip=False)の1件として返す。
    1件も試合が無ければ空リストを返す(テンプレート側で「未確定の試合はありません」
    を表示)。

    他にも未確定の試合が溜まっている場合に気付けるよう件数(pending_count、
    表示中の分を含む全件)もあわせて返す(Issue #308)。
    """
    conn = _connect(db_path)
    try:
        pending_count = fetch_pending_manual_rank_match_count(conn)
        clip_ids = _list_clip_match_ids(DEFAULT_CLIPS_DIR)
        gauge_clip_ids = set(_list_clip_match_ids(GAUGE_CLIPS_DIR))
        clips = []
        for match_id in clip_ids:
            row = fetch_match(conn, match_id)
            if row is None:
                # 通常は起きないはずだが、DBをリセットした場合等の防御的スキップ
                continue
            clips.append(
                _build_rank_entry_clip_info(
                    row, len(clips), has_clip=True, has_gauge_clip=match_id in gauge_clip_ids
                )
            )
        if not clips:
            row = fetch_oldest_pending_manual_rank_match(conn)
            if row is not None:
                clips = [_build_rank_entry_clip_info(row, 0, has_clip=False)]
    finally:
        conn.close()
    return {"clips": clips, "pending_count": pending_count}


def _list_clip_match_ids(clips_dir: Path) -> list[int]:
    """保存済みの動画クリップファイル名(`{match_id}.mp4`)から、match_idを新しい順に返す。"""
    if not clips_dir.is_dir():
        return []
    return sorted(
        (int(p.stem) for p in clips_dir.glob("*.mp4") if p.stem.isdigit()),
        reverse=True,
    )


def create_app(db_path: Path) -> FastAPI:
    app = FastAPI()
    app.mount("/static", StaticFiles(directory=_WEB_DIR / "static"), name="static")

    @app.get("/api/health")
    def health() -> dict:
        return {"status": "ok"}

    @app.get("/api/matches/count")
    def matches_count() -> dict:
        return _fetch_matches_count(db_path)

    @app.get("/api/winrate")
    def winrate() -> dict:
        return _fetch_winrate(db_path)

    @app.get("/")
    def index(request: Request):
        counts = _fetch_matches_count(db_path)
        return _TEMPLATES.TemplateResponse(request, "index.html", {"counts": counts})

    @app.get("/admin")
    def admin(request: Request, status: Optional[str] = None, error: Optional[str] = None):
        context = {
            "settings": get_editable_settings(),
            "status": status,
            "error": error,
            "overlay_links": app.state.overlay_links,
        }
        return _TEMPLATES.TemplateResponse(request, "admin.html", context)

    @app.post("/admin")
    def admin_update(
        allowed_players: str = Form(""),
        goal_record_mode: str = Form(...),
        rank_graph_match_limit: str = Form(...),
        rank_delta_distribution_scope: str = Form(...),
        obs_scene_switching_enabled: str = Form(...),
    ):
        old_values = get_editable_settings()
        new_values = {
            "ALLOWED_PLAYERS": allowed_players,
            "GOAL_RECORD_MODE": goal_record_mode,
            "RANK_GRAPH_MATCH_LIMIT": rank_graph_match_limit,
            "RANK_DELTA_DISTRIBUTION_SCOPE": rank_delta_distribution_scope,
            "OBS_SCENE_SWITCHING_ENABLED": obs_scene_switching_enabled,
        }
        try:
            update_editable_settings(new_values)
        except ConfigError as exc:
            _logger.warning("設定画面(/admin)からの更新が拒否されました: %s(送信値: %s)", exc, new_values)
            return RedirectResponse(f"/admin?error={quote(str(exc))}", status_code=303)
        _logger.info("設定画面(/admin)から設定を更新しました: %s -> %s", old_values, new_values)
        return RedirectResponse("/admin?status=updated", status_code=303)

    @app.get("/rank-entry")
    def rank_entry(request: Request, status: Optional[str] = None, error: Optional[str] = None):
        context = {**_build_rank_entry_context(db_path), "status": status, "error": error}
        return _TEMPLATES.TemplateResponse(request, "rank_entry.html", context)

    @app.post("/rank-entry")
    def rank_entry_submit(match_id: int = Form(...), rank_after: str = Form(...)):
        try:
            rank_after_value = float(rank_after)
        except ValueError:
            return RedirectResponse(f"/rank-entry?error={quote('数値を入力してください')}", status_code=303)
        conn = _connect(db_path)
        try:
            save_manual_rank_after(conn, match_id, rank_after_value)
        except ValueError as exc:
            _logger.warning("手動ランク入力(/rank-entry)からの更新が拒否されました: %s", exc)
            return RedirectResponse(f"/rank-entry?error={quote(str(exc))}", status_code=303)
        finally:
            conn.close()
        _logger.info("手動ランク入力(/rank-entry)からrank_afterを記録しました: match_id=%d rank_after=%s", match_id, rank_after_value)
        return RedirectResponse("/rank-entry?status=saved", status_code=303)

    @app.get("/api/rank-entry-clips")
    def rank_entry_clips() -> dict:
        return _build_rank_entry_context(db_path)

    @app.get("/rank-entry/clips/{match_id}.mp4")
    def rank_entry_clip_file(match_id: int):
        clip_path = DEFAULT_CLIPS_DIR / f"{match_id}.mp4"
        if not clip_path.is_file():
            raise HTTPException(status_code=404, detail="クリップが見つかりません")
        return FileResponse(clip_path, media_type="video/mp4")

    @app.get("/rank-entry/gauge-clips/{match_id}.mp4")
    def rank_entry_gauge_clip_file(match_id: int):
        clip_path = GAUGE_CLIPS_DIR / f"{match_id}.mp4"
        if not clip_path.is_file():
            raise HTTPException(status_code=404, detail="クリップが見つかりません")
        return FileResponse(clip_path, media_type="video/mp4")

    @app.get("/overlay/winrate")
    def overlay_winrate(request: Request):
        winrate_data = _fetch_winrate(db_path)
        context = {
            "session": winrate_data["session"],
            "session_win_rate_text": _format_win_rate_text(winrate_data["session"]),
            "cumulative": winrate_data["cumulative"],
            "cumulative_win_rate_text": _format_win_rate_text(winrate_data["cumulative"]),
            "refresh_interval_ms": _OVERLAY_REFRESH_INTERVAL_MS,
            "debug_bg_style": _overlay_debug_bg_style(request),
        }
        return _TEMPLATES.TemplateResponse(request, "overlay_winrate.html", context)

    @app.get("/api/rank-history")
    def rank_history() -> dict:
        return {"matches": _fetch_rank_history(db_path)}

    @app.get("/overlay/rank-graph")
    def overlay_rank_graph(request: Request):
        history = _fetch_rank_history(db_path)
        summary = _fetch_rank_graph_summary(db_path)
        svg = _render_rank_graph_svg(history, summary)
        context = {
            "svg": svg,
            "refresh_interval_ms": _OVERLAY_REFRESH_INTERVAL_MS,
            "debug_bg_style": _overlay_debug_bg_style(request),
        }
        return _TEMPLATES.TemplateResponse(request, "overlay_rank_graph.html", context)

    @app.get("/api/goal-stats")
    def goal_stats() -> dict:
        return {"players": _fetch_goal_stats(db_path)}

    @app.get("/overlay/goal-stats")
    def overlay_goal_stats(request: Request):
        players = _fetch_goal_stats(db_path)
        # 許可リストが1名(=配信者本人)だけの場合、プレイヤー名を表示する意味が
        # 無い(自明なため)上、配信画面に自分の実名を出したくない場合もあるため、
        # 名前を出さない簡略表示に切り替える(ユーザーとの相談で決定)
        single_player_mode = len(get_allowed_players()) == 1
        context = {
            "players": players,
            "single_player_mode": single_player_mode,
            "refresh_interval_ms": _OVERLAY_REFRESH_INTERVAL_MS,
            "debug_bg_style": _overlay_debug_bg_style(request),
        }
        return _TEMPLATES.TemplateResponse(request, "overlay_goal_stats.html", context)

    @app.get("/api/match-log")
    def match_log() -> dict:
        return {"results": _fetch_match_log(db_path)}

    @app.get("/overlay/match-log")
    def overlay_match_log(request: Request):
        results = _fetch_match_log(db_path)
        badges = _build_match_log_badges(results)
        context = {
            "badges": badges,
            "refresh_interval_ms": _OVERLAY_REFRESH_INTERVAL_MS,
            "debug_bg_style": _overlay_debug_bg_style(request),
        }
        return _TEMPLATES.TemplateResponse(request, "overlay_match_log.html", context)

    @app.get("/api/vs-rank-comparison")
    def vs_rank_comparison() -> dict:
        comparison = _fetch_vs_rank_comparison(db_path)
        if comparison is None:
            return {"mine": None, "opponent": None}
        return comparison

    @app.get("/overlay/vs-rank-comparison")
    def overlay_vs_rank_comparison(request: Request):
        comparison = _fetch_vs_rank_comparison(db_path)
        # スナップショットが1件も無い・直近の試合でVS画面を見逃した場合も表示形式
        # 自体は崩さず、値を"-"にするだけにする(ユーザーとの相談で決定、Issue #276で
        # "none"から変更)。チームカラーも同様に検知できていない場合は_DEFAULT_TEAM_COLORにする
        context = (
            {
                "mine_value": _format_vs_rank_value(comparison["mine"]),
                "opponent_value": _format_vs_rank_value(comparison["opponent"]),
                "mine_team_color": comparison["mine_team_color"],
                "opponent_team_color": comparison["opponent_team_color"],
            }
            if comparison is not None
            else {
                "mine_value": "-",
                "opponent_value": "-",
                "mine_team_color": _DEFAULT_TEAM_COLOR,
                "opponent_team_color": _DEFAULT_TEAM_COLOR,
            }
        )
        # 他ウィジェットより短い間隔にし、VS画面確定後できるだけ早く新しい試合の
        # 値に切り替わるようにする(#100、ユーザーとの相談で決定)
        context["refresh_interval_ms"] = _VS_RANK_COMPARISON_REFRESH_INTERVAL_MS
        context["debug_bg_style"] = _overlay_debug_bg_style(request)
        return _TEMPLATES.TemplateResponse(request, "overlay_vs_rank_comparison.html", context)

    @app.get("/api/rank-delta-distribution")
    def rank_delta_distribution() -> dict:
        return _fetch_rank_delta_distribution(db_path)

    @app.get("/overlay/rank-delta-distribution")
    def overlay_rank_delta_distribution(request: Request):
        distribution = _fetch_rank_delta_distribution(db_path)
        svg = _render_rank_delta_box_plot_svg(distribution["win"], distribution["lose"])
        context = {
            "svg": svg,
            "refresh_interval_ms": _OVERLAY_REFRESH_INTERVAL_MS,
            "debug_bg_style": _overlay_debug_bg_style(request),
        }
        return _TEMPLATES.TemplateResponse(request, "overlay_rank_delta_distribution.html", context)

    @app.get("/overlay/dive-time")
    def overlay_dive_time(request: Request):
        context = {
            "dive_time": youtube_chat.get_dive_time(),
            "refresh_interval_ms": _OVERLAY_REFRESH_INTERVAL_MS,
            "debug_bg_style": _overlay_debug_bg_style(request),
        }
        return _TEMPLATES.TemplateResponse(request, "overlay_dive_time.html", context)

    # Issue #257: 全overlayルートの登録が終わった時点で1回だけ組み立てる
    # (リクエストのたびに再計算する必要は無く、ラベル追記漏れがあれば
    # アプリ起動時=create_app()呼び出し時点でRuntimeErrorにより気づける)
    app.state.overlay_links = _overlay_widget_links(app)

    return app
