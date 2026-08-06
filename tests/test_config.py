import logging
from pathlib import Path

import pytest
from dotenv import dotenv_values

from nss_tracker.config import (
    ConfigError,
    get_allowed_players,
    get_capture_device_name,
    get_capture_fps,
    get_capture_resolution,
    get_db_path,
    get_editable_settings,
    get_frame_read_timeout_seconds,
    get_goal_assist_totals_scope,
    get_goal_record_mode,
    get_log_level,
    get_log_level_name,
    get_obs_browser_source_names,
    get_obs_scene_between_matches,
    get_obs_scene_in_match,
    get_obs_scene_switching_enabled,
    get_obs_websocket_host,
    get_obs_websocket_password,
    get_obs_websocket_port,
    get_rank_delta_distribution_scope,
    get_rank_graph_match_limit,
    get_web_host,
    get_web_port,
    is_allowed_player,
    update_editable_settings,
)


def test_get_allowed_players_returns_frozenset(monkeypatch):
    monkeypatch.setenv("ALLOWED_PLAYERS", "Alice,Bob")
    assert get_allowed_players() == frozenset({"Alice", "Bob"})


def test_get_allowed_players_empty_when_unset(monkeypatch):
    monkeypatch.delenv("ALLOWED_PLAYERS", raising=False)
    assert get_allowed_players() == frozenset()


def test_is_allowed_player_true_for_listed_name(monkeypatch):
    monkeypatch.setenv("ALLOWED_PLAYERS", "Alice,Bob")
    assert is_allowed_player("Alice") is True
    assert is_allowed_player("Bob") is True


def test_is_allowed_player_false_for_unlisted_name(monkeypatch):
    monkeypatch.setenv("ALLOWED_PLAYERS", "Alice,Bob")
    assert is_allowed_player("Stranger") is False


def test_is_allowed_player_handles_whitespace_around_names(monkeypatch):
    monkeypatch.setenv("ALLOWED_PLAYERS", " Alice , Bob ")
    assert is_allowed_player("Alice") is True
    assert is_allowed_player("Bob") is True


def test_is_allowed_player_false_when_env_unset(monkeypatch):
    monkeypatch.delenv("ALLOWED_PLAYERS", raising=False)
    assert is_allowed_player("Alice") is False


def test_get_capture_device_name_raises_when_unset(monkeypatch):
    monkeypatch.delenv("CAPTURE_DEVICE_NAME", raising=False)
    with pytest.raises(ConfigError, match="CAPTURE_DEVICE_NAME"):
        get_capture_device_name()


def test_get_capture_device_name_uses_env_value(monkeypatch):
    monkeypatch.setenv("CAPTURE_DEVICE_NAME", "Custom Capture Device")
    assert get_capture_device_name() == "Custom Capture Device"


def test_get_capture_resolution_raises_when_width_unset(monkeypatch):
    monkeypatch.delenv("CAPTURE_WIDTH", raising=False)
    monkeypatch.setenv("CAPTURE_HEIGHT", "1080")
    with pytest.raises(ConfigError, match="CAPTURE_WIDTH"):
        get_capture_resolution()


def test_get_capture_resolution_raises_when_height_unset(monkeypatch):
    monkeypatch.setenv("CAPTURE_WIDTH", "1920")
    monkeypatch.delenv("CAPTURE_HEIGHT", raising=False)
    with pytest.raises(ConfigError, match="CAPTURE_HEIGHT"):
        get_capture_resolution()


def test_get_capture_resolution_uses_env_values(monkeypatch):
    monkeypatch.setenv("CAPTURE_WIDTH", "1280")
    monkeypatch.setenv("CAPTURE_HEIGHT", "720")
    assert get_capture_resolution() == (1280, 720)


def test_get_capture_fps_raises_when_unset(monkeypatch):
    monkeypatch.delenv("CAPTURE_FPS", raising=False)
    with pytest.raises(ConfigError, match="CAPTURE_FPS"):
        get_capture_fps()


def test_get_capture_fps_uses_env_value(monkeypatch):
    monkeypatch.setenv("CAPTURE_FPS", "60")
    assert get_capture_fps() == 60.0


def test_get_db_path_raises_when_unset(monkeypatch):
    monkeypatch.delenv("DB_PATH", raising=False)
    with pytest.raises(ConfigError, match="DB_PATH"):
        get_db_path()


