"""記録漏れ試合の手動追加(Issue #489)の、入力値の検証と挿入位置の判定のテスト。"""

from datetime import datetime

import pytest

from nss_tracker import manual_match
from nss_tracker.manual_match import ManualMatchError, MatchRef, VsSlotEntry
from nss_tracker.timeutil import JST

NOW = datetime(2026, 11, 22, 22, 0, tzinfo=JST)


def _form(**overrides) -> dict:
    form = {
        "date": "2026-11-22",
        "hour": "21",
        "minute": "04",
        "second": "",
        "result": "win",
        "rank_tier": "43",
        "rank_fraction": "51",
    }
    for side in manual_match.VS_SIDES:
        for index in range(manual_match.VS_SLOT_COUNT):
            form[f"vs_{side}_{index}_label"] = "∞"
            form[f"vs_{side}_{index}_value"] = ""
    form.update(overrides)
    return form


def _at(hour: int, minute: int, second: int = 0) -> datetime:
    return datetime(2026, 11, 22, hour, minute, second, tzinfo=JST)


# ---- parse_form ----


def test_parse_form_reads_required_fields_and_treats_blank_second_as_zero():
    entry, errors = manual_match.parse_form(_form(), NOW)
    assert errors == {}
    assert entry.detected_at == _at(21, 4, 0)
    assert entry.result == "win"
    assert entry.rank_after == 43.51
    assert entry.player_shortage is False


def test_parse_form_reads_optional_second():
    entry, _ = manual_match.parse_form(_form(second="37"), NOW)
    assert entry.detected_at == _at(21, 4, 37)


@pytest.mark.parametrize(
    ("fraction", "expected"),
    [("5", 43.50), ("05", 43.05), ("0", 43.00), ("99", 43.99)],
)
def test_parse_form_reads_fraction_as_digits_after_decimal_point(fraction, expected):
    """/rank-entry(Issue #457)と同じく、"5"は.50、"05"は.05として読む。"""
    entry, _ = manual_match.parse_form(_form(rank_fraction=fraction), NOW)
    assert entry.rank_after == expected


@pytest.mark.parametrize(
    ("overrides", "field"),
    [
        ({"date": ""}, "detected_at"),
        ({"hour": "24"}, "detected_at"),
        ({"minute": ""}, "detected_at"),
        ({"minute": "4.5"}, "detected_at"),
        ({"second": "60"}, "detected_at"),
        ({"hour": "22", "minute": "01"}, "detected_at"),  # 未来の時刻
        ({"result": ""}, "result"),
        ({"result": "WIN"}, "result"),
        ({"rank_tier": ""}, "rank_after"),
        ({"rank_tier": "-1"}, "rank_after"),
        ({"rank_fraction": "123"}, "rank_after"),
        ({"rank_fraction": ""}, "rank_after"),
        ({"vs_mine_1_value": "50"}, "vs"),  # ∞帯は0〜49
        ({"vs_opponent_0_label": "S", "vs_opponent_0_value": "10"}, "vs"),  # S帯は0〜9
        ({"vs_opponent_0_label": "B", "vs_opponent_0_value": "3"}, "vs"),
    ],
)
def test_parse_form_rejects_invalid_values_per_field(overrides, field):
    entry, errors = manual_match.parse_form(_form(**overrides), NOW)
    assert entry is None
    assert field in errors


def test_parse_form_treats_blank_value_and_unknown_label_as_unknown():
    entry, _ = manual_match.parse_form(
        _form(
            vs_mine_0_value="43",
            vs_mine_1_label="S",
            vs_mine_1_value="7",
            vs_mine_2_label="unknown",
            vs_mine_2_value="30",
        ),
        NOW,
    )
    assert entry.vs_mine == (VsSlotEntry("∞", 43), VsSlotEntry("S", 7), VsSlotEntry(), VsSlotEntry())
    assert entry.has_vs_ranks


def test_parse_form_all_blank_vs_means_no_vs_ranks():
    """全員∞で初期選択していても、数値が空欄なら確認していない値として保存しない。"""
    entry, _ = manual_match.parse_form(_form(), NOW)
    assert not entry.has_vs_ranks


def test_parse_form_all_unknown_checkbox_ignores_vs_fields():
    entry, errors = manual_match.parse_form(
        _form(vs_all_unknown="on", vs_mine_0_value="43", vs_mine_1_value="999"), NOW
    )
    assert errors == {}
    assert not entry.has_vs_ranks


def test_parse_form_reads_player_shortage_checkbox():
    entry, _ = manual_match.parse_form(_form(player_shortage="on"), NOW)
    assert entry.player_shortage is True


# ---- decide_insert_position ----


