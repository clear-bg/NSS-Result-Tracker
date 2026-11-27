"""state/center_score_tracker.py(中央スコアの採用ロジック、Issue #493)のテスト。"""

import numpy as np
import pytest

from nss_tracker.detection.center_score import DigitReading
from nss_tracker.state.center_score_tracker import CenterScoreTracker, is_plausible_transition

_SHAPES = {}


def _mask(key) -> np.ndarray:
    """数字(またはNone=未知の形)ごとに決まった形のマスクを返す。"""
    if key not in _SHAPES:
        rng = np.random.default_rng(abs(hash(key)) % (2**32))
        _SHAPES[key] = rng.random((64, 48)) > 0.5
    return _SHAPES[key]


def _reading(left, right, left_shape=None, right_shape=None):
    return (
        DigitReading(digit=left, score=0.95 if left is not None else 0.5, mask=_mask(left_shape or left)),
        DigitReading(digit=right, score=0.95 if right is not None else 0.5, mask=_mask(right_shape or right)),
    )


def _feed(tracker, readings, start=0.0, step=1 / 30):
    requests = []
    for i, reading in enumerate(readings):
        request = tracker.observe(reading, start + i * step)
        if request is not None:
            requests.append(request)
    return requests


def test_adopts_last_stable_score_and_remembers_the_first():
    """表示の流れ(ゴール前のスコア→切り替わり→ゴール後のスコア→消える)を再現する。"""
    tracker = CenterScoreTracker(stable_seconds=0.3, gap_tolerance_seconds=0.5)
    readings = (
        [_reading(1, 1)] * 13  # ゴール前のスコア(約0.4秒)
        + [None] * 18  # 切り替わり途中(読まない)
        + [_reading(2, 1)] * 30  # ゴール後のスコア(約1.0秒)
        + [None] * 5  # 消える
    )
    assert _feed(tracker, readings) == []
    assert tracker.first_adopted == (1, 1)
    assert tracker.adopted == (2, 1)


def test_brief_misread_is_not_adopted():
    """切り替わり途中に1枚だけ別の値に見えても採用しない(実測で外れるのは1〜数枚)。"""
    tracker = CenterScoreTracker(stable_seconds=0.3, gap_tolerance_seconds=0.5)
    readings = [_reading(1, 0)] * 30 + [_reading(3, 0)] + [_reading(1, 0)] * 5
    _feed(tracker, readings)
    assert tracker.adopted == (1, 0)
    assert tracker.first_adopted == (1, 0)


def test_short_display_is_not_adopted():
    tracker = CenterScoreTracker(stable_seconds=0.3, gap_tolerance_seconds=0.5)
    _feed(tracker, [_reading(1, 0)] * 5)  # 約0.13秒
    assert tracker.adopted is None


def test_gap_within_tolerance_keeps_counting():
    """検知ループが一時的に遅くなりフレーム間隔が空いても、同じ値なら続けて数える。"""
    tracker = CenterScoreTracker(stable_seconds=0.3, gap_tolerance_seconds=0.5)
    tracker.observe(_reading(2, 2), 0.0)
    tracker.observe(_reading(2, 2), 0.4)
    assert tracker.adopted == (2, 2)


def test_gap_beyond_tolerance_restarts():
    tracker = CenterScoreTracker(stable_seconds=0.3, gap_tolerance_seconds=0.5)
    tracker.observe(_reading(2, 2), 0.0)
    tracker.observe(_reading(2, 2), 0.2)
    tracker.observe(_reading(2, 2), 1.0)  # 0.8秒空いたので数え直し
    assert tracker.adopted is None
    tracker.observe(_reading(2, 2), 1.3)
    assert tracker.adopted == (2, 2)


def test_unknown_digit_requests_ocr_once_and_applies_result():
    """テンプレートに無い数字(5・6)が続いたら、OCRを1回だけ頼む。"""
    tracker = CenterScoreTracker(stable_seconds=0.3, gap_tolerance_seconds=0.5)
    requests = _feed(tracker, [_reading(None, 1, left_shape="five")] * 30)
    assert len(requests) == 1
    request = requests[0]
    assert (request.known_left, request.known_right) == (None, 1)
    assert tracker.adopted is None
    assert tracker.apply_ocr(request, 5, None) == (5, 1)
    assert tracker.adopted == (5, 1)


def test_unknown_digit_with_changing_shape_does_not_request_ocr():
    """形が変わり続ける(切り替わり途中の)フレームではOCRを頼まない。"""
    tracker = CenterScoreTracker(stable_seconds=0.3, gap_tolerance_seconds=0.5)
    readings = [_reading(None, 1, left_shape=f"spin{i}") for i in range(30)]
    assert _feed(tracker, readings) == []


def test_ocr_failure_adopts_nothing():
    tracker = CenterScoreTracker(stable_seconds=0.3, gap_tolerance_seconds=0.5)
    [request] = _feed(tracker, [_reading(None, 1, left_shape="six")] * 30)
    assert tracker.apply_ocr(request, None, None) is None
    assert tracker.adopted is None


def test_late_ocr_result_does_not_override_newer_score():
    """OCRの結果が、それより後に出た表示を採用した後に届いても上書きしない。"""
    tracker = CenterScoreTracker(stable_seconds=0.3, gap_tolerance_seconds=0.5)
    [request] = _feed(tracker, [_reading(None, 4, left_shape="five")] * 15)  # ゴール前 5-4(OCR待ち)
    _feed(tracker, [_reading(4, 4)] * 30, start=2.0)  # 別の値の表示を後から採用
    tracker.apply_ocr(request, 5, None)
    assert tracker.adopted == (4, 4)
    assert tracker.first_adopted == (5, 4)


def test_reset_clears_everything():
    tracker = CenterScoreTracker(stable_seconds=0.3, gap_tolerance_seconds=0.5)
    _feed(tracker, [_reading(1, 0)] * 30)
    tracker.reset()
    assert tracker.adopted is None
    assert tracker.first_adopted is None


@pytest.mark.parametrize(
    "previous,new",
    [
        ((0, 0), (1, 0)),
        ((0, 0), (0, 1)),
        ((1, 1), (2, 1)),
        ((2, 0), (4, 0)),  # ゴールデンボール(勝っている側)→4点差でノックアウト
        ((0, 2), (2, 2)),  # ゴールデンボール(負けている側)→同点
        ((3, 1), (3, 3)),
    ],
)
def test_plausible_transitions(previous, new):
    assert is_plausible_transition(previous, new)


@pytest.mark.parametrize(
    "previous,new",
    [
        ((0, 0), (0, 0)),  # 変化なし
        ((0, 0), (2, 0)),  # 2点差未満なのに2点
        ((2, 0), (3, 0)),  # ゴールデンボールなのに1点
        ((1, 1), (2, 2)),  # 両方増えている
        ((2, 1), (1, 1)),  # 減っている
        ((0, 0), (1, 1)),
    ],
)
def test_implausible_transitions(previous, new):
    assert not is_plausible_transition(previous, new)
