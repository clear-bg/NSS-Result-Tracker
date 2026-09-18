"""試合レコードの、ランク以外の異常値検出(Issue #441)のテスト。

実画像に依存しない純粋な判定ロジックのため、合成データのみで検証する
(CLAUDE.mdのテスト方針どおり、CI・クローン直後でも常に実行できる)。
"""

from nss_tracker import match_warnings


def _codes(warnings) -> list[str]:
    return [w.rule_code for w in warnings]


def test_no_warning_when_all_slots_read():
    assert match_warnings.evaluate(mine_known_count=4, opponent_known_count=4) == []


def test_no_warning_for_one_unread_slot():
    """B〜E帯など1人だけ未識別(実データで8件、正常)。"""
    assert match_warnings.evaluate(mine_known_count=3, opponent_known_count=4) == []


def test_no_warning_when_fully_unread_on_both_sides():
    """ランクバッジが一切表示されない試合(専用部屋・ランクを賭けない試合、実データで20件、正常)。"""
    assert match_warnings.evaluate(mine_known_count=0, opponent_known_count=0) == []


def test_warns_when_mine_fully_unread_but_opponent_partially_read():
    """Issue #433のテニス誤検知そのもの(mine 4人未読・opponent 3人未読=計7人未読)。"""
    codes = _codes(match_warnings.evaluate(mine_known_count=0, opponent_known_count=1))

    assert codes == ["K"]


def test_warns_at_exactly_two_unread_slots():
    """known_total=6(未読2人)は境界値として警告対象に含まれる。"""
    codes = _codes(match_warnings.evaluate(mine_known_count=4, opponent_known_count=2))

    assert codes == ["K"]


def test_no_warning_at_exactly_one_unread_slot_boundary():
    """known_total=7(未読1人)は境界値として警告対象に含まれない。"""
    codes = _codes(match_warnings.evaluate(mine_known_count=4, opponent_known_count=3))

    assert codes == []


def test_warning_message_reports_per_side_unread_counts():
    warnings = match_warnings.evaluate(mine_known_count=0, opponent_known_count=1)

    assert "8人中7人" in warnings[0].message
    assert "自チーム4人" in warnings[0].message
    assert "相手チーム3人" in warnings[0].message


def test_acknowledged_rule_code_is_marked_but_still_returned():
    warnings = match_warnings.evaluate(
        mine_known_count=0, opponent_known_count=1, acknowledged_rule_codes=frozenset({"K"})
    )

    assert warnings[0].acknowledged is True
