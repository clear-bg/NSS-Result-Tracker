"""VS画面(マッチング完了直後)に表示される、両チーム最大4人分のランク数値の読み取り。

CLAUDE.md・Issue #39/#52/#40記載のとおり、VS画面確定時に自チーム/相手チームそれぞれ
最大4スロット分のランクバッジを読み取る。バッジは∞/S/Aいずれも「アイコン(∞記号
または英字)+ その下の数値ピル」という同一レイアウトで描画されるため、アイコン
部分をOCRして∞/S/Aのどれかをまず判定し、数値ピルは共通のロジックで読み取る
(read_slot_rank参照)。B/C/D/Eはアイコン自体の参照fixtureがまだ無くOCR精度を
検証できないため未対応とし、これらのバッジ・バッジ非表示・読み取り失敗はいずれも
SlotRank(None, None)として区別しない(Issue #43でfixtureが揃うまでの暫定)。

S/A帯内の数値がバッジ表示上「大きいほど良い(∞と同じ向き)」かどうかは、実際の
S/A帯での昇格/降格映像で未検証。参照fixture(70/71)は静止画1枚ずつのみで
前後比較ができないため、呼び出し側で大小比較による昇格/降格判定を行う場合は
別途の検証が必要(現時点でread_vs_screen_ranksの呼び出し元はスナップショットとして
保存するのみで、前後比較はしていない)。

数字自体はrank_ocr.pyと同じ「∞バッジの数字」だが、VS画面のバッジは1つ1つが
非常に小さく(画面に8個並ぶため)、rank_ocr.pyが使うEasyOCRでは小さな切り出し画像
だと文字検出自体に失敗することが多いと判明した。goal.pyの名前OCR同様、
PaddleOCR(lang="en")に切り替えたところ大幅に精度が改善したため、数字読み取りに
ここでもPaddleOCRを使う(EasyOCRとPaddleOCRのどちらが良いかは対象・文字種に依存する
ため、rank_ocr.py側は変更していない)。

さらに以下の前処理を組み合わせることで実用的な精度になることを確認した:

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

文字階級(S/A等)の除外は、バッジのアイコン部分(数字ピルのすぐ上)をOCRして、
結果が空でなく全て数字から成る場合のみ∞と判定する二値判定で行う。∞と判定
できなかった場合はスロット全体をNoneとする(文字階級・非表示試合のどちらで
あっても呼び出し側からは区別せずNoneで良いため)。

切り出し座標(Issue #57)は、fixture画像に切り出し範囲を重ねて1px単位で
実測した値を使っている。これによりfixtures/screenshots 6枚+fixtures/videos
3本(のべ72スロット)で全問正解(100%)を達成しており、超解像等の追加処理は
不要。以下の座標のみで読み取る。

自チーム(画面左側)4スロット分の「左上座標(x1, y1) + 幅 + 高さ」のみを正確に
決めており(MINE_ICON_XYWH / MINE_NUM_XYWH)、相手チーム(画面右側)は同じ奥行きの
自チームのスロットとy座標・幅・高さが同じで、x1(左上のx座標)だけが異なる
(OPPONENT_X1)。相手チームの座標を自チームの単純な左右反転では求められない
(スロットごとに逆算される軸の値が782.5→832まで変わってしまい、奥行きによって
見え方の対応が微妙に違うことが実測で判明した)ため、相手チームのx1は自チームの
値から計算せず、実データを見て個別に測定した値をそのまま持つ。
"""

import re
from functools import lru_cache
from typing import NamedTuple, Optional

import cv2
import numpy as np

from nss_tracker.detection_config import get_detection_value

# 自チーム(画面左側)4スロット分の切り出し領域を (x1, y1, 幅, 高さ) で保持する。
# スロット0が画面手前(自分自身)、スロット3が最も奥。解像度1920x1080のフレームを
# 前提とする(config/detection.tomlの[vs_rank]で上書き可能。以下同様)
MINE_ICON_XYWH = get_detection_value(
    "vs_rank",
    "MINE_ICON_XYWH",
    (
        (83, 830, 33, 33),
        (305, 752, 28, 26),
        (465, 686, 28, 22),
        (649, 629, 24, 16),
    ),
)
MINE_NUM_XYWH = get_detection_value(
    "vs_rank",
    "MINE_NUM_XYWH",
    (
        (83, 871, 33, 19),
        (305, 788, 28, 17),
        (465, 714, 28, 14),
        (649, 652, 24, 12),
    ),
)

# 相手チーム(画面右側)は自チームの対応するスロットとy座標・幅・高さが同じで、
# x1(左上のx座標)だけが異なる(モジュールdocstring参照)。icon・numとも同じx1を使う
OPPONENT_X1 = get_detection_value("vs_rank", "OPPONENT_X1", (1448, 1270, 1151, 991))

