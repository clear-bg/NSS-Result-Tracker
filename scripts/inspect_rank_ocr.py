"""ランク数値バッジのOCR読み取りを検証するための診断スクリプト。

fixtures/screenshots のうちランクバッジが写る状態について、コンパクト表示・
拡大表示それぞれの現行ROI(RANK_NUMBER_ROI_COMPACT/ENLARGED、Issue #143)を
EasyOCRにかけて認識結果を出力する。rank_ocr.py のROI・パース処理を
決めるための一次データ収集用(自動テストではない)。

コンパクト表示・拡大表示でバッジの実寸が異なるため、対応するfixtureを
それぞれ別グループとして出力する(inspect_gauge_fill.pyと同じ考え方)。
"""

from pathlib import Path

import cv2
import easyocr

from nss_tracker.detection.rank_ocr import RANK_NUMBER_ROI_COMPACT, RANK_NUMBER_ROI_ENLARGED

FIXTURES_DIR = Path(__file__).parent.parent / "fixtures" / "screenshots"

# Issue #148・#158: HDR無効化前fixture削除に伴い張り替え、その後84を追加収集
COMPACT_TARGETS = [
    "78_result_lose_with_rank_blue_hdr_off.png",
    "84_result_win_with_rank_blue_hdr_off.png",
]

# Issue #143: 77は当初コンパクト表示として扱われていたが、実際には拡大表示だった
# ことが判明し、こちらへ移動した(inspect_gauge_fill.pyと同じ経緯)
ENLARGED_TARGETS = [
    "77_result_win_with_rank_red_hdr_off.png",
    "85_result_win_with_rank_enlarged_blue_hdr_off.png",
]


def _print_group(reader: easyocr.Reader, label: str, names: list[str], roi: tuple[int, int, int, int]) -> None:
    x1, y1, x2, y2 = roi
    print(f"--- {label} ---")
    for name in names:
        path = FIXTURES_DIR / name
        img = cv2.imread(str(path))
        if img is None:
            print(f"[skip] {name} not found")
            continue
        crop = img[y1:y2, x1:x2]
        results = reader.readtext(crop, allowlist="0123456789")
        print(f"{name}:")
        for bbox, text, conf in results:
            print(f"  text={text!r} conf={conf:.2f} bbox={bbox}")
        if not results:
            print("  (no text detected)")


def main() -> None:
    reader = easyocr.Reader(["en"], gpu=False)
    _print_group(reader, "コンパクト表示", COMPACT_TARGETS, RANK_NUMBER_ROI_COMPACT)
    _print_group(reader, "拡大表示", ENLARGED_TARGETS, RANK_NUMBER_ROI_ENLARGED)


if __name__ == "__main__":
    main()
