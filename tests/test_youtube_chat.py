import logging
from datetime import datetime
from pathlib import Path

import httpx
import pytest

from nss_tracker import youtube_chat
from nss_tracker.youtube_chat import DiveTimeState, DiveTimeWatcher, _parse_dive_time_comment, _parse_snipe_comment

# 現在時刻を23:35に固定してテストする(分のみ指定の繰り上げを検証しやすいため)
_NOW = datetime(2026, 8, 7, 23, 35)


@pytest.mark.parametrize(
    "text,expected",
    [
        # 分のみ指定: 現在の分を過ぎていれば次の時に繰り上げる
        ("24", "00:24"),
        ("30", "00:30"),
        # 分のみ指定: 現在の分より未来ならそのまま同じ時
        ("40", "23:40"),
        # 全角数字は半角に正規化する
        ("２４", "00:24"),
        # 末尾の「分」は許容する
        ("24分", "00:24"),
        # HH:MM形式(コロンあり): 過ぎていなければそのまま
        ("23:45", "23:45"),
        # HH:MM形式(コロンなし、4桁)
        ("2345", "23:45"),
        # HH:MM形式(コロンなし、3桁=1桁の時)
        ("930", "09:30"),
        # 全角コロンも正規化する
        ("２３：４５", "23:45"),
    ],
)
def test_parse_dive_time_comment_valid_inputs(text, expected):
    assert _parse_dive_time_comment(text, _NOW) == expected


@pytest.mark.parametrize(
    "text",
    [
        "abc",  # 数値以外
        "99",  # 分が60以上
        "25:00",  # 時が24以上
        "23:60",  # 分が60以上(コロン形式)
        "",  # 空文字列
    ],
)
def test_parse_dive_time_comment_invalid_inputs_ignored(text):
    assert _parse_dive_time_comment(text, _NOW) is None


@pytest.mark.parametrize(
    "text,expected_prefix",
    [
        ("たろうさんスナイプ", "たろうさん"),
        ("たろうさんスナイプ中", "たろうさん"),  # Issue #356: 「スナイプ中」終わりも対象
        ("スナイプ", ""),  # prefix無し(コメントが「スナイプ」単独)
        ("スナイプ中", ""),  # prefix無し(コメントが「スナイプ中」単独)
        (" たろうさんスナイプ ", "たろうさん"),  # 前後の空白は無視する
    ],
)
def test_parse_snipe_comment_valid_inputs(text, expected_prefix):
    assert _parse_snipe_comment(text) == expected_prefix


@pytest.mark.parametrize(
    "text",
    [
        "24",  # 数値のみ検知パターンと排他
        "スナイプしてくる",  # 末尾がスナイプで終わっていない
        "スナイプ中に負けた",  # 末尾がスナイプ中で終わっていない
        "",  # 空文字列
    ],
)
def test_parse_snipe_comment_invalid_inputs_ignored(text):
    assert _parse_snipe_comment(text) is None


class _FakeResponse:
    def __init__(self, payload: dict):
        self._payload = payload

    def raise_for_status(self) -> None:
        pass

    def json(self) -> dict:
        return self._payload


class _FakeCredentials:
    valid = True
    token = "fake-token"

    def refresh(self, request) -> None:
        pass

    def to_json(self) -> str:
        return "{}"


def _make_watcher_with_fake_credentials(monkeypatch: pytest.MonkeyPatch) -> DiveTimeWatcher:
    monkeypatch.setattr(
        youtube_chat.Credentials,
        "from_authorized_user_file",
        staticmethod(lambda path, scopes: _FakeCredentials()),
    )
    return DiveTimeWatcher(token_path=Path("unused-token.json"))


def test_watcher_disables_itself_when_token_file_is_missing(caplog):
    """token.jsonが無い(未セットアップ)場合、WARNINGログのみで例外を送出せず、
    start()を呼んでもスレッドが起動しないことを確認する(本体機能を止めない設計)。
    """
    watcher = DiveTimeWatcher(token_path=Path("this-token-file-does-not-exist.json"))

    watcher.start()

    assert watcher._thread is None


