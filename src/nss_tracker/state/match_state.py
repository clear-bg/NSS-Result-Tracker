"""試合の状態遷移を管理する状態機械。

CLAUDE.md記載の「試合後の状態遷移」(結果バナー表示→ランク変動アニメーション→
ランク確定→暗転→マッチング画面)を、banner/rank_ocr/motion/league_changeの
各検知結果をつないで管理する。フレームを1枚ずつ process_frame() に渡すと、
試合の記録が完了した瞬間だけ MatchResult を返す。

「暗転」を明示的な輝度閾値で検知するのではなく、結果バナーが一定時間
確実に消えたこと(banner=Noneがbanner_absence_confirm_frames回連続)を
もって次の試合への再武装(WATCHING状態への復帰)とみなす。暗転〜マッチング
画面のどこかで必ずバナーが消えるため、この方が輝度閾値を新たに調整するより
頑健(検証済みの banner判定・デバウンスの仕組みをそのまま再利用できる)。

banner判定は単体だと試合中のゴール演出等で一瞬誤検知しうるため
(detection.banner参照)、ここでも banner_confirm_frames 回連続した判定
のみを採用する。デフォルト値は30fps想定で約1秒。60fps等より高フレーム
レートで使う場合は、呼び出し側でfpsに応じて調整すること。

ランク確定判定(TRACKING_RANK)は、ピクセル差分が一旦安定しても
「リーグ昇格/降格」の全画面演出がそのあとに続く場合があることが実データで
判明している(fixtures/videos/01_win_blue_2-1.mp4)。安定を検知
した直後にすぐ確定させず、league_change_grace_frames分だけ様子を見て、
その間に演出が現れたら演出が終わるまで待ち、再度安定するのを待ってから
確定する(detection.league_change参照)。
"""

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum, auto
from typing import Optional

import numpy as np

from nss_tracker.detection.banner import BannerResult, classify_banner
from nss_tracker.detection.league_change import is_league_change_screen
from nss_tracker.detection.motion import StabilityMonitor
from nss_tracker.detection.rank_ocr import RANK_ROI, read_rank

DEFAULT_BANNER_CONFIRM_FRAMES = 30
DEFAULT_BANNER_ABSENCE_CONFIRM_FRAMES = 30
# 実測(fixtures/videos/01_win_blue_2-1.mp4, 60fps):
# ランク数値が一旦静止してから昇格演出が始まるまで約270フレーム(4.5秒)の間があった
DEFAULT_LEAGUE_CHANGE_GRACE_FRAMES = 150


class _State(Enum):
    WATCHING = auto()
    TRACKING_RANK = auto()
    COOLDOWN = auto()

    @property
    def label(self) -> str:
        return self.name.lower()


class _RankPhase(Enum):
    WAITING_STABLE = auto()
    GRACE = auto()
    IN_LEAGUE_CHANGE = auto()


@dataclass
class MatchResult:
    result: BannerResult
    rank_before: Optional[int]
    rank_after: Optional[int]
    league_changed: Optional[str]  # "up" / "down" / None
    detected_at: datetime


