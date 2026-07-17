"""VS画面(マッチング完了直後)に表示される、両チーム最大4人分のランク数値の読み取り。

CLAUDE.md・Issue #39/#52記載のとおり、VS画面確定時に自チーム/相手チームそれぞれ
最大4スロット分の∞帯の数値をnull許容で読み取る。文字階級(S/A等の∞未満のランク帯)
は対象外とし、読み取れない場合と同様Noneを返す。

数字自体はrank_ocr.pyと同じ「∞バッジの数字」だが、VS画面のバッジは1つ1つが
非常に小さく(画面に8個並ぶため)、rank_ocr.pyが使うEasyOCRでは小さな切り出し画像
だと文字検出自体に失敗することが多いと判明した。goal.pyの名前OCR同様、
PaddleOCR(lang="en")に切り替えたところ大幅に精度が改善したため、数字読み取りに
ここでもPaddleOCRを使う(EasyOCRとPaddleOCRのどちらが良いかは対象・文字種に依存する
ため、rank_ocr.py側は変更していない)。

さらに以下の前処理を組み合わせることで実用的な精度になることを確認した
(fixtures/screenshots 4枚 + fixtures/videos 3本の実測、のべ72スロットで
正答率93%):

- 3倍に拡大(cv2.resize, INTER_CUBIC)してからOCRする。切り出しがそのままの
  解像度だとPaddleOCRの文字検出自体が働かないことがある
- 拡大後、外周に単色の余白(数字読み取り時は黒、後述のアイコン読み取り時は白)を
  人工的に追加する(cv2.copyMakeBorder)。周囲に何も無い切り出し画像だと文字検出の
  手がかりが乏しく、切り出し境界ギリギリの文字を見逃すことがあるため
- OCR結果の各文字列は、末尾に小さいアイコン(コントローラー種別表示等)が
  混ざり「10×」のような文字列になることがある。厳密な完全一致(text.isdigit())
  ではなく先頭の数字部分だけを正規表現で取り出す
- 同じ切り出し内に∞アイコンの誤読(後述)由来の数字と本物の数字の両方が
  文字列として検出されることがある。本物のランク数値は∞帯で最大2桁のため、
  検出された数字候補のうち最長の文字列を採用する(1桁の誤読より2桁の本物を優先)

文字階級(S/A等)の除外については、S/A自体の文字認識は小さすぎて安定しないと
判明したため、「∞かどうか」の二値判定に単純化した(Issue #52でのユーザーとの
すり合わせ済み、文字階級同士の区別は今回スコープ外)。∞アイコンは数字と誤認識
されやすい(実測でほぼ確実に何らかの数字として読める)一方、S/A等の文字は
数字としては読めない(空文字列か、数字以外の文字列になる)という傾向を利用し、
バッジのアイコン部分(数字ピルのすぐ上)をOCRして、結果が空でなく全て数字から
成る場合のみ∞と判定する。∞と判定できなかった場合はスロット全体をNoneとする
(文字階級・非表示試合のどちらであっても呼び出し側からは区別せずNoneで良いため)。

アイコンと数字ピルの縦方向の間隔は、遠近感でスロットごとにバッジの表示サイズが
異なるため画面上部(遠く)のスロットほど狭くなる。固定オフセット1つでは全スロットを
賄えないと判明したため、MINE_ICON_OFFSETS/OPPONENT_ICON_OFFSETSはスロットごとに
個別の値を持つ。

既知の制約: 相手チームスロット2(手前から3番目)は、実機キャプチャ動画では
数字ピル自体のOCRが他スロットより不安定になることを確認している(静止画fixtureでは
安定して読める)。VS画面は5〜9秒程度(150〜270フレーム)表示され続けるため、
状態機械側(Issue #54)で複数フレームにわたって読み取りを試行し、最初に成功した
値を採用する(または多数決を取る)運用にすることで実質的な精度はさらに上げられる
見込み(単発フレームでの読み取りを前提にしない)。
"""

import re
from functools import lru_cache
from typing import Optional

import cv2
import numpy as np

from nss_tracker.detection_config import get_detection_value

# バッジ下部の数字ピルのみを狙った矩形 (x1, y1, x2, y2)。スロット0が画面手前
# (自チーム側は自分自身)、スロット3が最も奥。解像度1920x1080のフレームを前提とする
# (config/detection.tomlの[vs_rank]で上書き可能。以下同様)
MINE_SLOT_ROIS = get_detection_value(
    "vs_rank",
    "MINE_SLOT_ROIS",
    (
        (70, 866, 130, 894),
        (292, 783, 353, 821),
        (456, 710, 502, 733),
        (640, 647, 684, 669),
    ),
)
OPPONENT_SLOT_ROIS = get_detection_value(
    "vs_rank",
    "OPPONENT_SLOT_ROIS",
    (
        (1435, 866, 1495, 894),
        (1259, 783, 1313, 809),
        (1132, 698, 1189, 733),
        (981, 647, 1025, 669),
    ),
)

