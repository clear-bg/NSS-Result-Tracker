import cv2
import pytest

from conftest import requires_fixtures, requires_video_fixtures
from nss_tracker.detection.vs_rank import SlotRank, read_vs_screen_ranks

_INF = "∞"


def _r(tier, value):
    return SlotRank(tier, value)


def _none():
    return SlotRank(None, None)


@pytest.mark.parametrize(
    "slot_rank, expected",
    [
        (SlotRank("∞", 39), "∞39"),
        (SlotRank("S", 9), "S9"),
        (SlotRank("A", 0), "A0"),
        (SlotRank("S", None), "S?"),
        (SlotRank(None, None), "-"),
    ],
)
def test_slot_rank_repr(slot_rank, expected):
    """Issue #121: ログにそのまま出せる簡潔表記("∞39"等)になることを確認する。"""
    assert repr(slot_rank) == expected
    assert str([slot_rank]) == f"[{expected}]"


# 正解値は各fixtureの静止画を目視確認して得たもの(read_vs_screen_ranks自体の出力を
# 転記したものではない)。スロット0が画面手前(自チーム側は自分自身)、スロット3が
# 最も奥。
#
# Issue #148(HDR無効化前fixture削除に伴う張り替え): 82番はIssue #147で新規収集した
# HDR無効化後のVS画面(4vs3の変則試合)。MINE_ICON_ROIS/MINE_NUM_ROIS/
# OPPONENT_ICON_ROIS/OPPONENT_NUM_ROISの実座標でそれぞれのスロットを切り出し、
# 8倍拡大したうえで目視確認して以下の正解値を確定した
# (mine=∞39/S8/∞9/∞34, opponent=S1/∞41/∞33/[3人しかいないため4人目は非表示])。
# S帯バッジ(mine[1]・opponent[0])と、相手が3人しかいない試合でのopponent[3]の
# SlotRank(None, None)(文字階級ではなく単に不在)を両方カバーできている
EXPECTED_SCREENSHOTS = {
    "82_matching_with_rank_4v3_hdr_off.png": (
        [_r(_INF, 39), _r("S", 8), _r(_INF, 9), _r(_INF, 34)],
        [_r("S", 1), _r(_INF, 41), _r(_INF, 33), _none()],
    ),
}


def _tally(mismatches, total, correct, filename, side, slot_index, actual, expected):
    """1スロット分をtier/valueそれぞれ独立に採点する。

    SlotRank全体の完全一致で1件と数えると、文字帯(tier)は合っているのに
    数値ピル(value)だけ誤読した場合と、tier自体を誤判定した場合が同じ1件の
    不一致として埋もれてしまう。どちらの精度も別々に見えるよう、tier/valueを
    2件として集計する。
    """
    for field_name, actual_field, expected_field in (
        ("tier", actual.tier, expected.tier),
        ("value", actual.value, expected.value),
    ):
        total[0] += 1
        if actual_field == expected_field:
            correct[0] += 1
        else:
            mismatches.append((filename, side, slot_index, field_name, expected_field, actual_field))


@pytest.mark.slow
@requires_fixtures
def test_read_vs_screen_ranks_accuracy(fixtures_dir):
    """VS画面ランクOCRの実現性検証。

    OCRである以上まれな誤読・検出漏れはありうるため(モジュールdocstring参照)、
    1件ずつの完全一致ではなく全体の正答率で実現性を判断する
    (test_goal.pyのtest_name_ocr_accuracyと同じ方針)。tier(∞/S/A判定)と
    value(数値ピル)は別々の失敗モードのため、それぞれ独立に採点する。
    """
    total = [0]
    correct = [0]
    mismatches = []
    for filename, (expected_mine, expected_opponent) in EXPECTED_SCREENSHOTS.items():
        frame = cv2.imread(str(fixtures_dir / filename))
        assert frame is not None, f"failed to load {filename}"

        mine, opponent = read_vs_screen_ranks(frame)
        for side, actual_list, expected_list in (("mine", mine, expected_mine), ("opponent", opponent, expected_opponent)):
            for slot_index, (actual, expected) in enumerate(zip(actual_list, expected_list)):
                _tally(mismatches, total, correct, filename, side, slot_index, actual, expected)

    accuracy = correct[0] / total[0]
    print(f"\nVS画面ランクOCR正答率: {correct[0]}/{total[0]} ({accuracy:.0%})")
    for filename, side, slot_index, field_name, expected, actual in mismatches:
        print(f"  誤読: {filename} {side}[{slot_index}].{field_name} 期待={expected!r} 実際={actual!r}")

    assert accuracy >= 0.85, f"VS画面ランクOCRの正答率が低すぎる: {correct[0]}/{total[0]}"


# 実際の配信クリップ(の1フレーム)を目視確認して得た正解値。32は82(静止画)と
# 同一試合・同一瞬間の収録(静止画/動画で切り出し方が異なっても同じ結果になる
# ことの確認を兼ねる。frame 660はスクリーンショット82と同一のフレーム)
EXPECTED_VIDEO_FRAMES = {
    "32_vs_screen_with_rank_4v3_hdr_off.mp4": (
        660,
        [_r(_INF, 39), _r("S", 8), _r(_INF, 9), _r(_INF, 34)],
        [_r("S", 1), _r(_INF, 41), _r(_INF, 33), _none()],
    ),
}


@pytest.mark.slow
@requires_video_fixtures
def test_read_vs_screen_ranks_accuracy_on_real_clips(videos_dir):
    """実機キャプチャ動画(VS画面)でのOCR実現性検証。

    静止画fixtureは可逆圧縮のPNGだが、実際の入力はffmpeg経由の生フレーム
    (キャプチャ後、必要に応じて動画エンコードされたもの)のため、圧縮由来の
    ノイズがある実データでも同程度の精度が出ることを確認する。
    """
    total = [0]
    correct = [0]
    mismatches = []
    for filename, (frame_index, expected_mine, expected_opponent) in EXPECTED_VIDEO_FRAMES.items():
        video_path = videos_dir / filename
        if not video_path.is_file():
            pytest.skip(f"{filename} が見つからない")

        cap = cv2.VideoCapture(str(video_path))
        cap.set(cv2.CAP_PROP_POS_FRAMES, frame_index)
        ok, frame = cap.read()
        cap.release()
        assert ok, f"{filename}のframe{frame_index}を読み込めなかった"

        mine, opponent = read_vs_screen_ranks(frame)
        for side, actual_list, expected_list in (("mine", mine, expected_mine), ("opponent", opponent, expected_opponent)):
            for slot_index, (actual, expected) in enumerate(zip(actual_list, expected_list)):
                _tally(mismatches, total, correct, filename, side, slot_index, actual, expected)

    accuracy = correct[0] / total[0]
    print(f"\nVS画面ランクOCR正答率(実機動画): {correct[0]}/{total[0]} ({accuracy:.0%})")
    for filename, side, slot_index, field_name, expected, actual in mismatches:
        print(f"  誤読: {filename} {side}[{slot_index}].{field_name} 期待={expected!r} 実際={actual!r}")

    assert accuracy >= 0.85, f"VS画面ランクOCR(実機動画)の正答率が低すぎる: {correct[0]}/{total[0]}"
