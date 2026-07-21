"""配信画面向けWebダッシュボードのFastAPIアプリ(Issue #80: 技術検証PoC)。

ここではエンドポイントは最小限(ヘルスチェックとDB読み取りが動くことの確認用)
にとどめる。実際の表示要件(勝率・ランク推移グラフ・ゴール/アシスト統計)に
沿ったAPI設計・実装はIssue #81のスコープ。

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


def create_app(db_path: Path) -> FastAPI:
    app = FastAPI()

    @app.get("/api/health")
    def health() -> dict:
        return {"status": "ok"}

    @app.get("/api/matches/count")
    def matches_count() -> dict:
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

    return app
