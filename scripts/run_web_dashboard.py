"""Webダッシュボードの見た目を手元で確認するための起動スクリプト。

main.pyはキャプチャ(実機映像またはffmpegに渡す動画ファイル)が無いと起動
できないが、Webダッシュボードの見た目確認自体にはキャプチャは不要
(web/server.pyは検知ループと独立にDBファイルを読むだけ)。このスクリプトは
.envのDB_PATH/WEB_HOST/WEB_PORTだけを使ってWebサーバーのみを起動する。

既存の(実プレイで記録済みの)DBファイルをそのまま読み取り専用で使うため、
記録内容が変わることはない。DB_PATHのファイルが一度も作られていない環境では
matchesテーブル等が無くエラーになるため、事前に一度main.pyを起動しておくこと。

`uv run python scripts/run_web_dashboard.py` で起動し、Ctrl+Cで終了する。
"""

import time

from nss_tracker.config import ConfigError, get_db_path, get_web_host, get_web_port
from nss_tracker.web.runner import start_web_server_thread
from nss_tracker.web.server import create_app


def main() -> None:
    try:
        db_path = get_db_path()
        host = get_web_host()
        port = get_web_port()
    except ConfigError as exc:
        print(f"設定エラー: {exc}")
        return

    handle = start_web_server_thread(create_app(db_path), host=host, port=port)
    print(f"Webダッシュボードを起動しました(DB: {db_path})")
    print(f"  値確認ページ: http://{host}:{port}/")
    print(f"  勝率ウィジェット: http://{host}:{port}/overlay/winrate")
    print("Ctrl+Cで終了します")
    try:
        while True:
            time.sleep(1.0)
    except KeyboardInterrupt:
        print("終了します")
    finally:
        handle.stop()


if __name__ == "__main__":
    main()
