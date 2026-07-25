"""obs-websocket経由でOBSのシーンを試合状態に応じて自動切り替える(Issue #83)。

`state.match_state.MatchStateMachine.in_match`(VS画面確定〜試合結果確定の間True)
を購読し、Trueになった瞬間に試合中用シーン、Falseに戻った瞬間に試合間用シーンへ
切り替える。レイアウト自体(ワイプの位置・ダッシュボードの配置)はOBS側のシーン編集で
事前に組んでおく前提で、ここでは「どのシーンに切り替えるか」の判断のみ持つ
(CLAUDE.md「配信画面向けWebダッシュボード」節と同じ、疎結合の考え方)。

obs-websocketクライアントは`obsws-python`(v5プロトコル、OBS 28以降に標準搭載)を使う。

OBSへの接続はあくまで配信演出のための付加機能であり、検知・DB記録という
本来の機能とは独立している。OBSが未起動・obs-websocketが無効・パスワード不一致
などで接続に失敗しても、アプリ全体を止める理由にはならないため、接続失敗時は
WARNINGログを出したうえでシーン切替を無効化した状態のまま動作を継続する
(以降の`set_in_match`呼び出しは何もしない)。
"""

import logging

import obsws_python as obs
import websocket
from obsws_python.error import OBSSDKError

logger = logging.getLogger("nss_tracker.obs_control")

# obs-websocketへの接続自体が詰まってアプリ起動を長時間ブロックしないための上限
_CONNECT_TIMEOUT_SECONDS = 3


class ObsSceneController:
    """MatchStateMachine.in_matchの変化に応じてOBSシーンを切り替える。"""

    def __init__(self, host: str, port: int, password: str, scene_in_match: str, scene_between_matches: str) -> None:
        self._scene_in_match = scene_in_match
        self._scene_between_matches = scene_between_matches
        self._client: obs.ReqClient | None = None
        try:
            self._client = obs.ReqClient(host=host, port=port, password=password, timeout=_CONNECT_TIMEOUT_SECONDS)
            logger.info("OBS(obs-websocket)へ接続しました: host=%s port=%d", host, port)
        except (OSError, OBSSDKError, websocket.WebSocketException) as exc:
            logger.warning(
                "OBS(obs-websocket)への接続に失敗しました。シーン自動切替は無効のまま動作を継続します: %s", exc
            )

    def set_in_match(self, in_match: bool) -> None:
        """試合中/試合間に応じたシーンへ切り替える。接続に失敗している場合は何もしない。"""
        if self._client is None:
            return
        scene = self._scene_in_match if in_match else self._scene_between_matches
        try:
            self._client.set_current_program_scene(scene)
            logger.info("OBSシーンを切り替えました: %s", scene)
        except (OSError, OBSSDKError, websocket.WebSocketException) as exc:
            logger.warning("OBSシーンの切り替えに失敗しました(scene=%s): %s", scene, exc)

    def close(self) -> None:
        if self._client is None:
            return
        try:
            self._client.disconnect()
        except (OSError, OBSSDKError, websocket.WebSocketException):
            pass
