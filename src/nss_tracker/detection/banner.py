"""結果バナー(勝ち/負け)の色判定。

CLAUDE.md記載の方針どおり、OCRではなく画面上部に出る斜めの結果バナーの
色で勝敗を判定する(形状判定は今後の精度向上時に追加検討)。

閾値は fixtures/screenshots の全状態画像に加え、実際の配信からのクリップ
(fixtures/videos/00〜03、scripts/inspect_banner_colors.py と同様の手法で採取)
を実測して決めている。同じOBS経由のキャプチャでも配信間でバナーの色味・
帯の太さ/角度に無視できない差があることが分かったため(勝ちはH80〜83で
ほぼ一致するが、負けはH84〜101まで幅がある)、ROIはバナー帯が細くなっても
確実に帯の内側に収まるよう画面最上部寄りの薄い帯にし、閾値も両者の実測
クラスタを包含する範囲にしている。

この判定は単体では「試合中のゴール演出」等でも稀に誤検知しうることを
確認済み(誤検知は最大でも0.5秒未満しか継続しない一方、本物のバナーは
1秒以上継続する)。呼び出し側で複数フレームにわたり同じ判定が一定時間
連続した場合のみ採用する運用を想定している(motion.find_confirmed_value参照)。

上記のデバウンスだけでは防げない誤検知として、マッチング待機画面
(「対戦相手をさがしています...」等)で背景のスタジアム建造物がBANNER_ROI内に
写り込み、その平均色がLOSE_HUE_RANGE等の閾値に偶然一致するケースが実データで
見つかった(fixtures/videos/14_matching_wait_1.mp4で2.5秒以上・3.4秒以上
持続、Issue #45参照)。この画面はカメラアングルがほぼ静止するため、
0.5秒未満という前提が崩れる。本物のバナーはリボン状の単色帯(白文字が
乗る程度のばらつき)なのに対し、背景の建造物は写実的で色相のばらつきが
大きいことを利用し、ROI内のHue標準偏差が閾値以下であることも判定条件に
追加した(実測: 本物のバナーはH標準偏差1〜3程度、マッチング画面の誤検知は
16〜52程度で明確に分離できる。fixtures/videos/00-03,10-16の全既知ケースで
検証済み)。

引き分け(draw)判定について(Issue #26、fixtures/videos/20_draw_blue_1-1.mp4
実測): 引き分けバナー「引き分け」の帯自体は負けバナーと似た暗い配色で、
BANNER_ROIの帯色だけではLOSE_HUE_RANGE等の条件に一致してしまい負けと
区別できない。一方、文字「引き分け」の縁取りには勝ちバナーと同じミント
グリーンが使われており(負けバナー「負け」の文字は白一色でグリーンの
縁取りが無い)、これを区別に使えることが実測で判明した。文字部分のROI
(DRAW_TEXT_ROI、画面左上の時計を避けた範囲)のうちミントグリーン
(H75-95, S>100, V>140)の画素が占める割合は、勝ち約55%・引き分け約25-27%・
負け0%と明確に分離できる(fixtures/screenshots 8枚+fixtures/videos/
20_draw_blue_1-1.mp4で実測)。そのため、帯色がLOSE_HUE_RANGE等に一致した
場合のみ追加でこの文字ROIを確認し、閾値(DRAW_TEXT_TEAL_FRACTION_MIN)以上
なら"draw"、未満なら"lose"と判定する。

なお引き分けの場合、ランクバッジは一切表示されない(引き分けはランク変動
そのものが無いため、CLAUDE.md「試合後の状態遷移」のランク変動アニメーション
〜暗転の演出も無く、バナー消灯後は直接メニュー画面に遷移する)。この挙動は
バッジ非表示のwin/loseケース(`*_without_rank_*.png`,
fixtures/videos/13_win_red_vs_screen_without_rank.mp4)と見た目上区別が
つかないため、state/match_state.py側の追加対応は不要で、既存の
「バッジが読み取れない場合はNoneのまま記録する」フォールバックがそのまま
機能する。
"""

from typing import Literal, Optional

import cv2
import numpy as np

from nss_tracker.detection_config import get_detection_value

BannerResult = Optional[Literal["win", "lose", "draw"]]

# バナー帯のうち、テキストや選手モデルにかぶらない右上寄りの領域 (x1, y1, x2, y2)
# 帯の太さ・角度は配信によって差があるため、画面最上部寄りの薄い帯にして
# 背景色の混入を避けている。解像度1920x1080のフレームを前提とする
# (config/detection.tomlの[banner]で上書き可能。以下同様)
BANNER_ROI = get_detection_value("banner", "BANNER_ROI", (1300, 5, 1750, 35))

