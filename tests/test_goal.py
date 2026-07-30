import json
from pathlib import Path

import cv2
import pytest

from conftest import requires_fixtures, requires_video_fixtures
from nss_tracker.detection.goal import is_goal_event, is_own_goal_event, read_assist_name, read_scorer_name

TARGET_SIZE = (1920, 1080)


def _read_frames(path):
    cap = cv2.VideoCapture(str(path))
    try:
        while True:
            ok, frame = cap.read()
            if not ok:
                break
            yield cv2.resize(frame, TARGET_SIZE)
    finally:
        cap.release()

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
    "83_goal_without_assist_blue_hdr_off.png": True,
    "84_result_win_with_rank_blue_hdr_off.png": False,
    "85_result_win_with_rank_enlarged_blue_hdr_off.png": False,
    "86_matching_with_rank_4v4_hdr_off.png": False,
    "87_matching_hdr_off_3.png": False,
}


@requires_fixtures
@pytest.mark.parametrize("filename, expected", sorted(EXPECTED_EVENT.items()))
def test_is_goal_event(fixtures_dir, filename, expected):
    frame = cv2.imread(str(fixtures_dir / filename))
    assert frame is not None, f"failed to load {filename}"
    assert is_goal_event(frame) == expected


# 色閾値の広域監査(Issue #144/#172/#182の関連作業)で判明した既知の誤検知。
# BANNER_ROI_LEFT/RIGHT(画面上部y=305-415)に青空・スタジアムのミント/ティール
# 色天蓋(banner.py/league_change.pyで既知のIssue #67/#159/#150と同系統の
# 天蓋)が写り込むと、is_goal_eventが本物の青ゴールバナーと誤って区別できず
# Trueを返す。本物の青ゴールバナー(26/27番動画)とH/S/V/Hue標準偏差いずれの
# 軸でも実測範囲が重複しており、単純な閾値再較正では安全に分離できないと
# 判明した(Issue #186で詳細調査・記録)。
#
# 現状はstate/match_state.pyのgoal_confirm_frames(60fps実キャプチャなら
# 60フレーム=1秒相当のデバウンス)により本番の誤検知(DB記録)には至って
# いない(24番の最大連続一致は33フレーム、25番は最大19フレームで、いずれも
# 必要な60フレームに届かない)。ただし安全マージンとは言えないため、xfailで
# 状況を可視化しておく(Issue #182・#150と同じ方針)。
_KNOWN_SKY_CANOPY_FALSE_POSITIVE_VIDEOS = {
    "24_no_vs_screen_hdr_off_gameplay.mp4",
    "25_inplay_false_positive_win_blue_teal_canopy.mp4",
}


@requires_video_fixtures
@pytest.mark.parametrize(
    "video_name",
    [
        pytest.param(
            name,
            marks=pytest.mark.xfail(
                reason="画面上部の青空・スタジアム天蓋の写り込みでis_goal_eventが誤検知する"
                "(Issue #186で調査、本物の青ゴールバナーとHSV範囲が重複するため単純な閾値"
                "再較正では未解決。goal_confirm_framesのデバウンスにより本番の誤検知には"
                "至っていない)",
                strict=False,
            ),
        )
        for name in sorted(_KNOWN_SKY_CANOPY_FALSE_POSITIVE_VIDEOS)
    ],
)
def test_is_goal_event_false_throughout_non_goal_gameplay_video(videos_dir, video_name):
    video_path = videos_dir / video_name
    if not video_path.is_file():
        pytest.skip(f"{video_name} が見つからない")

    for idx, frame in enumerate(_read_frames(video_path)):
        assert not is_goal_event(frame), f"{video_name}のフレーム{idx}で誤検知した"


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
def test_read_scorer_name_for_solo_goal_without_assist(fixtures_dir):
    """アシスト無しの単独ゴール(オウンゴールではない)では、「ゴール」ラベル+
    得点者名がアシスト側の位置にずれて表示されるが、read_scorer_name()は
    それでも正しく得点者名を読み取れるはず(Issue #141のモジュールdocstring
    参照。Issue #153で収集した参照fixtureにより実データ検証できた)。
    read_assist_name()はアシストが無いためNoneを返す。
    """
    frame = cv2.imread(str(fixtures_dir / "83_goal_without_assist_blue_hdr_off.png"))
    assert frame is not None

    scorer = read_scorer_name(frame)
    assert scorer is not None
    name, score = scorer
    assert isinstance(name, str) and name
    assert 0.0 <= score <= 1.0

    assert read_assist_name(frame) is None


@pytest.mark.slow
@requires_fixtures
def test_read_scorer_and_assist_name_return_none_for_own_goal(fixtures_dir):
    """オウンゴールでは得点者名が表示されないため、read_scorer_name()・
    read_assist_name()はどちらもNoneを返すべき(Issue #141)。

    名前パネルを4行の個別ROIに分割する前は、「オウンゴール」というラベル
    文字列自体が既知のラベルバリエーションに含まれておらず、名前として
    誤採用されてしまっていた(read_scorer_nameが"オウンゴール"を返す不具合)。
    """
    frame = cv2.imread(str(fixtures_dir / "75_goal_blue_owngoal_hdr_off.png"))
    assert frame is not None

    assert read_scorer_name(frame) is None
    assert read_assist_name(frame) is None


@pytest.mark.slow
@requires_fixtures
def test_is_own_goal_event(fixtures_dir):
    frame = cv2.imread(str(fixtures_dir / "75_goal_blue_owngoal_hdr_off.png"))
    assert frame is not None
    assert is_own_goal_event(frame)

    frame = cv2.imread(str(fixtures_dir / "74_goal_with_assist_red_hdr_off.png"))
    assert frame is not None
    assert not is_own_goal_event(frame)

    frame = cv2.imread(str(fixtures_dir / "83_goal_without_assist_blue_hdr_off.png"))
    assert frame is not None
    assert not is_own_goal_event(frame)


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
