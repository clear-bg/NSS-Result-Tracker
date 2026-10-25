"""過去の試合から次のランク値(rank_after)を推定するロジック(Issue #486)。

`/rank-entry`の推定値は、長らくGRACE中のゲージ読み取り(`rank_after_ocr`)を
そのまま使っていたが、実データ361試合で実際の`rank_after`との差が平均0.32
(中央値0.28)と大きく、▲▼の長押しで直す手間がかかっていた。

一方、ランクの増減量は「勝敗」と「両チームの合計ランク差」でほぼ決まる。
確定済みの試合(勝ち79・負け71試合、8人全員∞帯のもの)で直線を当てると、
勝ち: Δ ≈ 0.216 − 0.0188 × 差(r=−0.80)、負け: Δ ≈ −0.220 − 0.0129 × 差
(r=−0.73)となり、格上に勝つほど大きく上がり、格上に負けるほど下がり幅が
小さい。そこで、確定済みの試合から勝敗ごとに「合計ランク差→増減量」の直線を
当て、試合前のランクに足した値を推定値とする。`rank_after_ocr`の計算・保存は
従来どおり残す(置き換えない、ユーザーとの相談で決定)。

- 合計ランク差は∞/S/A帯を統一スケールに換算して求める(Issue #100と同じ換算)。
  8人の誰かが未読・B〜E帯の試合は差が過小に出るため使わず、勝敗ごとの中央値で
  代用する
- 引き分けはゲージが全く動かない(ゲーム仕様)。人数差のあった試合
  (`player_shortage`、Issue #462)も実測12試合すべてΔ=0だったため、どちらも
  試合前の値をそのまま返す
- ランク値は「帯番号+ゲージ」の連続値のため、帯をまたぐ試合でも足し算だけで
  おおむね合う(昇格した試合のΔも0.15〜0.35で普段と同じ範囲)。昇格演出/降格
  ラベルの検知結果(`league_change_label_detected`、Issue #374)は、**検知した
  ときにだけ**予測を帯の境目まで寄せる補正に使う。検知していないのに帯を
  またいだ予測は寄せない(実データで昇格/降格の見逃しが10件あり、寄せると
  その試合で大きく外すため)。また寄せる量が`MAX_LABEL_CORRECTION`を超える
  場合は検知の誤りとみなして寄せない(id=191で、44.03の勝ちに昇格を誤検知して
  いた例があった)

事前検証(その試合を学習から抜いて予測するleave-one-out): 合計ランク差の
分かる387試合で平均誤差0.032、分からない24試合で0.081。

この関数群はDBにもWebにも依存しない純粋な計算に留める(呼び出し元がDBから
値を集めて渡す。`rank_warnings.py`と同じ方針)。
"""

import math
import statistics
from dataclasses import dataclass
from typing import Iterable, Optional

# Issue #100: ∞/S/A帯を跨いで比較できるよう、帯内の数値を統一スケールに換算する
# オフセット。ユーザーから聞いたゲーム内の実際の仕様(∞は0〜49、Sは0〜9、Aは
# 最大29で下限不明。各帯とも帯内の上限に到達すると次の帯へ自動的に昇格する)に
# 基づく。この換算により A29(-11) < S0(-10) < S9(-1) < ∞0(0) のように、
# 昇格の順序と換算後の数値の大小関係が矛盾なく一致する連続したスケールになる。
# B~E帯は範囲が未検証のため対象外のまま(detection/vs_rank.pyのdocstring参照)
RANK_TIER_OFFSETS = {"∞": 0, "S": -10, "A": -40}

# 直線を当てるのに最低限必要な試合数。これ未満の勝敗は中央値で代用する
MIN_REGRESSION_SAMPLES = 10

# 昇格/降格の検知結果で予測を帯の境目へ寄せる量の上限。予測の誤差は実測で
# 9割が0.06以内・最大0.42のため、0.2を超えて寄せる必要がある場合は予測より
# 検知の方を疑う
MAX_LABEL_CORRECTION = 0.2