def test_find_active_broadcast_caches_live_chat_id_and_skips_first_page(monkeypatch):
    watcher = _make_watcher_with_fake_credentials(monkeypatch)
    monkeypatch.setattr(
        youtube_chat.httpx,
        "get",
        lambda *args, **kwargs: _FakeResponse({"items": [{"snippet": {"liveChatId": "chat123"}}]}),
    )

    watcher._find_active_broadcast()

    assert watcher._live_chat_id == "chat123"
    assert watcher._skip_next_result is True


def test_poll_chat_messages_ignores_backlog_on_first_poll(monkeypatch):
    """放送検出直後の最初の1ページは、過去に溜まっていたコメントを『今打たれた
    コメント』として誤検知しないよう状態更新に使わないことを確認する。
    """
    monkeypatch.setattr(youtube_chat, "_dive_time_state", None)
    watcher = _make_watcher_with_fake_credentials(monkeypatch)
    watcher._live_chat_id = "chat123"
    watcher._skip_next_result = True
    monkeypatch.setattr(
        youtube_chat.httpx,
        "get",
        lambda *args, **kwargs: _FakeResponse(
            {
                "nextPageToken": "token-1",
                "pollingIntervalMillis": 0,
                "items": [{"authorDetails": {"isChatOwner": True}, "snippet": {"displayMessage": "24"}}],
            }
        ),
    )

    watcher._poll_chat_messages()

    assert youtube_chat.get_dive_time_state() is None
    assert watcher._skip_next_result is False


def test_poll_chat_messages_updates_dive_time_only_for_chat_owner(monkeypatch):
    """視聴者(isChatOwner=False)のコメントでは絶対に表示が更新されないことを確認する。"""
    monkeypatch.setattr(youtube_chat, "_dive_time_state", None)
    # 分のみ指定のパース結果(繰り上げの有無)が実行時刻に依存しないよう固定する
    monkeypatch.setattr(youtube_chat, "now_jst", lambda: _NOW)
    watcher = _make_watcher_with_fake_credentials(monkeypatch)
    watcher._live_chat_id = "chat123"
    watcher._skip_next_result = False
    monkeypatch.setattr(
        youtube_chat.httpx,
        "get",
        lambda *args, **kwargs: _FakeResponse(
            {
                "nextPageToken": "token-2",
                "pollingIntervalMillis": 0,
                "items": [
                    {"authorDetails": {"isChatOwner": False}, "snippet": {"displayMessage": "24"}},
                    {"authorDetails": {"isChatOwner": True}, "snippet": {"displayMessage": "abc"}},
                ],
            }
        ),
    )

    watcher._poll_chat_messages()

    # 視聴者コメント(24)は無視され、配信者コメント(abc、パース不能)も無視されるため未設定のまま
    assert youtube_chat.get_dive_time_state() is None

    monkeypatch.setattr(
        youtube_chat.httpx,
        "get",
        lambda *args, **kwargs: _FakeResponse(
            {
                "nextPageToken": "token-3",
                "pollingIntervalMillis": 0,
                "items": [
                    {"authorDetails": {"isChatOwner": False}, "snippet": {"displayMessage": "45"}},
                    {"authorDetails": {"isChatOwner": True}, "snippet": {"displayMessage": "40"}},
                ],
            }
        ),
    )

    watcher._poll_chat_messages()

    # 配信者コメント(40)のみが反映され、同じページ内の視聴者コメント(45)は無視される
    assert youtube_chat.get_dive_time_state() == DiveTimeState(mode="time", time="23:40")


def test_poll_chat_messages_switches_to_snipe_state(monkeypatch):
    """Issue #356: 末尾が「スナイプ」のコメントでスナイプ中表示に切り替わる。"""
    monkeypatch.setattr(youtube_chat, "_dive_time_state", None)
    monkeypatch.setattr(youtube_chat, "now_jst", lambda: _NOW)
    watcher = _make_watcher_with_fake_credentials(monkeypatch)
    watcher._live_chat_id = "chat123"
    watcher._skip_next_result = False
    monkeypatch.setattr(
        youtube_chat.httpx,
        "get",
        lambda *args, **kwargs: _FakeResponse(
            {
                "nextPageToken": "token-1",
                "pollingIntervalMillis": 0,
                "items": [
                    {"authorDetails": {"isChatOwner": True}, "snippet": {"displayMessage": "たろうさんスナイプ"}}
                ],
            }
        ),
    )

    watcher._poll_chat_messages()

    assert youtube_chat.get_dive_time_state() == DiveTimeState(mode="snipe", snipe_target="たろうさん")


