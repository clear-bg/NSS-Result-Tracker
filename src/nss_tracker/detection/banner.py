"""結果バナー(勝ち/負け)の色判定。

CLAUDE.md記載の方針どおり、OCRではなく画面上部に出る斜めの結果バナーの
色で勝敗を判定する(形状判定は今後の精度向上時に追加検討)。

閾値は fixtures/screenshots の全状態画像の実測値をもとに決めている
(scripts/inspect_banner_colors.py で採取)。配信ごとの明るさ・コントラストの
違いを吸収できるよう、実測クラスタから余裕を持たせた範囲にしている。
実際の配信環境で誤検知/未検知が出た場合は、同スクリプトで再計測して
この範囲を調整すること。
"""

from typing import Literal, Optional

import cv2
import numpy as np

BannerResult = Optional[Literal["win", "lose"]]

# バナー帯のうち、テキストや選手モデルにかぶらない右上寄りの矩形領域 (x1, y1, x2, y2)
# 解像度1920x1080のフレームを前提とする
BANNER_ROI = (1300, 20, 1750, 100)

# 実測(scripts/inspect_banner_colors.py): 勝ち H≈80.4-80.6 / 負け H≈84.0-85.1
HUE_RANGE = (75, 90)
WIN_SAT_MIN = 100
WIN_VAL_MIN = 140
LOSE_SAT_RANGE = (40, 95)
LOSE_VAL_RANGE = (85, 145)


def classify_banner(frame: np.ndarray, roi: tuple[int, int, int, int] = BANNER_ROI) -> BannerResult:
    """勝敗結果バナーの色を判定する。バナーが写っていなければNoneを返す。"""
    x1, y1, x2, y2 = roi
    crop = frame[y1:y2, x1:x2]
    hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)
    h, s, v = hsv.reshape(-1, 3).mean(axis=0)

    if not (HUE_RANGE[0] <= h <= HUE_RANGE[1]):
        return None
    if s >= WIN_SAT_MIN and v >= WIN_VAL_MIN:
        return "win"
    if LOSE_SAT_RANGE[0] <= s <= LOSE_SAT_RANGE[1] and LOSE_VAL_RANGE[0] <= v <= LOSE_VAL_RANGE[1]:
        return "lose"
    return None
