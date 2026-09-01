"""固定領域のピクセル差分による状態監視。

CLAUDE.md記載のサンプリング戦略のとおり、OCR等の重い処理を毎フレーム回すのではなく、
軽量なピクセル差分監視で「対象領域の変化が止まった(=値が確定した)瞬間」を検知し、
そのときだけ重い処理を1回発火させるために使う。

閾値は fixtures/screenshots/試合結果付き動画.mp4 を
scripts/inspect_video_timeline.py で実測して決定した。ランクバッジが確定して
静止している区間はフレーム間差分が概ね1未満(まれに3台のノイズ)で推移する一方、
バナー切り替わり・暗転などのシーン転換時は20〜60程度まで跳ね上がるため、
その中間に余裕を持たせて閾値を置いている。

Issue #209: is_full_blackout()は上記のピクセル差分(2フレーム間の相対変化)とは
異なり、フレーム単体の絶対的な明るさで「画面全体が暗転しているか」を判定する。
試合結果〜ランク確定演出が完全に終わった直後、マッチング画面に戻る前に
必ず一瞬(実測0.3〜0.5秒程度)全画面が真っ黒になる区間があり(CLAUDE.md記載の
「4. 暗転」)、これを試合確定のフォールバック信号として使うために追加した
(呼び出し側はstate/match_state.py参照)。

fixtures/videos全24本(結果バナーを含まない試合中クリップ4本を除く)を
実測した結果、暗転区間は輝度平均0.40〜0.43・標準偏差8.0〜8.2で安定しており、
それ以外の区間で最も暗いフレームでも輝度平均30以上(fixtures/screenshots全35枚も
最低20以上)だったため、明確なギャップがあった。マッチング開始直後の暗転
(fixtures/videos/22・23・32番)や、対戦相手が集まらずゲームが再起動する際の
暗転など、ランク確定と無関係なタイミングでも同じ現象が起きることを確認済みだが、
このモジュールでは検知のみを提供し、いつ確定に使うか(ランクゲージ変更終了時のみ)は
呼び出し側の責務とする。

Issue #387: frame_brightness_stats()は1920x1080全画面をcv2.cvtColor(BGR2GRAY)+
mean()/std()で処理しており、実測8ms前後かかる重い処理だった。state/match_state.pyの
暗転待ち区間では判定用に加えログ表示用にも呼ばれ、1フレームにつき実質2回計算する
形になっており、これが支配的なコストになって検知ループの実効レートが60fps→
約28fpsまで半減、結果として0.55秒しかない暗転や1.9秒程度しか表示されない
「勝ち」結果バナーを取りこぼす実害が出た(#383/#387参照)。

「画面全体が一様に暗いか」という大域的な性質の判定であるため、間引きサンプリングで
劣化しないはずと考え、実フレーム1200枚(暗転区間を含む)で検証した:

    実装                    ms/frame   最大mean差  最大std差  暗転判定の不一致
    current(全画面)           10.11        -           -          -
    1/4間引き(stride=4)        1.07       0.34        0.46       0/1200
    1/8間引き(stride=8)        0.19       0.50        0.62       0/1200

いずれも暗転判定(mean<=15 and std<=15)の結果は1200フレーム全てで一致した。
安全マージン(暗転時mean 0.40〜0.43 vs 非暗転時最低30前後、閾値15.0)に対して
差分0.5前後は無視できる大きさだが、多少余裕を持たせてBRIGHTNESS_STATS_STRIDE=4
(縦横1/4に間引き=画素数1/16)をデフォルトにした。間引き後は1フレームにつき2回
呼んでも合計2ms程度で済むため、呼び出し構造(1回はログ用・1回は判定用)自体は
変えていない。
"""

from typing import Iterable, Optional, TypeVar

import cv2
import numpy as np

from nss_tracker.detection_config import get_detection_value

T = TypeVar("T")

# config/detection.tomlの[motion]で上書き可能
DEFAULT_DIFF_THRESHOLD = get_detection_value("motion", "DEFAULT_DIFF_THRESHOLD", 6.0)
DEFAULT_STABLE_FRAMES_REQUIRED = get_detection_value("motion", "DEFAULT_STABLE_FRAMES_REQUIRED", 10)

# Issue #209: 実測(モジュールdocstring参照)では暗転区間が輝度平均0.40〜0.43・
# 標準偏差8.0〜8.2、それ以外の最も暗いフレームでも輝度平均30以上だったため、
# 十分マージンを取った値にしている
FULL_BLACKOUT_MAX_MEAN_BRIGHTNESS = get_detection_value("motion", "FULL_BLACKOUT_MAX_MEAN_BRIGHTNESS", 15.0)
FULL_BLACKOUT_MAX_BRIGHTNESS_STD = get_detection_value("motion", "FULL_BLACKOUT_MAX_BRIGHTNESS_STD", 15.0)

