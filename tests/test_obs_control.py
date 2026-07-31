"""ObsSceneController(Issue #83)のテスト。

実際のOBS接続は行わず、obsws_python.ReqClientをモックして以下を確認する:
- in_matchの真偽に応じて正しいシーン名でset_current_program_sceneを呼ぶこと
- OBSへの接続・切替失敗時にアプリを止めず、WARNINGログを出して継続すること
  (検知・DB記録という本来機能から独立した付加機能であるため)
"""

from obsws_python.error import OBSSDKError

import nss_tracker.obs_control as obs_control_module
from nss_tracker.obs_control import ObsSceneController


class _FakeSceneListResponse:
    def __init__(self, scene_names: list[str]):
        self.scenes = [{"sceneName": name} for name in scene_names]


class _FakeReqClient:
    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.scenes_set: list[str] = []
        self.disconnected = False
        # 既存テストへの影響を避けるため、デフォルトではInMatch/BetweenMatchesの
        # 両方が存在するものとして振る舞う(Issue #218のシーン存在確認テストのみ、
        # このリストを差し替える)
        self._scene_list = ["InMatch", "BetweenMatches"]

    def set_current_program_scene(self, name):
        self.scenes_set.append(name)

    def get_scene_list(self):
        return _FakeSceneListResponse(self._scene_list)

    def disconnect(self):
        self.disconnected = True


class _RaisingReqClient:
    def __init__(self, **kwargs):
        raise OSError("connection refused")


def test_set_in_match_true_switches_to_in_match_scene(monkeypatch):
    fake_client = _FakeReqClient()
    monkeypatch.setattr(obs_control_module.obs, "ReqClient", lambda **kwargs: fake_client)

    controller = ObsSceneController(
        host="127.0.0.1", port=4455, password="", scene_in_match="InMatch", scene_between_matches="BetweenMatches"
    )
    controller.set_in_match(True)

    assert fake_client.scenes_set == ["InMatch"]


def test_set_in_match_false_switches_to_between_matches_scene(monkeypatch):
    fake_client = _FakeReqClient()
    monkeypatch.setattr(obs_control_module.obs, "ReqClient", lambda **kwargs: fake_client)

    controller = ObsSceneController(
        host="127.0.0.1", port=4455, password="", scene_in_match="InMatch", scene_between_matches="BetweenMatches"
    )
    controller.set_in_match(False)

    assert fake_client.scenes_set == ["BetweenMatches"]


def test_connect_failure_logs_warning_and_disables_switching(monkeypatch, caplog):
    monkeypatch.setattr(obs_control_module.obs, "ReqClient", _RaisingReqClient)

    with caplog.at_level("WARNING", logger="nss_tracker.obs_control"):
        controller = ObsSceneController(
            host="127.0.0.1",
            port=4455,
            password="",
            scene_in_match="InMatch",
            scene_between_matches="BetweenMatches",
        )
        # 接続に失敗していても例外を出さず、何もしないことを確認する
        controller.set_in_match(True)
        controller.close()

    assert "接続に失敗しました" in caplog.text


def test_set_in_match_failure_logs_warning_without_raising(monkeypatch, caplog):
    class _FailingSetSceneClient(_FakeReqClient):
        def set_current_program_scene(self, name):
            raise OBSSDKError("SetCurrentProgramScene", 600, "scene not found")

    monkeypatch.setattr(obs_control_module.obs, "ReqClient", lambda **kwargs: _FailingSetSceneClient())

    controller = ObsSceneController(
        host="127.0.0.1", port=4455, password="", scene_in_match="InMatch", scene_between_matches="BetweenMatches"
    )
    with caplog.at_level("WARNING", logger="nss_tracker.obs_control"):
        controller.set_in_match(True)

    assert "切り替えに失敗しました" in caplog.text


def test_close_disconnects_client(monkeypatch):
    fake_client = _FakeReqClient()
    monkeypatch.setattr(obs_control_module.obs, "ReqClient", lambda **kwargs: fake_client)

    controller = ObsSceneController(
        host="127.0.0.1", port=4455, password="", scene_in_match="InMatch", scene_between_matches="BetweenMatches"
    )
    controller.close()

    assert fake_client.disconnected is True


def test_verify_scenes_exist_logs_info_when_both_scenes_found(monkeypatch, caplog):
    fake_client = _FakeReqClient()
    fake_client._scene_list = ["InMatch", "BetweenMatches", "Other"]
    monkeypatch.setattr(obs_control_module.obs, "ReqClient", lambda **kwargs: fake_client)

    with caplog.at_level("INFO", logger="nss_tracker.obs_control"):
        ObsSceneController(
            host="127.0.0.1", port=4455, password="", scene_in_match="InMatch", scene_between_matches="BetweenMatches"
        )

    assert "準備が整っています" in caplog.text


def test_verify_scenes_exist_logs_warning_when_scene_missing(monkeypatch, caplog):
    fake_client = _FakeReqClient()
    fake_client._scene_list = ["InMatch"]  # BetweenMatchesが存在しない
    monkeypatch.setattr(obs_control_module.obs, "ReqClient", lambda **kwargs: fake_client)

    with caplog.at_level("WARNING", logger="nss_tracker.obs_control"):
        ObsSceneController(
            host="127.0.0.1", port=4455, password="", scene_in_match="InMatch", scene_between_matches="BetweenMatches"
        )

    assert "OBSに以下のシーンが見つかりません" in caplog.text
    assert "OBS_SCENE_BETWEEN_MATCHES" in caplog.text
    assert "OBS_SCENE_IN_MATCH" not in caplog.text


def test_verify_scenes_exist_logs_warning_when_both_scenes_missing(monkeypatch, caplog):
    fake_client = _FakeReqClient()
    fake_client._scene_list = ["Unrelated"]
    monkeypatch.setattr(obs_control_module.obs, "ReqClient", lambda **kwargs: fake_client)

    with caplog.at_level("WARNING", logger="nss_tracker.obs_control"):
        ObsSceneController(
            host="127.0.0.1", port=4455, password="", scene_in_match="InMatch", scene_between_matches="BetweenMatches"
        )

    assert "OBS_SCENE_IN_MATCH" in caplog.text
    assert "OBS_SCENE_BETWEEN_MATCHES" in caplog.text


def test_verify_scenes_exist_logs_warning_when_scene_list_fetch_fails(monkeypatch, caplog):
    class _FailingSceneListClient(_FakeReqClient):
        def get_scene_list(self):
            raise OBSSDKError("GetSceneList", 600, "unexpected error")

    monkeypatch.setattr(obs_control_module.obs, "ReqClient", lambda **kwargs: _FailingSceneListClient())

    with caplog.at_level("WARNING", logger="nss_tracker.obs_control"):
        ObsSceneController(
            host="127.0.0.1", port=4455, password="", scene_in_match="InMatch", scene_between_matches="BetweenMatches"
        )

    assert "シーン一覧を取得できなかった" in caplog.text


def test_connect_failure_skips_scene_verification(monkeypatch, caplog):
    monkeypatch.setattr(obs_control_module.obs, "ReqClient", _RaisingReqClient)

    with caplog.at_level("WARNING", logger="nss_tracker.obs_control"):
        ObsSceneController(
            host="127.0.0.1", port=4455, password="", scene_in_match="InMatch", scene_between_matches="BetweenMatches"
        )

    # 接続自体に失敗した場合はシーン一覧取得を試みない(クライアントが無いため)
    assert "シーン一覧を取得できなかった" not in caplog.text
    assert "OBSに以下のシーンが見つかりません" not in caplog.text
