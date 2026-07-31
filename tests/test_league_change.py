import cv2
import pytest

from conftest import list_screenshot_fixtures, requires_fixtures, requires_video_fixtures
from nss_tracker.detection.league_change import (
    confirm_demotion_label_text,
    is_demotion_label_candidate,
    is_league_change_screen,
)

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


# 79_result_rank_up_hdr_off.png・101_result_rank_up_hdr_off_2.pngはいずれも
# リーグ昇格の全画面オーバーレイそのもの(意図的にTrueになるべき唯一のfixture群、
# 101はIssue #192で2件目のサンプルとして追加)なので、以下の「非該当画面は
# 全てFalseのはず」テストの対象から除く
LEAGUE_CHANGE_OVERLAY_SCREENSHOTS = {
    "79_result_rank_up_hdr_off.png",
    "101_result_rank_up_hdr_off_2.png",
}


@requires_fixtures
def test_is_league_change_screen_false_for_non_overlay_screenshots(fixtures_dir):
    """ロビー・マッチング・試合中・結果バナー等、演出画面ではない静止画では
    is_league_change_screenが常にFalseであることを確認する
    (fixtures/screenshots/*.pngのうちLEAGUE_CHANGE_OVERLAY_SCREENSHOTS以外は
    演出画面を含まないため全件が非該当のはず)。
    """
    screenshots = list_screenshot_fixtures(fixtures_dir)
    assert screenshots, "fixtures/screenshots/にpngが見つからない"
    for path in screenshots:
        if path.name in LEAGUE_CHANGE_OVERLAY_SCREENSHOTS:
            continue
        frame = cv2.imread(str(path))
        assert frame is not None, f"failed to load {path.name}"
        assert not is_league_change_screen(frame), f"{path.name}で誤検知した"


@requires_fixtures
@pytest.mark.parametrize("filename", sorted(LEAGUE_CHANGE_OVERLAY_SCREENSHOTS))
def test_is_league_change_screen_true_for_promotion_overlay_screenshot(fixtures_dir, filename):
    path = fixtures_dir / filename
    frame = cv2.imread(str(path))
    assert frame is not None, f"failed to load {filename}"
    assert is_league_change_screen(frame), f"{filename}で検知できなかった"


# 実際の映像を目視確認して決めたフレーム区間(is_league_change_screen自体の
# 判定結果をそのまま転記したものではない、60fps)。
# - OVERLAY_RANGE: 「リーグ昇格!」の全画面オーバーレイが表示されている区間
# - BEFORE_RANGE / AFTER_RANGE: オーバーレイの前後、通常の結果バナー・ランクバッジ表示中
LEAGUE_UP_VIDEO = "30_win_blue_league_up_hdr_off.mp4"
BEFORE_RANGE = range(1830, 1950)
OVERLAY_RANGE = range(2070, 2280)
AFTER_RANGE = range(2370, 2460)


@requires_video_fixtures
def test_is_league_change_screen_detects_promotion_overlay(videos_dir):
    video_path = videos_dir / LEAGUE_UP_VIDEO
    if not video_path.is_file():
        pytest.skip(f"{LEAGUE_UP_VIDEO} が見つからない")

    detected_overlay = False
    false_positive_frame = None
    for idx, frame in enumerate(_read_frames(video_path)):
        result = is_league_change_screen(frame)
        if idx in OVERLAY_RANGE:
            detected_overlay = detected_overlay or result
        elif idx in BEFORE_RANGE or idx in AFTER_RANGE:
            if result and false_positive_frame is None:
                false_positive_frame = idx

    assert detected_overlay, "昇格演出の区間で一度もTrueにならなかった"
    assert false_positive_frame is None, f"演出区間外(フレーム{false_positive_frame})で誤検知した"


# 降格時は昇格と異なり全画面演出が一切出ず、ランクバッジ上に小さい「降格」ラベルが
# 乗るだけ(バッジ自体は隠れない)。目視確認済み(state/match_state.pyのモジュール
# docstring・detection/league_change.pyのモジュールdocstring参照)。
# state/match_state.pyのrank_recheck機構・バナー消灯時フォールバック確定は
# このケースに対応するために追加された
# (test_match_state.pyのtest_track_rank_grace_recheck_catches_tier_change参照)。
# is_league_change_screenが全画面演出だけを見て判定する設計上、これらの動画では
# 全編を通じて一度もTrueにならないのが正しい挙動。
#
# 以前は実際に降格したことを確認済みの動画(03/10番)を使っていたが、Issue #148で
# HDR無効化前fixtureとして削除された。代替のHDR無効化後fixtureはいずれも降格が
# 実際に発生したことまでは確認していない(negative-caseの一般的な誤検知防止の
# 回帰としてのみ使う)
#
# Issue #150(解消済み): 29番はフレーム270〜300付近(約0.5秒)でis_league_change_screenが
# 誤ってTrueになっていた(スタジアムのミント/ティール色の天蓋の写り込み、
# 画面全体の平均HSV判定だったことが原因)。Issue #160でROIベースの判定
# (detection/league_change.pyのモジュールdocstring参照)に切り替えたことで解消し、
# xfailを解除した。
LEAGUE_DOWN_WITHOUT_OVERLAY_VIDEOS = [
    "29_lose_blue_hdr_off.mp4",
    "31_lose_blue_without_rank_hdr_off.mp4",
]


