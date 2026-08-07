"""YouTube Data API連携(Issue #265)の初回OAuth同意を行うセットアップスクリプト。

`youtube_chat.DiveTimeWatcher`はYouTube Liveのチャットコメントから「次に潜る時間」を
検知するために、自分のGoogleアカウントで認可したOAuthトークンを使う。このスクリプトは
その同意フローを一度だけ手動実行するためのもので、本体アプリ(main.py)の起動経路には
組み込まない(常時起動するアプリがブラウザ同意でブロックしないようにするため)。

事前準備:
1. Google Cloud Consoleでプロジェクトを作成し、「YouTube Data API v3」を有効化する
2. OAuthクライアントID(アプリケーションの種類: デスクトップアプリ)を作成し、
   ダウンロードしたJSONをこのリポジトリのルートに`client_secret.json`として置く
   (.gitignore対象、コミットしないこと)

実行方法: `uv run python scripts/youtube_oauth_setup.py`
ブラウザが開くので、コメントを検知したい自分のGoogleアカウントで同意する。
成功するとリポジトリルートに`token.json`が生成される(以後main.py実行時に
自動的に読み込まれ、必要に応じて自動更新される)。
"""

import sys
from pathlib import Path

from google_auth_oauthlib.flow import InstalledAppFlow

# DB_PATH等と同じく、リポジトリルート(uv run実行時のカレントディレクトリ)からの
# 相対パスとして扱う
_CLIENT_SECRET_PATH = Path("client_secret.json")
_TOKEN_PATH = Path("token.json")

# 配信・チャットの読み取りのみ行い、投稿等は行わないため読み取り専用スコープで十分
_SCOPES = ["https://www.googleapis.com/auth/youtube.readonly"]


def main() -> None:
    if not _CLIENT_SECRET_PATH.exists():
        print(
            f"{_CLIENT_SECRET_PATH}が見つかりません。"
            "Google Cloud ConsoleでOAuthクライアントID(デスクトップアプリ)を作成し、"
            "ダウンロードしたJSONをこのパスに配置してください"
            "(詳細はdocs/youtube_dive_time_setup.md参照)。",
            file=sys.stderr,
        )
        sys.exit(1)

    flow = InstalledAppFlow.from_client_secrets_file(str(_CLIENT_SECRET_PATH), _SCOPES)
    credentials = flow.run_local_server(port=0)

    _TOKEN_PATH.write_text(credentials.to_json(), encoding="utf-8")
    print(f"認可が完了しました。トークンを保存しました: {_TOKEN_PATH}")


if __name__ == "__main__":
    main()
