"""配信画面向けWebダッシュボードのFastAPIアプリ。

Issue #80(技術検証PoC)ではヘルスチェックとDB読み取りが動くことの確認用に
JSON APIのみを最小限で実装した。Issue #81ではこれに加えて、値が実際に
読めることをブラウザ(OBSのブラウザソース含む)で目視確認できる最小限の
HTMLページ(`/`)を追加する。実際の表示要件(勝率・ランク推移グラフ・
ゴール/アシスト統計)に沿ったレイアウト・ウィジェット分割はIssue #83の
スコープのため、ここでは装飾・分割は行わない。

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

from fastapi import FastAPI
from fastapi.responses import HTMLResponse


def _fetch_matches_count(db_path: Path) -> dict:
    conn = sqlite3.connect(db_path)
    try:
        row = conn.execute(
            "SELECT COUNT(*) AS total, "
            "SUM(CASE WHEN result = 'win' THEN 1 ELSE 0 END) AS win, "
            "SUM(CASE WHEN result = 'lose' THEN 1 ELSE 0 END) AS lose, "
            "SUM(CASE WHEN result = 'draw' THEN 1 ELSE 0 END) AS draw "
            "FROM matches"
        ).fetchone()
    finally:
        conn.close()
    return {
        "total": row[0] or 0,
        "win": row[1] or 0,
        "lose": row[2] or 0,
        "draw": row[3] or 0,
    }


def create_app(db_path: Path) -> FastAPI:
    app = FastAPI()

    @app.get("/api/health")
    def health() -> dict:
        return {"status": "ok"}

    @app.get("/api/matches/count")
    def matches_count() -> dict:
        return _fetch_matches_count(db_path)

    @app.get("/", response_class=HTMLResponse)
    def index() -> str:
        counts = _fetch_matches_count(db_path)
        return (
            "<!DOCTYPE html><html><head><meta charset=\"utf-8\">"
            "<title>NSS Result Tracker</title></head><body>"
            "<h1>値確認</h1>"
            f"<p>試合数: {counts['total']}</p>"
            f"<p>win: {counts['win']} / lose: {counts['lose']} / draw: {counts['draw']}</p>"
            "</body></html>"
        )

    return app
