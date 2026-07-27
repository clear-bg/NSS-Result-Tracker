"""ゴール演出(得点・アシスト)の検知。

「ゴール!」の斜めバナーはチームカラーに依存する(banner.pyの勝敗バナーとは
異なり、青チーム得点時は青系、赤チーム得点時はピンク系)。閾値は
scripts/inspect_goal_colors.py でfixtures/screenshotsの該当・非該当状態を
実測して決定した。

得点者・アシスト名はOCRで読み取る。rank_ocr.pyで使っているEasyOCRは
このゲーム特有の縁取り太字フォントに対して信頼度0.00〜0.16程度でまともに
読めないことを確認済み。PaddleOCR(lang="japan")に切り替えたところ、
同じ画像でほぼ全て信頼度0.90以上で正しく読み取れた。そのため名前OCRは
PaddleOCRを使う(rank_ocr.pyの数字読み取りは既存どおりEasyOCRのまま)。

名前パネルは画面下部に上から「ゴール」ラベル→得点者名→「アシスト」ラベル→
アシスト者名、という4行の固定グリッドで構成される(Issue #141)。以前は
4行全体を1つの広いROI(NAME_PANEL_ROI)でまとめてOCRし、検出された行の
並び順(「アシスト」という文字列が出てくるまでは得点者ブロック、以降は
アシストブロック)から役割を推測していたが、行ごとに個別のROIへ分割し、
各ラベル行が実際に何と書かれているかを読んで役割を判定する方式に変更した
(GOAL_LABEL_ROI/ASSIST_LABEL_ROI参照)。

アシスト無しの単独ゴールの場合、「ゴール」ラベル+得点者名の組がアシスト側の
本来の位置(3行目・4行目)にそのままずれて表示される(2行目までに詰まる
のではなく、本来アシスト用の位置を使う)。この場合ASSIST_LABEL_ROIには
「アシスト」ではなく「ゴール」が表示される。read_scorer_name()は
GOAL_LABEL_ROI・ASSIST_LABEL_ROIの両方を確認し、実際にどちらに「ゴール」が
読めたかで得点者名の位置を判定するため、位置がずれても正しく対応できる。
ただしこの単独ゴール(アシスト無し・オウンゴールでもない)のケース自体は、
現時点でHDR無効化後の参照fixtureが無く未検証(Issue #153で収集予定)。

オウンゴールは上記4行のグリッドとは別に「オウンゴール」という単独ラベルのみが
表示され、得点者名は表示されない(実データで確認済み、
fixtures/screenshots/75_goal_blue_owngoal_hdr_off.png参照)。そのため
read_scorer_name()・read_assist_name()はオウンゴールの場合常にNoneを返す。
"""

from functools import lru_cache
from typing import Optional

import cv2
import numpy as np

from nss_tracker.detection_config import get_detection_value

# 「ゴール!」バナーの完全に単色の領域を左右2箇所実測した(Issue #141)。
# 従来の単一ROI(100, 280, 400, 350)はバナーの斜め境界にわずかにかかっており、
# フレームによっては背景色(芝生等)が混ざった状態で色閾値が調整されていた
# ことが判明したため、境界・文字・キャラクターを避けた領域に差し替えた。
# 左右をまとめて1つの大きなサンプルとして平均を取る(is_goal_event()参照)。
# 右側は文字・キャラクター・HUD要素に一切かからず完全に単色。左側も境界は
# 避けているが、フレームによってはプレイヤーキャラクターが横切り一時的に
# 単色でなくなる箇所がある(視点・タイミングによる変動)ため、これを見込んだ
# 上でのマージン設定で進める方針(ユーザーとの相談で決定)
BANNER_ROI_LEFT = get_detection_value("goal", "BANNER_ROI_LEFT", (100, 305, 650, 415))
BANNER_ROI_RIGHT = get_detection_value("goal", "BANNER_ROI_RIGHT", (1300, 305, 1850, 415))