# 数字ピルのすぐ上にあるアイコン(∞または文字階級)領域を、スロットのROIからの
# (上端オフセット, 下端オフセット)として指定する。バッジの表示サイズが遠近感で
# スロットごとに異なるため、間隔も個別に実測している
MINE_ICON_OFFSETS = get_detection_value(
    "vs_rank", "MINE_ICON_OFFSETS", ((-40, -6), (-40, -6), (-28, -4), (-22, -3))
)
OPPONENT_ICON_OFFSETS = get_detection_value(
    "vs_rank", "OPPONENT_ICON_OFFSETS", ((-40, -6), (-40, -6), (-25, -3), (-25, -3))
)

_UPSCALE = get_detection_value("vs_rank", "UPSCALE", 3)
_PAD = get_detection_value("vs_rank", "PAD", 20)
_LEADING_DIGITS = re.compile(r"^\d+")


@lru_cache(maxsize=1)
def _get_reader():
    from paddleocr import PaddleOCR

    return PaddleOCR(
        lang="en",
        use_doc_orientation_classify=False,
        use_doc_unwarping=False,
        use_textline_orientation=False,
        enable_mkldnn=False,
    )


def _ocr_texts(crop: np.ndarray, border_color: tuple[int, int, int]) -> list[tuple[str, float]]:
    resized = cv2.resize(crop, None, fx=_UPSCALE, fy=_UPSCALE, interpolation=cv2.INTER_CUBIC)
    padded = cv2.copyMakeBorder(resized, _PAD, _PAD, _PAD, _PAD, cv2.BORDER_CONSTANT, value=border_color)
    results = _get_reader().predict(padded)
    texts: list[tuple[str, float]] = []
    for result in results:
        texts.extend(zip(result.get("rec_texts", []), result.get("rec_scores", [])))
    return texts


def _is_infinity_icon(
    frame: np.ndarray,
    slot_roi: tuple[int, int, int, int],
    icon_offset: tuple[int, int],
) -> bool:
    x1, y1, x2, _y2 = slot_roi
    off_top, off_bottom = icon_offset
    icon_crop = frame[y1 + off_top : y1 + off_bottom, x1:x2]
    texts = [text for text, _score in _ocr_texts(icon_crop, (255, 255, 255))]
    if not texts:
        return False
    return all(text.isdigit() for text in texts)


def _read_pill_digits(frame: np.ndarray, slot_roi: tuple[int, int, int, int]) -> Optional[int]:
    x1, y1, x2, y2 = slot_roi
    crop = frame[y1:y2, x1:x2]
    candidates = []
    for text, score in _ocr_texts(crop, (0, 0, 0)):
        match = _LEADING_DIGITS.match(text)
        if match:
            candidates.append((match.group(), score))
    if not candidates:
        return None
    candidates.sort(key=lambda candidate: (-len(candidate[0]), -candidate[1]))
    return int(candidates[0][0])


def read_slot_rank(
    frame: np.ndarray,
    slot_roi: tuple[int, int, int, int],
    icon_offset: tuple[int, int],
) -> Optional[int]:
    """VS画面の1スロット分のランク数値を読み取る。

    文字階級(S/A等)バッジ、ランク非表示の試合、バッジ自体が写っていない
    場合はいずれもNoneを返す(呼び出し側からは区別しない)。
    """
    if not _is_infinity_icon(frame, slot_roi, icon_offset):
        return None
    return _read_pill_digits(frame, slot_roi)


def read_vs_screen_ranks(frame: np.ndarray) -> tuple[list[Optional[int]], list[Optional[int]]]:
    """VS画面の両チーム最大4人分のランク数値をまとめて読み取る。

    戻り値は (自チーム, 相手チーム) の順のタプル。各リストはスロット0
    (カメラに最も近い位置、自チーム側は自分自身)〜3(最も奥)の順。
    """
    mine = [
        read_slot_rank(frame, roi, MINE_ICON_OFFSETS[i]) for i, roi in enumerate(MINE_SLOT_ROIS)
    ]
    opponent = [
        read_slot_rank(frame, roi, OPPONENT_ICON_OFFSETS[i]) for i, roi in enumerate(OPPONENT_SLOT_ROIS)
    ]
    return mine, opponent
