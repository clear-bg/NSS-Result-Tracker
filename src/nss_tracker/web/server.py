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

import sqlite3
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from nss_tracker.database.db import fetch_current_session_id

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

    return app
