"""Issue #80: Webサーバー(FastAPI/uvicorn)を検知ループと同一プロセスの
別スレッドで動かせるかの技術検証(PoC)スクリプト。

検知ループを模したダミーループ(一定間隔でDBに試合結果を書き込む)を
メインスレッドで走らせながら、別スレッドで起動したWebサーバーにHTTP
リクエストを送り、以下を確認する:

- Webサーバーの起動がメインスレッドのダミーループをブロックしないこと
- Webサーバー側が、メインスレッドが書き込んだDBの内容を(別コネクション
  経由で)読み取れること
- WebServerHandle.stop()によるシャットダウンが正常に完了すること

`uv run python scripts/web_server_poc.py` で実行する。
"""

import tempfile
import time
from pathlib import Path

import httpx

from nss_tracker.database import db
from nss_tracker.state.match_state import MatchResult
from nss_tracker.timeutil import now_jst
from nss_tracker.web.runner import start_web_server_thread
from nss_tracker.web.server import create_app

HOST = "127.0.0.1"
PORT = 8765
BASE_URL = f"http://{HOST}:{PORT}"
DUMMY_LOOP_ITERATIONS = 10
DUMMY_LOOP_INTERVAL_SECONDS = 0.2
RESULTS = ["win", "lose", "draw"]


def main() -> None:
    with tempfile.TemporaryDirectory() as tmp_dir:
        db_path = Path(tmp_dir) / "poc.db"
        conn = db.connect(db_path)

        handle = start_web_server_thread(create_app(db_path), host=HOST, port=PORT)
        print("[PoC] Webサーバーを起動しました(別スレッド)")

        try:
            resp = httpx.get(f"{BASE_URL}/api/health", timeout=2.0)
            assert resp.status_code == 200, resp.text
            print(f"[PoC] /api/health OK: {resp.json()}")

            print(f"[PoC] ダミー検知ループを開始します({DUMMY_LOOP_ITERATIONS}件)")
            loop_start = time.monotonic()
            for i in range(DUMMY_LOOP_ITERATIONS):
                match = MatchResult(
                    result=RESULTS[i % len(RESULTS)],
                    rank_before=40.0 + i,
                    rank_after=40.0 + i,
                    league_changed=None,
                    detected_at=now_jst(),
                )
                db.save_match_result(conn, match)
                time.sleep(DUMMY_LOOP_INTERVAL_SECONDS)
            loop_elapsed = time.monotonic() - loop_start

            expected_min = DUMMY_LOOP_ITERATIONS * DUMMY_LOOP_INTERVAL_SECONDS
            print(f"[PoC] ダミー検知ループ完了: {loop_elapsed:.2f}秒(想定最小 {expected_min:.2f}秒)")
            assert loop_elapsed < expected_min + 2.0, "Webサーバーがメインループをブロックしている可能性があります"

            resp = httpx.get(f"{BASE_URL}/api/matches/count", timeout=2.0)
            assert resp.status_code == 200, resp.text
            counts = resp.json()
            print(f"[PoC] /api/matches/count OK: {counts}")
            assert counts["total"] == DUMMY_LOOP_ITERATIONS, counts

            print("[PoC] 全項目PASS: 同一プロセス内でのWebサーバー同時稼働は技術的に成立する")
        finally:
            handle.stop()
            print("[PoC] Webサーバーを停止しました")
            conn.close()


if __name__ == "__main__":
    main()
