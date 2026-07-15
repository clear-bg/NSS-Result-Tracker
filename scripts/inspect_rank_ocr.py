"""ランク数値バッジのOCR読み取りを検証するための診断スクリプト。

fixtures/screenshots のうちランクバッジが写る状態について、想定ROIを
EasyOCRにかけて認識結果を出力する。rank_ocr.py のROI・パース処理を
決めるための一次データ収集用(自動テストではない)。
"""

from pathlib import Path

import cv2
import easyocr

FIXTURES_DIR = Path(__file__).parent.parent / "fixtures" / "screenshots"

# ランクバッジが写りうる範囲(コンパクト表示・昇格/降格アニメ中の拡大表示の両方を含む余裕あり領域)
RANK_ROI = (90, 600, 420, 930)

TARGETS = [
    "43_result_win_without_rank_blue.png",
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
    reader = easyocr.Reader(["en"], gpu=False)
    x1, y1, x2, y2 = RANK_ROI
    for name in TARGETS:
        path = FIXTURES_DIR / name
        img = cv2.imread(str(path))
        if img is None:
            print(f"[skip] {name} not found")
            continue
        crop = img[y1:y2, x1:x2]
        results = reader.readtext(crop, allowlist="0123456789")
        print(f"--- {name} ---")
        for bbox, text, conf in results:
            print(f"  text={text!r} conf={conf:.2f} bbox={bbox}")
        if not results:
            print("  (no text detected)")


if __name__ == "__main__":
    main()