# 実測(fixtures/screenshots/75_goal_blue_owngoal_hdr_off.png、BANNER_ROI_LEFT/RIGHT
# 左右合算): 青チーム得点 H≈96-105(平均98.8)。既存のBLUE_HUE_RANGEに収まって
# いるため変更不要
BLUE_HUE_RANGE = get_detection_value("goal", "BLUE_HUE_RANGE", (83, 100))
# 実測(fixtures/screenshots/74_goal_with_assist_red_hdr_off.png、左右合算):
# 赤チーム得点 H≈158-166(平均161.1)。従来のRED_HUE_RANGE=(130, 155)から
# 外れていたため再較正した。HDR無効化後の赤チームgoal fixtureが現時点で
# この1件のみのため単一サンプルでのマージン設定(feedback:
# 色閾値は範囲+マージンで、の方針どおり複数fixtureが増え次第再確認すること)
RED_HUE_RANGE = get_detection_value("goal", "RED_HUE_RANGE", (150, 172))
SAT_MIN = get_detection_value("goal", "SAT_MIN", 100)
VAL_MIN = get_detection_value("goal", "VAL_MIN", 190)

# 得点者名パネルの4行グリッド(Issue #141、実測)
GOAL_LABEL_ROI = get_detection_value("goal", "GOAL_LABEL_ROI", (915, 751, 1009, 787))
SCORER_NAME_ROI = get_detection_value("goal", "SCORER_NAME_ROI", (842, 835, 1145, 878))
ASSIST_LABEL_ROI = get_detection_value("goal", "ASSIST_LABEL_ROI", (900, 900, 1020, 938))
ASSIST_NAME_ROI = get_detection_value("goal", "ASSIST_NAME_ROI", (842, 986, 1145, 1028))
# オウンゴールは上記4行とは別の単独ラベル(実測)
OWN_GOAL_LABEL_ROI = get_detection_value("goal", "OWN_GOAL_LABEL_ROI", (845, 957, 1073, 1004))

# 各ラベルROIは該当のラベル文字以外が写り込まない領域まで絞ってあるため
# (Issue #141)、既知の誤読パターンだけを許容する厳密一致で十分。
# OCRが「ゴール」を「コール」等に誤読することがあるため、既知のバリエーションは
# 許容する(_ASSIST_LABEL_VARIANTS/_OWN_GOAL_LABEL_VARIANTSは今のところ既知の
# 誤読例が無いため厳密一致のみ。新たな誤読パターンが判明次第ここに追加する)
_GOAL_LABEL_VARIANTS = {"ゴール", "コール"}
_ASSIST_LABEL_VARIANTS = {"アシスト"}
_OWN_GOAL_LABEL_VARIANTS = {"オウンゴール"}


def is_goal_event(
    frame: np.ndarray,
    roi_left: tuple[int, int, int, int] = BANNER_ROI_LEFT,
    roi_right: tuple[int, int, int, int] = BANNER_ROI_RIGHT,
) -> bool:
    """「ゴール!」バナーが表示されているかを判定する(色ベース、チームカラー問わず)。

    左右2箇所(バナーの斜め境界・文字・キャラクターを避けた完全に単色の領域)を
    まとめて1つのサンプルとして平均を取る(Issue #141)。
    """
    x1, y1, x2, y2 = roi_left
    crop_left = frame[y1:y2, x1:x2]
    x1, y1, x2, y2 = roi_right
    crop_right = frame[y1:y2, x1:x2]
    combined = np.concatenate([crop_left.reshape(-1, 3), crop_right.reshape(-1, 3)], axis=0)
    hsv = cv2.cvtColor(combined.reshape(1, -1, 3), cv2.COLOR_BGR2HSV).reshape(-1, 3)
    h, s, v = hsv.mean(axis=0)

    if s < SAT_MIN or v < VAL_MIN:
        return False
    return (BLUE_HUE_RANGE[0] <= h <= BLUE_HUE_RANGE[1]) or (RED_HUE_RANGE[0] <= h <= RED_HUE_RANGE[1])