def test_get_db_path_uses_env_value(monkeypatch):
    monkeypatch.setenv("DB_PATH", "custom/dir/tracker.db")
    assert get_db_path() == Path("custom/dir/tracker.db")


def test_get_frame_read_timeout_seconds_raises_when_unset(monkeypatch):
    monkeypatch.delenv("FRAME_READ_TIMEOUT_SECONDS", raising=False)
    with pytest.raises(ConfigError, match="FRAME_READ_TIMEOUT_SECONDS"):
        get_frame_read_timeout_seconds()


def test_get_frame_read_timeout_seconds_uses_env_value(monkeypatch):
    monkeypatch.setenv("FRAME_READ_TIMEOUT_SECONDS", "10.5")
    assert get_frame_read_timeout_seconds() == 10.5


def test_get_web_host_raises_when_unset(monkeypatch):
    monkeypatch.delenv("WEB_HOST", raising=False)
    with pytest.raises(ConfigError, match="WEB_HOST"):
        get_web_host()


def test_get_web_host_uses_env_value(monkeypatch):
    monkeypatch.setenv("WEB_HOST", "0.0.0.0")
    assert get_web_host() == "0.0.0.0"


def test_get_web_port_raises_when_unset(monkeypatch):
    monkeypatch.delenv("WEB_PORT", raising=False)
    with pytest.raises(ConfigError, match="WEB_PORT"):
        get_web_port()


def test_get_web_port_uses_env_value(monkeypatch):
    monkeypatch.setenv("WEB_PORT", "9000")
    assert get_web_port() == 9000


def test_get_log_level_name_raises_when_unset(monkeypatch):
    monkeypatch.delenv("NSS_TRACKER_LOG_LEVEL", raising=False)
    with pytest.raises(ConfigError, match="NSS_TRACKER_LOG_LEVEL"):
        get_log_level_name()


def test_get_log_level_name_raises_for_invalid_value(monkeypatch):
    monkeypatch.setenv("NSS_TRACKER_LOG_LEVEL", "TRACE")
    with pytest.raises(ConfigError, match="NSS_TRACKER_LOG_LEVEL"):
        get_log_level_name()


def test_get_log_level_name_uses_env_value(monkeypatch):
    monkeypatch.setenv("NSS_TRACKER_LOG_LEVEL", "debug")
    assert get_log_level_name() == "DEBUG"


def test_get_log_level_returns_logging_constant(monkeypatch):
    monkeypatch.setenv("NSS_TRACKER_LOG_LEVEL", "WARNING")
    assert get_log_level() == logging.WARNING


def test_get_goal_record_mode_raises_when_unset(monkeypatch):
    monkeypatch.delenv("GOAL_RECORD_MODE", raising=False)
    with pytest.raises(ConfigError, match="GOAL_RECORD_MODE"):
        get_goal_record_mode()


def test_get_goal_record_mode_raises_for_invalid_value(monkeypatch):
    monkeypatch.setenv("GOAL_RECORD_MODE", "everyone")
    with pytest.raises(ConfigError, match="GOAL_RECORD_MODE"):
        get_goal_record_mode()


@pytest.mark.parametrize("mode", ["all", "allowlist", "allowlist_redact"])
def test_get_goal_record_mode_uses_env_value(monkeypatch, mode):
    monkeypatch.setenv("GOAL_RECORD_MODE", mode)
    assert get_goal_record_mode() == mode


def test_get_rank_delta_distribution_scope_raises_when_unset(monkeypatch):
    monkeypatch.delenv("RANK_DELTA_DISTRIBUTION_SCOPE", raising=False)
    with pytest.raises(ConfigError, match="RANK_DELTA_DISTRIBUTION_SCOPE"):
        get_rank_delta_distribution_scope()


def test_get_rank_delta_distribution_scope_raises_for_invalid_value(monkeypatch):
    monkeypatch.setenv("RANK_DELTA_DISTRIBUTION_SCOPE", "everything")
    with pytest.raises(ConfigError, match="RANK_DELTA_DISTRIBUTION_SCOPE"):
        get_rank_delta_distribution_scope()


@pytest.mark.parametrize("scope", ["session", "all"])
def test_get_rank_delta_distribution_scope_uses_env_value(monkeypatch, scope):
    monkeypatch.setenv("RANK_DELTA_DISTRIBUTION_SCOPE", scope)
    assert get_rank_delta_distribution_scope() == scope


