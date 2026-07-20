"""マッチング完了(VS画面)の色判定。

CLAUDE.md記載の方針どおり、OCRではなく画面中央に一瞬表示される「VS」ロゴの
色で判定する(banner.py・league_change.pyと同様の軽量な色ベース判定)。

閾値は fixtures/screenshots の該当4画像(`11_matching_with_rank_blue`,
`12_matching_without_rank_blue`, `14_matching_with_rank_red`,
`15_matching_without_rank_red`)に加え、S/A帯バッジのOCR確認用に集めた
`70_rank_tier_s.png` / `71_rank_tier_a.png`(いずれも偶然VS画面を捉えている)
の計6枚を実測して決定した。6枚とも同じミント色のロゴ+白縁取りの組み合わせで
H62.1-74.9 / S63.0-66.2 / V192.6-208.1に収まり、値のばらつきは小さい。

ROIはロゴの文字部分だけを狙った小さな矩形にしている(周囲の芝生・空を含む
広いROIだと、対戦相手の背景色の違い(晴天/曇天等)でHSVの平均が引きずられ
判定がぶれるため)。fixtures/screenshotsの全44枚・fixtures/videosの全動画
(のべ約3万フレーム)を実測して他の画面状態との重複が無いことを確認済み。

なお試合中の稀なフレームで、プレイヤー頭上のミント色アイコン(スキル発動
などの演出)がROIにちょうど重なり単発フレームだけ誤検知することを確認した
(fixtures/videos/12_win_red_vs_screen_to_result.mp4のframe 4397)。この
誤検知は1フレーム(30fps換算で約33ms)しか継続せず、本物のVS画面は
150フレーム(5秒)以上安定して表示され続けるため、banner.pyのバナー判定
同様、呼び出し側でmotion.find_confirmed_valueによるデバウンス
(数百ms〜1秒程度の連続を要求)と組み合わせて使うことを前提とする。
"""

import cv2
import numpy as np

from nss_tracker.detection_config import get_detection_value

# 画面中央に表示される「VS」ロゴの文字部分のみを狙った矩形 (x1, y1, x2, y2)。
# 解像度1920x1080のフレームを前提とする
# (config/detection.tomlの[matchmaking]で上書き可能。以下同様)
VS_ROI = get_detection_value("matchmaking", "VS_ROI", (880, 495, 1050, 600))

# 実測(fixtures/screenshots内のVS画面6枚): H62.1-74.9 / S63.0-66.2 / V192.6-208.1
VS_HUE_RANGE = get_detection_value("matchmaking", "VS_HUE_RANGE", (62, 77))
VS_SAT_RANGE = get_detection_value("matchmaking", "VS_SAT_RANGE", (60, 70))
VS_VAL_MIN = get_detection_value("matchmaking", "VS_VAL_MIN", 180)


def read_vs_roi_hsv(frame: np.ndarray, roi: tuple[int, int, int, int] = VS_ROI) -> tuple[float, float, float]:
    """VS_ROI内の平均HSV値をそのまま返す(診断用)。

    Issue #68: 実プレイでVS画面検知が繰り返し失敗しており(2026-07-19・
    2026-07-20の実測とも0/5・0/4)、原因が未特定のまま。既存fixtureでの
    検証では色閾値自体に問題は見つからなかったため、実際のキャプチャ
    パイプラインで何が起きているかを次回セッションでDEBUGログから
    直接確認できるよう、is_vs_screen()の判定に使うHSV平均値を単体で
    呼び出せるようにしている(state.match_stateの_check_for_vs_screen参照)。
    """
    x1, y1, x2, y2 = roi
    crop = frame[y1:y2, x1:x2]
    hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV).reshape(-1, 3)
    h, s, v = hsv.mean(axis=0)
    return float(h), float(s), float(v)


def is_vs_screen(frame: np.ndarray, roi: tuple[int, int, int, int] = VS_ROI) -> bool:
    """マッチング完了(VS画面)の「VS」ロゴが表示されているかを判定する。

    単発フレームでは試合中の演出アイコン等で稀に誤検知しうる(モジュール
    docstring参照)。呼び出し側でmotion.find_confirmed_value等によるデバウンスと
    組み合わせて使うことを前提とする。
    """
    h, s, v = read_vs_roi_hsv(frame, roi)
    return VS_HUE_RANGE[0] <= h <= VS_HUE_RANGE[1] and VS_SAT_RANGE[0] <= s <= VS_SAT_RANGE[1] and v >= VS_VAL_MIN
