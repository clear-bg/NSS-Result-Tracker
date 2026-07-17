"""実際の配信クリップを使ったis_vs_screenの回帰テスト。

fixtures/videos/12_win_red_vs_screen_to_result.mp4(frame 1750, 1850)、
fixtures/videos/13_win_red_vs_screen_without_rank.mp4(frame 1700)、
fixtures/videos/16_matching_wait_3.mp4(frame 1400)は、実際に該当フレームの
静止画を目視確認して「VS」画面が写っていることを確認済み(is_vs_screen自体の
出力を転記したものではない)。

単発フレームでは試合中の演出アイコン等がROIに重なり稀に誤検知することがある
(video 12のframe 4397、プレイヤー頭上のミント色アイコンが1フレームだけ
重なるケースを確認済み、最長でも7フレーム程度)。一方、本物のVS画面は
fixtures/videos 12/13/16の実測で最短でも158フレーム(約5.3秒、30fps換算)
連続して表示され続けるため、banner.pyの結果バナー判定と同じ基準(1.0秒)を
デバウンス閾値として採用する。30fps環境では30フレームに相当し、最短の
実測ケース(158フレーム)に対しても約5倍の余裕がある(detection/matchmaking.py
のモジュールdocstring参照)。
"""

import cv2
import pytest

from conftest import requires_video_fixtures
from nss_tracker.detection.matchmaking import is_vs_screen
from nss_tracker.detection.motion import find_confirmed_value

# banner.pyの結果バナー判定と同じ基準(1.0秒 = 30fps換算で30フレーム)。
# 実測ではVS画面は最短でも158フレーム(約5.3秒)連続して表示されるため、
# この閾値でも約5倍の余裕がある
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


# VS画面を含むことを目視確認済みの動画
VS_SCREEN_VIDEOS = [
    "12_win_red_vs_screen_to_result.mp4",
    "13_win_red_vs_screen_without_rank.mp4",
    "16_matching_wait_3.mp4",
]


@requires_video_fixtures
@pytest.mark.parametrize("video_name", VS_SCREEN_VIDEOS)
def test_confirmed_vs_screen_detected_in_known_vs_clips(videos_dir, video_name):
    video_path = videos_dir / video_name
    if not video_path.is_file():
        pytest.skip(f"{video_name} が見つからない")

    assert _confirmed_vs_screen(video_path), f"{video_name}: VS画面を確定検知できなかった"


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
]


@requires_video_fixtures
@pytest.mark.parametrize("video_name", NO_VS_SCREEN_VIDEOS)
def test_confirmed_vs_screen_stays_false_without_vs_screen(videos_dir, video_name):
    video_path = videos_dir / video_name
    if not video_path.is_file():
        pytest.skip(f"{video_name} が見つからない")

    assert not _confirmed_vs_screen(video_path), f"{video_name}: VS画面を含まないのに誤検知した"
