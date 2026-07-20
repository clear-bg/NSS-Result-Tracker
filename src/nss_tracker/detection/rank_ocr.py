"""ランク数値のOCR読み取り、およびゲージ(帯の溜まり具合)の読み取り。

CLAUDE.md記載の方針どおり、画面左下の固定領域をOCRで読み取る。
RANK_ROIはランク変動アニメーション中(バッジが拡大表示される)・通常表示の
両方をカバーできるよう余裕を持たせている(scripts/inspect_rank_ocr.py参照)。
数字OCRはバッジ全体を包む余裕のあるROIで問題なく読めるが、ゲージの
塗りつぶし「割合」は列単位のピクセル計測のため、ROIの左右端が実際の
バー端とずれると割合そのものが狂う。バッジは表示タイミングによって
明確に異なる2つのサイズで描画される(実測、fixtures/screenshots):

- コンパクト表示: 結果バナー確定直後、ランク変動アニメーションが始まる前。
  ゲージバーの実測範囲は概ね x=120-350, y=966-993(約230px幅)
- 拡大表示: ランク変動アニメーションが始まった瞬間からバッジ全体(数値・
  ゲージとも)が一回り大きくなり、暗転するまでそのサイズを保つ。
  ゲージバーの実測範囲は概ね x=125-425, y=962-1001(約295px幅、
  コンパクト表示より明確に横長)

1つの固定ROIで両方を賄おうとすると、コンパクト表示では右端が本来の
バーより外(芝生)にはみ出し、拡大表示では逆にバーの一部(特に左右の
端)を切り捨ててしまう。切り捨て方が両状態で偶然近い相対位置になるため、
本来明確に異なる値(勝ち/負けで塗りつぶし量は変わるはず)が偶然近い値に
丸められてしまうという不具合が実データで見つかった(例: 同一帯のまま
負けて明らかに塗りつぶし量が減っているはずの前後ペアが、どちらも同じ
0.78という値を返していた)。そのため、ゲージ用のROIはコンパクト表示・
拡大表示それぞれに個別のものを用意する(GAUGE_ROI_COMPACT /
GAUGE_ROI_ENLARGED)。

state.match_state側の呼び出しタイミングは以下のように一意に決まるため、
呼び出し元でどちらのROIを使うべきかは常に自明である:
- 結果バナー確定直後に読む rank_before → 常にコンパクト表示
- ランク変動アニメーションが安定した後に読む rank_after → 常に拡大表示

バッジには「∞」アイコンも表示されており、数字のみ(allowlist)で読み取ると
"0"や"00"として誤認識される。∞アイコンは常にランク数値の上に表示されるため、
検出された数字のうち最も下側(bboxのy座標が最大)のものを実際のランク数値
として採用する。

Issue #73: S/A帯のバッジも∞と同じ「アイコン(∞記号/英字)+ 下に数値」という
レイアウトで描画される想定のため、read_rank_tier()はread_rank()と同じRANK_ROIを
allowlistなしでOCRし、最も上側(bboxのy座標が最小)のテキストをアイコンと
みなして判定する(新規のROI測定は不要)。実際に既存の∞帯fixture6枚全てで
「allowlistなしでも最上段は"0"/"00"に誤読される」ことを確認済みのため、
∞判定はこの方式で問題ない。一方S/A帯が実際にこの分類で正しく読めるかは、
結果バナー画面でS/A帯バッジが写った参照fixtureが無く未検証(vs_rank.pyの
VS画面用アイコンROIを代用して試したが、バッジが小さすぎてEasyOCRが
'S'/'A'の文字自体を安定して検出できないことが判明しており、代用にならない
と判明した)。Issue #43でS/A帯の結果バナーfixtureが集まり次第、実データで
検証すること。

ランク変動アニメーション中(帯が動いている最中、拡大表示の遷移演出)は
ゲージの見た目が遷移演出用のグラデーションになり塗りつぶし割合として
意味を持たないため、read_rankと同様に「安定している瞬間」にのみ
呼び出すこと(state.match_state参照)。
閾値はscripts/inspect_gauge_fill.pyでfixtures/screenshotsの
結果バナー画面(勝ち/負け双方、コンパクト表示・拡大表示それぞれの
安定表示)を実測して決定した。
"""

from functools import lru_cache
from typing import Optional

import cv2
import numpy as np

from nss_tracker.detection_config import get_detection_value

# ランクバッジが写りうる範囲(通常表示・昇格/降格アニメ中の拡大表示の両方を含む)
# 解像度1920x1080のフレームを前提とする
# (config/detection.tomlの[rank_ocr]で上書き可能。以下同様)
RANK_ROI = get_detection_value("rank_ocr", "RANK_ROI", (90, 600, 420, 930))

# ランク数値バッジ下部のゲージ(横長の帯)の領域。コンパクト表示・拡大表示で
# バーの実寸(幅・位置とも)が異なるため個別に用意する(モジュールdocstring参照)。
# 丸みを帯びた両端のアンチエイリアス部分を避けるため、実測した真のバー端から
# 内側に数px分マージンを取っている
GAUGE_ROI_COMPACT = get_detection_value("rank_ocr", "GAUGE_ROI_COMPACT", (125, 970, 345, 990))
GAUGE_ROI_ENLARGED = get_detection_value("rank_ocr", "GAUGE_ROI_ENLARGED", (130, 966, 420, 998))

