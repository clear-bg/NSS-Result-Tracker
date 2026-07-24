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

エンドポイントごとに新規のsqlite3コネクションを開いて処理後すぐ閉じる。
sqlite3のコネクションはデフォルトでは開いたスレッド以外から使えず
(check_same_thread=True)、FastAPI/uvicornは同期defのエンドポイントを
スレッドプール上の任意のスレッドで実行するため、検知ループ側のコネクション
(database.db.connect())を使い回すことはできない。SQLiteは複数の読み取り専用
コネクションが同時に存在すること自体は問題ないため、リクエストごとに開閉する
方式で十分。
"""

import math
import sqlite3
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from nss_tracker.config import get_allowed_players, get_rank_graph_match_limit, is_allowed_player
from nss_tracker.database.db import (
    fetch_all_matches,
    fetch_current_session_id,
    fetch_goals_for_session,
    fetch_recent_matches,
)

_WEB_DIR = Path(__file__).parent
_TEMPLATES = Jinja2Templates(directory=_WEB_DIR / "templates")


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


# ランク推移グラフのSVG座標系(viewBox内の論理サイズ)。width/height="100%"で
# 実際の表示サイズ(OBSブラウザソースの矩形)まで引き伸ばす。左右上下でマージンを
# 分けているのは、左に縦軸のラベル・下に横軸のラベル分の余白が必要なため
_RANK_GRAPH_VIEWBOX_WIDTH = 640
_RANK_GRAPH_VIEWBOX_HEIGHT = 220
_RANK_GRAPH_MARGIN_LEFT = 50
_RANK_GRAPH_MARGIN_RIGHT = 20
_RANK_GRAPH_MARGIN_TOP = 20
_RANK_GRAPH_MARGIN_BOTTOM = 30
# 一番左の点がプロット領域の左端(枠)に接しないための余白(px)。右側は
# _rank_graph_x_axis_maxで軸自体の右端(試合番号の上限)を実際の試合数より
# 広げることで余白を作る(縦軸の_rank_graph_y_boundsと同じ考え方)ため、
# 左側だけピクセル単位の余白が別途必要になる(軸の下限を1より前には拡張できないため)
_RANK_GRAPH_LEFT_PADDING = 24
# 横軸(試合番号)の目盛りは、この値の倍数(5, 10, 15, ...)の位置に置く
# (均等に割った本数で置く方式だと間隔が5,5,4,5のようにガタつくため、固定間隔にした。
# ユーザーとの相談で決定)
_RANK_GRAPH_X_TICK_STEP = 5


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


def _rank_graph_x_axis_max(point_count: int) -> int:
    """横軸(試合番号)の右端の値を返す(ユーザーとの相談で決定、縦軸のbounds拡張と同じ考え方)。

    実際の試合数(point_count)を必ず上回る、_RANK_GRAPH_X_TICK_STEPの倍数にする
    (例: 23試合なら25)。ちょうど倍数の試合数(例: 20試合)でも一番右の点が
    軸の端に接してしまうため、その場合はさらに1段広げる。
    """
    axis_max = math.ceil(point_count / _RANK_GRAPH_X_TICK_STEP) * _RANK_GRAPH_X_TICK_STEP
    if axis_max <= point_count:
        axis_max += _RANK_GRAPH_X_TICK_STEP
    return axis_max


def _rank_graph_x_tick_values(axis_max: int) -> list[int]:
    """横軸(試合番号)の目盛りとして表示する試合番号(1始まり)を返す。

    最初の試合(1)を必ず含み(ユーザーとの相談で決定)、そこに_RANK_GRAPH_X_TICK_STEP
    刻みの値をaxis_max(実際の試合数を上回るよう拡張した右端の値、
    _rank_graph_x_axis_max参照)まで加える。
    """
    values = {1}
    values.update(range(_RANK_GRAPH_X_TICK_STEP, axis_max + 1, _RANK_GRAPH_X_TICK_STEP))
    return sorted(values)


def _render_rank_graph_svg(history: list[dict]) -> str:
    """ランク推移を、枠・縦横の目盛り付きの折れ線グラフとしてSVG文字列で描画する。

    JS・外部チャートライブラリは使わずサーバー側でSVGを組み立てる(配信環境の
    ネット接続が不安定でも表示が壊れないようにするための方針、ユーザーとの
    相談で決定)。昇格/降格(league_changed)による点の色分けはしない(全て白、
    ユーザーとの相談で決定)。
    """
    width, height = _RANK_GRAPH_VIEWBOX_WIDTH, _RANK_GRAPH_VIEWBOX_HEIGHT
    svg_open = f'<svg viewBox="0 0 {width} {height}" width="100%" height="100%" xmlns="http://www.w3.org/2000/svg">'

    if not history:
        return (
            f"{svg_open}"
            f'<text x="{width / 2}" y="{height / 2}" text-anchor="middle" class="rank-graph-empty">'
            "データがありません</text></svg>"
        )

    plot_left, plot_right = _RANK_GRAPH_MARGIN_LEFT, width - _RANK_GRAPH_MARGIN_RIGHT
    plot_top, plot_bottom = _RANK_GRAPH_MARGIN_TOP, height - _RANK_GRAPH_MARGIN_BOTTOM
    plot_width = plot_right - plot_left
    plot_height = plot_bottom - plot_top
    # 一番左の点がプロット領域の左端に接しないよう、内側に寄せた左端を使う
    # (枠・グリッド線自体はplot_leftのまま、軸自体の見た目は変えない)
    points_left = plot_left + _RANK_GRAPH_LEFT_PADDING

    values = [point["rank_after"] for point in history]
    axis_min, axis_max = _rank_graph_y_bounds(min(values), max(values))
    axis_range = axis_max - axis_min
    x_axis_max = _rank_graph_x_axis_max(len(history))
    x_axis_max_index = x_axis_max - 1  # 試合番号(1始まり)を0始まりのインデックスに変換

    def x_at(index: int) -> float:
        return points_left + (plot_right - points_left) * index / x_axis_max_index

    def y_at(value: float) -> float:
        return plot_top + plot_height * (1 - (value - axis_min) / axis_range)

    # 縦軸目盛り: 横向きの薄いグリッド線+左側にランク値のラベル+横軸と同様の短い
    # 目盛り線。整数のみ・間隔1(ユーザーとの相談で決定)。軸の下限・上限自体を
    # 実データより広げてあるため(_rank_graph_y_bounds参照)、一番上・一番下の点は
    # 自然に軸の端から離れる
    y_axis_svg = []
    for tick_value in range(axis_min, axis_max + 1):
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

    # 枠(プロット領域を囲む矩形)
    frame_svg = (
        f'<rect x="{plot_left}" y="{plot_top}" width="{plot_width}" height="{plot_height}" '
        'class="rank-graph-frame" />'
    )

    # 横軸目盛り: 縦向きの薄いグリッド線(縦軸のグリッド線と同様)+下側に短い目盛り線+
    # 試合番号のラベル。1試合目を必ず含み、実際の試合数を上回る位置(x_axis_max)まで
    # 5刻みで表示する(ユーザーとの相談で決定)
    x_axis_svg = []
    for match_number in _rank_graph_x_tick_values(x_axis_max):
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

    markers = [f'<circle cx="{x:.1f}" cy="{y:.1f}" r="4" class="rank-graph-point" />' for x, y in coords]

    return (
        f"{svg_open}"
        f"{''.join(y_axis_svg)}"
        f"{frame_svg}"
        f"{''.join(x_axis_svg)}"
        f'<polyline points="{polyline_points}" class="rank-graph-line" />'
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
    """
    counts: dict[str, dict[str, int]] = {}
    for row in rows:
        scorer_name = row["scorer_name"]
        if scorer_name and is_allowed_player(scorer_name):
            counts.setdefault(scorer_name, {"goals": 0, "assists": 0})
            counts[scorer_name]["goals"] += 1
        assist_name = row["assist_name"]
        if assist_name and is_allowed_player(assist_name):
            counts.setdefault(assist_name, {"goals": 0, "assists": 0})
            counts[assist_name]["assists"] += 1

    players = [
        {"name": name, "goals": c["goals"], "assists": c["assists"], "involvement": c["goals"] + c["assists"]}
        for name, c in counts.items()
    ]
    players.sort(key=lambda p: (-p["involvement"], -p["goals"], p["name"]))
    return players