def test_poll_chat_messages_returns_to_time_state_from_snipe(monkeypatch):
    """Issue #356: スナイプ中表示に切り替わった後も、次に数値のみのコメントを
    受け取れば従来通りのHH:MM表示に自動的に戻る(解除専用コマンドは無い)。
    """
    monkeypatch.setattr(youtube_chat, "_dive_time_state", DiveTimeState(mode="snipe", snipe_target="たろうさん"))
    monkeypatch.setattr(youtube_chat, "now_jst", lambda: _NOW)
    watcher = _make_watcher_with_fake_credentials(monkeypatch)
    watcher._live_chat_id = "chat123"
    watcher._skip_next_result = False
    monkeypatch.setattr(
        youtube_chat.httpx,
        "get",
        lambda *args, **kwargs: _FakeResponse(
            {
                "nextPageToken": "token-1",
                "pollingIntervalMillis": 0,
                "items": [{"authorDetails": {"isChatOwner": True}, "snippet": {"displayMessage": "40"}}],
            }
        ),
    )

    watcher._poll_chat_messages()

    assert youtube_chat.get_dive_time_state() == DiveTimeState(mode="time", time="23:40")


def _make_http_status_error(status_code: int, body: str) -> httpx.HTTPStatusError:
    request = httpx.Request("GET", "https://example.invalid")
    response = httpx.Response(status_code, text=body, request=request)
    return httpx.HTTPStatusError(f"{status_code} error", request=request, response=response)


def test_describe_http_error_includes_response_body_for_http_status_error():
    """Issue #372: 403等のレスポンス本文(reasonフィールド等)をログに含められるよう、
    _describe_http_error()がhttpx.HTTPStatusErrorの本文を返すことを確認する。
    """
    exc = _make_http_status_error(403, '{"error": {"errors": [{"reason": "quotaExceeded"}]}}')

    detail = youtube_chat._describe_http_error(exc)

    assert "quotaExceeded" in detail


def test_describe_http_error_truncates_long_body():
    """本文が長すぎる場合はログを圧迫しないよう切り詰められることを確認する。"""
    exc = _make_http_status_error(403, "x" * 1000)

    detail = youtube_chat._describe_http_error(exc)

    assert len(detail) < 1000
    assert detail.endswith("...(truncated)")


def test_describe_http_error_empty_for_connection_error():
    """接続エラー等(レスポンス自体が無い)では空文字列を返すことを確認する。"""
    exc = httpx.ConnectTimeout("timeout")

    detail = youtube_chat._describe_http_error(exc)

    assert detail == ""


def test_run_logs_response_body_on_http_status_error(monkeypatch, caplog):
    """Issue #372: _run()のループが403等を捕まえた際、WARNINGログにレスポンス
    本文(reasonフィールド等)が含まれることを確認する(_describe_http_error()の
    呼び出し配線自体の検証)。
    """
    watcher = _make_watcher_with_fake_credentials(monkeypatch)
    watcher._live_chat_id = "chat123"  # _find_active_broadcast()を飛ばして_poll_chat_messages()へ
    exc = _make_http_status_error(403, '{"error": {"errors": [{"reason": "quotaExceeded"}]}}')

    def fake_poll_chat_messages() -> None:
        # ループを1回だけで終わらせる(次のwhileチェック前に停止フラグを立てる)
        watcher._stopped.set()
        raise exc

    monkeypatch.setattr(watcher, "_poll_chat_messages", fake_poll_chat_messages)

    with caplog.at_level(logging.WARNING, logger="nss_tracker.youtube_chat"):
        watcher._run()

    assert "quotaExceeded" in caplog.text
