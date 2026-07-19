import cv2
import pytest

from conftest import requires_fixtures, requires_video_fixtures
from nss_tracker.detection.vs_rank import SlotRank, read_vs_screen_ranks

_INF = "∞"


def _r(tier, value):
    return SlotRank(tier, value)


def _none():
    return SlotRank(None, None)


# 正解値は各fixtureの静止画を目視確認して得たもの(read_vs_screen_ranks自体の出力を
# 転記したものではない)。スロット0が画面手前(自チーム側は自分自身)、スロット3が
# 最も奥。70/71は文字階級(S/A)バッジのスロットを含む。Issue #40対応時
# (2026-07-20)、該当スロットのアイコン・数値ピルをそれぞれ実座標で切り出し10倍
# 拡大したうえで目視確認し、tier('S'/'A')と数値の正解値を確定した
# (70: mine[3]=S3, opponent[2]=S1 / 71: mine[2]=S0, opponent[2]=A28)。
# 14のopponent[3]も同様に確認したところ実際にはS9バッジで(旧実装は∞かどうかの
# 二値判定しかできず読み取れずNoneとしていた)、今回のS/A識別追加で正しく読める
# ようになった。11の残り1スロットのNoneは文字階級ではなくランク非表示の
# プレイヤーのため、引き続きSlotRank(None, None)のままとする
# (呼び出し側からは区別しない)。
# 2026-07-19時点、切り出し座標を目視確認のうえ精密化した結果、以下の6枚+動画3本
# (tests側では別関数)の全72スロットの数値自体は完全一致(100%)を達成している
EXPECTED_SCREENSHOTS = {
    "11_matching_with_rank_blue.png": (
        [_r(_INF, 38), _r(_INF, 1), _r(_INF, 24), _r(_INF, 9)],
        [_r(_INF, 10), _r(_INF, 12), _r(_INF, 33), _r(_INF, 18)],
    ),
    "14_matching_with_rank_red.png": (
        [_r(_INF, 37), _r(_INF, 19), _r(_INF, 4), _r(_INF, 2)],
        [_r(_INF, 23), _r(_INF, 23), _r(_INF, 14), _r("S", 9)],
    ),
    "70_rank_tier_s.png": (
        [_r(_INF, 40), _r(_INF, 9), _r(_INF, 16), _r("S", 3)],
        [_r(_INF, 14), _r(_INF, 20), _r("S", 1), _r(_INF, 32)],
    ),
    "71_rank_tier_a.png": (
        [_r(_INF, 39), _r(_INF, 8), _r("S", 0), _r(_INF, 5)],
        [_r(_INF, 32), _r(_INF, 33), _r("A", 28), _r(_INF, 0)],
    ),
    "12_matching_without_rank_blue.png": ([_none()] * 4, [_none()] * 4),
    "15_matching_without_rank_red.png": ([_none()] * 4, [_none()] * 4),
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


# 実際の配信クリップ(の1フレーム)を目視確認して得た正解値。17は11と同一試合の
# 収録(静止画/動画で切り出し方が異なっても同じ結果になることの確認を兼ねる)。
# いずれも文字階級バッジは映っていないため全スロット∞帯
EXPECTED_VIDEO_FRAMES = {
    "17_vs_screen_with_rank_1.mp4": (
        190,
        [_r(_INF, 38), _r(_INF, 1), _r(_INF, 24), _r(_INF, 9)],
        [_r(_INF, 10), _r(_INF, 12), _r(_INF, 33), _r(_INF, 18)],
    ),
    "18_vs_screen_with_rank_2.mp4": (
        220,
        [_r(_INF, 39), _r(_INF, 40), _r(_INF, 40), _r(_INF, 18)],
        [_r(_INF, 47), _r(_INF, 31), _r(_INF, 24), _r(_INF, 36)],
    ),
    "19_vs_screen_with_rank_3.mp4": (
        395,
        [_r(_INF, 38), _r(_INF, 1), _r(_INF, 14), _r(_INF, 31)],
        [_r(_INF, 18), _r(_INF, 9), _r(_INF, 7), _r(_INF, 47)],
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
