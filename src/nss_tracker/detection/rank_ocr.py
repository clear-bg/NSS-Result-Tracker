"""ランク数値のOCR読み取り。

CLAUDE.md記載の方針どおり、画面左下の固定領域をOCRで読み取る。
ROIはランク変動アニメーション中(バッジが拡大表示される)・通常表示の
両方をカバーできるよう余裕を持たせている(scripts/inspect_rank_ocr.py参照)。

バッジには「∞」アイコンも表示されており、数字のみ(allowlist)で読み取ると
"0"や"00"として誤認識される。∞アイコンは常にランク数値の上に表示されるため、
検出された数字のうち最も下側(bboxのy座標が最大)のものを実際のランク数値
として採用する。
"""

from functools import lru_cache
from typing import Optional

import numpy as np

# ランクバッジが写りうる範囲(通常表示・昇格/降格アニメ中の拡大表示の両方を含む)
# 解像度1920x1080のフレームを前提とする
RANK_ROI = (90, 600, 420, 930)


@lru_cache(maxsize=1)
def _get_reader():
    import easyocr

    return easyocr.Reader(["en"], gpu=False)


def read_rank(frame: np.ndarray, roi: tuple[int, int, int, int] = RANK_ROI) -> Optional[int]:
    """ランク数値バッジをOCRで読み取る。バッジが表示されていなければNoneを返す。"""
    x1, y1, x2, y2 = roi
    crop = frame[y1:y2, x1:x2]
    results = _get_reader().readtext(crop, allowlist="0123456789")
    if not results:
        return None

    def bbox_top(result: tuple) -> float:
        bbox, _text, _conf = result
        return min(point[1] for point in bbox)

    _bbox, text, _conf = max(results, key=bbox_top)
    if not text.isdigit():
        return None
    return int(text)
