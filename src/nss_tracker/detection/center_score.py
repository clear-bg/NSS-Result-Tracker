"""ゴール直後に画面中央へ出る大きなスコア表示(例: `1 - 2`)の読み取り(Issue #493)。

専用部屋で部屋主が「試合終了」表示を待たずに「やりなおす」を押すと、結果バナーが
出ないまま次のVS画面へ戻る。その試合の勝敗を、最後に読んだこのスコア表示から
決めるために使う(state/match_state.py参照)。

### 表示の性質(実測)

- **常に左=自チーム・右=相手チーム**。チームカラーによる左右の入れ替わりは無い
  (ユーザー確認済み)
- ゴールのたびに必ず出る(ゴールデンボール・オウンゴールを含む、ユーザー確認済み)
- 30fpsの動画で見た流れ: 「ゴール!」バナー(約1.5秒) → ゴール前のスコア(約0.4秒)
  → 得点した側の数字が回転・拡大しながら切り替わる(約0.6秒) → 新しいスコアで
  止まる(約1.0秒) → 消える
- 表示位置は野良・専用部屋で同じ(10枚のスクリーンショットで、白い数字の範囲が
  ±1pxに収まる)
- 数字は0〜6しか出ない前提にする(2桁・7以上は3年間のプレイで見たことが無い、
  ユーザー確認済み)。5はまれ、6はほぼ出ないため、手元にサンプルが無い

### 読み取り方法

数字のフォントが特殊なため、OCRではなく**テンプレートマッチング**を主にする。

1. ROI内でHSVの白(彩度<`WHITE_SAT_MAX`・明度>`WHITE_VAL_MIN`)を取り出す
2. 最も大きい連結成分だけを残す(背景のスタジアムの白い部分を除くため)
3. その外接矩形で切り出して48x64に縮め、手持ちのテンプレートとIoU(重なりの割合)を取る

1回0.2ms程度で、スコア表示が出ている数秒間は毎フレーム回せる(重いOCRを常時回さない
2層構成の方針には反しない。読み取るのはゴール検知から数秒間だけ)。

**切り替わり途中のフレームは読まない。** 白い成分がROIの端に触れている、または高さが
`DIGIT_HEIGHT_RANGE`の外にある場合はNoneを返す。止まった表示の数字は高さ109px前後で
揃っている。拡大の途中はもっと大きく、回転の途中は端に触れたり高さが変わったりする。

テンプレートは`center_score_templates/{数字}_{連番}.png`(48x64の2値画像)に置く。
作り方は`scripts/build_center_score_templates.py`参照。5・6のサンプルが手に入ったら
このスクリプトで足せば、OCRに頼る場面が減る。

### 検証結果(Issue #493のコメント参照)

- スクリーンショット10枚(26桁)のleave-one-out: 全問正解。正しい数字との一致度0.95〜0.98、
  別の数字との一致度は最大0.80(2と3が最も近い)
- fixtures/videos 23本の全フレーム: 11ゴールすべてで、止まった表示の値が正しかった。
  止まった表示の区間はほぼ全フレームで一致度0.90以上、試合中の誤読は0件
- そのため一致度の境目(`MATCH_MIN`)は0.90にした

### テンプレートに無い数字(5・6)

どのテンプレートとも`MATCH_MIN`以上で一致しないが、形は止まった数字として整っている
(上記の端・高さの条件を満たす)場合、`DigitReading.digit`をNoneにして返す。呼び出し側は
その形が一定時間続いたら`ocr_digit()`で読む(PaddleOCR、重いので別プロセスで呼ぶこと)。

OCRの余白は黒ではなく**端の色を引き伸ばす**(`cv2.BORDER_REPLICATE`)。rank_ocr.py・
vs_rank.pyと同じ黒い余白だと「1」が「2」と誤読された。端の色を引き伸ばすと
26桁すべて正しく読めた。
"""

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Optional

import cv2
import numpy as np

from nss_tracker.detection_config import get_detection_value

# (x1, y1, x2, y2)。左=自チーム、右=相手チーム。白い数字の実測範囲
# (左x644〜734・右x1184〜1274、y283〜392)に数pxの余白を足し、幅を112に揃えた
LEFT_ROI = get_detection_value("center_score", "LEFT_ROI", (635, 274, 747, 405))
RIGHT_ROI = get_detection_value("center_score", "RIGHT_ROI", (1173, 274, 1285, 405))

WHITE_SAT_MAX = get_detection_value("center_score", "WHITE_SAT_MAX", 60)
WHITE_VAL_MIN = get_detection_value("center_score", "WHITE_VAL_MIN", 200)
# 止まった表示の数字の高さ(実測109px前後)。拡大・回転の途中のフレームを除くため
DIGIT_HEIGHT_RANGE = get_detection_value("center_score", "DIGIT_HEIGHT_RANGE", (104, 114))
# 白い成分の最小面積(これより小さいものは数字とみなさない)
DIGIT_AREA_MIN = get_detection_value("center_score", "DIGIT_AREA_MIN", 300)
# テンプレートとの一致度(IoU)の境目。正しい数字は0.90以上、別の数字は最大0.80(実測)
MATCH_MIN = get_detection_value("center_score", "MATCH_MIN", 0.90)

TEMPLATE_SIZE = (48, 64)  # (幅, 高さ)
TEMPLATE_DIR = Path(__file__).parent / "center_score_templates"
# 画面に出る数字(0〜6、モジュールdocstring参照)。OCR結果もこの範囲だけ受け付ける
VALID_DIGITS = "0123456"


