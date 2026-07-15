from nss_tracker.config import is_allowed_player


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
