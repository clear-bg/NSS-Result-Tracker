"""YouTube Liveのチャットコメントから「次に潜る時間」を検知する(Issue #265)。

配信中、自分(配信者)がチャット欄に数値のみのコメント(例: `24`)を打つと、それを
自動検知して`/overlay/dive-time`の表示を更新する。当初検討したわんコメ(OneComme)
経由の案は不採用とし、YouTube Data API v3を直接ポーリングする方式にした
(CLAUDE.md「チャットコメント連動『次に潜る時間』オーバーレイ」節参照)。

## 認証

OAuth 2.0(installed app flow)で自分のGoogleアカウントに一度だけ同意し、
リポジトリルートの`token.json`にリフレッシュトークンを保存する
(`scripts/youtube_oauth_setup.py`を参照、初回のみ手動実行)。動画ID/放送を
手入力させる案もあったが、配信のたびに手入力する手間を避けるため、OAuth認可した
自分のチャンネルの`liveBroadcasts.list(broadcastStatus="active")`で配信中の
放送を自動検出する方式を選んだ。`google-api-python-client`(discovery
ベースの重量級クライアント)は使わず、`httpx`での素のREST呼び出しで済ませている。
`mine`と`broadcastStatus`はYouTube Data API v3の仕様上どちらか一方のみ指定
可能なフィルタのため、`broadcastStatus`のみを指定する(Issue #267、同時指定は
400 Bad Requestになる)。

トークン読み込みに失敗した場合(`token.json`が無い、壊れている等)は
`obs_control.ObsSceneController`の接続失敗時と同じ考え方で、WARNINGログを
出したうえで検知を無効化したまま動作を継続する(本体の試合検知・DB記録とは
独立した付加機能のため)。

## 状態の持ち方

検知結果はDBを経由せず、モジュールレベルのインメモリ状態(`DiveTimeState`、
`get_dive_time_state()`)として保持する。他のWebダッシュボードの値はSQLiteを
介した疎結合(CLAUDE.md「配信画面向けWebダッシュボード」節)を原則としているが、
この値は配信セッションをまたいで参照する意味が無い一過性の値であり、DBに
永続化する価値が無いための意図的な逸脱。

Issue #356: 当初は`HH:MM`文字列のみを保持していたが、「スナイプ中」表示を
追加するにあたり、状態を`mode`(`"time"` / `"snipe"`)付きの`DiveTimeState`に
拡張した。自分(`isChatOwner`)のコメント本文の末尾が「スナイプ」または
「スナイプ中」で終わる場合(`_parse_snipe_comment`、正規表現`^(.*)スナイプ中?$`)、
それより前の部分(prefix、コメントが「スナイプ」単独等で空文字列になる場合もある)
を`DiveTimeState.snipe_target`に保持する。既存の数値のみ・時刻表現の検知
(`_parse_dive_time_comment`)とは文字種上排他なため、`_poll_chat_messages`側は
まず`_parse_dive_time_comment`で判定し、一致しなければスナイプパターンで判定する
順序で問題ない。スナイプ表示に切り替わった後もコメント監視は止めず、次に
数値のみ(または時刻表現)のコメントを受け取った時点で従来通りの`HH:MM`表示
(`mode="time"`)に自動的に戻る(解除専用コマンドは無い)。

## 見た目・機能のスコープ

初期実装は最小構成(`HH:MM`表示のみ)。表示スタイルの切替・配置指定・手動での
時刻操作ボタン・localStorageでの復元・自動クリアは対象外(必要になった時点で
別途検討する)。

Issue #356で「スナイプ中」表示を追加した際の見た目の決め事(Artifactで複数案を
比較しユーザーと決定):
- 見出し(従来の「次に潜る時間」相当)を「スナイプ中」に差し替え、キャプション先頭の
  菱形アイコンをオレンジ(`#ff6a3d`)にして点滅させる。「スナイプ中」の文字自体は
  従来の白のまま(色は変えない)
- その下の値行(従来のHH:MM相当)には、コメントから抽出したprefixに「配信」を
  付けたテキスト(例: 「たろうさんスナイプ」→「たろうさん配信」)を、時刻より
  控えめな見た目(小さめ・非bold)で表示する。prefixが空文字列(コメントが
  「スナイプ」単独等)の場合、値行自体を表示しない(見出しのみ)
"""

import logging
import re
import threading
from datetime import datetime, timedelta
from pathlib import Path
from typing import NamedTuple, Optional

import httpx
from google.auth.exceptions import RefreshError
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials

from nss_tracker.timeutil import now_jst

logger = logging.getLogger("nss_tracker.youtube_chat")

# DB_PATH等と同じく、リポジトリルート(main.py実行時のカレントディレクトリ)からの
# 相対パスとして扱う
_TOKEN_PATH = Path("token.json")

# 配信・チャットの読み取りのみ行うため読み取り専用スコープで十分
_SCOPES = ["https://www.googleapis.com/auth/youtube.readonly"]
_API_BASE = "https://www.googleapis.com/youtube/v3"

