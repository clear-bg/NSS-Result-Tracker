"""ランク数値のOCR読み取り、およびゲージ(帯の溜まり具合)の読み取り。

CLAUDE.md記載の方針どおり、画面左下の固定領域をOCRで読み取る。
ROIはランク変動アニメーション中(バッジが拡大表示される)・通常表示の
両方をカバーできるよう余裕を持たせている(scripts/inspect_rank_ocr.py参照)。

バッジには「∞」アイコンも表示されており、数字のみ(allowlist)で読み取ると
"0"や"00"として誤認識される。∞アイコンは常にランク数値の上に表示されるため、
検出された数字のうち最も下側(bboxのy座標が最大)のものを実際のランク数値
として採用する。

ランク数値バッジの下には、次の帯までの溜まり具合を示す横長のゲージがある。
このゲージの塗りつぶし割合(0.0〜1.0)を読み取り、整数の帯番号と組み合わせる
ことで、より細かい小数のランク値を得られる(read_precise_rank参照)。
ゲージの位置はコンパクト表示・拡大表示のどちらでもほぼ同じ(ランク数値本体の
位置とは異なり、バッジの下端に固定されているため)。ただし、ランク変動
アニメーション中(帯が動いている最中)はゲージの見た目が遷移演出用の
グラデーションになり塗りつぶし割合として意味を持たないため、read_rankと
同様に「安定している瞬間」にのみ呼び出すこと(state.match_state参照)。
閾値はscripts/inspect_gauge_fill.pyでfixtures/screenshotsの
結果バナー画面(勝ち/負け双方、通常表示・昇格降格後の安定表示)を実測して
決定した。
"""

from functools import lru_cache
from typing import Optional

import cv2
import numpy as np

# ランクバッジが写りうる範囲(通常表示・昇格/降格アニメ中の拡大表示の両方を含む)
# 解像度1920x1080のフレームを前提とする
RANK_ROI = (90, 600, 420, 930)

# ランク数値バッジ下部のゲージ(横長の帯)の領域。丸みを帯びた両端を避けた
# 内側の水平帯を対象にする
GAUGE_ROI = (135, 975, 335, 990)

# 実測(scripts/inspect_gauge_fill.py): 塗りつぶし部分はV(明度)が200前後、
# 未塗りつぶし部分は80〜105程度で明確に分離できる
GAUGE_FILLED_VALUE_THRESHOLD = 150


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


def read_rank_gauge_fill(frame: np.ndarray, roi: tuple[int, int, int, int] = GAUGE_ROI) -> Optional[float]:
    """ランクゲージの塗りつぶし割合(0.0〜1.0)を読み取る。

    ゲージが表示されていない(バッジ自体が無い)場合の判定はしていないため、
    呼び出し側でread_rank()等がNoneでないことを確認してから呼ぶこと。
    """
    x1, y1, x2, y2 = roi
    crop = frame[y1:y2, x1:x2]
    hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)
    value_by_column = hsv[:, :, 2].mean(axis=0)
    filled = value_by_column > GAUGE_FILLED_VALUE_THRESHOLD
    return float(filled.mean())


def read_precise_rank(frame: np.ndarray) -> Optional[tuple[int, float]]:
    """整数の帯番号と、それにゲージの溜まり具合を加えた小数のランク値を読み取る。

    戻り値は (帯番号, 小数のランク値) のタプル。帯番号が読めなければNoneを返す。
    ゲージの溜まり具合が1.0(満タン)になることがあり、その場合
    tier + fill を単純に計算すると次の帯の数値と一致してしまい、後から
    小数値だけを見て帯番号を復元しようとすると誤る。そのため帯番号は
    別途そのまま返し、呼び出し側(league_changed判定等)はこちらを使うこと。
    ゲージが読めない場合は小数部を0として帯番号のみのランク値を返す
    (取れる情報だけ取っておき、後で表示時に丸める運用のため)。
    """
    tier = read_rank(frame)
    if tier is None:
        return None
    fill = read_rank_gauge_fill(frame)
    precise = tier + (fill if fill is not None else 0.0)
    return tier, precise