def _fetch_goal_stats(db_path: Path) -> list[dict]:
    """現在の配信セッションのゴール/アシスト統計を、プレイヤー別に集計して返す。

    配信セッションが1件も無い場合(main.py未起動でDBのみ閲覧している場合等)は
    空リストを返す。
    """
    conn = _connect(db_path)
    try:
        session_id = fetch_current_session_id(conn)
        if session_id is None:
            return []
        rows = fetch_goals_for_session(conn, session_id)
    finally:
        conn.close()
    return _aggregate_goal_stats(rows)


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

    @app.get("/overlay/winrate")
    def overlay_winrate(request: Request):
        winrate_data = _fetch_winrate(db_path)
        context = {
            "session": winrate_data["session"],
            "session_win_rate_text": _format_win_rate_text(winrate_data["session"]),
            "cumulative": winrate_data["cumulative"],
            "cumulative_win_rate_text": _format_win_rate_text(winrate_data["cumulative"]),
        }
        return _TEMPLATES.TemplateResponse(request, "overlay_winrate.html", context)

    @app.get("/api/rank-history")
    def rank_history() -> dict:
        return {"matches": _fetch_rank_history(db_path)}

    @app.get("/overlay/rank-graph")
    def overlay_rank_graph(request: Request):
        history = _fetch_rank_history(db_path)
        svg = _render_rank_graph_svg(history)
        return _TEMPLATES.TemplateResponse(request, "overlay_rank_graph.html", {"svg": svg})

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
        context = {"players": players, "single_player_mode": single_player_mode}
        return _TEMPLATES.TemplateResponse(request, "overlay_goal_stats.html", context)

    return app