# 配信中の放送が見つからない間、再検索するまでの待機秒数
_BROADCAST_SEARCH_INTERVAL_SECONDS = 60
# APIエラー(クォータ超過・一時的な通信障害等)発生時のリトライまでの待機秒数
_ERROR_BACKOFF_SECONDS = 30
_HTTP_TIMEOUT_SECONDS = 10.0

_FULLWIDTH_TO_HALFWIDTH = str.maketrans("０１２３４５６７８９：", "0123456789:")
_MINUTE_ONLY_PATTERN = re.compile(r"^\d{1,2}$")
_HOUR_MINUTE_COLON_PATTERN = re.compile(r"^(\d{1,2}):(\d{2})$")
_HOUR_MINUTE_COMPACT_PATTERN = re.compile(r"^(\d{3,4})$")
# Issue #356: 末尾が「スナイプ」または「スナイプ中」で終わるコメントを検知する。
# 末尾一致にしているのは、「スナイプ」を含みさえすれば良い部分一致だと雑談コメント
# での誤発火リスクが上がるため
_SNIPE_PATTERN = re.compile(r"^(.*)スナイプ中?$")


class DiveTimeState(NamedTuple):
    """「次に潜る時間」ウィジェットの表示状態(Issue #356)。

    mode="time"の場合はtimeに"HH:MM"、mode="snipe"の場合はsnipe_targetに
    コメントから抽出したprefix(空文字列になる場合もある。モジュールdocstring参照)
    が入る。
    """

    mode: str
    time: Optional[str] = None
    snipe_target: Optional[str] = None


_dive_time_lock = threading.Lock()
_dive_time_state: Optional[DiveTimeState] = None


def get_dive_time_state() -> Optional[DiveTimeState]:
    """直近に検知した表示状態を返す。未検知ならNone。"""
    with _dive_time_lock:
        return _dive_time_state


def _set_dive_time(value: str) -> None:
    global _dive_time_state
    with _dive_time_lock:
        _dive_time_state = DiveTimeState(mode="time", time=value)


def _set_snipe_target(prefix: str) -> None:
    global _dive_time_state
    with _dive_time_lock:
        _dive_time_state = DiveTimeState(mode="snipe", snipe_target=prefix)


def _parse_dive_time_comment(text: str, now: datetime) -> Optional[str]:
    """チャットコメントの文字列を「次に潜る時間」(`"HH:MM"`)へ解決する。

    - `24`/`30`のような1〜2桁の数値: 分のみの指定として、直近の未来の
      「その分」に解決する(例: 現在23:35で`24`なら翌0:24)
    - `23:45`/`2345`: 時刻の指定としてそのまま解釈する。既に過ぎていれば
      翌日の同時刻に繰り上げる(配信中に過去の時刻を案内する意味が無いため)
    - 全角数字・全角コロンは半角に正規化し、末尾の「分」は許容する
    - 分が60以上・時が24以上、上記いずれにも一致しない文字列は`None`(無視)
    """
    normalized = text.strip().translate(_FULLWIDTH_TO_HALFWIDTH)
    if normalized.endswith("分"):
        normalized = normalized[:-1]

    if _MINUTE_ONLY_PATTERN.match(normalized):
        minute = int(normalized)
        if minute >= 60:
            return None
        candidate = now.replace(minute=minute, second=0, microsecond=0)
        if candidate <= now:
            candidate += timedelta(hours=1)
        return candidate.strftime("%H:%M")

    colon_match = _HOUR_MINUTE_COLON_PATTERN.match(normalized)
    compact_match = _HOUR_MINUTE_COMPACT_PATTERN.match(normalized)
    if colon_match:
        hour, minute = int(colon_match.group(1)), int(colon_match.group(2))
    elif compact_match:
        digits = compact_match.group(1)
        split = len(digits) - 2
        hour, minute = int(digits[:split]), int(digits[split:])
    else:
        return None

    if not (0 <= hour < 24 and 0 <= minute < 60):
        return None
    candidate = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if candidate <= now:
        candidate += timedelta(days=1)
    return candidate.strftime("%H:%M")


def _parse_snipe_comment(text: str) -> Optional[str]:
    """コメント本文の末尾が「スナイプ」または「スナイプ中」の場合、それより前の
    部分(prefix)を返す。一致しなければNone。

    prefixは前後の空白を除去して返す。コメントが「スナイプ」単独等の場合、
    prefixは空文字列になる(Noneとは区別する。呼び出し側はNoneを「非該当」、
    空文字列を「該当したがprefix無し」として扱う)。
    """
    match = _SNIPE_PATTERN.match(text.strip())
    if match is None:
        return None
    return match.group(1).strip()


