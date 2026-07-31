import cv2
import pytest

from conftest import list_screenshot_fixtures, requires_fixtures, requires_video_fixtures
from nss_tracker.detection.match_end import confirm_match_end_text, is_match_end_screen

MATCH_END_SCREENSHOTS = {
    "76_match_end_hdr_off.png",
    "80_match_end_hdr_off_2.png",
    # Issue #192: 通常の「試合終了」単独表示とは異なり、上段に「ノックアウト」の
    # 文字が追加された複合バナー(fixtures/videos/41由来)。is_match_end_screen()・
    # confirm_match_end_text()とも実際に検証し、いずれも正しくTrueを返すことを
    # 確認済み(帯の位置・OCRとも問題なし)
    "103_match_end_knockout_hdr_off.png",
}


@requires_fixtures
@pytest.mark.parametrize("filename", sorted(MATCH_END_SCREENSHOTS))
def test_is_match_end_screen_true_for_match_end_screenshot(fixtures_dir, filename):
    frame = cv2.imread(str(fixtures_dir / filename))
    assert frame is not None, f"failed to load {filename}"
    assert is_match_end_screen(frame), f"{filename}で「試合終了」を検知できなかった"


@requires_fixtures
def test_is_match_end_screen_false_for_non_match_end_screenshots(fixtures_dir):
    """「試合終了」以外の静止画では常にFalseであることを確認する。

    特に60_start_overtime.pngは色味が非常によく似た「延長戦」バナーのため、
    誤認識しやすい既知のケースとして重要(detection/match_end.pyのモジュール
    docstring参照)。
    """
    screenshots = list_screenshot_fixtures(fixtures_dir)
    assert screenshots, "fixtures/screenshots/にpngが見つからない"
    for path in screenshots:
        if path.name in MATCH_END_SCREENSHOTS:
            continue
        frame = cv2.imread(str(path))
        assert frame is not None, f"failed to load {path.name}"
        assert not is_match_end_screen(frame), f"{path.name}で誤検知した"


@pytest.mark.slow
@requires_fixtures
@pytest.mark.parametrize("filename", sorted(MATCH_END_SCREENSHOTS))
def test_confirm_match_end_text_true_for_match_end_screenshot(fixtures_dir, filename):
    frame = cv2.imread(str(fixtures_dir / filename))
    assert frame is not None
    assert confirm_match_end_text(frame), f"{filename}で「試合終了」の文字を確認できなかった"


def _read_frame(path, frame_index: int):
    cap = cv2.VideoCapture(str(path))
    try:
        idx = 0
        while True:
            ok, frame = cap.read()
            if not ok:
                return None
            if idx == frame_index:
                return frame
            idx += 1
    finally:
        cap.release()


# 目視で実際に確認したフレーム(is_match_end_screen/confirm_match_end_textの
# 出力を転記したものではない、60fps)。26番のframe 1232は「キックオフ」バナーで、
# 色ベースのis_match_end_screenはTrueを返すがconfirm_match_end_textはFalseに
# ならなければならない(Issue #76、モジュールdocstring参照)。
#
# Issue #215: 以前はHDRオン収録の21番動画(frame 465)を使っていたが、fixtureを
# HDR無効化後の収録に統一するため、同じ「キックオフ」パターンをHDR無効化後の
# 26番動画で代用するよう差し替えた
KNOWN_KICKOFF_FRAMES = [
    ("26_goal_red_hdr_off.mp4", 1232),
]

# state/match_state.pyのDEFAULT_MATCH_END_CONFIRM_FRAMES(30fps換算)と同じ値。
# is_match_end_screenがこの回数だけ連続したタイミングで1回だけ
# confirm_match_end_textを呼ぶ、という実際の状態機械と同じロジックを
# _confirmed_match_end()でシミュレートする
MATCH_END_CONFIRM_FRAMES = 3