# 1チームの人数。合計ランク差はこの人数分すべて読めた試合でだけ求める
TEAM_SIZE = 4


def convert_rank_tier_to_unified_scale(tier_label: Optional[str], tier_value: Optional[int]) -> Optional[int]:
    """∞/S/A帯の帯内数値を、帯を跨いで比較できる統一スケールの数値に変換する。

    tier_labelが∞/S/Aのいずれでもない場合(B~E帯・読み取り失敗、rank_tier_label・
    rank_tierともNULL)、またはtier_valueがNoneの場合はNoneを返す(呼び出し側で
    「不明」として扱う)。
    """
    if tier_label not in RANK_TIER_OFFSETS or tier_value is None:
        return None
    return tier_value + RANK_TIER_OFFSETS[tier_label]


def team_rank_diff(
    mine: Iterable[tuple[Optional[str], Optional[int]]],
    opponent: Iterable[tuple[Optional[str], Optional[int]]],
) -> Optional[int]:
    """両チームの合計ランク差(自チーム−相手チーム、統一スケール)を返す。

    引数は各スロットの(rank_tier_label, rank_tier)。両チームとも4人全員を
    換算できた場合だけ値を返し、それ以外はNone(未読スロットがあると合計が
    過小に出るため。rank_warnings.pyのルールJと同じ扱い)。
    """
    totals = []
    for slots in (mine, opponent):
        values = [convert_rank_tier_to_unified_scale(label, value) for label, value in slots]
        if len(values) != TEAM_SIZE or any(v is None for v in values):
            return None
        totals.append(sum(values))
    return totals[0] - totals[1]


@dataclass(frozen=True)
class Sample:
    """学習に使う確定済みの1試合。"""

    result: str  # 'win' / 'lose'
    team_rank_diff: Optional[int]
    delta: float  # rank_after - rank_before


def _predict_delta(samples: list[Sample], result: str, diff: Optional[int]) -> Optional[float]:
    same_result = [s for s in samples if s.result == result]
    if not same_result:
        return None
    with_diff = [s for s in same_result if s.team_rank_diff is not None]
    if diff is None or len(with_diff) < MIN_REGRESSION_SAMPLES:
        return statistics.median(s.delta for s in same_result)
    mean_x = statistics.fmean(s.team_rank_diff for s in with_diff)
    mean_y = statistics.fmean(s.delta for s in with_diff)
    sxx = sum((s.team_rank_diff - mean_x) ** 2 for s in with_diff)
    if sxx == 0:
        return mean_y
    slope = sum((s.team_rank_diff - mean_x) * (s.delta - mean_y) for s in with_diff) / sxx
    return mean_y + slope * (diff - mean_x)


def _apply_league_change_label(rank_before: float, predicted: float, label: Optional[str]) -> float:
    tier_before = math.floor(rank_before)
    if label == "up" and predicted < tier_before + 1:
        corrected = float(tier_before + 1)
    elif label == "down" and predicted >= tier_before:
        corrected = tier_before - 0.01
    else:
        return predicted
    if abs(corrected - predicted) > MAX_LABEL_CORRECTION:
        return predicted
    return corrected


def estimate_rank_after(
    rank_before: Optional[float],
    result: str,
    team_rank_diff: Optional[int],
    league_change_label: Optional[str],
    player_shortage: bool,
    samples: list[Sample],
) -> Optional[float]:
    """試合後のランク値を推定する。推定できない場合はNoneを返す。

    rank_beforeが未確定(None)の場合、および同じ勝敗の学習データが1件も無い
    場合はNone。戻り値は小数第2位までに丸める。
    """
    if rank_before is None:
        return None
    if result == "draw" or player_shortage:
        return round(rank_before, 2)
    delta = _predict_delta(samples, result, team_rank_diff)
    if delta is None:
        return None
    predicted = _apply_league_change_label(rank_before, rank_before + delta, league_change_label)
    return round(predicted, 2)
