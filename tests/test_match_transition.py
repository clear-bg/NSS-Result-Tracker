from nss_tracker import match_transition


def test_notify_between_matches_increments_epoch():
    before = match_transition.get_between_matches_epoch()

    match_transition.notify_between_matches()

    assert match_transition.get_between_matches_epoch() == before + 1


def test_notify_between_matches_increments_once_per_call():
    before = match_transition.get_between_matches_epoch()

    match_transition.notify_between_matches()
    match_transition.notify_between_matches()
    match_transition.notify_between_matches()

    assert match_transition.get_between_matches_epoch() == before + 3


def test_get_between_matches_epoch_without_notify_is_unchanged():
    first = match_transition.get_between_matches_epoch()
    second = match_transition.get_between_matches_epoch()

    assert first == second
