from datetime import timedelta

from nss_tracker.timeutil import JST, now_jst


def test_jst_offset_is_plus_nine_hours():
    assert JST.utcoffset(None) == timedelta(hours=9)


def test_now_jst_returns_jst_aware_datetime():
    dt = now_jst()
    assert dt.tzinfo == JST
    assert dt.utcoffset() == timedelta(hours=9)
