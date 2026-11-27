"""detection/center_score.py(ゴール後の中央スコア表示の読み取り、Issue #493)のテスト。

実画像はfixtures(gitignore対象)にしか無いため、テンプレート自体を白く描いた
合成フレームで読み取りの仕組みを確かめる。fixtureがある環境では実画像でも確かめる。
"""

import cv2
import numpy as np
import pytest

from conftest import requires_fixtures
from nss_tracker.detection import center_score
from nss_tracker.detection.center_score import (
    LEFT_ROI,
    RIGHT_ROI,
    match_digit,
    normalize_mask,
    read_center_score,
    read_digit,
)

# 止まった表示の数字のおおよその大きさ(実測: 高さ109px、幅は「1」で約55px・他で約90px)
_DIGIT_HEIGHT = 109
_DIGIT_WIDTH = 88


def _template(digit: int) -> np.ndarray:
    for d, template in center_score._load_templates():
        if d == digit:
            return template
    raise AssertionError(f"{digit}のテンプレートがありません")


def _blank_frame() -> np.ndarray:
    frame = np.zeros((1080, 1920, 3), dtype=np.uint8)
    frame[:, :] = (40, 120, 40)  # 芝生っぽい緑
    return frame


def _draw_digit(frame, roi, digit, height=_DIGIT_HEIGHT, offset=(6, 10)):
    """テンプレートを拡大して白で描く。offsetはROI左上からの位置(x, y)。"""
    mask = cv2.resize(
        _template(digit).astype(np.uint8) * 255, (_DIGIT_WIDTH, height), interpolation=cv2.INTER_NEAREST
    )
    x = roi[0] + offset[0]
    y = roi[1] + offset[1]
    region = frame[y : y + height, x : x + _DIGIT_WIDTH]
    region[mask > 127] = (255, 255, 255)


def test_templates_cover_digits_zero_to_four():
    """手持ちのサンプル(0〜4)のテンプレートが同梱されていること。5・6はサンプルが無い。"""
    digits = {digit for digit, _ in center_score._load_templates()}
    assert {0, 1, 2, 3, 4} <= digits
    assert digits <= set(range(7))


@pytest.mark.parametrize("left,right", [(0, 0), (1, 2), (3, 4), (4, 0), (2, 3)])
def test_reads_both_sides(left, right):
    frame = _blank_frame()
    _draw_digit(frame, LEFT_ROI, left)
    _draw_digit(frame, RIGHT_ROI, right)
    reading = read_center_score(frame)
    assert reading is not None
    assert (reading[0].digit, reading[1].digit) == (left, right)
    assert reading[0].score >= center_score.MATCH_MIN


def test_no_digit_returns_none():
    assert read_center_score(_blank_frame()) is None


def test_one_side_missing_returns_none():
    frame = _blank_frame()
    _draw_digit(frame, LEFT_ROI, 1)
    assert read_center_score(frame) is None


def test_digit_touching_roi_edge_is_ignored():
    """拡大・回転の途中はROIの端に触れるため読まない(止まった表示だけを読む)。"""
    frame = _blank_frame()
    _draw_digit(frame, LEFT_ROI, 2, offset=(0, 10))
    assert read_digit(frame, LEFT_ROI) is None


def test_digit_with_unexpected_height_is_ignored():
    """拡大の途中は止まった表示(109px前後)より大きいため読まない。"""
    frame = _blank_frame()
    _draw_digit(frame, LEFT_ROI, 2, height=122, offset=(6, 4))
    assert read_digit(frame, LEFT_ROI) is None


def test_unknown_shape_has_no_digit_but_is_reported():
    """どのテンプレートとも一致しない形(テンプレートに無い5・6を想定)は、数字をNoneにして返す。"""
    frame = _blank_frame()
    x, y = LEFT_ROI[0] + 6, LEFT_ROI[1] + 10
    frame[y : y + _DIGIT_HEIGHT, x : x + _DIGIT_WIDTH] = (255, 255, 255)  # 塗りつぶしの四角
    reading = read_digit(frame, LEFT_ROI)
    assert reading is not None
    assert reading.digit is None
    assert reading.score < center_score.MATCH_MIN


def test_match_digit_returns_none_below_threshold():
    square = normalize_mask(np.ones((109, 88), dtype=bool))
    digit, score = match_digit(square)
    assert digit is None
    assert 0.0 <= score < center_score.MATCH_MIN


@requires_fixtures
def test_reads_left_digit_of_fixture_105(fixtures_dir):
    """fixture 105(ゴール後のスコア表示 1-2)の左側は止まった表示の「1」。

    右側の「2」は拡大の途中のフレームのため読まない(端・高さの条件で除かれるか、
    一致度が境目を下回る)。
    """
    path = fixtures_dir / "105_goal_with_assist_blue_hdr_off_2.png"
    if not path.exists():
        pytest.skip(f"{path.name}がありません")
    frame = cv2.imread(str(path))
    reading = read_digit(frame, LEFT_ROI)
    assert reading is not None
    assert reading.digit == 1
