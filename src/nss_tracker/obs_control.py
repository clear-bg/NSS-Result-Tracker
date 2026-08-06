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

Issue #218対応: 接続成功直後に1回だけ、`.env`で設定した`OBS_SCENE_IN_MATCH`/
`OBS_SCENE_BETWEEN_MATCHES`が実際にOBS側のシーン一覧に存在するかを確認する。
設定ミス(タイポ・OBS側でのシーン名変更等)があった場合、これまでは実際に試合が
終わってシーン切替が必要になったタイミングで初めて気付けたが、起動時にログへ
残すことで配信中に初めて気付く事態を避ける。シーン一覧の取得自体に失敗しても
接続失敗時と同じ考え方でWARNINGログのみで動作を継続する。

Issue #247対応: 配信用ダッシュボードのウィジェットはOBSの「ブラウザ」ソースとして
表示しているが、ブラウザソースは一度読み込んだきり保持されるため、本アプリ
(Webサーバー)を再起動すると、OBS側で手動で「更新」ボタンを押さない限り表示が
復帰しない。接続成功直後に1回だけ、`.env`で設定した`OBS_BROWSER_SOURCE_NAMES`
(カンマ区切り)それぞれに対してobs-websocketの`PressInputPropertiesButton`
リクエスト(`press_input_properties_button`、プロパティ名`"refreshnocache"`)を
送り、「現在のページのキャッシュを更新」ボタンを自動的に押す。1つのソースの
更新に失敗しても他のソースの更新は試みる(個別にWARNINGログを出す)。
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

    def __init__(
        self,
        host: str,
        port: int,
        password: str,
        scene_in_match: str,
        scene_between_matches: str,
        browser_source_names: tuple[str, ...] = (),
    ) -> None:
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
            return
        self._verify_scenes_exist()
        self._refresh_browser_sources(browser_source_names)

    def _verify_scenes_exist(self) -> None:
        """OBS_SCENE_IN_MATCH/OBS_SCENE_BETWEEN_MATCHESが実際にOBS側に存在するかを
        起動時に1回だけ確認する(Issue #218)。

        設定ミス(タイポ・OBS側でのシーン名変更等)があった場合、これまでは実際に
        試合が終わってシーン切替が必要になったタイミングで初めてset_in_match()内の
        WARNINGログでしか気付けなかった。ここで事前に検知しログに残すことで、
        配信中に初めて気付く事態を避ける。シーン一覧の取得自体に失敗しても
        (OBS接続失敗時と同じ考え方で)アプリを止める理由にはならないため、
        WARNINGログを出したうえで動作は継続する(以降のset_in_match呼び出しは
        通常通り試みる)。
        """
        try:
            response = self._client.get_scene_list()
            scene_names = {scene["sceneName"] for scene in response.scenes}
        except (OSError, OBSSDKError, websocket.WebSocketException) as exc:
            logger.warning("OBSのシーン一覧を取得できなかったため、シーン名の事前確認をスキップします: %s", exc)
            return

        missing = []
        if self._scene_in_match not in scene_names:
            missing.append(f"OBS_SCENE_IN_MATCH={self._scene_in_match!r}")
        if self._scene_between_matches not in scene_names:
            missing.append(f"OBS_SCENE_BETWEEN_MATCHES={self._scene_between_matches!r}")

        if missing:
            logger.warning(
                "OBSに以下のシーンが見つかりません。設定を確認してください: %s",
                ", ".join(missing),
            )
        else:
            logger.info(
                "OBSシーン名を確認しました(試合中=%s 試合間=%s)。シーン自動切替の準備が整っています",
                self._scene_in_match,
                self._scene_between_matches,
            )

    def _refresh_browser_sources(self, browser_source_names: tuple[str, ...]) -> None:
        """OBS_BROWSER_SOURCE_NAMESで指定されたブラウザソースを起動時に1回だけ再読み込みする(Issue #247)。

        1つのソースの更新に失敗しても、他のソースの更新は引き続き試みる
        (どれか1つの設定ミスで全体が止まらないようにする)。
        """
        for name in browser_source_names:
            try:
                self._client.press_input_properties_button(name, "refreshnocache")
                logger.info("OBSブラウザソースを再読み込みしました: %s", name)
            except (OSError, OBSSDKError, websocket.WebSocketException) as exc:
                logger.warning("OBSブラウザソースの再読み込みに失敗しました(source=%s): %s", name, exc)

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
