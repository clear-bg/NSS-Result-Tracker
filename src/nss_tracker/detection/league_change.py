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

Issue #176対応(2026-07-31): 降格を独立した信号で検知できないか調査した結果、
検知可能と判明したため`is_demotion_label_candidate()`/`confirm_demotion_label_text()`を
追加した。降格時にランクバッジの上に乗る小さな「降格」ラベル(白背景の吹き出し
+黒文字+下向きの小さな三角ポインター、fixtures/screenshots/98参照)は、
Issue #73で断念したS/A帯バッジのOCR(信頼度0.00〜0.16、バッジが小さすぎる)とは
異なり、ラベル自体が十分な大きさ・高コントラストのため両方の手法で安定して
検知できることを確認した:

- 形状(色): ラベルは白背景(輝度200以上)の割合が高い矩形として画面上の
  固定位置に現れる。fixtures/screenshots/98・106(降格2件、別セッション)・
  fixtures/videos/40のframe 450で、DEMOTION_LABEL_ROI内の輝度200以上の
  画素数を実測したところ10589〜10890に収まり(3件とも同じ範囲に収束)、
  非該当のfixtures/screenshots全43枚(通常の勝敗結果画面・promotion演出・
  試合終了・マッチング等)はいずれも4456以下、またはpromotion演出等の
  全画面が明るい特殊画面で25701以上と、明確なギャップがあった
  (DEMOTION_LABEL_WHITE_COUNT_RANGE参照)
- OCR: 同じ3件をPaddleOCR(goal.pyの_get_name_readerを再利用)で読み取ったところ、
  いずれも信頼度0.99以上で「降格」と正しく読み取れた(EasyOCRでも信頼度
  0.75〜0.91で読み取れることを確認したが、プロジェクト内の日本語文字OCRは
  PaddleOCRに統一する方針のためこちらを採用)

ラベルの画面上の位置は、バッジ自体がコンパクト表示か拡大表示かに関わらず
一定だった(video 40の同一クリップ内で複数フレームを比較して確認済み)。
そのためgauge_ocr.pyのGAUGE_ROI_COMPACT/ENLARGEDのような表示サイズ別の
ROI分割は不要と判断した。

is_match_end_screen()/confirm_match_end_text()やis_goal_event()/
confirm_goal_text()と同じ2段構成(色/形状の軽量な候補判定→デバウンス確定時に
1回だけ重いOCRで確認)を踏襲している(呼び出し側はstate/match_state.py参照)。
"""

import cv2
import numpy as np

from nss_tracker.detection.goal import _get_name_reader
from nss_tracker.detection_config import get_detection_value

# config/detection.tomlの[league_change]で上書き可能
HUE_RANGE = get_detection_value("league_change", "HUE_RANGE", (95, 108))
SAT_RANGE = get_detection_value("league_change", "SAT_RANGE", (55, 80))
VAL_MIN = get_detection_value("league_change", "VAL_MIN", 180)

# Issue #176: 降格ラベル(「降格」の吹き出し)の画面上の固定位置。解像度
# 1920x1080のフレームを前提とする(モジュールdocstring参照)
DEMOTION_LABEL_ROI = get_detection_value("league_change", "DEMOTION_LABEL_ROI", (190, 530, 420, 680))
# 上記ROI内で輝度(グレースケール)がこの値を超える画素を「白」とみなす
DEMOTION_LABEL_BRIGHTNESS_THRESHOLD = get_detection_value("league_change", "DEMOTION_LABEL_BRIGHTNESS_THRESHOLD", 200)
# 実測(モジュールdocstring参照): 降格ラベル表示中は10589〜10890、非該当は
# 4456以下または25701以上。上下に余裕を持たせた範囲にしている
DEMOTION_LABEL_WHITE_COUNT_RANGE = get_detection_value("league_change", "DEMOTION_LABEL_WHITE_COUNT_RANGE", (8000, 20000))


def is_league_change_screen(frame: np.ndarray) -> bool:
    """リーグ**昇格**の演出オーバーレイが表示されているかを判定する。

    降格時はこの全画面オーバーレイ自体が発生しないため、常にFalseを返す
    (モジュールdocstring参照)。
    """
    hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
    h, s, v = hsv.reshape(-1, 3).mean(axis=0)
    return HUE_RANGE[0] <= h <= HUE_RANGE[1] and SAT_RANGE[0] <= s <= SAT_RANGE[1] and v >= VAL_MIN


def is_demotion_label_candidate(frame: np.ndarray, roi: tuple[int, int, int, int] = DEMOTION_LABEL_ROI) -> bool:
    """降格ラベル(「降格」の吹き出し)の候補を軽量な輝度判定で検知する(Issue #176)。

    単体では確定情報として扱わず、呼び出し側が一定時間連続したタイミングで
    confirm_demotion_label_text()を1回だけ呼んでOCRにより確認すること
    (モジュールdocstring参照)。
    """
    x1, y1, x2, y2 = roi
    gray = cv2.cvtColor(frame[y1:y2, x1:x2], cv2.COLOR_BGR2GRAY)
    white_count = int((gray > DEMOTION_LABEL_BRIGHTNESS_THRESHOLD).sum())
    return DEMOTION_LABEL_WHITE_COUNT_RANGE[0] <= white_count <= DEMOTION_LABEL_WHITE_COUNT_RANGE[1]


def confirm_demotion_label_text(frame: np.ndarray, roi: tuple[int, int, int, int] = DEMOTION_LABEL_ROI) -> bool:
    """ラベルの文字をOCR(PaddleOCR)で読み取り、「降格」かどうかを判定する(重い処理)。

    is_demotion_label_candidate()で候補と判定されたフレームに対して、呼び出し側が
    デバウンス確定時に1回だけ呼ぶことを想定している(モジュールdocstring参照)。
    """
    x1, y1, x2, y2 = roi
    crop = frame[y1:y2, x1:x2]
    results = _get_name_reader().predict(crop)
    texts: list[str] = []
    for result in results:
        texts.extend(result.get("rec_texts", []))
    return any("降格" in text for text in texts)
