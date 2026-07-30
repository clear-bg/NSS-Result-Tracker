"""実際の配信クリップを使ったis_vs_screenの回帰テスト。

Issue #116: VS_HUE_RANGE等を2026-07-24の実機ライブパイプライン(FfmpegFrameReader)
実測値に更新した際、fixtures/videos/22・23_vs_screen_hdr_off_*.mp4(cv2.VideoCaptureで
読む)を使った真陽性の回帰テストは一時的に意味を失っていた(cv2.VideoCaptureの
色変換経路がライブパイプラインと異なり、どんな実機録画から切り出した動画でも
cv2経由では当時の閾値の色域を再現できなかったため)。

Issue #144/#189対応(2026-07-31)で、色判定単体からレターボックス判定
(is_letterboxed、detection/matchmaking.pyのモジュールdocstring参照)+色判定の
組み合わせに変更した後、22・23番は実際にcv2.VideoCapture経由でも
VS画面を正しく検知できる(11〜14秒の長い確定区間)ことを確認した。輝度ベースの
判定は読み込み経路の違いに強いため、この2本は真陽性の回帰テストとして
復活させている。
"""

import cv2
import pytest

from conftest import requires_video_fixtures
from nss_tracker.detection.matchmaking import is_vs_screen
from nss_tracker.detection.motion import find_confirmed_value

# banner.pyの結果バナー判定と同じ基準(1.0秒 = 30fps換算で30フレーム)
MIN_CONFIRM_SECONDS = 1.0


def _confirmed_vs_screen(path, min_confirm_seconds: float = MIN_CONFIRM_SECONDS) -> bool:
    cap = cv2.VideoCapture(str(path))
    try:
        fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
        min_run_length = round(min_confirm_seconds * fps)

        def results():
            while True:
                ok, frame = cap.read()
                if not ok:
                    return
                yield True if is_vs_screen(frame) else None

        return find_confirmed_value(results(), min_run_length=min_run_length) is True
    finally:
        cap.release()


# Issue #144/#189: 実際にVS画面を含むことを確認済みの動画(モジュールdocstring参照)
VS_SCREEN_VIDEOS = [
    "22_vs_screen_hdr_off_1.mp4",
    "23_vs_screen_hdr_off_2.mp4",
]


@requires_video_fixtures
@pytest.mark.parametrize("video_name", VS_SCREEN_VIDEOS)
def test_confirmed_vs_screen_true_for_vs_screen_videos(videos_dir, video_name):
    video_path = videos_dir / video_name
    if not video_path.is_file():
        pytest.skip(f"{video_name} が見つからない")

    assert _confirmed_vs_screen(video_path), f"{video_name}: VS画面を含むのに検知できなかった"


# VS画面を含まない(結果バナー付近のみ切り出した、またはマッチング待機のみの)動画。
# いずれも試合中の演出アイコン等による単発フレームの誤検知はあり得るが、
# デバウンス後は一度も確定しないはず
NO_VS_SCREEN_VIDEOS = [
    "24_no_vs_screen_hdr_off_gameplay.mp4",
    "28_win_red_1-0_hdr_off.mp4",
    "29_lose_blue_hdr_off.mp4",
]


@requires_video_fixtures
@pytest.mark.parametrize("video_name", NO_VS_SCREEN_VIDEOS)
def test_confirmed_vs_screen_stays_false_without_vs_screen(videos_dir, video_name):
    video_path = videos_dir / video_name
    if not video_path.is_file():
        pytest.skip(f"{video_name} が見つからない")

    assert not _confirmed_vs_screen(video_path), f"{video_name}: VS画面を含まないのに誤検知した"