class DiveTimeWatcher:
    """配信中の放送を自動検出し、自分自身のチャットコメントから
    「次に潜る時間」を検知してモジュールレベルの状態を更新するバックグラウンド監視。

    `obs_control.ObsSceneController`と同じく、初期化に失敗しても(トークンが
    無い・壊れている等)WARNINGログのみで以後何もしない無効状態になり、
    アプリ全体を止める理由にはしない。
    """

    def __init__(self, token_path: Path = _TOKEN_PATH) -> None:
        self._thread: Optional[threading.Thread] = None
        self._stopped = threading.Event()
        self._credentials: Optional[Credentials] = None
        self._live_chat_id: Optional[str] = None
        self._next_page_token: Optional[str] = None
        self._skip_next_result = False

        try:
            self._credentials = Credentials.from_authorized_user_file(str(token_path), _SCOPES)
        except (OSError, ValueError) as exc:
            logger.warning(
                "YouTube連携のトークン(%s)を読み込めませんでした。"
                "scripts/youtube_oauth_setup.pyを実行してください。"
                "「次に潜る時間」の検知は無効のまま動作を継続します: %s",
                token_path,
                exc,
            )

    def start(self) -> None:
        if self._credentials is None:
            return
        self._stopped.clear()
        self._thread = threading.Thread(target=self._run, daemon=True, name="nss-tracker-youtube-chat")
        self._thread.start()
        logger.info("YouTube Liveチャット監視を開始しました")

    def stop(self) -> None:
        self._stopped.set()

    def _run(self) -> None:
        while not self._stopped.is_set():
            try:
                if self._live_chat_id is None:
                    self._find_active_broadcast()
                else:
                    self._poll_chat_messages()
            except httpx.HTTPError as exc:
                logger.warning("YouTube Data APIの呼び出しに失敗しました: %s", exc)
                self._stopped.wait(_ERROR_BACKOFF_SECONDS)

    def _access_token(self) -> Optional[str]:
        assert self._credentials is not None
        if not self._credentials.valid:
            try:
                self._credentials.refresh(Request())
                _TOKEN_PATH.write_text(self._credentials.to_json(), encoding="utf-8")
            except RefreshError as exc:
                logger.warning(
                    "YouTube連携のトークン更新に失敗しました。"
                    "scripts/youtube_oauth_setup.pyを再実行してください: %s",
                    exc,
                )
                return None
        return self._credentials.token

    def _find_active_broadcast(self) -> None:
        token = self._access_token()
        if token is None:
            self._stopped.wait(_ERROR_BACKOFF_SECONDS)
            return
        response = httpx.get(
            f"{_API_BASE}/liveBroadcasts",
            params={"part": "snippet", "broadcastStatus": "active"},
            headers={"Authorization": f"Bearer {token}"},
            timeout=_HTTP_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
        items = response.json().get("items", [])
        if not items:
            self._stopped.wait(_BROADCAST_SEARCH_INTERVAL_SECONDS)
            return
        self._live_chat_id = items[0]["snippet"]["liveChatId"]
        self._next_page_token = None
        # 起動時点で既にチャット欄に溜まっている過去コメントを「今打たれたコメント」
        # として誤検知しないよう、最初の1ページはnextPageTokenの取得のみに使う
        self._skip_next_result = True
        logger.info("配信中の放送を検出しました。チャット監視を開始します(liveChatId=%s)", self._live_chat_id)

    def _poll_chat_messages(self) -> None:
        token = self._access_token()
        if token is None:
            self._stopped.wait(_ERROR_BACKOFF_SECONDS)
            return

        params = {"liveChatId": self._live_chat_id, "part": "snippet,authorDetails"}
        if self._next_page_token is not None:
            params["pageToken"] = self._next_page_token
        try:
            response = httpx.get(
                f"{_API_BASE}/liveChat/messages",
                params=params,
                headers={"Authorization": f"Bearer {token}"},
                timeout=_HTTP_TIMEOUT_SECONDS,
            )
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 404:
                logger.info("配信が終了したため、放送の再検出に戻ります")
                self._live_chat_id = None
                return
            raise

        payload = response.json()
        self._next_page_token = payload.get("nextPageToken")
        poll_interval_seconds = payload.get("pollingIntervalMillis", 10000) / 1000

        if self._skip_next_result:
            self._skip_next_result = False
        else:
            for item in payload.get("items", []):
                if not item.get("authorDetails", {}).get("isChatOwner"):
                    continue
                text = item.get("snippet", {}).get("displayMessage", "")
                dive_time = _parse_dive_time_comment(text, now_jst())
                if dive_time is not None:
                    _set_dive_time(dive_time)
                    logger.info(
                        "チャットコメントから「次に潜る時間」を検知しました: %s(コメント: %r)",
                        dive_time,
                        text,
                    )
                    continue
                snipe_target = _parse_snipe_comment(text)
                if snipe_target is not None:
                    _set_snipe_target(snipe_target)
                    logger.info(
                        "チャットコメントから「スナイプ中」を検知しました: prefix=%r(コメント: %r)",
                        snipe_target,
                        text,
                    )

        self._stopped.wait(poll_interval_seconds)
