"""ランク入力値の矛盾検出(Issue #407)のテスト。

実画像に依存しない純粋な判定ロジックのため、合成データのみで検証する
(CLAUDE.mdのテスト方針どおり、CI・クローン直後でも常に実行できる)。
"""

import pytest

from nss_tracker import rank_warnings
from nss_tracker.rank_warnings import TeamRankTotals


def _codes(warnings) -> list[str]:
    return [w.rule_code for w in warnings]


def _evaluate(**overrides):
    params = {
        "result": "win",
        "rank_before": 42.20,
        "rank_after": 42.40,
        "league_change_label_detected": None,
    }
    params.update(overrides)
    return rank_warnings.evaluate(**params)


def test_no_warning_for_normal_win():
    assert _evaluate(result="win", rank_before=42.20, rank_after=42.40) == []


def test_no_warning_for_normal_lose():
    assert _evaluate(result="lose", rank_before=42.40, rank_after=42.18) == []


@pytest.mark.parametrize(
    "rank_before, rank_after",
    [
        (42.16, 41.98),  # 負けて帯が1つ下がる(ゲージが0を割り込んで前の帯へ)
        (41.98, 42.28),  # 勝って帯が1つ上がる
    ],
)
def test_no_warning_for_league_change_with_matching_label(rank_before, rank_after):
    """帯が動いた試合でも、対応する昇格演出/降格ラベルを検知していれば警告しない。"""
    label = "up" if rank_after > rank_before else "down"
    result = "win" if label == "up" else "lose"
    assert _evaluate(result=result, rank_before=rank_before, rank_after=rank_after,
                     league_change_label_detected=label) == []


# --- ルールA: 勝敗と増減の符号 ---


def test_rule_a_win_with_decrease():
    warnings = _evaluate(result="win", rank_before=42.90, rank_after=42.75)
    assert _codes(warnings) == ["A"]
    assert "勝ちですがランクが下がっています" in warnings[0].message


def test_rule_a_lose_with_increase():
    warnings = _evaluate(result="lose", rank_before=42.33, rank_after=42.34)
    assert _codes(warnings) == ["A"]
    assert "負けですがランクが上がっています" in warnings[0].message


def test_rule_a_draw_with_change():
    warnings = _evaluate(result="draw", rank_before=42.20, rank_after=42.30)
    assert _codes(warnings) == ["A"]


@pytest.mark.parametrize("result", ["win", "lose"])
def test_rule_a_ignores_zero_delta(result):
    """合計ランク差が大きい試合・味方が抜けた試合ではΔ=0が正常に起こるため、
    Δ=0そのものは警告対象にしない(rank_warnings.pyのモジュールdocstring参照)。
    """
    assert _evaluate(result=result, rank_before=42.28, rank_after=42.28) == []


def test_rule_a_draw_with_zero_delta_is_normal():
    assert _evaluate(result="draw", rank_before=42.28, rank_after=42.28) == []


# --- ルールC: 帯変化と昇格/降格ラベルの矛盾 ---


def test_rule_c_tier_moved_without_label():
    """Issue #407の発端になった実データ(id=15、42.28を41.28と入力)と同じ形。"""
    warnings = _evaluate(result="lose", rank_before=42.40, rank_after=41.28, league_change_label_detected=None)
    assert "C" in _codes(warnings)
    assert "降格ラベルを検知していません" in [w.message for w in warnings if w.rule_code == "C"][0]


def test_rule_c_label_detected_but_tier_unchanged():
    warnings = _evaluate(result="lose", rank_before=42.40, rank_after=42.20, league_change_label_detected="down")
    assert _codes(warnings) == ["C"]
    assert "帯が42のまま変わっていません" in warnings[0].message


def test_rule_c_label_direction_mismatch():
    warnings = _evaluate(result="win", rank_before=41.98, rank_after=42.28, league_change_label_detected="down")
    assert "C" in _codes(warnings)
    assert "逆に動いています" in [w.message for w in warnings if w.rule_code == "C"][0]


# --- ルールD/E: 帯変化そのものの矛盾 ---


def test_rule_d_two_or_more_tiers():
    warnings = _evaluate(result="lose", rank_before=42.20, rank_after=40.20, league_change_label_detected="down")
    assert "D" in _codes(warnings)


