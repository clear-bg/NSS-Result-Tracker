"""Webサーバーを検知ループと同一プロセス内の別スレッドで動かすランナー(Issue #80)。

uvicorn.Server.run()は自前でasyncioイベントループを起動する設計のため、
検知ループ(main.pyの同期処理)と同じプロセス内で共存させるには専用スレッドで
動かす必要がある。uvicornはメインスレッド以外で実行された場合はシグナル
ハンドラ(Ctrl+C等)を登録しない(Server.install_signal_handlers内で
threading.current_thread() is threading.main_thread()をチェックしている)ため、
メインスレッド側の検知ループのKeyboardInterrupt処理と衝突しない。
"""

import threading
import time
from dataclasses import dataclass

import uvicorn
from fastapi import FastAPI


@dataclass
class WebServerHandle:
    server: uvicorn.Server
    thread: threading.Thread

    def stop(self, timeout: float = 5.0) -> None:
        """サーバーに終了を指示し、スレッドの終了を待つ。"""
        self.server.should_exit = True
        self.thread.join(timeout=timeout)


def start_web_server_thread(app: FastAPI, host: str = "127.0.0.1", port: int = 8000) -> WebServerHandle:
    """FastAPIアプリをバックグラウンドスレッドでuvicorn起動し、ハンドルを返す。

    呼び出し元(検知ループ)をブロックしないよう、起動完了(server.started)を
    待ってから返す。
    """
    config = uvicorn.Config(app, host=host, port=port, log_level="warning")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True, name="nss-tracker-web")
    thread.start()

    while not server.started:
        time.sleep(0.01)

    return WebServerHandle(server=server, thread=thread)