# Issue #387: 縦横をこの値で間引いてから輝度統計を計算する(モジュールdocstring参照)。
# 1だと間引きなし(従来どおり全画素)。
BRIGHTNESS_STATS_STRIDE = get_detection_value("motion", "BRIGHTNESS_STATS_STRIDE", 4)


def frame_brightness_stats(frame: np.ndarray) -> tuple[float, float]:
    """フレーム全体のグレースケール輝度平均・標準偏差を返す。

    Issue #383: is_full_blackout()の閾値判定そのものに使う値だが、暗転を
    取りこぼした際の原因究明(「閾値にどれだけ近づけていたか」を暗転待ち中の
    DEBUGログに残す用途、state/match_state.py参照)のため単体で呼べるように
    切り出した。

    Issue #387: BRIGHTNESS_STATS_STRIDEで間引いてから計算する(モジュール
    docstring参照、実測で判定結果への影響が無いことを確認済み)。呼び出し側
    (state/match_state.py)はログ用にこの関数を呼んだ後、判定用に改めて
    is_full_blackout(frame)を呼んでおり同じ計算を2回行う形になっているが、
    間引き後は2回呼んでも合計2ms程度で60fps予算に対して十分小さいため、
    呼び出し構造は変えていない(is_full_blackout()はテストからモジュール
    直下の名前でmonkeypatchされ暗転タイミングを厳密制御する使われ方をして
    いるため、判定をこの関数の戻り値経由に置き換えるとテストの制御が効かなく
    なる)。
    """
    sampled = frame[::BRIGHTNESS_STATS_STRIDE, ::BRIGHTNESS_STATS_STRIDE]
    gray = cv2.cvtColor(sampled, cv2.COLOR_BGR2GRAY)
    return float(gray.mean()), float(gray.std())


def is_full_blackout(frame: np.ndarray) -> bool:
    """画面全体が暗転しているかを判定する(Issue #209)。

    輝度平均だけでなく標準偏差も低いことを合わせて確認し、単に暗いだけの
    シーン(夜間演出等)ではなく一様な黒であることを確認する(モジュール
    docstring参照)。
    """
    mean, std = frame_brightness_stats(frame)
    return bool(mean <= FULL_BLACKOUT_MAX_MEAN_BRIGHTNESS and std <= FULL_BLACKOUT_MAX_BRIGHTNESS_STD)


def region_diff(prev_frame: np.ndarray, curr_frame: np.ndarray, roi: tuple[int, int, int, int]) -> float:
    """2フレーム間の指定領域における平均絶対輝度差分を返す。"""
    x1, y1, x2, y2 = roi
    prev_crop = prev_frame[y1:y2, x1:x2].astype(np.int16)
    curr_crop = curr_frame[y1:y2, x1:x2].astype(np.int16)
    return float(np.abs(prev_crop - curr_crop).mean())


class StabilityMonitor:
    """指定領域を継続的に監視し、変化が止まった状態を検知する。

    frame単位で update() を呼び出す。差分が diff_threshold 以下の状態が
    stable_frames_required 回連続すると is_stable が True になる。
    """

    def __init__(
        self,
        roi: tuple[int, int, int, int],
        diff_threshold: float = DEFAULT_DIFF_THRESHOLD,
        stable_frames_required: int = DEFAULT_STABLE_FRAMES_REQUIRED,
    ) -> None:
        self.roi = roi
        self.diff_threshold = diff_threshold
        self.stable_frames_required = stable_frames_required
        self._prev_frame: Optional[np.ndarray] = None
        self._stable_streak = 0

    @property
    def is_stable(self) -> bool:
        return self._stable_streak >= self.stable_frames_required

    def update(self, frame: np.ndarray) -> bool:
        """1フレーム分の状態を更新し、更新後の is_stable を返す。"""
        if self._prev_frame is not None:
            diff = region_diff(self._prev_frame, frame, self.roi)
            if diff <= self.diff_threshold:
                self._stable_streak += 1
            else:
                self._stable_streak = 0
        self._prev_frame = frame
        return self.is_stable

    def reset(self) -> None:
        """次の監視対象(次の試合など)に切り替える際に内部状態をリセットする。"""
        self._prev_frame = None
        self._stable_streak = 0


def find_confirmed_value(values: Iterable[Optional[T]], min_run_length: int) -> Optional[T]:
    """同じ値がmin_run_length回以上連続して現れたら、その値を「確定」として返す。

    banner.classify_banner のように単発では誤検知しうる判定を、複数フレームに
    わたる連続性で確認(デバウンス)するために使う。Noneは連続数に数えない
    (どれだけ続いてもリセットのみ行う)。条件を満たす値が現れなければNoneを返す。
    """
    current_value: Optional[T] = None
    current_run = 0
    for value in values:
        if value is not None and value == current_value:
            current_run += 1
        elif value is not None:
            current_value = value
            current_run = 1
        else:
            current_value = None
            current_run = 0
        if current_value is not None and current_run >= min_run_length:
            return current_value
    return None