@requires_video_fixtures
@pytest.mark.parametrize("video_name", LEAGUE_DOWN_WITHOUT_OVERLAY_VIDEOS)
def test_is_league_change_screen_false_throughout_when_no_dedicated_overlay(videos_dir, video_name):
    video_path = videos_dir / video_name
    if not video_path.is_file():
        pytest.skip(f"{video_name} が見つからない")

    for idx, frame in enumerate(_read_frames(video_path)):
        assert not is_league_change_screen(frame), (
            f"{video_name}のフレーム{idx}で誤検知した"
            "(この動画は降格が小さいラベル表示のみで全画面演出が出ないケース)"
        )


# Issue #215: 以前はここに、Issue #160のROIベース再較正の検証中に見つかった
# 21番動画内の(それまで気付かれていなかった)本物の昇格演出区間を検知できるかの
# 回帰テストがあった。21番はHDRオン収録のfixtureだったため削除し、このテストも
# 削除した。同じ「実際の昇格演出動画で真陽性を検知できる」内容は、専用の
# 昇格演出動画である30_win_blue_league_up_hdr_off.mp4(test_is_league_change_
# screen_detects_promotion_overlay、上記参照)で既にカバーされているため、
# 実質的な検証内容の損失は無い。


# Issue #176: 降格ラベル(「降格」の吹き出し)の検知。98・106はいずれも実際に
# 降格した試合の結果画面(別セッション、106は動画43のframe 360から切り出し)。
# 「非該当画面は全てFalseのはず」テストの対象からは除く
DEMOTION_LABEL_SCREENSHOTS = {
    "98_result_lose_with_rank_demotion_red_hdr_off.png",
    "106_result_lose_with_rank_demotion_red_hdr_off_2.png",
}


@requires_fixtures
@pytest.mark.parametrize("filename", sorted(DEMOTION_LABEL_SCREENSHOTS))
def test_is_demotion_label_candidate_true_for_demotion_screenshot(fixtures_dir, filename):
    path = fixtures_dir / filename
    frame = cv2.imread(str(path))
    assert frame is not None, f"failed to load {filename}"
    assert is_demotion_label_candidate(frame), f"{filename}で降格ラベルを検知できなかった"


@requires_fixtures
@pytest.mark.parametrize("filename", sorted(DEMOTION_LABEL_SCREENSHOTS))
def test_confirm_demotion_label_text_true_for_demotion_screenshot(fixtures_dir, filename):
    """PaddleOCRで実際に「降格」の文字が読み取れることを確認する(重い処理、slow指定はしない。

    goal.py/match_end.pyの確認関数と同じPaddleOCRエンジンを共有しているため、
    tests/test_goal.py等が既にPaddleOCRの初期化コストを許容している前提と揃える)。
    """
    path = fixtures_dir / filename
    frame = cv2.imread(str(path))
    assert frame is not None, f"failed to load {filename}"
    assert confirm_demotion_label_text(frame), f"{filename}でOCRにより「降格」を確認できなかった"


@requires_fixtures
def test_is_demotion_label_candidate_false_for_non_demotion_screenshots(fixtures_dir):
    """降格ラベルを含まない静止画では常にFalseであることを確認する(誤検知防止)。

    Issue #176の調査でfixtures/screenshots全43枚(DEMOTION_LABEL_SCREENSHOTS除く)を
    実測し、リーグ昇格の全画面オーバーレイ等の「画面全体が明るい」特殊画面を
    含め、いずれもDEMOTION_LABEL_WHITE_COUNT_RANGEの範囲外だったことを確認済み。
    """
    screenshots = list_screenshot_fixtures(fixtures_dir)
    assert screenshots, "fixtures/screenshots/にpngが見つからない"
    for path in screenshots:
        if path.name in DEMOTION_LABEL_SCREENSHOTS:
            continue
        frame = cv2.imread(str(path))
        assert frame is not None, f"failed to load {path.name}"
        assert not is_demotion_label_candidate(frame), f"{path.name}で誤検知した"


# 実際の映像を目視確認して決めたフレーム区間(60fps換算ではなく実ファイルの
# フレーム番号。fps=30)。frame 350は降格前(帯39、ラベル無し)、frame 450は
# 降格ラベル表示中(帯38)であることをcv2.imwrite経由で切り出した画像を目視して
# 直接確認済み(is_demotion_label_candidate自体の出力をそのまま転記したものではない)。
DEMOTION_LABEL_VIDEO = "40_lose_red_demotion_hdr_off.mp4"
BEFORE_LABEL_RANGE = range(0, 300)
LABEL_VISIBLE_RANGE = range(420, 495)


@requires_video_fixtures
def test_is_demotion_label_candidate_detects_label_in_video(videos_dir):
    video_path = videos_dir / DEMOTION_LABEL_VIDEO
    if not video_path.is_file():
        pytest.skip(f"{DEMOTION_LABEL_VIDEO} が見つからない")

    detected_label = False
    false_positive_frame = None
    for idx, frame in enumerate(_read_frames(video_path)):
        result = is_demotion_label_candidate(frame)
        if idx in LABEL_VISIBLE_RANGE:
            detected_label = detected_label or result
        elif idx in BEFORE_LABEL_RANGE:
            if result and false_positive_frame is None:
                false_positive_frame = idx

    assert detected_label, "降格ラベル表示区間で一度もTrueにならなかった"
    assert false_positive_frame is None, f"ラベル表示前(フレーム{false_positive_frame})で誤検知した"