# 実測(scripts/inspect_banner_colors.py, fixtures/screenshots + fixtures/videos/00-03):
# 勝ち: H80.7-83.2 / 負け: H89.0-99.0(配信間の差が大きい)
WIN_HUE_RANGE = get_detection_value("banner", "WIN_HUE_RANGE", (77, 86))
WIN_SAT_MIN = get_detection_value("banner", "WIN_SAT_MIN", 120)
WIN_VAL_MIN = get_detection_value("banner", "WIN_VAL_MIN", 165)
LOSE_HUE_RANGE = get_detection_value("banner", "LOSE_HUE_RANGE", (87, 103))
LOSE_SAT_RANGE = get_detection_value("banner", "LOSE_SAT_RANGE", (35, 65))
LOSE_VAL_RANGE = get_detection_value("banner", "LOSE_VAL_RANGE", (65, 130))

# 実測(Issue #45): 本物のバナーはH標準偏差1〜3程度(単色のリボン状の帯に白文字が
# 乗る程度のばらつき)。マッチング待機画面の誤検知は背景の建造物が写実的なため
# 16〜52程度と大きく上回る。余裕を持って10を閾値とする
BANNER_HUE_STD_MAX = get_detection_value("banner", "BANNER_HUE_STD_MAX", 10.0)

# 「引き分け」の文字の縁取り(ミントグリーン)を判定するための領域。
# 時計表示(画面左上)を避けた文字部分のみを狙う (x1, y1, x2, y2)
# 解像度1920x1080のフレームを前提とする
# (config/detection.tomlの[banner]で上書き可能。以下同様)
DRAW_TEXT_ROI = get_detection_value("banner", "DRAW_TEXT_ROI", (270, 45, 650, 250))

# 実測(fixtures/screenshots + fixtures/videos/20_draw_blue_1-1.mp4):
# 勝ち55% / 引き分け25-27% / 負け0%(DRAW_TEXT_ROI内でのミントグリーン画素の割合)
DRAW_TEXT_HUE_RANGE = get_detection_value("banner", "DRAW_TEXT_HUE_RANGE", (75, 95))
DRAW_TEXT_SAT_MIN = get_detection_value("banner", "DRAW_TEXT_SAT_MIN", 100)
DRAW_TEXT_VAL_MIN = get_detection_value("banner", "DRAW_TEXT_VAL_MIN", 140)
DRAW_TEXT_TEAL_FRACTION_MIN = get_detection_value("banner", "DRAW_TEXT_TEAL_FRACTION_MIN", 0.10)


def _is_draw_text(frame: np.ndarray, roi: tuple[int, int, int, int] = DRAW_TEXT_ROI) -> bool:
    x1, y1, x2, y2 = roi
    crop = frame[y1:y2, x1:x2]
    hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV).reshape(-1, 3)
    mask = (
        (hsv[:, 1] > DRAW_TEXT_SAT_MIN)
        & (hsv[:, 0] >= DRAW_TEXT_HUE_RANGE[0])
        & (hsv[:, 0] <= DRAW_TEXT_HUE_RANGE[1])
        & (hsv[:, 2] > DRAW_TEXT_VAL_MIN)
    )
    return bool(mask.mean() >= DRAW_TEXT_TEAL_FRACTION_MIN)


def classify_banner(frame: np.ndarray, roi: tuple[int, int, int, int] = BANNER_ROI) -> BannerResult:
    """勝敗結果バナーの色を判定する。バナーが写っていなければNoneを返す。"""
    x1, y1, x2, y2 = roi
    crop = frame[y1:y2, x1:x2]
    hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV).reshape(-1, 3)
    h, s, v = hsv.mean(axis=0)
    hue_std = hsv[:, 0].std()

    if hue_std > BANNER_HUE_STD_MAX:
        return None
    if WIN_HUE_RANGE[0] <= h <= WIN_HUE_RANGE[1] and s >= WIN_SAT_MIN and v >= WIN_VAL_MIN:
        return "win"
    if (
        LOSE_HUE_RANGE[0] <= h <= LOSE_HUE_RANGE[1]
        and LOSE_SAT_RANGE[0] <= s <= LOSE_SAT_RANGE[1]
        and LOSE_VAL_RANGE[0] <= v <= LOSE_VAL_RANGE[1]
    ):
        return "draw" if _is_draw_text(frame) else "lose"
    return None
