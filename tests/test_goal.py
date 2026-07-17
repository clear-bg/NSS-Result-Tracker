import json
from pathlib import Path

import cv2
import pytest

from conftest import requires_fixtures
from nss_tracker.detection.goal import is_goal_event, read_assist_name, read_scorer_name

# 得点者・アシスト名の正解データはプレイヤー実名を含むため、fixtures/screenshots
# 本体と同様に.gitignore対象のローカルファイルから読み込む(リポジトリには含めない)
NAME_EXPECTATIONS_FILENAME = "goal_name_expectations.json"

EXPECTED_EVENT = {
    "00_lobby.png": False,
    "01_before_start.png": False,
    "02_matching_in_progress.png": False,
    "10_waiting_for_other_players_blue.png": False,
    "11_matching_with_rank_blue.png": False,
    "12_matching_without_rank_blue.png": False,
    "13_waiting_for_other_players_red.png": False,
    "14_matching_with_rank_red.png": False,
    "20_in_game_blue.png": False,
    "21_goal_with_assist_blue.png": True,
    "22_goal_without_assist_blue.png": True,
    "23_assist_blue.png": True,
    "24_GA_without_me_blue.png": True,
    "25_resume_game_blue.png": False,
    "30_in_game_red.png": False,
    "31_goal_with_assist_red.png": True,
    "32_goal_without_assist_red.png": True,
    "33_assist_red.png": True,
    "34_GA_without_me_red.png": True,
    "35_resume_game_red.png": False,
    "43_result_win_without_rank_blue.png": False,
    "44_result_lose_with_rank_blue.png": False,
    "46_result_lose_after_rank_decrease_blue.png": False,
    "50_result_win_with_rank_red.png": False,
    "52_result_after_rank_increase_red.png": False,
    "54_result_lose_with_rank_red.png": False,
    "56_result_lose_after_rank_decrease_red.png": False,
    "60_start_overtime.png": False,
    "61_overtime_in_game.png": False,
}


@requires_fixtures
@pytest.mark.parametrize("filename, expected", sorted(EXPECTED_EVENT.items()))
def test_is_goal_event(fixtures_dir, filename, expected):
    frame = cv2.imread(str(fixtures_dir / filename))
    assert frame is not None, f"failed to load {filename}"
    assert is_goal_event(frame) == expected


@pytest.mark.slow
@requires_fixtures
def test_name_ocr_accuracy(fixtures_dir):
    """得点者・アシスト名OCRの実現性検証。

    OCRである以上まれな誤読はありうるため、1件ずつの完全一致ではなく
    全体の正答率で実現性を判断する(フェーズAの検証目的)。
    正解データ(プレイヤー実名を含む)がローカルに無い場合はskipする。
    """
    expectations_path: Path = fixtures_dir / NAME_EXPECTATIONS_FILENAME
    if not expectations_path.is_file():
        pytest.skip(f"{NAME_EXPECTATIONS_FILENAME} が存在しません(プレイヤー実名を含むためローカルにのみ配置)")
    name_expectations = json.loads(expectations_path.read_text(encoding="utf-8"))

    total = 0
    correct = 0
    mismatches = []
    for filename, (expected_scorer, expected_assist) in name_expectations.items():
        frame = cv2.imread(str(fixtures_dir / filename))
        assert frame is not None, f"failed to load {filename}"

        scorer = read_scorer_name(frame)
        total += 1
        if scorer == expected_scorer:
            correct += 1
        else:
            mismatches.append((filename, "scorer", expected_scorer, scorer))

        assist = read_assist_name(frame)
        total += 1
        if assist == expected_assist:
            correct += 1
        else:
            mismatches.append((filename, "assist", expected_assist, assist))

    accuracy = correct / total
    print(f"\n名前OCR正答率: {correct}/{total} ({accuracy:.0%})")
    for filename, role, expected, actual in mismatches:
        print(f"  誤読: {filename} {role} 期待={expected!r} 実際={actual!r}")

    assert accuracy >= 0.85, f"名前OCRの正答率が低すぎる: {correct}/{total}"
