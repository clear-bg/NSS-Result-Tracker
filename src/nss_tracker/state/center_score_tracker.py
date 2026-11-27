"""ゴール直後の中央スコア表示から「その試合の今のスコア」を決める(Issue #493)。

`detection/center_score.py`が1フレームずつ返す読み取り結果を受け取り、
**同じ形が一定時間(stable_seconds)続いた値**だけを採用する。スコア表示は
「ゴール前のスコア→切り替わり→新しいスコア」の順に出るため、採用される値は
ゴール前→ゴール後の順に更新され、最後に残るのはゴール後のスコアになる。
切り替わり途中・消え際のフレームは一瞬しか続かないため採用されない
(fixtures/videosの実測で、止まった表示は30fpsで30〜62枚続き、外れるフレームは
いずれも1〜数枚がばらばらに出るだけだった)。

テンプレートに無い数字(5・6)は、形が一定時間続いたらOCRを頼む
(`observe()`が`OcrRequest`を返す)。OCRは重いため呼び出し側(state/match_state.py)が
別プロセスで行い、結果を`apply_ocr()`で返す。

フレームに依存しない純粋なロジックにしてあるため、合成した読み取り結果でテストできる。
"""

from dataclasses import dataclass
from typing import Optional

import numpy as np

from nss_tracker.detection.center_score import DigitReading, mask_similarity

Score = tuple[int, int]

# 同じ形が続いているとみなすマスク同士の一致度。止まった表示同士はほぼ1.0になる
_SAME_SHAPE_MIN = 0.90


@dataclass(frozen=True)
class OcrRequest:
    """テンプレートで読めなかった側をOCRで読んでほしい、という依頼。

    known_left/known_rightはテンプレートで読めた側の値(読めなかった側はNone)。
    observed_atは採用の順序づけに使う(apply_ocr()にそのまま返すこと)。
    """

    known_left: Optional[int]
    known_right: Optional[int]
    observed_at: float


@dataclass
class _Candidate:
    left: Optional[int]
    right: Optional[int]
    left_mask: np.ndarray
    right_mask: np.ndarray
    started_at: float
    last_seen_at: float
    handled: bool = False


class CenterScoreTracker:
    def __init__(self, stable_seconds: float, gap_tolerance_seconds: float) -> None:
        self._stable_seconds = stable_seconds
        # 数字が見えない・切り替わり途中のフレームを何秒まで挟んでも同じ表示が
        # 続いているとみなすか(1枚抜けただけで最初から数え直さないため)
        self._gap_tolerance_seconds = gap_tolerance_seconds
        self._candidate: Optional[_Candidate] = None
        self._adopted: Optional[Score] = None
        self._adopted_at: Optional[float] = None
        self._first_adopted: Optional[Score] = None
        self._first_adopted_at: Optional[float] = None

    @property
    def adopted(self) -> Optional[Score]:
        """これまでに採用した最新のスコア(自チーム, 相手チーム)。無ければNone。"""
        return self._adopted

    @property
    def first_adopted(self) -> Optional[Score]:
        """最初に採用したスコア。表示は「ゴール前のスコア」から始まるため、
        読めていればゴール前のスコアになる(妥当性チェックの基準に使う)。
        """
        return self._first_adopted

    def reset(self) -> None:
        self._candidate = None
        self._adopted = None
        self._adopted_at = None
        self._first_adopted = None
        self._first_adopted_at = None

    def observe(
        self, reading: Optional[tuple[DigitReading, DigitReading]], now: float
    ) -> Optional[OcrRequest]:
        """1フレーム分の読み取り結果を渡す。OCRが必要になった瞬間だけOcrRequestを返す。"""
        candidate = self._candidate
        if reading is None:
            if candidate is not None and now - candidate.last_seen_at > self._gap_tolerance_seconds:
                self._candidate = None
            return None

        left, right = reading
        if candidate is None or not self._continues(candidate, left, right, now):
            self._candidate = _Candidate(
                left=left.digit,
                right=right.digit,
                left_mask=left.mask,
                right_mask=right.mask,
                started_at=now,
                last_seen_at=now,
            )
            return None

        candidate.last_seen_at = now
        if candidate.handled or now - candidate.started_at < self._stable_seconds:
            return None
        candidate.handled = True
        if candidate.left is not None and candidate.right is not None:
            self._adopt((candidate.left, candidate.right), candidate.started_at)
            return None
        return OcrRequest(known_left=candidate.left, known_right=candidate.right, observed_at=candidate.started_at)

    def apply_ocr(self, request: OcrRequest, ocr_left: Optional[int], ocr_right: Optional[int]) -> Optional[Score]:
        """OCRの結果を反映する。両側の値が揃えば採用し、そのスコアを返す(揃わなければNone)。"""
        left = request.known_left if request.known_left is not None else ocr_left
        right = request.known_right if request.known_right is not None else ocr_right
        if left is None or right is None:
            return None
        self._adopt((left, right), request.observed_at)
        return (left, right)

    def _continues(self, candidate: _Candidate, left: DigitReading, right: DigitReading, now: float) -> bool:
        if now - candidate.last_seen_at > self._gap_tolerance_seconds:
            return False
        if left.digit != candidate.left or right.digit != candidate.right:
            return False
        return (
            mask_similarity(left.mask, candidate.left_mask) >= _SAME_SHAPE_MIN
            and mask_similarity(right.mask, candidate.right_mask) >= _SAME_SHAPE_MIN
        )

    def _adopt(self, score: Score, observed_at: float) -> None:
        # OCRの結果は後から届くため、それより新しい表示を既に採用していれば上書きしない
        if self._first_adopted_at is None or observed_at < self._first_adopted_at:
            self._first_adopted = score
            self._first_adopted_at = observed_at
        if self._adopted_at is not None and observed_at < self._adopted_at:
            return
        self._adopted = score
        self._adopted_at = observed_at


def is_plausible_transition(previous: Score, new: Score) -> bool:
    """1回のゴールでありうるスコアの変化か(加算方式による妥当性チェック、Issue #493)。

    ゲーム仕様: 通常は得点した側に1点。2点差以上のときはゴールデンボールになり、
    得点すると2点入る(負けている側なら同点、勝っている側なら4点差=ノックアウト)。
    ゴールデンボール中にどちらの側が得点しても2点になる。
    """
    gained_left = new[0] - previous[0]
    gained_right = new[1] - previous[1]
    if gained_left < 0 or gained_right < 0:
        return False
    if (gained_left > 0) == (gained_right > 0):
        return False
    gained = gained_left + gained_right
    golden_ball = abs(previous[0] - previous[1]) >= 2
    return gained == (2 if golden_ball else 1)