class MatchStateMachine:
    """フレームを1枚ずつ渡して試合結果を検知する状態機械。"""

    def __init__(
        self,
        rank_roi: tuple[int, int, int, int] = RANK_ROI,
        banner_confirm_frames: int = DEFAULT_BANNER_CONFIRM_FRAMES,
        banner_absence_confirm_frames: int = DEFAULT_BANNER_ABSENCE_CONFIRM_FRAMES,
        league_change_grace_frames: int = DEFAULT_LEAGUE_CHANGE_GRACE_FRAMES,
        rank_stability_monitor: Optional[StabilityMonitor] = None,
    ) -> None:
        self._banner_confirm_frames = banner_confirm_frames
        self._banner_absence_confirm_frames = banner_absence_confirm_frames
        self._league_change_grace_frames = league_change_grace_frames
        self._rank_monitor = rank_stability_monitor or StabilityMonitor(roi=rank_roi)

        self._state = _State.WATCHING
        self._rank_phase = _RankPhase.WAITING_STABLE
        self._grace_counter = 0
        self._banner_candidate: BannerResult = None
        self._banner_streak = 0
        self._absence_streak = 0
        self._pending_result: BannerResult = None
        self._pending_rank_before: Optional[int] = None
        self._grace_candidate_rank: Optional[int] = None

    @property
    def current_state(self) -> str:
        """現在の状態("watching" / "tracking_rank" / "cooldown")。テスト等での観測用。"""
        return self._state.label

    def process_frame(self, frame: np.ndarray) -> Optional[MatchResult]:
        if self._state is _State.WATCHING:
            return self._watch_for_banner(frame)
        if self._state is _State.TRACKING_RANK:
            return self._track_rank(frame)
        return self._watch_for_banner_absence(frame)

    def _watch_for_banner(self, frame: np.ndarray) -> Optional[MatchResult]:
        result = classify_banner(frame)
        if result is not None and result == self._banner_candidate:
            self._banner_streak += 1
        elif result is not None:
            self._banner_candidate = result
            self._banner_streak = 1
        else:
            self._banner_candidate = None
            self._banner_streak = 0

        if self._banner_streak >= self._banner_confirm_frames:
            self._pending_result = self._banner_candidate
            self._pending_rank_before = read_rank(frame)
            self._banner_candidate = None
            self._banner_streak = 0
            self._rank_phase = _RankPhase.WAITING_STABLE
            self._grace_counter = 0
            self._grace_candidate_rank = None
            self._rank_monitor.reset()
            self._rank_monitor.update(frame)
            self._state = _State.TRACKING_RANK
        return None

    def _track_rank(self, frame: np.ndarray) -> Optional[MatchResult]:
        if is_league_change_screen(frame):
            self._rank_phase = _RankPhase.IN_LEAGUE_CHANGE
            self._grace_counter = 0
            return None

        if self._rank_phase is _RankPhase.IN_LEAGUE_CHANGE:
            # 演出が終わった直後。新しいランク値が安定するのを最初から待ち直す
            self._rank_monitor.reset()
            self._rank_monitor.update(frame)
            self._rank_phase = _RankPhase.WAITING_STABLE
            return None

        was_stable = self._rank_monitor.is_stable
        is_stable = self._rank_monitor.update(frame)

        if self._rank_phase is _RankPhase.WAITING_STABLE:
            if is_stable and not was_stable:
                self._rank_phase = _RankPhase.GRACE
                self._grace_counter = 0
                # 安定した瞬間(まだ画面が遷移し始めていない良いフレーム)でOCRしておく。
                # 猶予期間の最後まで待つとバナー自体が消えかけの不安定なフレームに
                # なりOCRが失敗しうるため、値はここで確定させて使い回す。
                # 微小なノイズで安定が何度か途切れて再試行することがあるが、
                # 直近の試行がたまたま失敗しても直前までの正常な読み取り結果を
                # 上書きしないよう、Noneの場合は前回値を保持する
                candidate = read_rank(frame)
                if candidate is not None:
                    self._grace_candidate_rank = candidate
            return None

        # _RankPhase.GRACE: 安定はしたが、直後に昇格/降格演出が始まらないか
        # league_change_grace_frames分だけ様子を見る。バナー自体が消えたら
        # 演出は来ないと判断し、猶予期間を待たずに確定してよい
        if not is_stable:
            self._rank_phase = _RankPhase.WAITING_STABLE
            self._grace_counter = 0
            return None

        if classify_banner(frame) is None:
            return self._finalize(self._grace_candidate_rank)

        self._grace_counter += 1
        if self._grace_counter < self._league_change_grace_frames:
            return None
        return self._finalize(self._grace_candidate_rank)

    def _finalize(self, rank_after: Optional[int]) -> MatchResult:
        league_changed = None
        if self._pending_rank_before is not None and rank_after is not None:
            if rank_after > self._pending_rank_before:
                league_changed = "up"
            elif rank_after < self._pending_rank_before:
                league_changed = "down"

        match_result = MatchResult(
            result=self._pending_result,
            rank_before=self._pending_rank_before,
            rank_after=rank_after,
            league_changed=league_changed,
            detected_at=datetime.now(timezone.utc),
        )
        self._pending_result = None
        self._pending_rank_before = None
        self._absence_streak = 0
        self._state = _State.COOLDOWN
        return match_result

    def _watch_for_banner_absence(self, frame: np.ndarray) -> Optional[MatchResult]:
        if classify_banner(frame) is None:
            self._absence_streak += 1
        else:
            self._absence_streak = 0

        if self._absence_streak >= self._banner_absence_confirm_frames:
            self._absence_streak = 0
            self._state = _State.WATCHING
        return None
