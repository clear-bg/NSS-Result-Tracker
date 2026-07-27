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
    "72_matching_hdr_off_1.png": False,
    "73_matching_hdr_off_2.png": False,
    "74_goal_with_assist_red_hdr_off.png": True,
    "75_goal_blue_owngoal_hdr_off.png": True,
    "76_match_end_hdr_off.png": False,
    "77_result_win_with_rank_red_hdr_off.png": False,
    "78_result_lose_with_rank_blue_hdr_off.png": False,
    "79_result_rank_up_hdr_off.png": False,
    "80_match_end_hdr_off_2.png": False,
    "81_result_lose_without_rank_blue_hdr_off.png": False,
    "82_matching_with_rank_4v3_hdr_off.png": False,
}


@requires_fixtures
@pytest.mark.parametrize("filename, expected", sorted(EXPECTED_EVENT.items()))
def test_is_goal_event(fixtures_dir, filename, expected):
    frame = cv2.imread(str(fixtures_dir / filename))
    assert frame is not None, f"failed to load {filename}"
    assert is_goal_event(frame) == expected


@pytest.mark.slow
@requires_fixtures
def test_read_scorer_name_returns_name_and_confidence_score(fixtures_dir):
    """read_scorer_name/read_assist_nameの戻り値が(名前, 信頼度スコア)のタプルに
    なっていることを確認する(Issue #71: OCRの誤読診断のためスコアも返すよう
    戻り値を拡張した)。名前の値そのものは実名のため検証しない(構造のみ確認)。
    """
    frame = cv2.imread(str(fixtures_dir / "74_goal_with_assist_red_hdr_off.png"))
    assert frame is not None

    scorer = read_scorer_name(frame)
    assert scorer is not None
    name, score = scorer
    assert isinstance(name, str) and name
    assert 0.0 <= score <= 1.0

    assist = read_assist_name(frame)
    assert assist is not None
    name, score = assist
    assert isinstance(name, str) and name
    assert 0.0 <= score <= 1.0


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

        scorer_result = read_scorer_name(frame)
        scorer = scorer_result[0] if scorer_result is not None else None
        total += 1
        if scorer == expected_scorer:
            correct += 1
        else:
            mismatches.append((filename, "scorer", expected_scorer, scorer))

        assist_result = read_assist_name(frame)
        assist = assist_result[0] if assist_result is not None else None
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