def test_get_goal_assist_totals_scope_raises_when_unset(monkeypatch):
    monkeypatch.delenv("GOAL_ASSIST_TOTALS_SCOPE", raising=False)
    with pytest.raises(ConfigError, match="GOAL_ASSIST_TOTALS_SCOPE"):
        get_goal_assist_totals_scope()


def test_get_goal_assist_totals_scope_raises_for_invalid_value(monkeypatch):
    monkeypatch.setenv("GOAL_ASSIST_TOTALS_SCOPE", "everything")
    with pytest.raises(ConfigError, match="GOAL_ASSIST_TOTALS_SCOPE"):
        get_goal_assist_totals_scope()


@pytest.mark.parametrize("scope", ["session", "all"])
def test_get_goal_assist_totals_scope_uses_env_value(monkeypatch, scope):
    monkeypatch.setenv("GOAL_ASSIST_TOTALS_SCOPE", scope)
    assert get_goal_assist_totals_scope() == scope


def test_get_rank_graph_match_limit_raises_when_unset(monkeypatch):
    monkeypatch.delenv("RANK_GRAPH_MATCH_LIMIT", raising=False)
    with pytest.raises(ConfigError, match="RANK_GRAPH_MATCH_LIMIT"):
        get_rank_graph_match_limit()


def test_get_rank_graph_match_limit_raises_when_blank(monkeypatch):
    monkeypatch.setenv("RANK_GRAPH_MATCH_LIMIT", "  ")
    with pytest.raises(ConfigError, match="RANK_GRAPH_MATCH_LIMIT"):
        get_rank_graph_match_limit()


def test_get_rank_graph_match_limit_returns_none_for_all(monkeypatch):
    monkeypatch.setenv("RANK_GRAPH_MATCH_LIMIT", "all")
    assert get_rank_graph_match_limit() is None


def test_get_rank_graph_match_limit_uses_env_value(monkeypatch):
    monkeypatch.setenv("RANK_GRAPH_MATCH_LIMIT", "30")
    assert get_rank_graph_match_limit() == 30


def test_get_rank_graph_match_limit_raises_for_non_numeric_value(monkeypatch):
    monkeypatch.setenv("RANK_GRAPH_MATCH_LIMIT", "abc")
    with pytest.raises(ConfigError, match="RANK_GRAPH_MATCH_LIMIT"):
        get_rank_graph_match_limit()


@pytest.mark.parametrize("value", ["0", "-5"])
def test_get_rank_graph_match_limit_raises_for_non_positive_value(monkeypatch, value):
    monkeypatch.setenv("RANK_GRAPH_MATCH_LIMIT", value)
    with pytest.raises(ConfigError, match="RANK_GRAPH_MATCH_LIMIT"):
        get_rank_graph_match_limit()


def test_get_obs_websocket_host_raises_when_unset(monkeypatch):
    monkeypatch.delenv("OBS_WEBSOCKET_HOST", raising=False)
    with pytest.raises(ConfigError, match="OBS_WEBSOCKET_HOST"):
        get_obs_websocket_host()


def test_get_obs_websocket_host_uses_env_value(monkeypatch):
    monkeypatch.setenv("OBS_WEBSOCKET_HOST", "192.168.1.10")
    assert get_obs_websocket_host() == "192.168.1.10"


def test_get_obs_websocket_port_raises_when_unset(monkeypatch):
    monkeypatch.delenv("OBS_WEBSOCKET_PORT", raising=False)
    with pytest.raises(ConfigError, match="OBS_WEBSOCKET_PORT"):
        get_obs_websocket_port()


def test_get_obs_websocket_port_uses_env_value(monkeypatch):
    monkeypatch.setenv("OBS_WEBSOCKET_PORT", "4455")
    assert get_obs_websocket_port() == 4455


def test_get_obs_websocket_password_raises_when_unset(monkeypatch):
    monkeypatch.delenv("OBS_WEBSOCKET_PASSWORD", raising=False)
    with pytest.raises(ConfigError, match="OBS_WEBSOCKET_PASSWORD"):
        get_obs_websocket_password()


