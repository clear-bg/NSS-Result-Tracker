import re

import cv2
import numpy as np
import pytest

from conftest import requires_fixtures
from nss_tracker.detection.team_color import read_team_colors

_HEX_COLOR_PATTERN = re.compile(r"^#[0-9a-f]{6}$")


def _hex_to_rgb(hex_color: str) -> tuple[int, int, int]:
    hex_color = hex_color.lstrip("#")
    return tuple(int(hex_color[i : i + 2], 16) for i in (0, 2, 4))


def test_read_team_colors_does_not_crash_when_roi_out_of_bounds():
    """1920x1080未満のフレーム(テスト用ダミーフレーム等)が渡されてROIが範囲外に
    なっても、クラッシュせずニュートラルな色を返すことを確認する。
    """
    frame = np.zeros((10, 10, 3), dtype=np.uint8)

    mine, opponent = read_team_colors(frame)

    assert mine == "#000000"
    assert opponent == "#000000"


def _distance(a: str, b: str) -> float:
    ar, ag, ab = _hex_to_rgb(a)
    br, bg, bb = _hex_to_rgb(b)
    return ((ar - br) ** 2 + (ag - bg) ** 2 + (ab - bb) ** 2) ** 0.5


@requires_fixtures
@pytest.mark.parametrize(
    "filename",
    [
        "72_matching_hdr_off_1.png",
        "73_matching_hdr_off_2.png",
        "82_matching_with_rank_4v3_hdr_off.png",
        "86_matching_with_rank_4v4_hdr_off.png",
        "87_matching_hdr_off_3.png",
    ],
)
def test_read_team_colors_returns_valid_hex_strings(fixtures_dir, filename):
    frame = cv2.imread(str(fixtures_dir / filename))

    mine, opponent = read_team_colors(frame)

    assert _HEX_COLOR_PATTERN.match(mine)
    assert _HEX_COLOR_PATTERN.match(opponent)


@requires_fixtures
@pytest.mark.parametrize(
    "filename",
    [
        "72_matching_hdr_off_1.png",
        "73_matching_hdr_off_2.png",
        "82_matching_with_rank_4v3_hdr_off.png",
        "86_matching_with_rank_4v4_hdr_off.png",
        "87_matching_hdr_off_3.png",
    ],
)
def test_read_team_colors_mine_and_opponent_are_visually_distinct(fixtures_dir, filename):
    """VS画面は自チーム/相手チームの名前タグが目視で明確に異なる色で塗られているため、
    サンプリングした2色も十分に離れていることを確認する。特定のhex値をハードコード
    するのではなく、色差という目視でも確認できる性質で検証する
    (テスト正解データの自己参照禁止の方針、CLAUDE.md参照)。
    """
    frame = cv2.imread(str(fixtures_dir / filename))

    mine, opponent = read_team_colors(frame)

    assert _distance(mine, opponent) > 100
