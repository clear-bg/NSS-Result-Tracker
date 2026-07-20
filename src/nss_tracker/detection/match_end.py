"""「試合終了」バナーの検知(Issue #76)。

時間切れでスコアが決定した場合、または延長戦でどちらかがゴールを決めて
試合が終了した場合、画面中央やや上に「試合終了」という文字が乗った
横長の角丸ミントグリーン帯が表示される(fixtures/screenshots/65_match_end.png
参照)。これは常に試合の本当の終了時点でのみ表示されるため、
state.match_state側でこれを検知済みかどうかを結果バナーの確定デバウンスの
長さを決める補助信号として使う(Issue #67の対症療法である2秒デバウンスを、
「試合終了」を確認済みの場合だけ元の1秒に短縮する。詳細はstate/match_state.py
参照)。

このバナーは中央に文字が大きく表示され、帯の左右の余白が狭いため、
banner.pyのBANNER_ROIのように文字を避けた薄い帯を1箇所だけ取るやり方が
使えない(実測、fixtures/videos/00-03: 帯の内側でも文字がほぼ帯の横幅
いっぱいに広がっている箇所がある)。そのため文字の隙間にあたる2箇所
(左端寄りの余白 MATCH_END_LEFT_ROI、右端寄りの余白 MATCH_END_RIGHT_ROI)を
個別に測り、両方が閾値を満たした場合のみ「試合終了」と判定する。

さらに、色味が非常によく似た「延長戦」バナー(60_start_overtime.png、
Issue #67のfixture21番で実際に誤検知した)との混同に注意が必要。
「試合終了」(4文字)は「延長戦」(3文字)より文字列が長く帯も横に広いため、
MATCH_END_RIGHT_ROIの位置は「延長戦」の帯の外(背景)に出る。実測
(fixtures/videos/21_goal_event_false_positive_win_blue_4-3.mp4の延長戦区間):
「試合終了」ではH≈84の帯色、「延長戦」では同じ位置がH≈104(背景の空の色)と
明確に分離できるため、2点ROIのAND条件により「延長戦」を除外できる。

ただし色・形状だけでは区別しきれないケースが実データで見つかった。ゴールの
たびに表示される「キックオフ」バナー(5文字)は「試合終了」(4文字)と同等以上に
帯が広く、上記の2点ROIチェックをすり抜けて誤って一致してしまう
(fixtures/videos/21_goal_event_false_positive_win_blue_4-3.mp4のframe 465で
実際に発生した誤検知)。「キックオフ」は1試合中に何度も表示されるイベントのため、
もしこれを「試合終了」と誤認識すると、試合の早い段階でstate.match_state側の
高速デバウンス経路が誤って有効になり、Issue #67で対処した誤検知のリスクを
実質的に再度持ち込んでしまう。これは看過できないため、色チェックだけを
`is_match_end_screen()`とし、それが一定時間連続した場合(state.match_state側の
デバウンス確定時)に限って`confirm_match_end_text()`でOCRにより文字そのものを
読み、「試合終了」と一致した場合のみ確定として扱う2段構成にした(CLAUDE.mdの
サンプリング戦略のとおり、重いOCRは毎フレームではなく確定時に1回だけ呼ぶ)。
実測(PaddleOCR): 「試合終了」「キックオフ」いずれも正しく読み分けられることを
確認済み。

閾値は fixtures/screenshots/65_match_end.png と fixtures/videos/00,01,02,03
(いずれも実際に「試合終了」が表示される区間)、fixtures/videos/20
(引き分け・延長戦のケース)を実測して決定した。
"""

import cv2
import numpy as np

from nss_tracker.detection.goal import _get_name_reader
from nss_tracker.detection_config import get_detection_value

# 「試合終了」の帯全体(文字を含む)を覆う領域。OCRでの文字読み取り専用に使う
# (色判定のMATCH_END_LEFT_ROI/MATCH_END_RIGHT_ROIとは別)
MATCH_END_TEXT_ROI = get_detection_value("match_end", "MATCH_END_TEXT_ROI", (600, 385, 1330, 480))

