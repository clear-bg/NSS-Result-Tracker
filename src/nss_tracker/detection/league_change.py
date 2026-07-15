"""リーグ昇格/降格の演出画面(全画面の半透明オーバーレイ)の検知。

CLAUDE.md記載のとおり、この演出はランクの帯(数値)が実際に変化した場合のみ
表示される特別イベント。通常のランク確定より低彩度・高輝度な半透明の白っぽい
オーバーレイが画面全体にかぶるため、フレーム全体の平均HSVで判定する。

閾値は fixtures/videos/01_win_blue_2-1.mp4 の昇格演出区間と、
fixtures/screenshots の非該当状態(ロビー・マッチング・試合中・結果バナー等)
を実測して決定した(オーバーレイ: H≈100-103, S≈66-70, V≈183-194、
非該当状態はいずれもHがもっと低いか、Sがもっと高い)。
"""

import cv2
import numpy as np

HUE_RANGE = (95, 108)
SAT_RANGE = (55, 80)
VAL_MIN = 180


def is_league_change_screen(frame: np.ndarray) -> bool:
    """リーグ昇格/降格の演出オーバーレイが表示されているかを判定する。"""
    hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
    h, s, v = hsv.reshape(-1, 3).mean(axis=0)
    return HUE_RANGE[0] <= h <= HUE_RANGE[1] and SAT_RANGE[0] <= s <= SAT_RANGE[1] and v >= VAL_MIN