def test_insert_before_first_match_of_a_session():
    matches = [MatchRef(1, _at(19, 0), 1), MatchRef(2, _at(19, 10), 1), MatchRef(3, _at(21, 9), 2)]
    position = manual_match.decide_insert_position(_at(21, 4), matches, current_session_id=2)
    assert position.new_id == 3
    assert position.session_id == 2
    assert position.previous.id == 2
    assert position.following.id == 3


def test_insert_into_the_middle_of_a_session_is_rejected():
    matches = [MatchRef(1, _at(21, 0), 2), MatchRef(2, _at(21, 9), 2)]
    with pytest.raises(ManualMatchError) as exc:
        manual_match.decide_insert_position(_at(21, 4), matches, current_session_id=2)
    assert exc.value.field == "detected_at"
    assert "配信の途中" in str(exc.value)


def test_insert_before_the_very_first_match_of_the_db():
    matches = [MatchRef(1, _at(21, 9), 1)]
    position = manual_match.decide_insert_position(_at(21, 4), matches, current_session_id=1)
    assert position.new_id == 1
    assert position.previous is None


def test_append_when_current_session_has_no_matches_yet():
    matches = [MatchRef(1, _at(19, 0), 1)]
    position = manual_match.decide_insert_position(_at(21, 4), matches, current_session_id=2)
    assert position.new_id is None
    assert position.session_id == 2
    assert position.previous.id == 1


def test_append_after_a_match_of_the_current_session_is_rejected():
    matches = [MatchRef(1, _at(21, 0), 2)]
    with pytest.raises(ManualMatchError):
        manual_match.decide_insert_position(_at(21, 4), matches, current_session_id=2)


def test_same_time_as_an_existing_match_is_rejected():
    matches = [MatchRef(1, _at(19, 0), 1), MatchRef(2, _at(21, 4), 2)]
    with pytest.raises(ManualMatchError) as exc:
        manual_match.decide_insert_position(_at(21, 4), matches, current_session_id=2)
    assert "同じ時刻" in str(exc.value)


def test_out_of_order_detected_at_is_rejected():
    matches = [MatchRef(1, _at(19, 0), 1), MatchRef(2, _at(21, 9), 2), MatchRef(3, _at(21, 0), 2)]
    with pytest.raises(ManualMatchError) as exc:
        manual_match.decide_insert_position(_at(21, 4), matches, current_session_id=2)
    assert "並び" in str(exc.value)


# ---- check_rank_change ----


@pytest.mark.parametrize(
    ("result", "before", "after"),
    [
        ("win", 43.27, 43.10),  # 勝ちなのに減少(ルールA)
        ("lose", 43.27, 43.40),  # 負けなのに増加(ルールA)
        ("draw", 43.27, 43.28),  # 引き分けなのに変化(ルールA)
        ("win", 43.27, 45.10),  # 2帯以上の変動(ルールD)
    ],
)
def test_check_rank_change_rejects_impossible_combinations(result, before, after):
    assert manual_match.check_rank_change(result, before, after)


@pytest.mark.parametrize(
    ("result", "before", "after"),
    [
        ("win", 43.27, 43.51),
        ("win", 43.90, 44.10),  # 昇格
        ("lose", 43.10, 42.90),  # 降格
        ("lose", 43.27, 43.27),  # Δ=0は正常(合計ランク差・人数差)
        ("draw", 43.27, 43.27),
    ],
)
def test_check_rank_change_accepts_possible_combinations(result, before, after):
    assert manual_match.check_rank_change(result, before, after) == []


# ---- past_session_started_at(Issue #491) ----


def test_past_session_start_is_five_minutes_before_the_match_when_appending_before_startup():
    position = manual_match.InsertPosition(session_id=2, previous=MatchRef(1, _at(19, 0), 1), following=None)
    started = manual_match.past_session_started_at(_at(21, 4), position, session_started_at=_at(23, 30))
    assert started == _at(20, 59)


def test_past_session_start_is_kept_when_the_match_is_after_startup():
    """起動はしていたが仮想カメラの開始が遅れたケース。今回の配信の試合なので開始時刻は変えない。"""
    position = manual_match.InsertPosition(session_id=2, previous=MatchRef(1, _at(19, 0), 1), following=None)
    assert manual_match.past_session_started_at(_at(21, 4), position, session_started_at=_at(21, 0)) is None


def test_past_session_start_is_kept_when_inserting_before_an_existing_match():
    position = manual_match.InsertPosition(
        session_id=2, previous=MatchRef(1, _at(19, 0), 1), following=MatchRef(2, _at(21, 9), 2)
    )
    assert manual_match.past_session_started_at(_at(21, 4), position, session_started_at=_at(21, 8)) is None
