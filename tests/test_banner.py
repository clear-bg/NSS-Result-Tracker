import cv2
import pytest

from conftest import requires_fixtures
from nss_tracker.detection.banner import classify_banner

EXPECTED = {
    "00_lobby.png": None,
    "01_before_start.png": None,
    "02_matching_in_progress.png": None,
    "10_waiting_for_other_players_blue.png": None,
    "11_matching_with_rank_blue.png": None,
    "12_matching_without_rank_blue.png": None,
    "13_waiting_for_other_players_red.png": None,
    "14_matching_with_rank_red.png": None,
    "20_in_game_blue.png": None,
    "21_goal_with_assist_blue.png": None,
    "22_goal_without_assist_blue.png": None,
    "23_assist_blue.png": None,
    "24_GA_without_me_blue.png": None,
    "25_resume_game_blue.png": None,
    "30_in_game_red.png": None,
    "31_goal_with_assist_red.png": None,
    "32_goal_without_assist_red.png": None,
    "33_assist_red.png": None,
    "34_GA_without_me_red.png": None,
    "35_resume_game_red.png": None,
    "43_result_win_without_rank_blue.png": "win",
    "44_result_lose_with_rank_blue.png": "lose",
    "46_result_lose_after_rank_decrease_blue.png": "lose",
    "50_result_win_with_rank_red.png": "win",
    "52_result_after_rank_increase_red.png": "win",
    "54_result_lose_with_rank_red.png": "lose",
    "56_result_lose_after_rank_decrease_red.png": "lose",
    "60_start_overtime.png": None,
    "61_overtime_in_game.png": None,
}


@requires_fixtures
@pytest.mark.parametrize("filename, expected", sorted(EXPECTED.items()))
def test_classify_banner(fixtures_dir, filename, expected):
    frame = cv2.imread(str(fixtures_dir / filename))
    assert frame is not None, f"failed to load {filename}"
    assert classify_banner(frame) == expected