_UPSCALE = get_detection_value("vs_rank", "UPSCALE", 3)
_PAD = get_detection_value("vs_rank", "PAD", 20)
_LEADING_DIGITS = re.compile(r"^\d+")
_LETTER_TIERS = ("S", "A")


class SlotRank(NamedTuple):
    """1スロット分のランクバッジ読み取り結果。

    tierは'∞'/'S'/'A'のいずれか(未識別・非表示・B~E(未対応)はNone)。
    valueは帯内の数値(tierがNoneの場合、または数値ピルのOCRが失敗した場合はNone)。
    """

    tier: Optional[str]
    value: Optional[int]


def _xywh_to_roi(x1: int, y1: int, w: int, h: int) -> tuple[int, int, int, int]:
    return (x1, y1, x1 + w, y1 + h)


MINE_ICON_ROIS = tuple(_xywh_to_roi(*xywh) for xywh in MINE_ICON_XYWH)
MINE_NUM_ROIS = tuple(_xywh_to_roi(*xywh) for xywh in MINE_NUM_XYWH)
OPPONENT_ICON_ROIS = tuple(
    _xywh_to_roi(OPPONENT_X1[i], MINE_ICON_XYWH[i][1], MINE_ICON_XYWH[i][2], MINE_ICON_XYWH[i][3])
    for i in range(4)
)
OPPONENT_NUM_ROIS = tuple(
    _xywh_to_roi(OPPONENT_X1[i], MINE_NUM_XYWH[i][1], MINE_NUM_XYWH[i][2], MINE_NUM_XYWH[i][3])
    for i in range(4)
)


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


def _classify_tier_icon(frame: np.ndarray, icon_roi: tuple[int, int, int, int]) -> Optional[str]:
    """アイコン領域をOCRし、'∞'/'S'/'A'のいずれかを判定する。

    検出テキストが全て数字なら∞アイコン(数字の誤読)とみなす。B~E・非表示・
    どちらとも判定できない誤読はいずれもNoneを返し、呼び出し側からは区別しない。
    """
    x1, y1, x2, y2 = icon_roi
    icon_crop = frame[y1:y2, x1:x2]
    texts = [text.strip().upper() for text, _score in _ocr_texts(icon_crop, (255, 255, 255))]
    if not texts:
        return None
    if all(text.isdigit() for text in texts):
        return "∞"
    for text in texts:
        if text in _LETTER_TIERS:
            return text
    return None


def _read_pill_digits(frame: np.ndarray, num_roi: tuple[int, int, int, int]) -> Optional[int]:
    x1, y1, x2, y2 = num_roi
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
    icon_roi: tuple[int, int, int, int],
    num_roi: tuple[int, int, int, int],
) -> SlotRank:
    """VS画面の1スロット分のランクバッジを読み取る。

    B/C/D/Eバッジ、ランク非表示の試合、バッジ自体が写っていない場合は
    いずれもSlotRank(None, None)を返す(呼び出し側からは区別しない)。

    ∞判定は「検出テキストが全て数字」という緩い条件のため、バッジが無い
    (芝生など背景のみの)クロップでもノイズを数字と誤読して∞と判定して
    しまうことがある(実データで確認済み)。そのため∞の場合のみ、数値ピル
    も実際に読み取れたときに限ってSlotRankを返す(数値も読めて初めて∞と
    見なす、旧実装の暗黙の二重チェックを踏襲)。S/Aはアイコンの文字自体が
    ∞判定用の「全て数字」より遥かに特異的な一致('S'/'A'との完全一致)の
    ため、同じ問題は起きていない(実データで確認済み)。
    """
    tier = _classify_tier_icon(frame, icon_roi)
    if tier is None:
        return SlotRank(None, None)
    value = _read_pill_digits(frame, num_roi)
    if tier == "∞" and value is None:
        return SlotRank(None, None)
    return SlotRank(tier, value)


def read_vs_screen_ranks(frame: np.ndarray) -> tuple[list[SlotRank], list[SlotRank]]:
    """VS画面の両チーム最大4人分のランクバッジをまとめて読み取る。

    戻り値は (自チーム, 相手チーム) の順のタプル。各リストはスロット0
    (カメラに最も近い位置、自チーム側は自分自身)〜3(最も奥)の順。
    """
    mine = [read_slot_rank(frame, MINE_ICON_ROIS[i], MINE_NUM_ROIS[i]) for i in range(4)]
    opponent = [read_slot_rank(frame, OPPONENT_ICON_ROIS[i], OPPONENT_NUM_ROIS[i]) for i in range(4)]
    return mine, opponent
