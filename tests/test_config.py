import logging
from pathlib import Path

import pytest

from nss_tracker.config import (
    ConfigError,
    get_capture_device_name,
    get_capture_resolution,
    get_db_path,
    get_frame_read_timeout_seconds,
    get_goal_record_mode,
    get_log_level,
    get_log_level_name,
    get_web_host,
    get_web_port,
    is_allowed_player,
)


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
