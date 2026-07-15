"""結果バナー(勝ち/負け)の色判定。

CLAUDE.md記載の方針どおり、OCRではなく画面上部に出る斜めの結果バナーの
色で勝敗を判定する(形状判定は今後の精度向上時に追加検討)。

閾値は fixtures/screenshots の全状態画像に加え、実際の配信からのクリップ
(fixtures/videos/00〜03、scripts/inspect_banner_colors.py と同様の手法で採取)
を実測して決めている。同じOBS経由のキャプチャでも配信間でバナーの色味・
帯の太さ/角度に無視できない差があることが分かったため(勝ちはH80〜83で
ほぼ一致するが、負けはH84〜101まで幅がある)、ROIはバナー帯が細くなっても
確実に帯の内側に収まるよう画面最上部寄りの薄い帯にし、閾値も両者の実測
クラスタを包含する範囲にしている。

この判定は単体では「試合中のゴール演出」等でも稀に誤検知しうることを
確認済み(誤検知は最大でも0.5秒未満しか継続しない一方、本物のバナーは
1秒以上継続する)。呼び出し側で複数フレームにわたり同じ判定が一定時間
連続した場合のみ採用する運用を想定している(motion.find_confirmed_value参照)。
"""

from typing import Literal, Optional

import cv2
import numpy as np

BannerResult = Optional[Literal["win", "lose"]]

# バナー帯のうち、テキストや選手モデルにかぶらない右上寄りの領域 (x1, y1, x2, y2)
# 帯の太さ・角度は配信によって差があるため、画面最上部寄りの薄い帯にして
# 背景色の混入を避けている。解像度1920x1080のフレームを前提とする
BANNER_ROI = (1300, 5, 1750, 35)

# 実測(scripts/inspect_banner_colors.py, fixtures/screenshots + fixtures/videos/00-03):
# 勝ち: H80.7-83.2 / 負け: H89.0-99.0(配信間の差が大きい)
WIN_HUE_RANGE = (77, 86)
WIN_SAT_MIN = 120
WIN_VAL_MIN = 165
LOSE_HUE_RANGE = (87, 103)
LOSE_SAT_RANGE = (35, 65)
LOSE_VAL_RANGE = (65, 130)


def classify_banner(frame: np.ndarray, roi: tuple[int, int, int, int] = BANNER_ROI) -> BannerResult:
    """勝敗結果バナーの色を判定する。バナーが写っていなければNoneを返す。"""
    x1, y1, x2, y2 = roi
    crop = frame[y1:y2, x1:x2]
    hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)
    h, s, v = hsv.reshape(-1, 3).mean(axis=0)

    if WIN_HUE_RANGE[0] <= h <= WIN_HUE_RANGE[1] and s >= WIN_SAT_MIN and v >= WIN_VAL_MIN:
        return "win"
    if (
        LOSE_HUE_RANGE[0] <= h <= LOSE_HUE_RANGE[1]
        and LOSE_SAT_RANGE[0] <= s <= LOSE_SAT_RANGE[1]
        and LOSE_VAL_RANGE[0] <= v <= LOSE_VAL_RANGE[1]
    ):
        return "lose"
    return None