@dataclass(frozen=True)
class DigitReading:
    """片側の数字の読み取り結果。

    digitがNoneのときは、形は止まった数字として整っているがどのテンプレートとも
    MATCH_MIN以上で一致しなかったことを表す(テンプレートに無い5・6の可能性)。
    maskは正規化後の2値画像で、呼び出し側が「同じ形が続いているか」を比べるのに使う。
    """

    digit: Optional[int]
    score: float
    mask: np.ndarray


def _largest_white_component(frame: np.ndarray, roi: tuple[int, int, int, int]):
    x1, y1, x2, y2 = roi
    crop = frame[y1:y2, x1:x2]
    if crop.size == 0:
        return None
    hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)
    white = ((hsv[:, :, 1] < WHITE_SAT_MAX) & (hsv[:, :, 2] > WHITE_VAL_MIN)).astype(np.uint8)
    count, labels, stats, _ = cv2.connectedComponentsWithStats(white)
    if count < 2:
        return None
    index = 1 + int(np.argmax(stats[1:, cv2.CC_STAT_AREA]))
    return labels == index, stats[index], white.shape


def normalize_mask(mask: np.ndarray) -> np.ndarray:
    """2値マスクを外接矩形で切り出してTEMPLATE_SIZEに縮める。"""
    ys, xs = np.nonzero(mask)
    cropped = mask[ys.min() : ys.max() + 1, xs.min() : xs.max() + 1].astype(np.uint8) * 255
    return cv2.resize(cropped, TEMPLATE_SIZE, interpolation=cv2.INTER_AREA) > 127


def extract_digit_mask(frame: np.ndarray, roi: tuple[int, int, int, int]) -> Optional[np.ndarray]:
    """止まった表示の数字の形(正規化済みの2値画像)を取り出す。

    数字が無い、または切り替わり途中(ROIの端に触れている・高さが範囲外)ならNone。
    """
    found = _largest_white_component(frame, roi)
    if found is None:
        return None
    mask, stats, shape = found
    x, y, w, h, area = (int(v) for v in stats)
    if area < DIGIT_AREA_MIN:
        return None
    if x == 0 or y == 0 or x + w == shape[1] or y + h == shape[0]:
        return None
    if not (DIGIT_HEIGHT_RANGE[0] <= h <= DIGIT_HEIGHT_RANGE[1]):
        return None
    return normalize_mask(mask)


@lru_cache(maxsize=1)
def _load_templates() -> tuple[tuple[int, np.ndarray], ...]:
    templates = []
    for path in sorted(TEMPLATE_DIR.glob("*.png")):
        image = cv2.imread(str(path), cv2.IMREAD_GRAYSCALE)
        if image is None:
            continue
        templates.append((int(path.name.split("_")[0]), image > 127))
    return tuple(templates)


def match_digit(mask: np.ndarray) -> tuple[Optional[int], float]:
    """正規化済みのマスクに最も近いテンプレートの数字と一致度(IoU)を返す。

    一致度がMATCH_MIN未満なら数字はNone(一致度はそのまま返す)。
    """
    best_digit: Optional[int] = None
    best_score = 0.0
    for digit, template in _load_templates():
        union = (mask | template).sum()
        score = float((mask & template).sum() / union) if union else 0.0
        if score > best_score:
            best_digit, best_score = digit, score
    if best_score < MATCH_MIN:
        return None, best_score
    return best_digit, best_score


def read_digit(frame: np.ndarray, roi: tuple[int, int, int, int]) -> Optional[DigitReading]:
    """片側の数字を読む。止まった表示の数字が見えていなければNone。"""
    mask = extract_digit_mask(frame, roi)
    if mask is None:
        return None
    digit, score = match_digit(mask)
    return DigitReading(digit=digit, score=score, mask=mask)


def read_center_score(frame: np.ndarray) -> Optional[tuple[DigitReading, DigitReading]]:
    """(自チーム側, 相手チーム側)の読み取り結果。両側とも見えていなければNone。"""
    left = read_digit(frame, LEFT_ROI)
    if left is None:
        return None
    right = read_digit(frame, RIGHT_ROI)
    if right is None:
        return None
    return left, right


def mask_similarity(a: np.ndarray, b: np.ndarray) -> float:
    """正規化済みマスク同士のIoU。同じ形が続いているかの判定に使う。"""
    union = (a | b).sum()
    return float((a & b).sum() / union) if union else 0.0


def ocr_digit(frame: np.ndarray, roi: tuple[int, int, int, int]) -> Optional[tuple[int, float]]:
    """PaddleOCRで片側の数字を読む(テンプレートに無い数字の補助、重い処理)。

    VALID_DIGITSの1文字として読めた場合だけ(数字, 信頼度)を返す。
    """
    from nss_tracker.detection.goal import _get_name_reader

    x1, y1, x2, y2 = roi
    crop = frame[y1:y2, x1:x2]
    if crop.size == 0:
        return None
    padded = cv2.copyMakeBorder(crop, 40, 40, 40, 40, cv2.BORDER_REPLICATE)
    texts: list[tuple[str, float]] = []
    for result in _get_name_reader().predict(padded):
        for text, score in zip(result.get("rec_texts", []), result.get("rec_scores", [])):
            texts.append((text.strip(), float(score)))
    if len(texts) != 1:
        return None
    text, score = texts[0]
    if len(text) != 1 or text not in VALID_DIGITS:
        return None
    return int(text), score