# 「試合終了」の帯のうち、文字にかぶらない左寄りの余白 (x1, y1, x2, y2)
# 解像度1920x1080のフレームを前提とする
# (config/detection.tomlの[match_end]で上書き可能。以下同様)
MATCH_END_LEFT_ROI = get_detection_value("match_end", "MATCH_END_LEFT_ROI", (630, 405, 750, 465))

# 「試合終了」の帯のうち、右端寄りの余白。「延長戦」(文字数が少なく帯が狭い)
# との区別に使う(モジュールdocstring参照)
MATCH_END_RIGHT_ROI = get_detection_value("match_end", "MATCH_END_RIGHT_ROI", (1200, 415, 1270, 450))

# 実測(fixtures/screenshots/65_match_end.png, fixtures/videos/00,01,03,20):
# 帯の色はH80-85程度、余白部分はS125-143・V195-209程度で明確に高彩度・高輝度
MATCH_END_HUE_RANGE = get_detection_value("match_end", "MATCH_END_HUE_RANGE", (75, 92))
MATCH_END_SAT_MIN = get_detection_value("match_end", "MATCH_END_SAT_MIN", 90)
MATCH_END_VAL_MIN = get_detection_value("match_end", "MATCH_END_VAL_MIN", 150)
MATCH_END_HUE_STD_MAX = get_detection_value("match_end", "MATCH_END_HUE_STD_MAX", 12.0)


def _matches_band_color(frame: np.ndarray, roi: tuple[int, int, int, int]) -> bool:
    x1, y1, x2, y2 = roi
    hsv = cv2.cvtColor(frame[y1:y2, x1:x2], cv2.COLOR_BGR2HSV).reshape(-1, 3)
    h, s, v = hsv.mean(axis=0)
    hue_std = hsv[:, 0].std()
    return (
        MATCH_END_HUE_RANGE[0] <= h <= MATCH_END_HUE_RANGE[1]
        and s >= MATCH_END_SAT_MIN
        and v >= MATCH_END_VAL_MIN
        and hue_std <= MATCH_END_HUE_STD_MAX
    )


def is_match_end_screen(frame: np.ndarray) -> bool:
    """「試合終了」候補の帯が表示されているかを判定する(色ベース、軽量)。

    左右2箇所のROIがいずれも帯の色に一致した場合のみTrueを返す(「延長戦」バナーは
    この時点で除外できるが、「キックオフ」バナーは除外できない。モジュール
    docstring参照)。呼び出し側は、これが一定時間連続したタイミングで
    confirm_match_end_text()を1回だけ呼び、実際に「試合終了」の文字かどうかを
    OCRで確認すること(is_match_end_screen単体では確定情報として扱わない)。
    """
    return _matches_band_color(frame, MATCH_END_LEFT_ROI) and _matches_band_color(frame, MATCH_END_RIGHT_ROI)


def confirm_match_end_text(frame: np.ndarray, roi: tuple[int, int, int, int] = MATCH_END_TEXT_ROI) -> bool:
    """帯の文字をOCRで読み取り、「試合終了」かどうかを判定する(重い処理)。

    is_match_end_screen()で候補と判定されたフレームに対して、呼び出し側が
    デバウンス確定時に1回だけ呼ぶことを想定している(モジュールdocstring参照)。
    「延長戦」「キックオフ」等、色では区別できない他のバナーはここで除外される。

    完全一致ではなく部分一致で判定する。実測(fixtures/videos/10_RankDown_red.mp4)で
    「試合終了」の前に余分な文字(タイマー表示等の誤読とみられる、例:「一試合終了」)が
    混ざって認識されることがあったため(goal.pyの「ゴール」→「コール」誤読対策と
    同じ考え方)。
    """
    x1, y1, x2, y2 = roi
    crop = frame[y1:y2, x1:x2]
    results = _get_name_reader().predict(crop)
    texts: list[str] = []
    for result in results:
        texts.extend(result.get("rec_texts", []))
    return any("試合終了" in text for text in texts)
