"""ランクゲージの塗りつぶし割合を調べるための診断スクリプト。

fixtures/screenshots のランクバッジが写る状態について、GAUGE_ROIの
塗りつぶし割合を出力する。rank_ocr.read_rank_gauge_fill() の閾値を
決めるための一次データ収集用(自動テストではない)。
"""

from pathlib import Path

import cv2

from nss_tracker.detection.rank_ocr import read_rank_gauge_fill

FIXTURES_DIR = Path(__file__).parent.parent / "fixtures" / "screenshots"

TARGETS = [
    "44_result_lose_with_rank_blue.png",
    "45_result_lose_in_rank_decrease_blue.png",
    "46_result_lose_after_rank_decrease_blue.png",
    "50_result_win_with_rank_red.png",
    "51_result_in_rank_increase_red.png",
    "52_result_after_rank_increase_red.png",
    "54_result_lose_with_rank_red.png",
    "55_result_lose_in_rank_decrease_red.png",
    "56_result_lose_after_rank_decrease_red.png",
]


def main() -> None:
    for name in TARGETS:
        path = FIXTURES_DIR / name
        img = cv2.imread(str(path))
        if img is None:
            print(f"[skip] {name} not found")
            continue
        fill = read_rank_gauge_fill(img)
        print(f"{name:55s} fill={fill:.2f}")


if __name__ == "__main__":
    main()
