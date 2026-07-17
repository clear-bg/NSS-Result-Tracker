import cv2
import pytest

from conftest import requires_fixtures, requires_video_fixtures
from nss_tracker.detection.league_change import is_league_change_screen

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


# 62_result_rank_up.pngはリーグ昇格の全画面オーバーレイそのもの(意図的にTrueになる
# べき唯一のfixture)なので、以下の「非該当画面は全てFalseのはず」テストの対象から除く
LEAGUE_CHANGE_OVERLAY_SCREENSHOT = "62_result_rank_up.png"


@requires_fixtures
def test_is_league_change_screen_false_for_non_overlay_screenshots(fixtures_dir):
    """ロビー・マッチング・試合中・結果バナー等、演出画面ではない静止画では
    is_league_change_screenが常にFalseであることを確認する
    (fixtures/screenshots/*.pngのうち62_result_rank_up.png以外は演出画面を
    含まないため全件が非該当のはず)。
    """
    screenshots = sorted(fixtures_dir.glob("*.png"))
    assert screenshots, "fixtures/screenshots/にpngが見つからない"
    for path in screenshots:
        if path.name == LEAGUE_CHANGE_OVERLAY_SCREENSHOT:
            continue
        frame = cv2.imread(str(path))
        assert frame is not None, f"failed to load {path.name}"
        assert not is_league_change_screen(frame), f"{path.name}で誤検知した"


@requires_fixtures
def test_is_league_change_screen_true_for_promotion_overlay_screenshot(fixtures_dir):
    path = fixtures_dir / LEAGUE_CHANGE_OVERLAY_SCREENSHOT
    frame = cv2.imread(str(path))
    assert frame is not None, f"failed to load {path.name}"
    assert is_league_change_screen(frame), f"{path.name}で検知できなかった"


# 実際の映像を目視確認して決めたフレーム区間(is_league_change_screen自体の
# 判定結果をそのまま転記したものではない)。
# - OVERLAY_RANGE: 「リーグ昇格!」の全画面オーバーレイが表示されている区間
# - BEFORE_RANGE / AFTER_RANGE: オーバーレイの前後、通常の結果バナー・ランクバッジ表示中
#   (BEFORE_RANGEの開始はbanner_confirmed_frame_range、AFTER_RANGEの終端は
#   match_result_frame_rangeとfixtures/videos/metadata.jsonで整合させている)
LEAGUE_UP_VIDEO = "01_win_blue_2-1.mp4"
BEFORE_RANGE = range(950, 1221)
OVERLAY_RANGE = range(1250, 1446)
AFTER_RANGE = range(1490, 1556)


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


# 降格時は専用の全画面演出が出ず、小さい「降格」ラベルのみが表示されるケースがある
# (目視確認済み)。state/match_state.pyのrank_recheck機構はこのケースに対応するために
# 追加された(test_match_state.pyのtest_track_rank_grace_recheck_catches_tier_change参照)。
# is_league_change_screenが全画面演出だけを見て判定する設計上、この動画では
# 全編を通じて一度もTrueにならないのが正しい挙動。
LEAGUE_DOWN_WITHOUT_OVERLAY_VIDEO = "03_lose_blue_2-3.mp4"


@requires_video_fixtures
def test_is_league_change_screen_false_throughout_when_no_dedicated_overlay(videos_dir):
    video_path = videos_dir / LEAGUE_DOWN_WITHOUT_OVERLAY_VIDEO
    if not video_path.is_file():
        pytest.skip(f"{LEAGUE_DOWN_WITHOUT_OVERLAY_VIDEO} が見つからない")

    for idx, frame in enumerate(_read_frames(video_path)):
        assert not is_league_change_screen(frame), (
            f"{LEAGUE_DOWN_WITHOUT_OVERLAY_VIDEO}のフレーム{idx}で誤検知した"
            "(この動画は降格が小さいラベル表示のみで全画面演出が出ないケース)"
        )
