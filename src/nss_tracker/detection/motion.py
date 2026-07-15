"""固定領域のピクセル差分による状態監視。

CLAUDE.md記載のサンプリング戦略のとおり、OCR等の重い処理を毎フレーム回すのではなく、
軽量なピクセル差分監視で「対象領域の変化が止まった(=値が確定した)瞬間」を検知し、
そのときだけ重い処理を1回発火させるために使う。

閾値は fixtures/screenshots/試合結果付き動画.mp4 を
scripts/inspect_video_timeline.py で実測して決定した。ランクバッジが確定して
静止している区間はフレーム間差分が概ね1未満(まれに3台のノイズ)で推移する一方、
バナー切り替わり・暗転などのシーン転換時は20〜60程度まで跳ね上がるため、
その中間に余裕を持たせて閾値を置いている。
"""

from typing import Optional

import numpy as np

DEFAULT_DIFF_THRESHOLD = 6.0
DEFAULT_STABLE_FRAMES_REQUIRED = 10


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