def test_get_obs_websocket_password_returns_empty_string_for_none(monkeypatch):
    """OBS側で認証を無効化している場合、noneを空文字列のパスワードとして扱う。"""
    monkeypatch.setenv("OBS_WEBSOCKET_PASSWORD", "none")
    assert get_obs_websocket_password() == ""


def test_get_obs_websocket_password_uses_env_value(monkeypatch):
    monkeypatch.setenv("OBS_WEBSOCKET_PASSWORD", "secret")
    assert get_obs_websocket_password() == "secret"


def test_get_obs_scene_in_match_raises_when_unset(monkeypatch):
    monkeypatch.delenv("OBS_SCENE_IN_MATCH", raising=False)
    with pytest.raises(ConfigError, match="OBS_SCENE_IN_MATCH"):
        get_obs_scene_in_match()


def test_get_obs_scene_in_match_uses_env_value(monkeypatch):
    monkeypatch.setenv("OBS_SCENE_IN_MATCH", "InMatch")
    assert get_obs_scene_in_match() == "InMatch"


def test_get_obs_scene_between_matches_raises_when_unset(monkeypatch):
    monkeypatch.delenv("OBS_SCENE_BETWEEN_MATCHES", raising=False)
    with pytest.raises(ConfigError, match="OBS_SCENE_BETWEEN_MATCHES"):
        get_obs_scene_between_matches()


def test_get_obs_scene_between_matches_uses_env_value(monkeypatch):
    monkeypatch.setenv("OBS_SCENE_BETWEEN_MATCHES", "BetweenMatches")
    assert get_obs_scene_between_matches() == "BetweenMatches"


def test_get_obs_browser_source_names_raises_when_unset(monkeypatch):
    monkeypatch.delenv("OBS_BROWSER_SOURCE_NAMES", raising=False)
    with pytest.raises(ConfigError, match="OBS_BROWSER_SOURCE_NAMES"):
        get_obs_browser_source_names()


def test_get_obs_browser_source_names_returns_empty_tuple_for_none(monkeypatch):
    """この機能を使わない場合、noneを再読み込み対象なしとして扱う。"""
    monkeypatch.setenv("OBS_BROWSER_SOURCE_NAMES", "none")
    assert get_obs_browser_source_names() == ()


def test_get_obs_browser_source_names_parses_comma_separated_value(monkeypatch):
    monkeypatch.setenv("OBS_BROWSER_SOURCE_NAMES", "rank_graph, vs_rank_comparison ,goal_stats")
    assert get_obs_browser_source_names() == ("rank_graph", "vs_rank_comparison", "goal_stats")


def test_get_obs_scene_switching_enabled_raises_when_unset(monkeypatch):
    monkeypatch.delenv("OBS_SCENE_SWITCHING_ENABLED", raising=False)
    with pytest.raises(ConfigError, match="OBS_SCENE_SWITCHING_ENABLED"):
        get_obs_scene_switching_enabled()


def test_get_obs_scene_switching_enabled_raises_for_invalid_value(monkeypatch):
    monkeypatch.setenv("OBS_SCENE_SWITCHING_ENABLED", "yes")
    with pytest.raises(ConfigError, match="OBS_SCENE_SWITCHING_ENABLED"):
        get_obs_scene_switching_enabled()


@pytest.mark.parametrize("raw,expected", [("true", True), ("false", False)])
def test_get_obs_scene_switching_enabled_parses_bool_string(monkeypatch, raw, expected):
    monkeypatch.setenv("OBS_SCENE_SWITCHING_ENABLED", raw)
    assert get_obs_scene_switching_enabled() is expected


def test_get_editable_settings_returns_current_env_values(monkeypatch):
    monkeypatch.setenv("ALLOWED_PLAYERS", "Alice,Bob")
    monkeypatch.setenv("GOAL_RECORD_MODE", "allowlist")
    monkeypatch.setenv("RANK_GRAPH_MATCH_LIMIT", "30")
    monkeypatch.setenv("RANK_DELTA_DISTRIBUTION_SCOPE", "session")
    monkeypatch.setenv("OBS_SCENE_SWITCHING_ENABLED", "true")

    assert get_editable_settings() == {
        "ALLOWED_PLAYERS": "Alice,Bob",
        "GOAL_RECORD_MODE": "allowlist",
        "RANK_GRAPH_MATCH_LIMIT": "30",
        "RANK_DELTA_DISTRIBUTION_SCOPE": "session",
        "OBS_SCENE_SWITCHING_ENABLED": "true",
    }


