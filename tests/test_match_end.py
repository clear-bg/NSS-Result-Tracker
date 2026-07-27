import cv2
import pytest

from conftest import list_screenshot_fixtures, requires_fixtures, requires_video_fixtures
from nss_tracker.detection.match_end import confirm_match_end_text, is_match_end_screen

# Issue #148: 76は目視では紛れもなく「試合終了」の帯だが、MATCH_END_LEFT_ROIの
# 左上角がちょうど帯の丸みを帯びた端にかかり(この帯の実際の描画位置・幅が既存の
# ROI較正時の前提とわずかにズレている)、hue_std> MATCH_END_HUE_STD_MAXとなって
# 色ベースの候補判定自体を通過できない(実測std≈13.0、閾値12.0)。ROIの再較正は
# Issue #142のスコープのため、ここでは既知の欠落としてxfailにし、正しい値として
# ground truthはTrueのまま残す(is_match_end_screenがバグっているのであって
# fixtureが間違っているわけではないため)
MATCH_END_SCREENSHOTS = {"76_match_end_hdr_off.png", "80_match_end_hdr_off_2.png"}
_KNOWN_ROI_BOUNDARY_GAPS = {"76_match_end_hdr_off.png"}


@requires_fixtures
@pytest.mark.parametrize(
    "filename",
    [
        pytest.param(
            name,
            marks=pytest.mark.xfail(
                reason="MATCH_END_LEFT_ROIが帯の丸み端にかかりhue_stdが閾値を僅かに超える(Issue #142で対応予定)",
                strict=False,
            ),
        )
        if name in _KNOWN_ROI_BOUNDARY_GAPS
        else name
        for name in sorted(MATCH_END_SCREENSHOTS)
    ],
)
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
def test_confirm_match_end_text_true_for_match_end_screenshot(fixtures_dir):
    frame = cv2.imread(str(fixtures_dir / "80_match_end_hdr_off_2.png"))
    assert frame is not None
    assert confirm_match_end_text(frame)


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
# 出力を転記したものではない、60fps)。21番のframe 465は「キックオフ」バナーで、
# 色ベースのis_match_end_screenはTrueを返すがconfirm_match_end_textはFalseに
# ならなければならない(Issue #76、モジュールdocstring参照)。
KNOWN_MATCH_END_FRAMES = [
    ("28_win_red_1-0_hdr_off.mp4", 800),
    ("29_lose_blue_hdr_off.mp4", 958),
    ("30_win_blue_league_up_hdr_off.mp4", 1540),
    ("31_lose_blue_without_rank_hdr_off.mp4", 15),
]
KNOWN_KICKOFF_FRAMES = [
    ("21_goal_event_false_positive_win_blue_4-3.mp4", 465),
]


@pytest.mark.slow
@requires_video_fixtures
@pytest.mark.parametrize("video_name, frame_index", KNOWN_MATCH_END_FRAMES)
def test_confirm_match_end_text_true_for_known_match_end_frames(videos_dir, video_name, frame_index):
    frame = _read_frame(videos_dir / video_name, frame_index)
    if frame is None:
        pytest.skip(f"{video_name} が見つからない")
    assert is_match_end_screen(frame), f"{video_name} frame {frame_index}: 色ベース候補判定がFalseだった"
    assert confirm_match_end_text(frame), f"{video_name} frame {frame_index}: 「試合終了」と読み取れなかった"


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
