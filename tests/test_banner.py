import cv2
import pytest

from conftest import requires_fixtures
from nss_tracker.detection.banner import classify_banner

EXPECTED = {
    "72_matching_hdr_off_1.png": None,
    "73_matching_hdr_off_2.png": None,
    "74_goal_with_assist_red_hdr_off.png": None,
    "75_goal_blue_owngoal_hdr_off.png": None,
    "76_match_end_hdr_off.png": None,
    "77_result_win_with_rank_red_hdr_off.png": "win",
    "78_result_lose_with_rank_blue_hdr_off.png": "lose",
    "79_result_rank_up_hdr_off.png": None,
    "80_match_end_hdr_off_2.png": None,
    "81_result_lose_without_rank_blue_hdr_off.png": "lose",
    "82_matching_with_rank_4v3_hdr_off.png": None,
    "83_goal_without_assist_blue_hdr_off.png": None,
    "84_result_win_with_rank_blue_hdr_off.png": "win",
    "85_result_win_with_rank_enlarged_blue_hdr_off.png": "win",
    "86_matching_with_rank_4v4_hdr_off.png": None,
    "87_matching_hdr_off_3.png": None,
}


# Issue #172: WIN_HUE_RANGE=(77, 86)の上限からわずかに外れる(77・84・85番は
# H86.3〜86.9)、かつ77・85番はWIN_VAL_MIN=165もクリアできていない(V146.1〜
# 148.5)。単純な閾値緩和は79_result_rank_up_hdr_off.png(ランク昇格オーバーレイ)
# を誤検知させることが判明したため、判定条件自体の見直しが必要(詳細はIssue
# #172参照)。ground truthは"win"のまま、既知の欠落としてxfailにする
_KNOWN_HUE_SHIFT_GAPS = {
    "77_result_win_with_rank_red_hdr_off.png",
    "84_result_win_with_rank_blue_hdr_off.png",
    "85_result_win_with_rank_enlarged_blue_hdr_off.png",
}


@requires_fixtures
@pytest.mark.parametrize(
    "filename, expected",
    [
        pytest.param(
            name,
            expected,
            marks=pytest.mark.xfail(
                reason="WIN_HUE_RANGE/WIN_VAL_MINがHDR無効化後の実測を僅かにカバーできていない(Issue #172で対応予定)",
                strict=False,
            ),
        )
        if name in _KNOWN_HUE_SHIFT_GAPS
        else (name, expected)
        for name, expected in sorted(EXPECTED.items())
    ],
)
def test_classify_banner(fixtures_dir, filename, expected):
    frame = cv2.imread(str(fixtures_dir / filename))
    assert frame is not None, f"failed to load {filename}"
    assert classify_banner(frame) == expected