def _confirmed_match_end(path, confirm_frames: int = MATCH_END_CONFIRM_FRAMES) -> bool:
    """state/match_state.pyの_check_for_match_end()と同じロジックで動画を通しで
    読み、「試合終了」を検知できるかを確認する(Issue #142)。

    is_match_end_screenの色ベース判定がconfirm_frames回連続したタイミングで
    1回だけconfirm_match_end_textを呼ぶ(state/match_state.pyと同じく、その
    ストリークが途切れるまでは再度OCRを呼ばない。OCRがFalseを返した場合は
    確定させず、ストリークが途切れて再度連続した際に改めて確認する)。

    以前は動画から目視で選んだ1フレームだけを抜き出して直接判定する方式
    だったが、この方式だと「試合終了」の文字が消える直前の一瞬(拡大しながら
    消えるアニメーション中)を偶然選んでしまうと誤って失敗する
    (`29_lose_blue_hdr_off.mp4`のframe 958で実際に発生)。実運用では
    confirm_frames分の短いデバウンスで表示直後に1回だけ確認するため、この
    アニメーション区間に当たることは起こり得ない。動画を通しで実際の
    ロジックのまま流すことで、単一フレームの抜き取りに起因するこの種の
    偽陰性を避けられる。
    """
    cap = cv2.VideoCapture(str(path))
    try:
        streak = 0
        recorded = False
        while True:
            ok, frame = cap.read()
            if not ok:
                return False
            if not is_match_end_screen(frame):
                streak = 0
                recorded = False
                continue
            streak += 1
            if streak >= confirm_frames and not recorded:
                recorded = True
                if confirm_match_end_text(frame):
                    return True
    finally:
        cap.release()


MATCH_END_VIDEOS = [
    "28_win_red_1-0_hdr_off.mp4",
    "29_lose_blue_hdr_off.mp4",
    "30_win_blue_league_up_hdr_off.mp4",
    "31_lose_blue_without_rank_hdr_off.mp4",
    # Issue #192で追加。37は延長戦バナー→ゴール→試合終了→勝ちを1本で含む
    "37_win_red_overtime_goal_hdr_off.mp4",
    "39_lose_red_goal_hdr_off.mp4",
    "40_lose_red_demotion_hdr_off.mp4",
    # 41は「ノックアウト」併記の複合バナー(103参照)を含む唯一の動画
    "41_win_blue_goal_knockout_hdr_off.mp4",
    "42_win_blue_league_up_hdr_off_2.mp4",
]


@pytest.mark.slow
@requires_video_fixtures
@pytest.mark.parametrize("video_name", MATCH_END_VIDEOS)
def test_confirmed_match_end_true_for_match_end_videos(videos_dir, video_name):
    video_path = videos_dir / video_name
    if not video_path.is_file():
        pytest.skip(f"{video_name} が見つからない")
    assert _confirmed_match_end(video_path), f"{video_name}: 「試合終了」を検知できなかった"


@pytest.mark.slow
@requires_video_fixtures
@pytest.mark.parametrize("video_name, frame_index", KNOWN_KICKOFF_FRAMES)
def test_confirm_match_end_text_false_for_kickoff_banner(videos_dir, video_name, frame_index):
    """色ベースのis_match_end_screenは「キックオフ」バナーもTrueにしてしまうが
    (「延長戦」と違い帯の横幅で区別できないため)、confirm_match_end_textの
    OCRで正しく除外できることを確認する回帰テスト(Issue #76)。
    """
    frame = _read_frame(videos_dir / video_name, frame_index)
    if frame is None:
        pytest.skip(f"{video_name} が見つからない")
    assert is_match_end_screen(frame), (
        f"{video_name} frame {frame_index}: 色ベース候補判定がFalseだった"
        "(このテストの前提が崩れている可能性がある)"
    )
    assert not confirm_match_end_text(frame), f"{video_name} frame {frame_index}: 「キックオフ」を誤って試合終了と判定した"