# 実測(scripts/inspect_gauge_fill.py): 塗りつぶし部分はV(明度)が200前後、
# 未塗りつぶし部分は80〜105程度で明確に分離できる
GAUGE_FILLED_VALUE_THRESHOLD = get_detection_value("rank_ocr", "GAUGE_FILLED_VALUE_THRESHOLD", 150)

# バッジのアイコン部分が英字帯の場合に取りうる文字(vs_rank.pyの_LETTER_TIERSと同じ、
# B~E帯は参照fixtureが無く未対応のため対象外)
RANK_TIER_LETTERS = ("S", "A")


@lru_cache(maxsize=1)
def _get_reader():
    import easyocr

    return easyocr.Reader(["en"], gpu=False)


def read_rank(frame: np.ndarray, roi: tuple[int, int, int, int] = RANK_ROI) -> Optional[int]:
    """ランク数値バッジをOCRで読み取る。バッジが表示されていなければNoneを返す。"""
    x1, y1, x2, y2 = roi
    crop = frame[y1:y2, x1:x2]
    results = _get_reader().readtext(crop, allowlist="0123456789")
    if not results:
        return None

    def bbox_top(result: tuple) -> float:
        bbox, _text, _conf = result
        return min(point[1] for point in bbox)

    _bbox, text, _conf = max(results, key=bbox_top)
    if not text.isdigit():
        return None
    return int(text)


def read_rank_tier(frame: np.ndarray, roi: tuple[int, int, int, int] = RANK_ROI) -> Optional[str]:
    """バッジのアイコン部分から帯('∞'/'S'/'A')を判定する。判定できなければNone。

    read_rank()と同じROIをallowlistなしでOCRし、最も上側(bboxのy座標が最小)の
    テキストをアイコンとみなす(モジュールdocstring参照。アイコンは常に数値の上に
    表示される)。全て数字であれば∞アイコンの誤読とみなし、RANK_TIER_LETTERSの
    いずれかと完全一致すればその帯を返す。B~E帯・非表示・どちらとも判定できない
    誤読はいずれもNoneを返し、呼び出し側からは区別しない(vs_rank.pyと同じ方針)。

    S/A帯の判定は結果バナー画面での参照fixtureが無く未検証(モジュールdocstring参照)。
    """
    x1, y1, x2, y2 = roi
    crop = frame[y1:y2, x1:x2]
    results = _get_reader().readtext(crop)
    if not results:
        return None

    def bbox_top(result: tuple) -> float:
        bbox, _text, _conf = result
        return min(point[1] for point in bbox)

    _bbox, text, _conf = min(results, key=bbox_top)
    text = text.strip().upper()
    if text.isdigit():
        return "∞"
    if text in RANK_TIER_LETTERS:
        return text
    return None


def read_rank_gauge_fill(frame: np.ndarray, roi: tuple[int, int, int, int]) -> Optional[float]:
    """ランクゲージの塗りつぶし割合(0.0〜1.0)を読み取る。

    roiにはGAUGE_ROI_COMPACT / GAUGE_ROI_ENLARGEDのうち、その瞬間のバッジ
    表示サイズに合ったものを呼び出し側が指定すること(モジュールdocstring参照)。
    デフォルト値を持たせない(どちらか一方を既定にすると、もう一方のサイズで
    誤って使われたときに気付けないため)。

    ゲージが表示されていない(バッジ自体が無い)場合の判定はしていないため、
    呼び出し側でread_rank()等がNoneでないことを確認してから呼ぶこと。
    """
    x1, y1, x2, y2 = roi
    crop = frame[y1:y2, x1:x2]
    hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)
    value_by_column = hsv[:, :, 2].mean(axis=0)
    filled = value_by_column > GAUGE_FILLED_VALUE_THRESHOLD
    return float(filled.mean())


def read_precise_rank(frame: np.ndarray, gauge_roi: tuple[int, int, int, int]) -> Optional[tuple[int, float]]:
    """整数の帯番号と、それにゲージの溜まり具合を加えた小数のランク値を読み取る。

    gauge_roiにはGAUGE_ROI_COMPACT / GAUGE_ROI_ENLARGEDのうち、呼び出し時点の
    バッジ表示サイズに合ったものを渡すこと。state.match_state内の呼び出しは
    タイミングによってどちらのサイズかが一意に決まる(モジュールdocstring参照)。

    戻り値は (帯番号, 小数のランク値) のタプル。帯番号が読めなければNoneを返す。
    ゲージの溜まり具合が1.0(満タン)になることがあり、その場合
    tier + fill を単純に計算すると次の帯の数値と一致してしまい、後から
    小数値だけを見て帯番号を復元しようとすると誤る。そのため帯番号は
    別途そのまま返し、呼び出し側(league_changed判定等)はこちらを使うこと。
    ゲージが読めない場合は小数部を0として帯番号のみのランク値を返す
    (取れる情報だけ取っておき、後で表示時に丸める運用のため)。
    """
    tier = read_rank(frame)
    if tier is None:
        return None
    fill = read_rank_gauge_fill(frame, gauge_roi)
    precise = tier + (fill if fill is not None else 0.0)
    return tier, precise
