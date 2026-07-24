"""実際の配信クリップを使ったis_vs_screenの回帰テスト(誤検知防止側のみ)。

Issue #116: VS_HUE_RANGE等を2026-07-24の実機ライブパイプライン(FfmpegFrameReader)
実測値に更新した(detection/matchmaking.pyのモジュールdocstring参照)。この変更に
伴い、fixtures/videos/22・23_vs_screen_hdr_off_*.mp4(cv2.VideoCaptureで読む)を
使った真陽性の回帰テストは意味を失った(cv2.VideoCaptureの色変換経路がライブ
パイプラインと異なり、どんな実機録画から切り出した動画でもcv2経由では新しい
閾値の色域を再現できないため。実測: 両動画ともH97-98までは近いがS65-66で新閾値
S88-98の範囲外)。真陽性の検証はtests/test_matchmaking.pyの合成フレームによる
テストに置き換えている。

本ファイルはVS画面を含まない動画で誤検知が無いことの回帰テストのみ残す
(この観点は読み込み経路によらず有効)。12/13/16/22/23番はVS画面を捉えた
fixtureとして他用途(マッチング完了直後の遷移の参照素材等)に残す。
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


# VS画面を含まない(結果バナー付近のみ切り出した、またはマッチング待機のみの)動画。
# いずれも試合中の演出アイコン等による単発フレームの誤検知はあり得るが、
# デバウンス後は一度も確定しないはず
NO_VS_SCREEN_VIDEOS = [
    "00_lose_red_2-3.mp4",
    "01_win_blue_2-1.mp4",
    "02_lose_red_1-2.mp4",
    "03_lose_blue_2-3.mp4",
    "10_RankDown_red.mp4",
    "11_lose_blue_minimal_rank_decrease.mp4",
    "14_matching_wait_1.mp4",
    "15_matching_wait_2.mp4",
    "22_vs_screen_hdr_off_1.mp4",
    "23_vs_screen_hdr_off_2.mp4",
    "24_no_vs_screen_hdr_off_gameplay.mp4",
]


@requires_video_fixtures
@pytest.mark.parametrize("video_name", NO_VS_SCREEN_VIDEOS)
def test_confirmed_vs_screen_stays_false_without_vs_screen(videos_dir, video_name):
    video_path = videos_dir / video_name
    if not video_path.is_file():
        pytest.skip(f"{video_name} が見つからない")

    assert not _confirmed_vs_screen(video_path), f"{video_name}: VS画面を含まないのに誤検知した"
