"""ObsSceneController(Issue #83)のテスト。

実際のOBS接続は行わず、obsws_python.ReqClientをモックして以下を確認する:
- in_matchの真偽に応じて正しいシーン名でset_current_program_sceneを呼ぶこと
- OBSへの接続・切替失敗時にアプリを止めず、WARNINGログを出して継続すること
  (検知・DB記録という本来機能から独立した付加機能であるため)
"""

from obsws_python.error import OBSSDKError

import nss_tracker.obs_control as obs_control_module
from nss_tracker.obs_control import ObsSceneController


class _FakeReqClient:
    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.scenes_set: list[str] = []
        self.disconnected = False

    def set_current_program_scene(self, name):
        self.scenes_set.append(name)

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