def _write_env_file(path: Path) -> None:
    path.write_text(
        "ALLOWED_PLAYERS=OldName\n"
        "GOAL_RECORD_MODE=all\n"
        "RANK_GRAPH_MATCH_LIMIT=all\n"
        "RANK_DELTA_DISTRIBUTION_SCOPE=all\n"
        "OBS_SCENE_SWITCHING_ENABLED=true\n",
        encoding="utf-8",
    )


def test_update_editable_settings_updates_environ_and_env_file(tmp_path, monkeypatch):
    env_path = tmp_path / ".env"
    _write_env_file(env_path)
    monkeypatch.setattr("nss_tracker.config.find_dotenv", lambda: str(env_path))
    monkeypatch.setenv("ALLOWED_PLAYERS", "OldName")
    monkeypatch.setenv("GOAL_RECORD_MODE", "all")
    monkeypatch.setenv("RANK_GRAPH_MATCH_LIMIT", "all")
    monkeypatch.setenv("RANK_DELTA_DISTRIBUTION_SCOPE", "all")
    monkeypatch.setenv("OBS_SCENE_SWITCHING_ENABLED", "true")

    update_editable_settings(
        {
            "ALLOWED_PLAYERS": "NewName,Second",
            "GOAL_RECORD_MODE": "allowlist",
            "RANK_GRAPH_MATCH_LIMIT": "10",
            "RANK_DELTA_DISTRIBUTION_SCOPE": "session",
            "OBS_SCENE_SWITCHING_ENABLED": "false",
        }
    )

    assert get_editable_settings() == {
        "ALLOWED_PLAYERS": "NewName,Second",
        "GOAL_RECORD_MODE": "allowlist",
        "RANK_GRAPH_MATCH_LIMIT": "10",
        "RANK_DELTA_DISTRIBUTION_SCOPE": "session",
        "OBS_SCENE_SWITCHING_ENABLED": "false",
    }
    persisted = dotenv_values(env_path)
    assert persisted["ALLOWED_PLAYERS"] == "NewName,Second"
    assert persisted["GOAL_RECORD_MODE"] == "allowlist"
    assert persisted["RANK_GRAPH_MATCH_LIMIT"] == "10"
    assert persisted["RANK_DELTA_DISTRIBUTION_SCOPE"] == "session"
    assert persisted["OBS_SCENE_SWITCHING_ENABLED"] == "false"


def test_update_editable_settings_raises_and_applies_nothing_when_one_value_invalid(tmp_path, monkeypatch):
    env_path = tmp_path / ".env"
    _write_env_file(env_path)
    monkeypatch.setattr("nss_tracker.config.find_dotenv", lambda: str(env_path))
    monkeypatch.setenv("ALLOWED_PLAYERS", "OldName")
    monkeypatch.setenv("GOAL_RECORD_MODE", "all")
    monkeypatch.setenv("RANK_GRAPH_MATCH_LIMIT", "all")
    monkeypatch.setenv("RANK_DELTA_DISTRIBUTION_SCOPE", "all")
    monkeypatch.setenv("OBS_SCENE_SWITCHING_ENABLED", "true")

    with pytest.raises(ConfigError, match="GOAL_RECORD_MODE"):
        update_editable_settings(
            {
                "ALLOWED_PLAYERS": "NewName",
                "GOAL_RECORD_MODE": "not-a-real-mode",
                "RANK_GRAPH_MATCH_LIMIT": "10",
                "RANK_DELTA_DISTRIBUTION_SCOPE": "session",
                "OBS_SCENE_SWITCHING_ENABLED": "false",
            }
        )

    # 一部の項目が不正な場合、他の正しい項目も含めて何一つ反映されない(部分適用を避ける)
    assert get_editable_settings() == {
        "ALLOWED_PLAYERS": "OldName",
        "GOAL_RECORD_MODE": "all",
        "RANK_GRAPH_MATCH_LIMIT": "all",
        "RANK_DELTA_DISTRIBUTION_SCOPE": "all",
        "OBS_SCENE_SWITCHING_ENABLED": "true",
    }
    persisted = dotenv_values(env_path)
    assert persisted["ALLOWED_PLAYERS"] == "OldName"
