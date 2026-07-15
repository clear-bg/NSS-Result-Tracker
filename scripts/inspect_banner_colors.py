"""結果バナー(勝ち/負け)の色域を調べるための診断スクリプト。

fixtures/screenshots 全体について、バナー帯が通ると想定される右上寄りの
矩形領域(テキストや選手モデルにかぶらない位置)の平均BGR/HSVを出力する。
banner.py の色閾値を決めるための一次データ収集用(自動テストではない)。
"""

from pathlib import Path

import cv2
import numpy as np

FIXTURES_DIR = Path(__file__).parent.parent / "fixtures" / "screenshots"

# バナー帯のうち、テキストや選手モデルにかぶらない右上寄りの矩形 (x1, y1, x2, y2)
ROI = (1300, 20, 1750, 100)


def region_mean_hsv(img: np.ndarray, roi: tuple[int, int, int, int]) -> tuple[float, float, float]:
    x1, y1, x2, y2 = roi
    crop = img[y1:y2, x1:x2]
    hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)
    h, s, v = hsv.reshape(-1, 3).mean(axis=0)
    return float(h), float(s), float(v)


def main() -> None:
    for path in sorted(FIXTURES_DIR.glob("*.png")):
        img = cv2.imread(str(path))
        if img is None:
            print(f"[skip] {path.name} failed to load")
            continue
        h, s, v = region_mean_hsv(img, ROI)
        print(f"{path.name:55s} H={h:6.1f} S={s:6.1f} V={v:6.1f}")


if __name__ == "__main__":
    main()
