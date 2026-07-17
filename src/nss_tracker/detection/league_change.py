"""リーグ**昇格**演出画面(全画面の半透明オーバーレイ)の検知。

CLAUDE.md記載のとおり、この演出はランクの帯(数値)が実際に変化した場合のみ
表示される特別イベント。通常のランク確定より低彩度・高輝度な半透明の白っぽい
オーバーレイが画面全体にかぶるため、フレーム全体の平均HSVで判定する。

**この関数が検知するのは昇格側の全画面オーバーレイのみ**。降格時は全画面
オーバーレイが表示されず、ランクバッジの上に小さな「降格」ラベルが乗るだけで
バッジ自体は隠れない(実データで確認済み、fixtures/videos/10_RankDown_red.mp4
参照)。そのため降格の演出中もis_league_change_screen()は常にFalseを返すのが
正しい挙動であり、これはバグではない。降格の検知はこの関数に頼らず、
state.match_state側のバナー消灯時フォールバック確定・rank_recheck機構で
別途対応している(state/match_state.py参照)。

閾値は fixtures/videos/01_win_blue_2-1.mp4 の昇格演出区間と、
fixtures/screenshots の非該当状態(ロビー・マッチング・試合中・結果バナー等)
を実測して決定した(オーバーレイ: H≈100-103, S≈66-70, V≈183-194、
非該当状態はいずれもHがもっと低いか、Sがもっと高い)。
"""

import cv2
import numpy as np

from nss_tracker.detection_config import get_detection_value

# config/detection.tomlの[league_change]で上書き可能
HUE_RANGE = get_detection_value("league_change", "HUE_RANGE", (95, 108))
SAT_RANGE = get_detection_value("league_change", "SAT_RANGE", (55, 80))
VAL_MIN = get_detection_value("league_change", "VAL_MIN", 180)


def is_league_change_screen(frame: np.ndarray) -> bool:
    """リーグ**昇格**の演出オーバーレイが表示されているかを判定する。

    降格時はこの全画面オーバーレイ自体が発生しないため、常にFalseを返す
    (モジュールdocstring参照)。
    """
    hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
    h, s, v = hsv.reshape(-1, 3).mean(axis=0)
    return HUE_RANGE[0] <= h <= HUE_RANGE[1] and SAT_RANGE[0] <= s <= SAT_RANGE[1] and v >= VAL_MIN