def test_rule_e_win_with_demotion():
    warnings = _evaluate(result="win", rank_before=42.20, rank_after=41.90, league_change_label_detected="down")
    assert "E" in _codes(warnings)


def test_rule_e_lose_with_promotion():
    warnings = _evaluate(result="lose", rank_before=41.90, rank_after=42.20, league_change_label_detected="up")
    assert "E" in _codes(warnings)


# --- ルールI: 次の試合のVS画面の帯との照合 ---


def test_rule_i_next_match_vs_tier_mismatch():
    warnings = _evaluate(result="lose", rank_before=42.40, rank_after=41.28, next_match_vs_tier=42)
    assert "I" in _codes(warnings)


def test_rule_i_no_warning_when_tier_matches():
    assert _evaluate(result="win", rank_before=42.20, rank_after=42.40, next_match_vs_tier=42) == []


def test_rule_i_skipped_when_next_match_vs_tier_unknown():
    """次の試合がまだ無い、またはVS画面で自分のバッジを読めていない場合はスキップする。"""
    assert _evaluate(result="win", rank_before=42.20, rank_after=42.40, next_match_vs_tier=None) == []


# --- ルールJ: 合計ランク差が小さいのにΔ=0 ---


def _totals(mine: int, opponent: int, mine_known: int = 4, opponent_known: int = 4) -> TeamRankTotals:
    return TeamRankTotals(
        mine=mine, opponent=opponent, mine_known_count=mine_known, opponent_known_count=opponent_known
    )


def test_rule_j_zero_delta_with_evenly_matched_teams():
    """実データのid=11(合計44対44、Δ=0)と同じ形。"""
    warnings = _evaluate(result="lose", rank_before=42.20, rank_after=42.20, team_rank_totals=_totals(44, 44))
    assert _codes(warnings) == ["J"]


def test_rule_j_no_warning_when_team_gap_is_large():
    """実データのid=17(合計36対56、差-20、Δ=0)は正常なので警告しない。"""
    assert _evaluate(result="lose", rank_before=42.28, rank_after=42.28, team_rank_totals=_totals(36, 56)) == []


def test_rule_j_boundary_is_exclusive():
    """差が閾値ちょうど(20)なら正常、1つ内側(19)なら警告する。"""
    threshold = rank_warnings.TEAM_RANK_DIFF_THRESHOLD
    assert _evaluate(result="lose", rank_before=42.28, rank_after=42.28,
                     team_rank_totals=_totals(0, threshold)) == []
    assert _codes(_evaluate(result="lose", rank_before=42.28, rank_after=42.28,
                            team_rank_totals=_totals(0, threshold - 1))) == ["J"]


@pytest.mark.parametrize("mine_known, opponent_known", [(3, 4), (4, 3)])
def test_rule_j_skipped_when_a_slot_is_unread(mine_known, opponent_known):
    """未読スロットがあると合計が過小に出るため、判定自体をスキップする。"""
    totals = _totals(23, -16, mine_known=mine_known, opponent_known=opponent_known)
    assert _evaluate(result="win", rank_before=42.28, rank_after=42.28, team_rank_totals=totals) == []


def test_rule_j_skipped_when_delta_is_not_zero():
    assert _evaluate(result="lose", rank_before=42.40, rank_after=42.18, team_rank_totals=_totals(44, 44)) == []


# --- 共通の振る舞い ---


def test_returns_empty_when_rank_is_missing():
    """ランクを賭けていない試合・まだ手動入力していない試合は判定しない。"""
    assert _evaluate(rank_before=None, rank_after=42.40) == []
    assert _evaluate(rank_before=42.20, rank_after=None) == []


def test_acknowledged_rules_are_flagged_but_still_evaluated():
    warnings = _evaluate(result="win", rank_before=42.90, rank_after=42.75,
                         acknowledged_rule_codes=frozenset({"A"}))
    assert _codes(warnings) == ["A"]
    assert warnings[0].acknowledged is True


def test_warnings_are_sorted_by_rule_code_order():
    warnings = _evaluate(result="win", rank_before=42.20, rank_after=40.10, next_match_vs_tier=42)
    codes = _codes(warnings)
    assert codes == sorted(codes, key=rank_warnings.RULE_CODES.index)
    assert set(codes) == {"A", "C", "D", "E", "I"}
