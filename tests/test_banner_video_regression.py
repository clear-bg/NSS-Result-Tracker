"""実際の配信クリップを使ったclassify_bannerの回帰テスト。

fixtures/videos/ 配下の `{番号}_{win|lose}_{blue|red}_...` という命名規則の
動画について、classify_banner の判定結果をfind_confirmed_value でデバウン
スした上で、ファイル名が示す勝敗と一致することを確認する。

単発フレームでのclassify_bannerは、試合中のゴール演出などで数フレーム
(0.5秒未満)だけ誤検知することがあると分かっている。一方、本物の結果
バナーは1秒以上安定して表示され続ける。そのためデバウンス(一定時間
連続した判定のみを採用)と組み合わせることで、実際の配信映像に対しても
正しく勝敗を判定できることをここで検証する。
"""

import re
from pathlib import Path

import cv2
import pytest

from conftest import requires_video_fixtures
from nss_tracker.detection.banner import classify_banner
from nss_tracker.detection.motion import find_confirmed_value

# 命名規則: {番号}_{win|lose}_{blue|red}_{...任意...}.mp4
NAME_PATTERN = re.compile(r"^\d+_(win|lose)_(blue|red)_?")

# 実測(このテストファイル作成時点): 誤検知の最長継続は0.5秒未満、
# 本物のバナーは1.37秒以上継続する。余裕を持って1.0秒をデバウンス閾値とする
MIN_CONFIRM_SECONDS = 1.0


def _discover_result_videos(videos_dir: Path) -> list[tuple[Path, str]]:
    videos = []
    for path in sorted(videos_dir.glob("*.mp4")):
        match = NAME_PATTERN.match(path.name)
        if match:
            videos.append((path, match.group(1)))
    return videos


def _confirmed_banner_result(path: Path) -> str | None:
    cap = cv2.VideoCapture(str(path))
    try:
        fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
        min_run_length = round(MIN_CONFIRM_SECONDS * fps)

        def results():
            while True:
                ok, frame = cap.read()
                if not ok:
                    return
                yield classify_banner(frame)

        return find_confirmed_value(results(), min_run_length=min_run_length)
    finally:
        cap.release()


@requires_video_fixtures
def test_confirmed_banner_matches_filename_across_real_clips(videos_dir):
    videos = _discover_result_videos(videos_dir)
    assert videos, "命名規則に沿った動画がfixtures/videos/に見つからない"

    for path, expected in videos:
        confirmed = _confirmed_banner_result(path)
        assert confirmed == expected, f"{path.name}: 期待={expected} 実際={confirmed}"


# 実際の試合を1件も含まない、マッチング待機画面のみの動画(Issue #45)。
# 背景のスタジアム建造物の色がLOSE_HUE_RANGE等に偶然一致し、classify_bannerが
# 誤って"lose"を返すケースが実データで見つかった(14は2.5秒以上・3.4秒以上
# 持続する誤検知があった)。BANNER_HUE_STD_MAX導入後は、単発フレームだけでなく
# find_confirmed_valueでデバウンスした結果も一貫してNoneのままであることを検証する
MATCHING_ONLY_VIDEOS = [
    "14_matching_wait_1.mp4",
    "15_matching_wait_2.mp4",
    "16_matching_wait_3.mp4",
]


@requires_video_fixtures
@pytest.mark.parametrize("video_name", MATCHING_ONLY_VIDEOS)
def test_confirmed_banner_stays_none_on_matching_only_clips(videos_dir, video_name):
    video_path = videos_dir / video_name
    if not video_path.is_file():
        pytest.skip(f"{video_name} が見つからない")

    confirmed = _confirmed_banner_result(video_path)
    assert confirmed is None, f"{video_name}: 試合を含まないのに{confirmed}と確定してしまった"
