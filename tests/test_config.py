import pytest

from nss_tracker.config import ConfigError, get_capture_device_name, get_capture_resolution, is_allowed_player


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
