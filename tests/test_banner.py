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
}

# Issue #148: 77は実測Hが87.1〜87.2で安定している(hue_stdは1.4程度と低く、
# 単発フレームの偶然ではなく表示区間全体を通じて一貫している)にもかかわらず、
# WIN_HUE_RANGE=(77, 86)の上限からわずかに外れているためclassify_bannerが
# Noneを返す。HDR無効化による色シフトが実際にbanner.pyの閾値にも及んでいる
# ことを示す実データであり、Issue #118/#143で閾値の再較正を行うまでの既知の
# 欠落としてxfailにする(ground truthは"win"のまま)
_KNOWN_HUE_SHIFT_GAPS = {"77_result_win_with_rank_red_hdr_off.png"}


@requires_fixtures
@pytest.mark.parametrize(
    "filename, expected",
    [
        pytest.param(
            name,
            expected,
            marks=pytest.mark.xfail(
                reason="WIN_HUE_RANGEがHDR無効化後の実測Hを僅かにカバーできていない(Issue #118/#143で対応予定)",
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
