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

名前パネルは「ゴール」ラベル→得点者(肩書き+名前)→「アシスト」ラベル→
アシスト者(肩書き+名前)という並びで、アシストが無い場合は「アシスト」以降が
存在しない。肩書き文字列(例:「スタッフ」)が名前の上に乗ることがあるため、
各ブロックの最後(最も下)の行を名前として採用する(rank_ocr.pyの
∞アイコン除去と同じ「一番下の行を採用する」考え方)。
"""

from functools import lru_cache
from typing import Optional

import cv2
import numpy as np

from nss_tracker.detection_config import get_detection_value

# 「ゴール!」バナーのうち、中央の白文字にかぶらない左寄りの領域 (x1, y1, x2, y2)
# 解像度1920x1080のフレームを前提とする
# (config/detection.tomlの[goal]で上書き可能。以下同様)
BANNER_ROI = get_detection_value("goal", "BANNER_ROI", (100, 280, 400, 350))

# 実測(scripts/inspect_goal_colors.py): 青チーム得点 H88-96 / 赤チーム得点 H137-150
# いずれも非該当状態(プレイ中・ロビー等)より明確に高彩度・高輝度
BLUE_HUE_RANGE = get_detection_value("goal", "BLUE_HUE_RANGE", (83, 100))
RED_HUE_RANGE = get_detection_value("goal", "RED_HUE_RANGE", (130, 155))
SAT_MIN = get_detection_value("goal", "SAT_MIN", 100)
VAL_MIN = get_detection_value("goal", "VAL_MIN", 190)

# 得点者・アシスト名のパネル全体を覆う領域。パネルは段数(アシスト有無)に応じて
# 縦位置が変わるため、両パターンを包含する広めの範囲にしている
NAME_PANEL_ROI = get_detection_value("goal", "NAME_PANEL_ROI", (700, 780, 1250, 1030))

_ASSIST_LABEL = "アシスト"
# OCRが「ゴール」を「コール」等に誤読することがあるため、既知のラベル文字列は
# 複数バリエーションを許容し、名前として誤採用しないよう除外する
_GOAL_LABEL_VARIANTS = {"ゴール", "コール"}


def is_goal_event(frame: np.ndarray, roi: tuple[int, int, int, int] = BANNER_ROI) -> bool:
    """「ゴール!」バナーが表示されているかを判定する(色ベース、チームカラー問わず)。"""
    x1, y1, x2, y2 = roi
    crop = frame[y1:y2, x1:x2]
    hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)
    h, s, v = hsv.reshape(-1, 3).mean(axis=0)

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


def _read_panel_lines(frame: np.ndarray, roi: tuple[int, int, int, int]) -> list[tuple[float, str]]:
    x1, y1, x2, y2 = roi
    crop = frame[y1:y2, x1:x2]
    results = _get_name_reader().predict(crop)

    lines: list[tuple[float, str]] = []
    for result in results:
        texts = result.get("rec_texts", [])
        boxes = result.get("rec_boxes", [])
        for text, box in zip(texts, boxes):
            lines.append((float(box[1]), text))
    lines.sort(key=lambda line: line[0])
    return lines


def read_scorer_name(frame: np.ndarray, roi: tuple[int, int, int, int] = NAME_PANEL_ROI) -> Optional[str]:
    """得点者名をOCRで読み取る。パネルが表示されていなければNoneを返す。"""
    block: list[str] = []
    for _y, text in _read_panel_lines(frame, roi):
        if text == _ASSIST_LABEL:
            break
        if text in _GOAL_LABEL_VARIANTS:
            continue
        block.append(text)
    return block[-1] if block else None


def read_assist_name(frame: np.ndarray, roi: tuple[int, int, int, int] = NAME_PANEL_ROI) -> Optional[str]:
    """アシスト者名をOCRで読み取る。アシストが無い場合はNoneを返す。"""
    block: list[str] = []
    in_assist_block = False
    for _y, text in _read_panel_lines(frame, roi):
        if text == _ASSIST_LABEL:
            in_assist_block = True
            continue
        if in_assist_block:
            block.append(text)
    return block[-1] if block else None
