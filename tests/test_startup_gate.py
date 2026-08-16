import threading
import time

import pytest

from nss_tracker import startup_gate
from nss_tracker.config import ConfigError


@pytest.fixture(autouse=True)
def _reset_startup_gate(monkeypatch):
    """各テストの前後で状態を独立させる(threading.Eventはset()が不可逆なため)。"""
    monkeypatch.setattr(startup_gate, "_obs_scene_switching_confirmed", False)
    monkeypatch.setattr(startup_gate, "_confirmed_event", threading.Event())
    monkeypatch.setattr("nss_tracker.config._current_room_type", None)


def test_can_confirm_start_false_when_room_type_unset(monkeypatch):
    monkeypatch.setattr("nss_tracker.config._current_room_type", None)
    startup_gate.mark_obs_scene_switching_confirmed()

    assert startup_gate.can_confirm_start() is False


def test_can_confirm_start_false_when_obs_scene_switching_not_confirmed(monkeypatch):
    monkeypatch.setattr("nss_tracker.config._current_room_type", "random")

    assert startup_gate.can_confirm_start() is False


def test_can_confirm_start_true_when_both_selected(monkeypatch):
    monkeypatch.setattr("nss_tracker.config._current_room_type", "random")
    startup_gate.mark_obs_scene_switching_confirmed()

    assert startup_gate.can_confirm_start() is True


def test_confirm_start_raises_when_not_ready():
    with pytest.raises(ConfigError):
        startup_gate.confirm_start()

    assert startup_gate.is_confirmed() is False


def test_confirm_start_sets_confirmed_state(monkeypatch):
    monkeypatch.setattr("nss_tracker.config._current_room_type", "private")
    startup_gate.mark_obs_scene_switching_confirmed()

    startup_gate.confirm_start()

    assert startup_gate.is_confirmed() is True


def test_confirm_start_is_idempotent(monkeypatch):
    monkeypatch.setattr("nss_tracker.config._current_room_type", "random")
    startup_gate.mark_obs_scene_switching_confirmed()
    startup_gate.confirm_start()

    startup_gate.confirm_start()  # 例外を送出せず、既に確認済みのまま

    assert startup_gate.is_confirmed() is True


def test_wait_for_confirmation_blocks_until_confirm_start(monkeypatch):
    monkeypatch.setattr("nss_tracker.config._current_room_type", "random")
    startup_gate.mark_obs_scene_switching_confirmed()

    unblocked = threading.Event()

    def waiter():
        startup_gate.wait_for_confirmation()
        unblocked.set()

    thread = threading.Thread(target=waiter, daemon=True)
    thread.start()
    try:
        assert not unblocked.wait(timeout=0.2)  # まだ確認していないためブロックしたまま

        startup_gate.confirm_start()

        assert unblocked.wait(timeout=2.0)
    finally:
        thread.join(timeout=2.0)
