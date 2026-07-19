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

ランク確定判定(TRACKING_RANK)は、ピクセル差分が一旦安定しても「リーグ昇格」の
全画面演出がそのあとに続く場合があることが実データで判明している
(fixtures/videos/01_win_blue_2-1.mp4)。安定を検知した直後にすぐ
確定させず、league_change_grace_frames分だけ様子を見て、その間に演出が現れたら
演出が終わるまで待ち、再度安定するのを待ってから確定する(detection.league_change
参照)。なお、この全画面演出が出るのは**昇格時のみ**。降格時は全画面演出が出ず
ランクバッジ上に小さな「降格」ラベルが乗るだけでバッジ自体は隠れない
(fixtures/videos/10_RankDown_red.mp4で確認済み)ため、is_league_change_screen()
はIN_LEAGUE_CHANGE状態には遷移しない。この場合でも下記のGRACE中のバナー消灯
フォールバック・rank_recheck機構により正しく確定できる。

さらに、ゲージがフレーム間差分の閾値を下回る速度でごく緩やかに増減し
続けるケースが実データで確認されている(fixtures/videos/00_lose_red_2-3.mp4,
03_lose_blue_2-3.mp4)。StabilityMonitorは直前フレームとの差分しか見ないため、
1フレームごとの変化量が小さいまま数十フレームかけて値が動き続けても
「安定」の判定が崩れず、GRACE突入直後に読んだ値が古いまま確定されてしまう
(例: 00は真の最終値40.43より先に一時的な40.77を確定、03は降格後の
39台への遷移を見逃す)。これに対処するため、GRACE中もrank_recheck_interval_frames
おきにOCRを読み直し、値が変わっていれば候補を更新してgrace_counterを
リセットする(値そのものが変化し続けている間は確定を先延ばしにする)。

ゴール(得点・アシスト)はWATCHING中(試合結果バナーを待っている=まさに
プレイ中の期間)にのみ起こりうるため、_watch_for_banner()と並行して
毎フレームチェックする。検知したゴールは試合単位でメモリ上にバッファし
(_pending_goals)、_finalize()でMatchResult.goalsとして払い出す。
得点者が許可リスト(config.is_allowed_player)に無い場合に記録すら
しないという方針は、この状態機械ではなく永続化層(database.db.save_goal)
の責務とする(検知層はポリシーを持たず、見えたものをそのまま報告する)。

VS画面(マッチング完了、Issue #39)もWATCHING中にのみ起こりうる(結果バナーより
前、試合開始時点の一瞬だけ表示される)ため、ゴールと同様_watch_for_banner()と
並行してチェックする。banner判定と同じデバウンス(vs_screen_confirm_frames回
連続)で確定させ、確定した瞬間に1回だけdetection.vs_rank.read_vs_screen_ranks()
を呼び出してMatchResult.vs_mine_ranks/vs_opponent_ranksとして払い出す
(detection.vs_rank側のOCRは重い処理のため、CLAUDE.mdのサンプリング戦略どおり
毎フレームは呼ばない)。VS画面を見逃した試合ではどちらも空リストのままになる
(Issue #39で「VS画面検知は任意のエンリッチとし、見逃しても既存の結果バナー
起点フローは従来通り動作させる」と定めたとおり、必須の前提にしない)。

