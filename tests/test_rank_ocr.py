import cv2
import pytest

from conftest import requires_fixtures
from nss_tracker.detection.rank_ocr import read_rank

# 同一プレイ記録からのfixtureのため全て同じランク値(38)になっているが、
# バッジの表示有無・通常表示/昇格・降格アニメ中の拡大表示それぞれで
# 正しく読み取れる(またはバッジ非表示時にNoneを返す)ことを確認する
EXPECTED = {
    "43_result_win_without_rank_blue.png": None,
    "44_result_lose_with_rank_blue.png": 38,
    "45_result_lose_in_rank_decrease_blue.png": 38,
    "46_result_lose_after_rank_decrease_blue.png": 38,
    "50_result_win_with_rank_red.png": 38,
    "51_result_in_rank_increase_red.png": 38,
    "52_result_after_rank_increase_red.png": 38,
    "54_result_lose_with_rank_red.png": 38,
    "55_result_lose_in_rank_decrease_red.png": 38,
    "56_result_lose_after_rank_decrease_red.png": 38,
}


@pytest.mark.slow
@requires_fixtures
@pytest.mark.parametrize("filename, expected", sorted(EXPECTED.items()))
def test_read_rank(fixtures_dir, filename, expected):
    frame = cv2.imread(str(fixtures_dir / filename))
    assert frame is not None, f"failed to load {filename}"
    assert read_rank(frame) == expected