@lru_cache(maxsize=1)
def _get_name_reader():
    from paddleocr import PaddleOCR

    return PaddleOCR(
        lang="japan",
        use_doc_orientation_classify=False,
        use_doc_unwarping=False,
        use_textline_orientation=False,
        enable_mkldnn=False,
    )


def _read_panel_lines(frame: np.ndarray, roi: tuple[int, int, int, int]) -> list[tuple[float, str, float]]:
    x1, y1, x2, y2 = roi
    crop = frame[y1:y2, x1:x2]
    results = _get_name_reader().predict(crop)

    lines: list[tuple[float, str, float]] = []
    for result in results:
        texts = result.get("rec_texts", [])
        scores = result.get("rec_scores", [])
        boxes = result.get("rec_boxes", [])
        for text, score, box in zip(texts, scores, boxes):
            lines.append((float(box[1]), text, float(score)))
    lines.sort(key=lambda line: line[0])
    return lines


def _label_matches(frame: np.ndarray, roi: tuple[int, int, int, int], variants: set[str]) -> bool:
    """roiの領域に、variantsのいずれかに一致する文字列が読み取れるかを判定する。"""
    return any(text in variants for _y, text, _score in _read_panel_lines(frame, roi))


def _read_name(frame: np.ndarray, roi: tuple[int, int, int, int]) -> Optional[tuple[str, float]]:
    """roiの領域からプレイヤー名を読み取る。

    プレイヤー名は任意の文字列のため、ラベルのような既知バリエーションでの
    吸収はせず、OCR結果をそのまま返す(複数行検出された場合は最後の行を採用)。
    """
    lines = _read_panel_lines(frame, roi)
    return (lines[-1][1], lines[-1][2]) if lines else None


def read_scorer_name(frame: np.ndarray) -> Optional[tuple[str, float]]:
    """得点者名をOCRで読み取る。

    パネルが表示されていない場合、またはオウンゴールで得点者名自体が
    表示されない場合はNoneを返す。戻り値は(名前, OCRの信頼度スコア)のタプル
    (Issue #71: 誤読診断のため信頼度も返す)。
    """
    if _label_matches(frame, GOAL_LABEL_ROI, _GOAL_LABEL_VARIANTS):
        return _read_name(frame, SCORER_NAME_ROI)
    if _label_matches(frame, ASSIST_LABEL_ROI, _GOAL_LABEL_VARIANTS):
        # 単独ゴール(アシスト無し)で「ゴール」行がアシスト側の位置にずれる
        # ケース(モジュールdocstring参照、未検証)
        return _read_name(frame, ASSIST_NAME_ROI)
    return None


def read_assist_name(frame: np.ndarray) -> Optional[tuple[str, float]]:
    """アシスト者名をOCRで読み取る。アシストが無い場合はNoneを返す。

    戻り値は(名前, OCRの信頼度スコア)のタプル(Issue #71: 誤読診断のため信頼度も返す)。
    """
    if _label_matches(frame, ASSIST_LABEL_ROI, _ASSIST_LABEL_VARIANTS):
        return _read_name(frame, ASSIST_NAME_ROI)
    return None


def is_own_goal_event(frame: np.ndarray, roi: tuple[int, int, int, int] = OWN_GOAL_LABEL_ROI) -> bool:
    """「オウンゴール」ラベルが表示されているかを判定する(Issue #141)。

    オウンゴールは得点者名が表示されないため、read_scorer_name()・
    read_assist_name()はどちらもNoneを返す(それ単独で正しく空扱いになる)。
    この関数は「ゴール自体は検知したが名前が無い」ことの理由がオウンゴールで
    あることを区別したい呼び出し元向けの補助。現時点ではstate/match_state.py
    側への組み込みは行っておらず、検知のみ可能な状態(Issue #141はROI分割まで
    がスコープ、記録方針側の変更は別途要相談)。
    """
    return _label_matches(frame, roi, _OWN_GOAL_LABEL_VARIANTS)