MatchResult/GoalEventのdetected_atはJST(timeutil.now_jst参照)で記録する。
また、結果バナー確定時・試合終了確定時にランクバッジのOCRが失敗した場合
(バッジがそもそも表示されていない場合と見た目上区別できない)は、後から
記録結果だけを見ても原因が分からないためログに残す。
"""

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum, auto
from logging import getLogger
from typing import Optional

import numpy as np

from nss_tracker.detection.banner import BannerResult, classify_banner
from nss_tracker.detection.goal import is_goal_event, read_assist_name, read_scorer_name
from nss_tracker.detection.league_change import is_league_change_screen
from nss_tracker.detection.matchmaking import is_vs_screen
from nss_tracker.detection.motion import StabilityMonitor
from nss_tracker.detection.rank_ocr import GAUGE_ROI_COMPACT, GAUGE_ROI_ENLARGED, RANK_ROI, read_precise_rank
from nss_tracker.detection.vs_rank import SlotRank, read_vs_screen_ranks
from nss_tracker.detection_config import get_detection_value
from nss_tracker.timeutil import now_jst

logger = getLogger("nss_tracker.state")

DEFAULT_BANNER_CONFIRM_FRAMES = 30
DEFAULT_BANNER_ABSENCE_CONFIRM_FRAMES = 30
DEFAULT_GOAL_CONFIRM_FRAMES = 30
DEFAULT_VS_SCREEN_CONFIRM_FRAMES = 30
# 実測(fixtures/videos/01_win_blue_2-1.mp4, 60fps):
# ランク数値が一旦静止してから昇格演出が始まるまで約270フレーム(4.5秒)の間があった
DEFAULT_LEAGUE_CHANGE_GRACE_FRAMES = 150
# GRACE中にゲージの緩やかな変化を見逃さないよう再読み取りする間隔(フレーム数)
DEFAULT_RANK_RECHECK_INTERVAL_FRAMES = 15
# 再読み取りで「値が変わった」とみなす閾値。ゲージ読み取り自体の測定誤差
# (tests/test_rank_ocr.pyでabs=0.02を許容)より大きく取り、ノイズで
# 猶予期間を無駄に延長し続けないようにする
# (config/detection.tomlの[match_state]で上書き可能)
RANK_RECHECK_CHANGE_TOLERANCE = get_detection_value("match_state", "RANK_RECHECK_CHANGE_TOLERANCE", 0.05)


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
class GoalEvent:
    scorer_name: Optional[str]
    assist_name: Optional[str]
    detected_at: datetime


@dataclass
class MatchResult:
    result: BannerResult
    rank_before: Optional[float]
    rank_after: Optional[float]
    league_changed: Optional[str]  # "up" / "down" / None
    detected_at: datetime
    goals: list[GoalEvent] = field(default_factory=list)
    # VS画面(マッチング完了)を見逃した試合ではどちらも空リストのまま
    # (Issue #39: VS画面検知は任意のエンリッチであり必須の前提にしない)
    vs_mine_ranks: list[SlotRank] = field(default_factory=list)
    vs_opponent_ranks: list[SlotRank] = field(default_factory=list)


class MatchStateMachine:
    """フレームを1枚ずつ渡して試合結果を検知する状態機械。"""

    def __init__(
        self,
        rank_roi: tuple[int, int, int, int] = RANK_ROI,
        banner_confirm_frames: int = DEFAULT_BANNER_CONFIRM_FRAMES,
        banner_absence_confirm_frames: int = DEFAULT_BANNER_ABSENCE_CONFIRM_FRAMES,
        league_change_grace_frames: int = DEFAULT_LEAGUE_CHANGE_GRACE_FRAMES,
        goal_confirm_frames: int = DEFAULT_GOAL_CONFIRM_FRAMES,
        rank_recheck_interval_frames: int = DEFAULT_RANK_RECHECK_INTERVAL_FRAMES,
        vs_screen_confirm_frames: int = DEFAULT_VS_SCREEN_CONFIRM_FRAMES,
        rank_stability_monitor: Optional[StabilityMonitor] = None,
    ) -> None:
        self._banner_confirm_frames = banner_confirm_frames
        self._banner_absence_confirm_frames = banner_absence_confirm_frames
        self._league_change_grace_frames = league_change_grace_frames
        self._goal_confirm_frames = goal_confirm_frames
        self._rank_recheck_interval_frames = rank_recheck_interval_frames
        self._vs_screen_confirm_frames = vs_screen_confirm_frames
        self._rank_monitor = rank_stability_monitor or StabilityMonitor(roi=rank_roi)

        self._state = _State.WATCHING
        self._rank_phase = _RankPhase.WAITING_STABLE
        self._grace_counter = 0
        self._banner_candidate: BannerResult = None
        self._banner_streak = 0
        self._absence_streak = 0
        self._pending_result: BannerResult = None
        # 帯番号(int)はleague_changed判定に、小数のランク値(float)はMatchResultの
        # 報告値に使う。ゲージの溜まり具合による僅かな変動をリーグ変動と
        # 誤検知しないよう、判定には必ず帯番号(整数)側を使うこと
        self._pending_rank_before_tier: Optional[int] = None
        self._pending_rank_before: Optional[float] = None
        self._grace_candidate_rank_tier: Optional[int] = None
        self._grace_candidate_rank: Optional[float] = None
        self._goal_streak = 0
        self._goal_recorded_this_event = False
        self._pending_goals: list[GoalEvent] = []
        self._vs_streak = 0
        self._vs_recorded_this_match = False
        self._pending_vs_mine_ranks: list[SlotRank] = []
        self._pending_vs_opponent_ranks: list[SlotRank] = []

    @property
    def current_state(self) -> str:
        """現在の状態("watching" / "tracking_rank" / "cooldown")。テスト等での観測用。"""
        return self._state.label

    def process_frame(self, frame: np.ndarray) -> Optional[MatchResult]:
        if self._state is _State.WATCHING:
            self._check_for_vs_screen(frame)
            self._check_for_goal(frame)
            return self._watch_for_banner(frame)
        if self._state is _State.TRACKING_RANK:
            return self._track_rank(frame)
        return self._watch_for_banner_absence(frame)

    def _check_for_vs_screen(self, frame: np.ndarray) -> None:
        if not is_vs_screen(frame):
            self._vs_streak = 0
            self._vs_recorded_this_match = False
            return

        self._vs_streak += 1
        if self._vs_streak >= self._vs_screen_confirm_frames and not self._vs_recorded_this_match:
            self._pending_vs_mine_ranks, self._pending_vs_opponent_ranks = read_vs_screen_ranks(frame)
            self._vs_recorded_this_match = True

    def _check_for_goal(self, frame: np.ndarray) -> None:
        if not is_goal_event(frame):
            self._goal_streak = 0
            self._goal_recorded_this_event = False
            return

        self._goal_streak += 1
        if self._goal_streak >= self._goal_confirm_frames and not self._goal_recorded_this_event:
            self._pending_goals.append(
                GoalEvent(
                    scorer_name=read_scorer_name(frame),
                    assist_name=read_assist_name(frame),
                    detected_at=now_jst(),
                )
            )
            self._goal_recorded_this_event = True

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
            # バナー確定直後 = ランク変動アニメーションが始まる前 = 常にコンパクト表示
            precise_result = read_precise_rank(frame, GAUGE_ROI_COMPACT)
            if precise_result is not None:
                self._pending_rank_before_tier, self._pending_rank_before = precise_result
            else:
                self._pending_rank_before_tier = None
                self._pending_rank_before = None
                logger.info(
                    "結果バナー確定時点でランクバッジを読み取れませんでした"
                    "(バッジ非表示、または読み取り失敗の可能性)"
                )
            self._banner_candidate = None
            self._banner_streak = 0
            self._rank_phase = _RankPhase.WAITING_STABLE
            self._grace_counter = 0
            self._grace_candidate_rank_tier = None
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
                # 上書きしないよう、Noneの場合は前回値を保持する。
                # TRACKING_RANK中(アニメーション開始後)は常に拡大表示
                precise_result = read_precise_rank(frame, GAUGE_ROI_ENLARGED)
                if precise_result is not None:
                    self._grace_candidate_rank_tier, self._grace_candidate_rank = precise_result
            return None

        # _RankPhase.GRACE: 安定はしたが、直後に昇格/降格演出が始まらないか
        # league_change_grace_frames分だけ様子を見る。バナー自体が消えたら
        # 演出は来ないと判断し、猶予期間を待たずに確定してよい
        if not is_stable:
            self._rank_phase = _RankPhase.WAITING_STABLE
            self._grace_counter = 0
            return None

        if classify_banner(frame) is None:
            self._fill_grace_candidate_if_missing(frame)
            return self._finalize(self._grace_candidate_rank_tier, self._grace_candidate_rank)

        self._grace_counter += 1

        # ピクセル差分では検知できない緩やかな変化を見逃さないよう、
        # 一定間隔で読み直して候補値が古くなっていないか確認する。
        # 変化していれば、まだ表示が動き続けている途中とみなし猶予期間をやり直す
        if self._grace_counter % self._rank_recheck_interval_frames == 0:
            precise_result = read_precise_rank(frame, GAUGE_ROI_ENLARGED)
            if precise_result is not None:
                tier, precise = precise_result
                candidate = self._grace_candidate_rank
                changed = tier != self._grace_candidate_rank_tier or (
                    candidate is None or abs(precise - candidate) > RANK_RECHECK_CHANGE_TOLERANCE
                )
                if changed:
                    self._grace_candidate_rank_tier, self._grace_candidate_rank = tier, precise
                    self._grace_counter = 0
                    return None

        if self._grace_counter < self._league_change_grace_frames:
            return None
        self._fill_grace_candidate_if_missing(frame)
        return self._finalize(self._grace_candidate_rank_tier, self._grace_candidate_rank)

    def _fill_grace_candidate_if_missing(self, frame: np.ndarray) -> None:
        """確定直前の時点で候補値が一度も読めていない場合のみ、最後にもう一度読み取りを試みる。

        実キャプチャ(FfmpegFrameReader)は処理が追いつかない間のフレームを間引くため、
        GRACE突入直後にたまたまサンプリングしたフレームがバッジの遷移中で
        読み取れず、そのまま候補が更新されないままバナーが消える(または
        猶予期間が満了する)ことがありうる。既に有効な候補があればここでは
        何もしない(古い正常値を上書きしない)。

        呼び出し元はいずれも_RankPhase.GRACE中(ランク変動アニメーション開始後)
        のため、常に拡大表示のROIを使う。
        """
        if self._grace_candidate_rank_tier is not None:
            return
        precise_result = read_precise_rank(frame, GAUGE_ROI_ENLARGED)
        if precise_result is not None:
            self._grace_candidate_rank_tier, self._grace_candidate_rank = precise_result

    def _finalize(self, rank_after_tier: Optional[int], rank_after: Optional[float]) -> MatchResult:
        if rank_after is None:
            logger.info(
                "試合終了時点でもランクバッジを読み取れませんでした"
                "(バッジ非表示、または読み取り失敗の可能性)"
            )
        # league_changedはゲージの溜まり具合を含まない帯番号(整数)同士で判定する。
        # 小数のランク値同士で比較すると、帯は変わっていないのにゲージが
        # 僅かに増減しただけで昇格/降格と誤判定してしまうため
        league_changed = None
        if self._pending_rank_before_tier is not None and rank_after_tier is not None:
            if rank_after_tier > self._pending_rank_before_tier:
                league_changed = "up"
            elif rank_after_tier < self._pending_rank_before_tier:
                league_changed = "down"

        match_result = MatchResult(
            result=self._pending_result,
            rank_before=self._pending_rank_before,
            rank_after=rank_after,
            league_changed=league_changed,
            detected_at=now_jst(),
            goals=self._pending_goals,
            vs_mine_ranks=self._pending_vs_mine_ranks,
            vs_opponent_ranks=self._pending_vs_opponent_ranks,
        )
        self._pending_result = None
        self._pending_rank_before = None
        self._pending_rank_before_tier = None
        self._pending_goals = []
        self._goal_streak = 0
        self._goal_recorded_this_event = False
        self._pending_vs_mine_ranks = []
        self._pending_vs_opponent_ranks = []
        self._vs_streak = 0
        self._vs_recorded_this_match = False
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
