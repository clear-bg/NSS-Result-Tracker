import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from nss_tracker.database import db
from nss_tracker.detection.vs_rank import SlotRank
from nss_tracker.web import server as server_module
from nss_tracker.state.match_state import MatchResult
from nss_tracker.timeutil import now_jst
from nss_tracker.web.runner import start_web_server_thread
from nss_tracker.web.server import (
    _BOX_PLOT_TITLE,
    _OVERLAY_REFRESH_INTERVAL_MS,
    _OVERLAY_WIDGET_LABELS,
    _RANK_GRAPH_LEFT_PADDING,
    _RANK_GRAPH_MARGIN_LEFT,
    _RANK_GRAPH_MARGIN_RIGHT,
    _RANK_GRAPH_TITLE,
    _RANK_GRAPH_VIEWBOX_HEIGHT,
    _RANK_GRAPH_Y_HALF_STEP_LABEL_MAX_RANGE,
    _RANK_GRAPH_VIEWBOX_WIDTH,
    _VS_RANK_COMPARISON_REFRESH_INTERVAL_MS,
    _aggregate_goal_stats,
    _build_match_log_badges,
    _compute_box_stats,
    _convert_rank_tier_to_unified_scale,
    _fetch_goal_stats,
    _fetch_rank_graph_summary,
    _fetch_winrate,
    _format_vs_rank_value,
    _overlay_widget_links,
    _percentile,
    _rank_delta_axis_max,
    _rank_graph_x_axis_max,
    _rank_graph_x_tick_step,
    _rank_graph_x_tick_values,
    _rank_graph_y_bounds,
    _rank_graph_y_tick_step,
    _render_rank_delta_box_plot_svg,
    _render_rank_graph_svg,
    _summarize_vs_slot_ranks,
    create_app,
)


def _save_match_result(conn, match: MatchResult, session_id=None) -> int:
    """Issue #306: db.save_match_result()はrank_after/league_changedを書かなく
    なった(手動入力専用、rank_after_ocrへ移動)ため、このモジュールの既存テストが
    前提としていた「MatchResultに渡した値がそのままrank_after/league_changedに
    入る」という挙動をテスト側で再現するヘルパー。save_manual_rank_after()は
    帯番号比較でleague_changedを再計算してしまうため使わず、match側の値を
    そのままUPDATEする(意図的に不整合な値を使うテストの意図を変えないため)。
    """
    match_id = db.save_match_result(conn, match, session_id=session_id)
    if match.rank_after is not None:
        conn.execute(
            "UPDATE matches SET rank_after = ?, league_changed = ? WHERE id = ?",
            (match.rank_after, match.league_changed, match_id),
        )
        conn.commit()
    return match_id


def _extract_rank_entry_clips(html: str) -> list[dict]:
    """Issue #307: /rank-entryの左側(試合情報・入力フォーム)は動画選択と連動して
    JS側で描画するようになったため、レンダリング結果のHTML文字列ではなく、
    ページに埋め込まれたJSONデータ(サーバー側の実際の出力)を検証する。
    """
    match = re.search(
        r'<script id="rank-entry-clips-data" type="application/json">(.*?)</script>', html, re.DOTALL
    )
    assert match is not None, "rank-entry-clips-dataが見つかりません"
    return json.loads(match.group(1))


def test_health_endpoint(tmp_path: Path):
    app = create_app(tmp_path / "test.db")
    client = TestClient(app)

    response = client.get("/api/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_matches_count_reflects_db_contents(tmp_path: Path):
    db_path = tmp_path / "test.db"
    conn = db.connect(db_path)
    for result in ["win", "win", "lose", "draw"]:
        _save_match_result(
            conn,
            MatchResult(result=result, rank_before=1, rank_after=1, league_changed=None, detected_at=now_jst()),
        )
    conn.close()

    client = TestClient(create_app(db_path))

    response = client.get("/api/matches/count")

    assert response.status_code == 200
    assert response.json() == {"total": 4, "win": 2, "lose": 1, "draw": 1}


def test_matches_count_with_no_matches(tmp_path: Path):
    db_path = tmp_path / "test.db"
    db.connect(db_path).close()

    client = TestClient(create_app(db_path))

    response = client.get("/api/matches/count")

    assert response.json() == {"total": 0, "win": 0, "lose": 0, "draw": 0}


def test_index_page_shows_match_counts(tmp_path: Path):
    db_path = tmp_path / "test.db"
    conn = db.connect(db_path)
    for result in ["win", "win", "lose"]:
        _save_match_result(
            conn,
            MatchResult(result=result, rank_before=1, rank_after=1, league_changed=None, detected_at=now_jst()),
        )
    conn.close()

    client = TestClient(create_app(db_path))

    response = client.get("/")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert "3" in response.text
    assert "win: 2" in response.text
    assert "lose: 1" in response.text
    assert "draw: 0" in response.text


def test_fetch_winrate_with_no_sessions_returns_empty_session_counts(tmp_path: Path):
    db_path = tmp_path / "test.db"
    conn = db.connect(db_path)
    _save_match_result(
        conn,
        MatchResult(result="win", rank_before=1, rank_after=1, league_changed=None, detected_at=now_jst()),
    )
    conn.close()

    result = _fetch_winrate(db_path)

    assert result == {
        "session": {"total": 0, "win": 0, "lose": 0, "draw": 0},
        "cumulative": {"total": 1, "win": 1, "lose": 0, "draw": 0},
    }


def test_fetch_winrate_splits_session_and_cumulative_counts(tmp_path: Path):
    db_path = tmp_path / "test.db"
    conn = db.connect(db_path)
    first_session_id = db.create_session(conn)
    _save_match_result(
        conn,
        MatchResult(result="lose", rank_before=1, rank_after=1, league_changed=None, detected_at=now_jst()),
        session_id=first_session_id,
    )
    second_session_id = db.create_session(conn)
    for result in ["win", "win"]:
        _save_match_result(
            conn,
            MatchResult(result=result, rank_before=1, rank_after=1, league_changed=None, detected_at=now_jst()),
            session_id=second_session_id,
        )
    conn.close()

    result = _fetch_winrate(db_path)

    assert result == {
        "session": {"total": 2, "win": 2, "lose": 0, "draw": 0},
        "cumulative": {"total": 3, "win": 2, "lose": 1, "draw": 0},
    }


def test_rank_history_returns_recent_matches_oldest_first(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("RANK_GRAPH_MATCH_LIMIT", "all")
    db_path = tmp_path / "test.db"
    conn = db.connect(db_path)
    for i, league_changed in enumerate([None, "up", None]):
        _save_match_result(
            conn,
            MatchResult(
                result="win",
                rank_before=i,
                rank_after=i + 1,
                league_changed=league_changed,
                detected_at=now_jst(),
            ),
        )
    conn.close()

    client = TestClient(create_app(db_path))

    response = client.get("/api/rank-history")

    assert response.status_code == 200
    assert response.json() == {
        "matches": [
            {"rank_after": 1.0, "league_changed": None},
            {"rank_after": 2.0, "league_changed": "up"},
            {"rank_after": 3.0, "league_changed": None},
        ]
    }


def test_rank_history_skips_matches_without_rank_after(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("RANK_GRAPH_MATCH_LIMIT", "all")
    db_path = tmp_path / "test.db"
    conn = db.connect(db_path)
    _save_match_result(
        conn,
        MatchResult(result="draw", rank_before=None, rank_after=None, league_changed=None, detected_at=now_jst()),
    )
    conn.close()

    client = TestClient(create_app(db_path))

    response = client.get("/api/rank-history")

    assert response.json() == {"matches": []}


def test_rank_graph_y_bounds_extends_beyond_actual_min_max():
    # ユーザーの例: 実データが30.3〜32.6なら下限30・上限33
    assert _rank_graph_y_bounds(30.3, 32.6) == (30, 33)


def test_rank_graph_y_bounds_widens_when_min_max_land_exactly_on_integers():
    # 実データがちょうど整数の場合、そのまま軸の下限/上限にすると端に接してしまうため
    # さらに1つ広げる
    assert _rank_graph_y_bounds(30.0, 33.0) == (29, 34)


def test_rank_graph_y_bounds_single_flat_value():
    assert _rank_graph_y_bounds(42, 42) == (41, 43)


def test_rank_graph_y_tick_step_stays_one_for_narrow_range():
    """通常運用の狭い範囲(数〜十数)では、これまで通り間隔1のまま変わらないことを確認する。"""
    assert _rank_graph_y_tick_step(2) == 1
    assert _rank_graph_y_tick_step(15) == 1


def test_rank_graph_y_tick_step_widens_for_outlier_range():
    """Issue #123: OCR誤読等の外れ値で軸の範囲が広がっても、きりの良い間隔に広がることを確認する。"""
    assert _rank_graph_y_tick_step(411) == 50
    assert _rank_graph_y_tick_step(0) == 1


def test_render_rank_graph_svg_height_is_always_fixed():
    """Issue #336: Issue #281で導入したaxis_rangeに応じた220px〜350pxの可変ロジックを
    撤廃し、グラフ本体の縦幅は常に_RANK_GRAPH_VIEWBOX_HEIGHT(350px)固定になったことを
    確認する(狭い範囲・外れ値で範囲が広い場合のいずれも同じ高さになる)。
    """
    narrow_history = _continuous_history([10, 11, 12])
    wide_history = _continuous_history([2, 411])

    def _extract_height(svg: str) -> int:
        match = re.search(r'viewBox="0 0 \d+ (\d+)"', svg)
        assert match is not None
        return int(match.group(1))

    assert _extract_height(_render_rank_graph_svg(narrow_history)) == _RANK_GRAPH_VIEWBOX_HEIGHT
    assert _extract_height(_render_rank_graph_svg(wide_history)) == _RANK_GRAPH_VIEWBOX_HEIGHT


def test_rank_history_returns_all_matches_when_limit_env_is_all(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("RANK_GRAPH_MATCH_LIMIT", "all")
    db_path = tmp_path / "test.db"
    conn = db.connect(db_path)
    for i in range(35):
        _save_match_result(
            conn,
            MatchResult(result="win", rank_before=i, rank_after=i + 1, league_changed=None, detected_at=now_jst()),
        )
    conn.close()

    client = TestClient(create_app(db_path))

    response = client.get("/api/rank-history")

    assert len(response.json()["matches"]) == 35


def test_rank_history_respects_limit_env_value(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("RANK_GRAPH_MATCH_LIMIT", "3")
    db_path = tmp_path / "test.db"
    conn = db.connect(db_path)
    for i in range(5):
        _save_match_result(
            conn,
            MatchResult(result="win", rank_before=i, rank_after=i + 1, league_changed=None, detected_at=now_jst()),
        )
    conn.close()

    client = TestClient(create_app(db_path))

    response = client.get("/api/rank-history")

    matches = response.json()["matches"]
    assert [m["rank_after"] for m in matches] == [3.0, 4.0, 5.0]


def _continuous_history(values: list[float], league_changed: list = None) -> list[dict]:
    """_render_rank_graph_svgに渡す単純なhistoryのテストデータ。"""
    league_changed_values = league_changed if league_changed is not None else [None] * len(values)
    return [
        {
            "rank_after": value,
            "league_changed": league_changed_values[i],
        }
        for i, value in enumerate(values)
    ]


def test_render_rank_graph_svg_with_no_data_shows_empty_message():
    svg = _render_rank_graph_svg([])

    assert "データがありません" in svg
    assert "<polyline" not in svg


def test_render_rank_graph_svg_always_shows_title():
    history = _continuous_history([10, 20, 15])

    assert f'class="rank-graph-title">{_RANK_GRAPH_TITLE}<' in _render_rank_graph_svg(history)
    assert f'class="rank-graph-title">{_RANK_GRAPH_TITLE}<' in _render_rank_graph_svg([])


def test_render_rank_graph_svg_always_shows_panel_behind_other_elements():
    """Issue #113: 配信画面での視認性対策の半透明パネルは、他の要素より先(=一番背面)に描画する。"""
    history = _continuous_history([10, 20, 15])

    svg = _render_rank_graph_svg(history)

    assert 'class="rank-graph-panel"' in svg
    assert svg.index('class="rank-graph-panel"') < svg.index('class="rank-graph-title"')
    assert 'class="rank-graph-panel"' in _render_rank_graph_svg([])


def test_render_rank_graph_svg_with_single_point_does_not_divide_by_zero():
    svg = _render_rank_graph_svg(_continuous_history([10]))

    assert svg.count("<circle") == 1


def test_render_rank_graph_svg_with_flat_values_does_not_divide_by_zero():
    history = _continuous_history([5, 5, 5])

    svg = _render_rank_graph_svg(history)

    assert svg.count("<circle") == 3


def test_render_rank_graph_svg_points_are_always_white_regardless_of_league_changed():
    """ユーザーとの相談で、昇格/降格による点の色分けはしない(全て白)方針にした。"""
    history = _continuous_history([1, 2, 1], league_changed=[None, "up", "down"])

    svg = _render_rank_graph_svg(history)

    assert svg.count('class="rank-graph-point"') == 3
    assert "rank-graph-point-up" not in svg
    assert "rank-graph-point-down" not in svg


def test_render_rank_graph_svg_draws_frame_and_axis_ticks():
    # 7試合(5の倍数ではない)にして、横軸が次の倍数(10)まで拡張されることを
    # 確認できるようにする(Issue #331、5試合ちょうどだと拡張されなくなったため)
    history = _continuous_history([10, 20, 15, 25, 12, 18, 22])

    svg = _render_rank_graph_svg(history)

    assert 'class="rank-graph-frame"' in svg
    # 縦軸: 最小値10・最大値25を含む整数の目盛りラベルが表示されること
    assert ">10<" in svg
    assert ">25<" in svg
    # 横軸: 1試合目と、7試合を上回るまで拡張した10試合目分の目盛りが
    # 表示されること(_rank_graph_x_axis_max(7, 5) == 10)
    assert ">1<" in svg
    assert ">5<" in svg
    assert ">10<" in svg


def test_render_rank_graph_svg_outlier_value_does_not_flood_y_axis_ticks():
    """Issue #123: OCR誤読等で1件だけ極端な外れ値が混ざり縦軸の範囲が広がっても、
    目盛りラベルの本数が一定数以下に収まることを確認する(修正前は412本の目盛りが
    密集して描画が崩れていた)。
    """
    history = _continuous_history([40, 41, 40, 2, 41, 40, 411, 40])

    svg = _render_rank_graph_svg(history)

    assert svg.count('class="rank-graph-tick-label"') <= 25


def test_render_rank_graph_svg_draws_vertical_gridlines_at_x_ticks():
    """横軸の目盛り位置(1, 5, 10試合目)にも縦軸と同様の薄いグリッド線を引く。"""
    history = _continuous_history([10, 20, 15, 25, 12, 18, 22])

    svg = _render_rank_graph_svg(history)

    # 縦軸のグリッド線(横向き)+横軸のグリッド線(縦向き、1試合目・5試合目・拡張後の10試合目分)
    assert svg.count('class="rank-graph-gridline"') >= 3 + 3


def test_render_rank_graph_svg_adds_half_step_minor_gridlines_when_step_is_one():
    """Issue #146: 目盛り間隔が1(通常運用)かつ、Issue #336の
    _RANK_GRAPH_Y_HALF_STEP_LABEL_MAX_RANGEより範囲が広いとき、整数目盛りの間に
    0.5刻みの補助グリッド線を追加する。ラベル・目盛り線自体は増やさない。
    """
    history = _continuous_history([10, 12])

    svg = _render_rank_graph_svg(history)

    axis_min, axis_max = _rank_graph_y_bounds(10, 12)
    assert _rank_graph_y_tick_step(axis_max - axis_min) == 1
    assert axis_max - axis_min > _RANK_GRAPH_Y_HALF_STEP_LABEL_MAX_RANGE
    assert svg.count('class="rank-graph-gridline-minor"') == axis_max - axis_min
    # 0.5刻みの補助線に対応するラベル(小数)は追加しない
    assert ".5<" not in svg


def test_render_rank_graph_svg_shows_half_step_labels_for_narrow_range():
    """Issue #336: 縦軸の範囲が_RANK_GRAPH_Y_HALF_STEP_LABEL_MAX_RANGE以下と狭いときは、
    0.5刻みも(補助線ではなく)ラベル・短い目盛り線付きの通常の目盛りとして表示する。
    """
    history = _continuous_history([10, 11])

    svg = _render_rank_graph_svg(history)

    axis_min, axis_max = _rank_graph_y_bounds(10, 11)
    assert axis_max - axis_min <= _RANK_GRAPH_Y_HALF_STEP_LABEL_MAX_RANGE
    assert 'class="rank-graph-gridline-minor"' not in svg
    for tick_value in range(axis_min, axis_max):
        assert f'>{tick_value + 0.5:g}<' in svg


def test_render_rank_graph_svg_omits_minor_gridlines_when_step_widens():
    """Issue #146: 外れ値で目盛り間隔が1以外に広がった場合、0.5刻みの補助線は追加しない
    (ユーザーとの相談で決定、issue #146のコメント参照)。
    """
    history = _continuous_history([40, 41, 40, 2, 41, 40, 411, 40])

    svg = _render_rank_graph_svg(history)

    axis_min, axis_max = _rank_graph_y_bounds(2, 411)
    assert _rank_graph_y_tick_step(axis_max - axis_min) != 1
    assert 'class="rank-graph-gridline-minor"' not in svg


def test_render_rank_graph_svg_last_point_stops_short_of_right_edge():
    """一番右の点は、実際の試合数を上回るまで拡張した横軸(x_axis_max)を使うことで
    枠の右端に接しないようにする(縦軸のbounds拡張と同じ考え方、ユーザーとの相談で決定)。
    """
    history = _continuous_history([10, 20, 30])

    svg = _render_rank_graph_svg(history)

    plot_left = _RANK_GRAPH_MARGIN_LEFT
    plot_right = _RANK_GRAPH_VIEWBOX_WIDTH - _RANK_GRAPH_MARGIN_RIGHT
    points_left = plot_left + _RANK_GRAPH_LEFT_PADDING
    x_axis_max_index = _rank_graph_x_axis_max(len(history), _rank_graph_x_tick_step(len(history))) - 1
    expected_right_x = points_left + (plot_right - points_left) * (len(history) - 1) / x_axis_max_index

    assert expected_right_x < plot_right
    assert f'cx="{expected_right_x:.1f}"' in svg


def test_render_rank_graph_svg_first_point_stops_short_of_left_edge():
    """一番左の点が枠の左端に接しないよう、左側にRANK_GRAPH_LEFT_PADDING分の余白を空ける。"""
    history = _continuous_history([10, 20, 30])

    svg = _render_rank_graph_svg(history)

    expected_left_x = _RANK_GRAPH_MARGIN_LEFT + _RANK_GRAPH_LEFT_PADDING
    assert f'cx="{expected_left_x:.1f}"' in svg


def test_render_rank_graph_svg_without_summary_shows_no_stat_tiles():
    history = _continuous_history([10, 20, 15])

    svg = _render_rank_graph_svg(history)

    assert 'class="rank-graph-stat-value"' not in svg
    assert 'class="rank-graph-stat-label"' not in svg


def test_render_rank_graph_svg_with_summary_shows_three_stat_tiles():
    """Issue #313: 現在のランク・最高ランク・配信開始時のランクの3タイルを表示する(案B)。

    3つ目のタイルは配信開始時のランク自体を常に表示し、その右に増減値を
    小さく添える(ユーザーフィードバックにより、増減値だけを大きく出す案から変更)。
    """
    history = _continuous_history([10, 20, 15])
    summary = {"current_rank": 15.0, "max_rank": 22.5, "session_start_rank": 12.0, "delta": 3.0}

    svg = _render_rank_graph_svg(history, summary)

    assert svg.count('class="rank-graph-stat-label"') == 3
    assert ">現在のランク<" in svg
    assert ">最高ランク<" in svg
    assert ">配信開始時<" in svg
    # Issue #(今回のフィードバック): 整数ぴったりの値でも小数第2位まで0埋めで表示する
    assert 'class="rank-graph-stat-value">15.00<' in svg
    assert 'class="rank-graph-stat-value">22.50<' in svg
    assert 'class="rank-graph-stat-value">12.00<' in svg
    assert 'class="rank-graph-stat-delta rank-graph-stat-delta-up" dx="6">+3.00<' in svg


def test_render_rank_graph_svg_summary_delta_down_uses_down_color():
    summary = {"current_rank": 10.0, "max_rank": 20.0, "session_start_rank": 12.5, "delta": -2.5}

    svg = _render_rank_graph_svg(_continuous_history([10]), summary)

    assert 'class="rank-graph-stat-value">12.50<' in svg
    assert 'class="rank-graph-stat-delta rank-graph-stat-delta-down" dx="6">-2.50<' in svg


def test_render_rank_graph_svg_summary_delta_zero_uses_neutral_color():
    """今のセッションでまだランクを賭けた試合が無い場合の代わりの表示(ユーザー確認済み)。"""
    summary = {"current_rank": 10.0, "max_rank": 20.0, "session_start_rank": 10.0, "delta": 0.0}

    svg = _render_rank_graph_svg(_continuous_history([10]), summary)

    assert 'class="rank-graph-stat-delta rank-graph-stat-delta-neutral" dx="6">+0.00<' in svg
    assert "rank-graph-stat-delta-up" not in svg
    assert "rank-graph-stat-delta-down" not in svg


def test_render_rank_graph_svg_summary_with_no_max_rank_shows_dash():
    summary = {"current_rank": 10.0, "max_rank": None, "session_start_rank": 10.0, "delta": 0.0}

    svg = _render_rank_graph_svg(_continuous_history([10]), summary)

    assert ">-<" in svg


def test_render_rank_graph_svg_with_no_data_but_summary_still_shows_stat_tiles():
    """historyが空でもsummary(DB全体からの集計)は独立して出せるため、統計欄だけ表示する。"""
    summary = {"current_rank": 10.0, "max_rank": 20.0, "session_start_rank": 10.0, "delta": 0.0}

    svg = _render_rank_graph_svg([], summary)

    assert "データがありません" in svg
    assert 'class="rank-graph-stat-label"' in svg


def test_fetch_rank_graph_summary_returns_none_when_no_confirmed_rank(tmp_path: Path):
    db_path = tmp_path / "test.db"
    db.connect(db_path).close()

    assert _fetch_rank_graph_summary(db_path) is None


def test_fetch_rank_graph_summary_uses_session_start_rank_before(tmp_path: Path):
    db_path = tmp_path / "test.db"
    conn = db.connect(db_path)
    old_session_id = db.create_session(conn)
    _save_match_result(
        conn,
        MatchResult(result="win", rank_before=10, rank_after=11, league_changed=None, detected_at=now_jst()),
        session_id=old_session_id,
    )
    current_session_id = db.create_session(conn)
    _save_match_result(
        conn,
        MatchResult(result="win", rank_before=11, rank_after=13, league_changed=None, detected_at=now_jst()),
        session_id=current_session_id,
    )
    _save_match_result(
        conn,
        MatchResult(result="lose", rank_before=13, rank_after=12.5, league_changed=None, detected_at=now_jst()),
        session_id=current_session_id,
    )
    conn.close()

    summary = _fetch_rank_graph_summary(db_path)

    assert summary == {
        "current_rank": 12.5,
        "max_rank": 13.0,
        "session_start_rank": 11.0,
        "delta": 1.5,
    }


def test_fetch_rank_graph_summary_falls_back_to_zero_delta_when_session_has_no_ranked_match(tmp_path: Path):
    """今の配信セッションでまだランクを賭けた試合が無い場合、現在のランクをそのまま
    配信開始時のランクとみなし、増減を0として扱う(ユーザー確認済み)。
    """
    db_path = tmp_path / "test.db"
    conn = db.connect(db_path)
    old_session_id = db.create_session(conn)
    _save_match_result(
        conn,
        MatchResult(result="win", rank_before=10, rank_after=11, league_changed=None, detected_at=now_jst()),
        session_id=old_session_id,
    )
    db.create_session(conn)  # 今のセッション、まだランクを賭けた試合が無い
    conn.close()

    summary = _fetch_rank_graph_summary(db_path)

    assert summary == {
        "current_rank": 11.0,
        "max_rank": 11.0,
        "session_start_rank": 11.0,
        "delta": 0.0,
    }


def test_overlay_rank_graph_page_includes_summary_stats(tmp_path: Path):
    db_path = tmp_path / "test.db"
    conn = db.connect(db_path)
    session_id = db.create_session(conn)
    _save_match_result(
        conn,
        MatchResult(result="win", rank_before=10, rank_after=12, league_changed=None, detected_at=now_jst()),
        session_id=session_id,
    )
    conn.close()

    client = TestClient(create_app(db_path))

    response = client.get("/overlay/rank-graph")

    assert response.status_code == 200
    assert "現在のランク" in response.text
    assert "最高ランク" in response.text
    assert "配信開始時" in response.text


def test_rank_graph_x_axis_max_extends_beyond_uneven_match_count():
    # ユーザーの例: 23試合なら25まで表示する
    assert _rank_graph_x_axis_max(23, 5) == 25


def test_rank_graph_x_axis_max_does_not_widen_when_count_is_exact_multiple():
    """Issue #331: 以前はちょうど20試合でも、一番右の点が軸の端に接してしまうことを
    避けるためさらに1段広げていたが、右側に丸々1目盛り分の空白ができるのが
    気になるというフィードバックを受けて撤廃した(ユーザー確認済み)。
    """
    assert _rank_graph_x_axis_max(20, 5) == 20


def test_rank_graph_x_axis_max_small_count():
    assert _rank_graph_x_axis_max(3, 5) == 5


def test_rank_graph_x_tick_step_stays_five_below_threshold():
    assert _rank_graph_x_tick_step(0) == 5
    assert _rank_graph_x_tick_step(69) == 5


def test_rank_graph_x_tick_step_widens_to_ten_at_threshold():
    """Issue #330: 試合数が70以上になったら目盛り間隔を10刻みに広げる。"""
    assert _rank_graph_x_tick_step(70) == 10
    assert _rank_graph_x_tick_step(200) == 10


def test_rank_graph_x_tick_values_always_includes_one_and_steps_of_five():
    assert _rank_graph_x_tick_values(25, 5) == [1, 5, 10, 15, 20, 25]


def test_rank_graph_x_tick_values_small_axis_max():
    assert _rank_graph_x_tick_values(5, 5) == [1, 5]


def test_rank_graph_x_tick_values_steps_of_ten():
    assert _rank_graph_x_tick_values(30, 10) == [1, 10, 20, 30]


def test_render_rank_graph_svg_flat_values_widens_y_axis_around_the_value():
    """全試合が同じランク値でも、軸の下限・上限を1つ広げて点が端に接しないようにする。"""
    history = _continuous_history([42, 42, 42])

    svg = _render_rank_graph_svg(history)

    assert ">41<" in svg
    assert ">42<" in svg
    assert ">43<" in svg


def test_render_rank_graph_svg_always_draws_single_solid_line():
    """Issue #180で導入した「連続していない区間は点線」の区別は、ユーザーとの
    相談で廃止した(2026-08-04)。試合間の値の差が大きくても、常に1本の
    実線(rank-graph-line)のみで描画する。
    """
    history = [
        {"rank_after": 38.1, "league_changed": None},
        {"rank_after": 39.0, "league_changed": None},
        {"rank_after": 37.6, "league_changed": None},
        {"rank_after": 37.9, "league_changed": None},
    ]

    svg = _render_rank_graph_svg(history)

    assert svg.count("<polyline") == 1
    assert "rank-graph-line-gap" not in svg


def test_overlay_rank_graph_page_links_transparent_background_stylesheet(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("RANK_GRAPH_MATCH_LIMIT", "all")
    db_path = tmp_path / "test.db"
    conn = db.connect(db_path)
    _save_match_result(
        conn,
        MatchResult(result="win", rank_before=1, rank_after=2, league_changed=None, detected_at=now_jst()),
    )
    conn.close()

    client = TestClient(create_app(db_path))

    response = client.get("/overlay/rank-graph")

    assert response.status_code == 200
    assert '<link rel="stylesheet" href="/static/overlay.css">' in response.text
    assert "<svg" in response.text

    css_response = client.get("/static/overlay.css")
    assert "background: transparent" in css_response.text


def test_aggregate_goal_stats_counts_goals_assists_and_involvement(monkeypatch):
    monkeypatch.setenv("ALLOWED_PLAYERS", "Alice,Bob")
    rows = [
        {"scorer_name": "Alice", "assist_name": "Bob"},
        {"scorer_name": "Alice", "assist_name": None},
        {"scorer_name": "Bob", "assist_name": "Alice"},
    ]

    players = _aggregate_goal_stats(rows)

    assert players == [
        {"name": "Alice", "goals": 2, "assists": 1, "involvement": 3},
        {"name": "Bob", "goals": 1, "assists": 1, "involvement": 2},
    ]


def test_aggregate_goal_stats_excludes_disallowed_players(monkeypatch):
    monkeypatch.setenv("ALLOWED_PLAYERS", "Alice")
    rows = [{"scorer_name": "Alice", "assist_name": "Stranger"}]

    players = _aggregate_goal_stats(rows)

    assert players == [{"name": "Alice", "goals": 1, "assists": 0, "involvement": 1}]


def test_aggregate_goal_stats_returns_empty_list_for_no_rows_and_no_allowed_players(monkeypatch):
    monkeypatch.delenv("ALLOWED_PLAYERS", raising=False)
    assert _aggregate_goal_stats([]) == []


def test_aggregate_goal_stats_returns_zero_counts_for_allowed_players_with_no_goals(monkeypatch):
    """Issue #271: ゴールが1件も無くても、許可リストプレイヤーは0件として結果に含める。"""
    monkeypatch.setenv("ALLOWED_PLAYERS", "Alice,Bob")

    players = _aggregate_goal_stats([])

    assert players == [
        {"name": "Alice", "goals": 0, "assists": 0, "involvement": 0},
        {"name": "Bob", "goals": 0, "assists": 0, "involvement": 0},
    ]


def test_fetch_goal_stats_scoped_to_current_session(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("ALLOWED_PLAYERS", "Alice,Bob")
    monkeypatch.setenv("GOAL_RECORD_MODE", "all")
    db_path = tmp_path / "test.db"
    conn = db.connect(db_path)

    old_session_id = db.create_session(conn)
    old_match_id = _save_match_result(
        conn,
        MatchResult(result="win", rank_before=1, rank_after=1, league_changed=None, detected_at=now_jst()),
        session_id=old_session_id,
    )
    db.save_goal(conn, old_match_id, "Alice", None, now_jst())

    current_session_id = db.create_session(conn)
    current_match_id = _save_match_result(
        conn,
        MatchResult(result="win", rank_before=1, rank_after=1, league_changed=None, detected_at=now_jst()),
        session_id=current_session_id,
    )
    db.save_goal(conn, current_match_id, "Bob", "Alice", now_jst())
    conn.close()

    result = _fetch_goal_stats(db_path)

    assert result == [
        {"name": "Bob", "goals": 1, "assists": 0, "involvement": 1},
        {"name": "Alice", "goals": 0, "assists": 1, "involvement": 1},
    ]


def test_fetch_goal_stats_empty_when_no_sessions_and_no_allowed_players(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("ALLOWED_PLAYERS", raising=False)
    db_path = tmp_path / "test.db"
    db.connect(db_path).close()

    assert _fetch_goal_stats(db_path) == []


def test_fetch_goal_stats_zero_counts_when_no_sessions(tmp_path: Path, monkeypatch):
    """Issue #271: 配信セッションが1件も無くても、許可リストプレイヤーは0件として返す。"""
    monkeypatch.setenv("ALLOWED_PLAYERS", "Alice")
    db_path = tmp_path / "test.db"
    db.connect(db_path).close()

    assert _fetch_goal_stats(db_path) == [{"name": "Alice", "goals": 0, "assists": 0, "involvement": 0}]


def test_overlay_goal_stats_winrate_page_shows_readable_summary(tmp_path: Path, monkeypatch):
    """Issue #339: /overlay/goal-stats(得点/アシスト)と/overlay/winrate(勝率)を
    1つのウィジェットに統合した。両方の内容が同じページに表示されることを確認する。
    """
    monkeypatch.setenv("ALLOWED_PLAYERS", "Alice,Bob")
    monkeypatch.setenv("GOAL_RECORD_MODE", "all")
    db_path = tmp_path / "test.db"
    conn = db.connect(db_path)
    session_id = db.create_session(conn)
    match_id = _save_match_result(
        conn,
        MatchResult(result="win", rank_before=1, rank_after=1, league_changed=None, detected_at=now_jst()),
        session_id=session_id,
    )
    db.save_goal(conn, match_id, "Alice", None, now_jst())
    for result in ["win", "lose"]:
        _save_match_result(
            conn,
            MatchResult(result=result, rank_before=1, rank_after=1, league_changed=None, detected_at=now_jst()),
            session_id=session_id,
        )
    conn.close()

    client = TestClient(create_app(db_path))

    response = client.get("/overlay/goal-stats-winrate")

    assert response.status_code == 200
    assert '<link rel="stylesheet" href="/static/overlay.css">' in response.text
    assert "今回: Alice: 得点 1 / アシスト 0 (関与 1)" in response.text
    assert "今回: 3試合" in response.text
    assert "win 2 / lose 1 / draw 0" in response.text
    assert "勝率 66.7%" in response.text
    assert "累計: 3試合" in response.text

    css_response = client.get("/static/overlay.css")
    assert "background: transparent" in css_response.text


def test_overlay_goal_stats_winrate_page_hides_name_when_single_allowed_player(tmp_path: Path, monkeypatch):
    """許可リストが1名だけの場合(=配信者本人が自明)は、名前を出さず得点/アシストのみ表示する。"""
    monkeypatch.setenv("ALLOWED_PLAYERS", "Alice")
    monkeypatch.setenv("GOAL_RECORD_MODE", "all")
    db_path = tmp_path / "test.db"
    conn = db.connect(db_path)
    session_id = db.create_session(conn)
    match_id = _save_match_result(
        conn,
        MatchResult(result="win", rank_before=1, rank_after=1, league_changed=None, detected_at=now_jst()),
        session_id=session_id,
    )
    db.save_goal(conn, match_id, "Alice", None, now_jst())
    conn.close()

    client = TestClient(create_app(db_path))

    response = client.get("/overlay/goal-stats-winrate")

    assert "Alice" not in response.text
    assert "今回: 得点 1 / アシスト 0 (関与 1)" in response.text


def test_overlay_goal_stats_winrate_page_shows_empty_message_and_dash_when_no_matches(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("ALLOWED_PLAYERS", raising=False)
    db_path = tmp_path / "test.db"
    db.connect(db_path).close()

    client = TestClient(create_app(db_path))

    response = client.get("/overlay/goal-stats-winrate")

    assert "データがありません" in response.text
    assert "勝率 -" in response.text


def test_overlay_goal_stats_winrate_page_shows_zero_when_no_goals_yet(tmp_path: Path, monkeypatch):
    """Issue #271: 許可リストプレイヤーがいてもゴールが無い場合は0を表示する(データがありません、ではない)。"""
    monkeypatch.setenv("ALLOWED_PLAYERS", "Alice,Bob")
    db_path = tmp_path / "test.db"
    db.connect(db_path).close()

    client = TestClient(create_app(db_path))

    response = client.get("/overlay/goal-stats-winrate")

    assert "データがありません" not in response.text
    assert "今回: Alice: 得点 0 / アシスト 0 (関与 0)" in response.text
    assert "今回: Bob: 得点 0 / アシスト 0 (関与 0)" in response.text


def test_match_log_returns_recent_results_oldest_first(tmp_path: Path):
    db_path = tmp_path / "test.db"
    conn = db.connect(db_path)
    for result in ["win", "lose", "draw", "win"]:
        _save_match_result(
            conn,
            MatchResult(result=result, rank_before=1, rank_after=1, league_changed=None, detected_at=now_jst()),
        )
    conn.close()

    client = TestClient(create_app(db_path))

    response = client.get("/api/match-log")

    assert response.status_code == 200
    assert response.json() == {"results": ["win", "lose", "draw", "win"]}


def test_match_log_limited_to_fixed_recent_count(tmp_path: Path):
    db_path = tmp_path / "test.db"
    conn = db.connect(db_path)
    for i in range(15):
        _save_match_result(
            conn,
            MatchResult(result="win", rank_before=i, rank_after=i, league_changed=None, detected_at=now_jst()),
        )
    conn.close()

    client = TestClient(create_app(db_path))

    response = client.get("/api/match-log")

    assert len(response.json()["results"]) == 10


def test_build_match_log_badges_maps_results_to_letters_and_colors():
    """3件(fade_count=3//2=1)のうち先頭1件だけがフェード対象になる。"""
    badges = _build_match_log_badges(["win", "lose", "draw"])

    assert badges == [
        {"letter": "W", "color": "#0ca30c", "opacity": 0.5},
        {"letter": "L", "color": "#d03b3b", "opacity": 1.0},
        {"letter": "D", "color": "#898781", "opacity": 1.0},
    ]


def test_build_match_log_badges_returns_empty_list_for_no_results():
    assert _build_match_log_badges([]) == []


def test_build_match_log_badges_single_result_is_fully_opaque():
    """1件だけの場合はフェード対象(fade_count=1//2=0件)が無いため、常に1.0にする。"""
    badges = _build_match_log_badges(["win"])

    assert badges == [{"letter": "W", "color": "#0ca30c", "opacity": 1.0}]


def test_build_match_log_badges_newer_half_is_never_faded():
    """Issue #262: 新しい方の半分ははっきり見せたいというユーザー要望から、
    フェードするのは古い方の前半count // 2件のみ。後半は常にopacity 1.0にする
    (並び順反転・最新のみ拡大・矢印表示などの他案を見た目付きで比較した上で選ばれた方式)。
    """
    badges = _build_match_log_badges(["win"] * 10)

    opacities = [badge["opacity"] for badge in badges]

    assert opacities[:5] == sorted(opacities[:5])
    assert opacities[0] == 0.5
    assert opacities[4] == 1.0
    assert opacities[5:] == [1.0] * 5


def test_overlay_match_log_page_shows_win_lose_draw_badges(tmp_path: Path):
    """Issue #262: 各試合結果を色分けバッジ(win=緑・lose=赤・draw=グレー)で、
    古い方の半分だけ不透明度のグラデーションを付けて表示する。
    """
    db_path = tmp_path / "test.db"
    conn = db.connect(db_path)
    for result in ["win", "win", "lose", "draw"]:
        _save_match_result(
            conn,
            MatchResult(result=result, rank_before=1, rank_after=1, league_changed=None, detected_at=now_jst()),
        )
    conn.close()

    client = TestClient(create_app(db_path))

    response = client.get("/overlay/match-log")

    assert response.status_code == 200
    assert '<link rel="stylesheet" href="/static/overlay.css">' in response.text
    assert '<link rel="stylesheet" href="/static/match_log.css">' in response.text
    # 4件のうち前半2件(fade_count=4//2=2)だけがフェード対象、後半2件は常に1.0
    expected_badges = [
        '<span class="match-log-badge" style="background-color: #0ca30c; opacity: 0.5;">W</span>',
        '<span class="match-log-badge" style="background-color: #0ca30c; opacity: 1.0;">W</span>',
        '<span class="match-log-badge" style="background-color: #d03b3b; opacity: 1.0;">L</span>',
        '<span class="match-log-badge" style="background-color: #898781; opacity: 1.0;">D</span>',
    ]
    positions = [response.text.index(badge) for badge in expected_badges]
    assert positions == sorted(positions)

    css_response = client.get("/static/overlay.css")
    assert "background: transparent" in css_response.text


def test_overlay_match_log_page_shows_empty_message_when_no_matches(tmp_path: Path):
    db_path = tmp_path / "test.db"
    db.connect(db_path).close()

    client = TestClient(create_app(db_path))

    response = client.get("/overlay/match-log")

    assert "データがありません" in response.text


@pytest.mark.parametrize(
    "tier_label, tier_value, expected",
    [
        ("∞", 42, 42),
        ("∞", 0, 0),
        ("S", 9, -1),
        ("S", 0, -10),
        ("A", 29, -11),
        ("A", 0, -40),
        (None, None, None),
        ("∞", None, None),
        ("B", 5, None),
    ],
)
def test_convert_rank_tier_to_unified_scale(tier_label, tier_value, expected):
    assert _convert_rank_tier_to_unified_scale(tier_label, tier_value) == expected


def test_convert_rank_tier_unified_scale_preserves_promotion_order():
    # A29(-11) < S0(-10) < S9(-1) < 無限0(0) の順序が保たれること
    a29 = _convert_rank_tier_to_unified_scale("A", 29)
    s0 = _convert_rank_tier_to_unified_scale("S", 0)
    s9 = _convert_rank_tier_to_unified_scale("S", 9)
    mu0 = _convert_rank_tier_to_unified_scale("∞", 0)

    assert a29 < s0 < s9 < mu0


def test_summarize_vs_slot_ranks_sums_known_members_and_counts_unknown():
    rows = [
        {"rank_tier_label": "∞", "rank_tier": 40},
        {"rank_tier_label": "S", "rank_tier": 9},
        {"rank_tier_label": None, "rank_tier": None},
        {"rank_tier_label": None, "rank_tier": None},
    ]

    summary = _summarize_vs_slot_ranks(rows)

    assert summary == {"total": 39, "known_count": 2, "unknown_count": 2}


def test_summarize_vs_slot_ranks_total_none_when_all_unknown():
    rows = [{"rank_tier_label": None, "rank_tier": None}] * 4

    summary = _summarize_vs_slot_ranks(rows)

    assert summary == {"total": None, "known_count": 0, "unknown_count": 4}


def test_format_vs_rank_value_returns_total_as_string():
    assert _format_vs_rank_value({"total": 160, "known_count": 4, "unknown_count": 0}) == "160"


def test_format_vs_rank_value_returns_dash_when_total_missing():
    """Issue #113/#276: 直近試合自体が無い場合の表示("-" VS "-")と表記を揃える。"""
    assert _format_vs_rank_value({"total": None, "known_count": 0, "unknown_count": 4}) == "-"


def test_vs_rank_comparison_endpoint_uses_latest_snapshot(tmp_path: Path):
    db_path = tmp_path / "test.db"
    conn = db.connect(db_path)
    db.save_vs_rank_snapshot(
        conn,
        session_id=None,
        mine_ranks=[SlotRank("∞", 40), SlotRank("S", 9), SlotRank(None, None), SlotRank(None, None)],
        opponent_ranks=[SlotRank("A", 29), SlotRank("A", 0), SlotRank(None, None), SlotRank(None, None)],
        mine_team_color="#64bde2",
        opponent_team_color="#f87abe",
        detected_at=now_jst(),
    )
    conn.close()

    client = TestClient(create_app(db_path))

    response = client.get("/api/vs-rank-comparison")

    assert response.status_code == 200
    assert response.json() == {
        "mine": {"total": 39, "known_count": 2, "unknown_count": 2},
        "opponent": {"total": -51, "known_count": 2, "unknown_count": 2},
        "mine_team_color": "#64bde2",
        "opponent_team_color": "#f87abe",
    }


def test_vs_rank_comparison_endpoint_uses_newer_snapshot_over_older_one(tmp_path: Path):
    """Issue #145: 試合結果確定を待たず、VS画面確定を検知した瞬間のスナップショットを即座に反映する。"""
    db_path = tmp_path / "test.db"
    conn = db.connect(db_path)
    db.save_vs_rank_snapshot(
        conn,
        session_id=None,
        mine_ranks=[SlotRank("∞", 1)] * 4,
        opponent_ranks=[SlotRank("∞", 1)] * 4,
        mine_team_color="#111111",
        opponent_team_color="#222222",
        detected_at=now_jst(),
    )
    db.save_vs_rank_snapshot(
        conn,
        session_id=None,
        mine_ranks=[SlotRank("∞", 40)] * 4,
        opponent_ranks=[SlotRank("∞", 10)] * 4,
        mine_team_color="#64bde2",
        opponent_team_color="#f87abe",
        detected_at=now_jst(),
    )
    conn.close()

    client = TestClient(create_app(db_path))

    response = client.get("/api/vs-rank-comparison")

    assert response.json()["mine"]["total"] == 160
    assert response.json()["mine_team_color"] == "#64bde2"


def test_vs_rank_comparison_endpoint_none_when_no_snapshots(tmp_path: Path):
    db_path = tmp_path / "test.db"
    db.connect(db_path).close()

    client = TestClient(create_app(db_path))

    response = client.get("/api/vs-rank-comparison")

    assert response.json() == {"mine": None, "opponent": None}


def test_vs_rank_comparison_endpoint_none_when_latest_snapshot_has_no_vs_data(tmp_path: Path):
    """Issue #145: VS画面を見逃した試合が終わった際、main.pyが空スナップショットを書き込んでリセットする想定。"""
    db_path = tmp_path / "test.db"
    conn = db.connect(db_path)
    db.save_vs_rank_snapshot(
        conn,
        session_id=None,
        mine_ranks=[],
        opponent_ranks=[],
        mine_team_color=None,
        opponent_team_color=None,
        detected_at=now_jst(),
    )
    conn.close()

    client = TestClient(create_app(db_path))

    response = client.get("/api/vs-rank-comparison")

    assert response.json() == {"mine": None, "opponent": None}


def test_overlay_vs_rank_comparison_page_shows_readable_summary(tmp_path: Path):
    db_path = tmp_path / "test.db"
    conn = db.connect(db_path)
    db.save_vs_rank_snapshot(
        conn,
        session_id=None,
        mine_ranks=[SlotRank("∞", 40)] * 4,
        opponent_ranks=[SlotRank("∞", 10)] * 4,
        mine_team_color="#64bde2",
        opponent_team_color="#f87abe",
        detected_at=now_jst(),
    )
    conn.close()

    client = TestClient(create_app(db_path))

    response = client.get("/overlay/vs-rank-comparison")

    assert response.status_code == 200
    assert '<link rel="stylesheet" href="/static/overlay.css">' in response.text
    assert '<link rel="stylesheet" href="/static/vs_rank_comparison.css">' in response.text
    assert '<span class="vs-rank-pill" style="background-color: #64bde2;">160</span>' in response.text
    assert '<span class="vs-rank-pill" style="background-color: #f87abe;">40</span>' in response.text
    assert '<div class="vs-rank-caption">Rank</div>' in response.text
    assert "160</span><span class=\"vs-rank-vs\">VS</span><span" in response.text


def test_overlay_vs_rank_comparison_page_shows_dash_placeholders_when_no_data(tmp_path: Path):
    """データが無い場合も表示形式は崩さず、値を"-"にするだけにする(Issue #276)。"""
    db_path = tmp_path / "test.db"
    db.connect(db_path).close()

    client = TestClient(create_app(db_path))

    response = client.get("/overlay/vs-rank-comparison")

    assert '<span class="vs-rank-pill" style="background-color: #666666;">-</span>' in response.text
    assert '<div class="vs-rank-caption">Rank</div>' in response.text


def test_overlay_vs_rank_comparison_page_shows_dash_for_side_with_only_unknown_members(tmp_path: Path):
    """Issue #113/#276: VS画面自体は検知できているが片側が全員不明人数の場合も、"-"で表記を揃える。"""
    db_path = tmp_path / "test.db"
    conn = db.connect(db_path)
    db.save_vs_rank_snapshot(
        conn,
        session_id=None,
        mine_ranks=[SlotRank("∞", 40)] * 4,
        opponent_ranks=[SlotRank(None, None)] * 4,
        mine_team_color="#64bde2",
        opponent_team_color="#f87abe",
        detected_at=now_jst(),
    )
    conn.close()

    client = TestClient(create_app(db_path))

    response = client.get("/overlay/vs-rank-comparison")

    assert '<span class="vs-rank-pill" style="background-color: #64bde2;">160</span>' in response.text
    assert '<span class="vs-rank-pill" style="background-color: #f87abe;">-</span>' in response.text


def test_overlay_vs_rank_comparison_page_uses_default_color_when_team_color_not_detected(tmp_path: Path):
    """Issue #113: VS画面自体は検知できたがチームカラーが未検知(古いDB等)の場合もフォールバック色で表示する。"""
    db_path = tmp_path / "test.db"
    conn = db.connect(db_path)
    db.save_vs_rank_snapshot(
        conn,
        session_id=None,
        mine_ranks=[SlotRank("∞", 40)] * 4,
        opponent_ranks=[SlotRank("∞", 10)] * 4,
        mine_team_color=None,
        opponent_team_color=None,
        detected_at=now_jst(),
    )
    conn.close()

    client = TestClient(create_app(db_path))

    response = client.get("/overlay/vs-rank-comparison")

    assert '<span class="vs-rank-pill" style="background-color: #666666;">160</span>' in response.text
    assert '<span class="vs-rank-pill" style="background-color: #666666;">40</span>' in response.text


def test_percentile_linear_interpolation():
    sorted_values = [1, 2, 3, 4]

    assert _percentile(sorted_values, 0) == 1
    assert _percentile(sorted_values, 25) == 1.75
    assert _percentile(sorted_values, 50) == 2.5
    assert _percentile(sorted_values, 75) == 3.25
    assert _percentile(sorted_values, 100) == 4


def test_percentile_single_value():
    assert _percentile([5], 50) == 5


def test_compute_box_stats_returns_expected_summary():
    stats = _compute_box_stats([1, 2, 3, 4])

    assert stats == {"min": 1, "q1": 1.75, "median": 2.5, "q3": 3.25, "max": 4, "mean": 2.5}


def test_compute_box_stats_returns_none_for_empty_list():
    assert _compute_box_stats([]) is None


def test_rank_delta_distribution_endpoint_separates_win_and_lose_and_excludes_draw(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("RANK_DELTA_DISTRIBUTION_SCOPE", "session")
    db_path = tmp_path / "test.db"
    conn = db.connect(db_path)
    session_id = db.create_session(conn)
    # Issue #253: 直前の試合のrank_afterは次の試合のrank_beforeで常に補正されるため、
    # 試合間の連続性を保った値にする(そうしないと後続の補正で意図したdeltaが崩れる)
    _save_match_result(
        conn,
        MatchResult(result="win", rank_before=10, rank_after=12, league_changed=None, detected_at=now_jst()),
        session_id=session_id,
    )
    _save_match_result(
        conn,
        MatchResult(result="lose", rank_before=12, rank_after=10, league_changed=None, detected_at=now_jst()),
        session_id=session_id,
    )
    _save_match_result(
        conn,
        MatchResult(result="draw", rank_before=10, rank_after=10, league_changed=None, detected_at=now_jst()),
        session_id=session_id,
    )
    _save_match_result(
        conn,
        MatchResult(result="win", rank_before=None, rank_after=None, league_changed=None, detected_at=now_jst()),
        session_id=session_id,
    )
    conn.close()

    client = TestClient(create_app(db_path))

    response = client.get("/api/rank-delta-distribution")

    assert response.status_code == 200
    assert response.json() == {"win": [2], "lose": [2]}


def test_rank_delta_distribution_endpoint_scoped_to_current_session(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("RANK_DELTA_DISTRIBUTION_SCOPE", "session")
    db_path = tmp_path / "test.db"
    conn = db.connect(db_path)
    old_session_id = db.create_session(conn)
    _save_match_result(
        conn,
        MatchResult(result="win", rank_before=10, rank_after=15, league_changed=None, detected_at=now_jst()),
        session_id=old_session_id,
    )
    db.create_session(conn)
    conn.close()

    client = TestClient(create_app(db_path))

    response = client.get("/api/rank-delta-distribution")

    assert response.json() == {"win": [], "lose": []}


def test_rank_delta_distribution_endpoint_empty_when_no_sessions(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("RANK_DELTA_DISTRIBUTION_SCOPE", "session")
    db_path = tmp_path / "test.db"
    db.connect(db_path).close()

    client = TestClient(create_app(db_path))

    response = client.get("/api/rank-delta-distribution")

    assert response.json() == {"win": [], "lose": []}


def test_rank_delta_distribution_endpoint_uses_all_matches_when_scope_is_all(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("RANK_DELTA_DISTRIBUTION_SCOPE", "all")
    db_path = tmp_path / "test.db"
    conn = db.connect(db_path)
    old_session_id = db.create_session(conn)
    _save_match_result(
        conn,
        MatchResult(result="win", rank_before=10, rank_after=13, league_changed=None, detected_at=now_jst()),
        session_id=old_session_id,
    )
    current_session_id = db.create_session(conn)
    # Issue #253: rank_after補正はセッションを跨いでも常に働くため、直前の試合の
    # rank_after(13)と連続するrank_beforeにする
    _save_match_result(
        conn,
        MatchResult(result="lose", rank_before=13, rank_after=12, league_changed=None, detected_at=now_jst()),
        session_id=current_session_id,
    )
    conn.close()

    client = TestClient(create_app(db_path))

    response = client.get("/api/rank-delta-distribution")

    # scope=allの場合、旧セッションの試合も含めて全期間を対象にする
    assert response.json() == {"win": [3], "lose": [1]}


def test_rank_delta_axis_max_extends_to_next_tenth():
    stats_by_category = {"win": _compute_box_stats([0.55]), "lose": None}

    assert _rank_delta_axis_max(stats_by_category) == 0.6


def test_rank_delta_axis_max_widens_when_max_lands_exactly_on_a_tenth():
    stats_by_category = {"win": _compute_box_stats([0.3]), "lose": None}

    assert _rank_delta_axis_max(stats_by_category) == 0.4


def test_rank_delta_axis_max_default_when_no_data():
    assert _rank_delta_axis_max({"win": None, "lose": None}) == 0.1


def test_render_rank_delta_box_plot_svg_uses_fixed_tenth_tick_step():
    svg = _render_rank_delta_box_plot_svg([0.25], [0.4])

    # 最大値0.4 -> 軸は0.5まで拡張され、0.0刻みで0.1ずつの目盛りが並ぶ
    for expected_label in ["0.0", "0.1", "0.2", "0.3", "0.4", "0.5"]:
        assert f">{expected_label}<" in svg


def test_render_rank_delta_box_plot_svg_always_shows_title():
    assert f'class="rank-delta-title">{_BOX_PLOT_TITLE}<' in _render_rank_delta_box_plot_svg([1, 2], [3, 4])
    assert f'class="rank-delta-title">{_BOX_PLOT_TITLE}<' in _render_rank_delta_box_plot_svg([], [])


def test_render_rank_delta_box_plot_svg_always_shows_panel_behind_other_elements():
    """Issue #113: 配信画面での視認性対策の半透明パネルは、他の要素より先(=一番背面)に描画する。"""
    svg = _render_rank_delta_box_plot_svg([1, 2], [3, 4])

    assert 'class="rank-delta-panel"' in svg
    assert svg.index('class="rank-delta-panel"') < svg.index('class="rank-delta-title"')
    assert 'class="rank-delta-panel"' in _render_rank_delta_box_plot_svg([], [])


def test_render_rank_delta_box_plot_svg_with_no_data_shows_empty_message():
    svg = _render_rank_delta_box_plot_svg([], [])

    assert "データがありません" in svg
    assert "rank-delta-box" not in svg


def test_render_rank_delta_box_plot_svg_draws_both_categories():
    svg = _render_rank_delta_box_plot_svg([1, 2, 3], [4, 5])

    assert svg.count('class="rank-delta-box rank-delta-win"') == 1
    assert svg.count('class="rank-delta-box rank-delta-lose"') == 1
    assert svg.count('class="rank-delta-mean"') == 2
    assert svg.count('class="rank-delta-median"') == 2


def test_render_rank_delta_box_plot_svg_handles_one_category_missing():
    svg = _render_rank_delta_box_plot_svg([1, 2, 3], [])

    assert svg.count('class="rank-delta-box rank-delta-win"') == 1
    assert "rank-delta-box rank-delta-lose" not in svg


def test_render_rank_delta_box_plot_svg_single_value_does_not_crash():
    svg = _render_rank_delta_box_plot_svg([2], [3])

    assert svg.count('class="rank-delta-mean"') == 2


def test_overlay_rank_delta_distribution_page_links_transparent_background_stylesheet(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("RANK_DELTA_DISTRIBUTION_SCOPE", "session")
    db_path = tmp_path / "test.db"
    conn = db.connect(db_path)
    session_id = db.create_session(conn)
    _save_match_result(
        conn,
        MatchResult(result="win", rank_before=10, rank_after=12, league_changed=None, detected_at=now_jst()),
        session_id=session_id,
    )
    conn.close()

    client = TestClient(create_app(db_path))

    response = client.get("/overlay/rank-delta-distribution")

    assert response.status_code == 200
    assert '<link rel="stylesheet" href="/static/overlay.css">' in response.text
    assert '<link rel="stylesheet" href="/static/rank_delta_distribution.css">' in response.text
    assert "<svg" in response.text

    css_response = client.get("/static/overlay.css")
    assert "background: transparent" in css_response.text


def test_overlay_rank_delta_distribution_page_shows_empty_message_when_no_data(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("RANK_DELTA_DISTRIBUTION_SCOPE", "session")
    db_path = tmp_path / "test.db"
    db.connect(db_path).close()

    client = TestClient(create_app(db_path))

    response = client.get("/overlay/rank-delta-distribution")

    assert "データがありません" in response.text


def test_overlay_refresh_script_is_served_and_reads_the_page_it_is_embedded_in(tmp_path: Path):
    """Issue #104: 共有の自動更新スクリプトが静的ファイルとして配信され、
    自分自身のURLをfetchし直してbody差し替えする実装になっていることを確認する。
    """
    db_path = tmp_path / "test.db"
    db.connect(db_path).close()
    client = TestClient(create_app(db_path))

    response = client.get("/static/overlay-refresh.js")

    assert response.status_code == 200
    assert "window.location.href" in response.text
    assert "document.body.innerHTML" in response.text


@pytest.mark.parametrize(
    "path",
    [
        "/overlay/goal-stats-winrate",
        "/overlay/rank-graph",
        "/overlay/match-log",
        "/overlay/rank-delta-distribution",
        "/overlay/dive-time",
    ],
)
def test_overlay_pages_include_refresh_script_with_default_interval(tmp_path: Path, path: str, monkeypatch):
    monkeypatch.setenv("RANK_DELTA_DISTRIBUTION_SCOPE", "session")
    monkeypatch.setenv("RANK_GRAPH_MATCH_LIMIT", "all")
    db_path = tmp_path / "test.db"
    db.connect(db_path).close()

    client = TestClient(create_app(db_path))

    response = client.get(path)

    expected_tag = f'<script src="/static/overlay-refresh.js" data-interval-ms="{_OVERLAY_REFRESH_INTERVAL_MS}">'
    assert expected_tag in response.text


def test_overlay_vs_rank_comparison_page_uses_shorter_refresh_interval(tmp_path: Path):
    """#100はVS画面確定後できるだけ早く反映してほしいという要望から、他より短い
    間隔にしている(ユーザーとの相談で決定)。
    """
    db_path = tmp_path / "test.db"
    db.connect(db_path).close()

    client = TestClient(create_app(db_path))

    response = client.get("/overlay/vs-rank-comparison")

    expected_tag = (
        f'<script src="/static/overlay-refresh.js" data-interval-ms="{_VS_RANK_COMPARISON_REFRESH_INTERVAL_MS}">'
    )
    assert expected_tag in response.text


@pytest.mark.parametrize(
    "path",
    [
        "/overlay/goal-stats-winrate",
        "/overlay/rank-graph",
        "/overlay/match-log",
        "/overlay/vs-rank-comparison",
        "/overlay/rank-delta-distribution",
        "/overlay/dive-time",
    ],
)
def test_overlay_pages_have_no_debug_bg_style_by_default(tmp_path: Path, path: str, monkeypatch):
    """Issue #259: OBSのブラウザソースが実際に使うURL(パラメータ無し)では、
    従来通り透過のまま(<body>にstyle属性を付けない)ことを確認する。
    """
    monkeypatch.setenv("RANK_DELTA_DISTRIBUTION_SCOPE", "session")
    monkeypatch.setenv("RANK_GRAPH_MATCH_LIMIT", "all")
    db_path = tmp_path / "test.db"
    db.connect(db_path).close()

    client = TestClient(create_app(db_path))

    response = client.get(path)

    assert "<body>" in response.text
    assert "background: #000" not in response.text


@pytest.mark.parametrize(
    "path",
    [
        "/overlay/goal-stats-winrate",
        "/overlay/rank-graph",
        "/overlay/match-log",
        "/overlay/vs-rank-comparison",
        "/overlay/rank-delta-distribution",
        "/overlay/dive-time",
    ],
)
def test_overlay_pages_apply_debug_bg_style_when_query_param_present(tmp_path: Path, path: str, monkeypatch):
    """Issue #259: ?debug_bg=1が付いている場合のみ、<body>の背景を黒にする
    (通常のブラウザで白文字が読めるようにするための暫定策)。値そのものは
    見ないため、どんな値でも(空文字含め)パラメータの有無だけで判定する。
    """
    monkeypatch.setenv("RANK_DELTA_DISTRIBUTION_SCOPE", "session")
    monkeypatch.setenv("RANK_GRAPH_MATCH_LIMIT", "all")
    db_path = tmp_path / "test.db"
    db.connect(db_path).close()

    client = TestClient(create_app(db_path))

    response = client.get(path, params={"debug_bg": "1"})

    assert '<body style="background: #000;">' in response.text
    assert _VS_RANK_COMPARISON_REFRESH_INTERVAL_MS < _OVERLAY_REFRESH_INTERVAL_MS


def test_index_page_does_not_include_refresh_script(tmp_path: Path):
    """値確認用の`/`ページはOBSへの配置を想定していないため、自動更新は不要。"""
    db_path = tmp_path / "test.db"
    db.connect(db_path).close()

    client = TestClient(create_app(db_path))

    response = client.get("/")

    assert "overlay-refresh.js" not in response.text


def _write_admin_env_file(path: Path) -> None:
    path.write_text(
        "ALLOWED_PLAYERS=OldName\n"
        "GOAL_RECORD_MODE=all\n"
        "RANK_GRAPH_MATCH_LIMIT=all\n"
        "RANK_DELTA_DISTRIBUTION_SCOPE=all\n"
        "OBS_SCENE_SWITCHING_ENABLED=true\n",
        encoding="utf-8",
    )


def test_admin_get_shows_current_settings(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("ALLOWED_PLAYERS", "Alice,Bob")
    monkeypatch.setenv("GOAL_RECORD_MODE", "allowlist")
    monkeypatch.setenv("RANK_GRAPH_MATCH_LIMIT", "30")
    monkeypatch.setenv("RANK_DELTA_DISTRIBUTION_SCOPE", "session")
    monkeypatch.setenv("OBS_SCENE_SWITCHING_ENABLED", "false")
    client = TestClient(create_app(tmp_path / "test.db"))

    response = client.get("/admin")

    assert response.status_code == 200
    assert 'value="Alice,Bob"' in response.text
    assert 'value="30"' in response.text
    assert '<option value="false" selected>' in response.text


def test_admin_get_shows_overlay_widget_links(tmp_path: Path):
    """Issue #257: 各overlayウィジェットへのリンク一覧をリンク先付きで表示する。

    Issue #259: 通常のブラウザで開いても白文字が読めるよう、リンク先には
    ?debug_bg=1を付ける(OBS側に登録するURL自体にはこのパラメータを付けない)。
    """
    client = TestClient(create_app(tmp_path / "test.db"))

    response = client.get("/admin")

    assert response.status_code == 200
    for path, label in _OVERLAY_WIDGET_LABELS.items():
        assert f'<a href="{path}?debug_bg=1" target="_blank" rel="noopener">{label}</a>' in response.text


def test_admin_get_shows_rank_entry_link(tmp_path: Path):
    """Issue #314: /rank-entryへのリンクを表示する。"""
    client = TestClient(create_app(tmp_path / "test.db"))

    response = client.get("/admin")

    assert response.status_code == 200
    assert '<a href="/rank-entry" target="_blank" rel="noopener">' in response.text


def test_admin_get_shows_dashboard_heading_before_settings_heading(tmp_path: Path):
    """Issue #314: 見出し構成を「配信ダッシュボード」→「配信ウィジェット一覧」→
    「配信中の設定」の順に再構成したことを確認する。
    """
    client = TestClient(create_app(tmp_path / "test.db"))

    response = client.get("/admin")

    assert response.status_code == 200
    assert "<h1>配信ダッシュボード</h1>" in response.text
    dashboard_index = response.text.index("配信ダッシュボード")
    widget_list_index = response.text.index("配信ウィジェット一覧")
    settings_index = response.text.index("配信中の設定")
    assert dashboard_index < widget_list_index < settings_index


def test_overlay_widget_links_matches_registered_overlay_routes(tmp_path: Path):
    """全/overlay/xxxルートが漏れなくリンク集に含まれることを確認する。"""
    app = create_app(tmp_path / "test.db")
    registered_paths = {
        route.path
        for route in app.routes
        if getattr(route, "path", "").startswith("/overlay/")
    }

    links = _overlay_widget_links(app)

    assert {link["path"] for link in links} == registered_paths
    assert registered_paths  # そもそもoverlayルートが1つも無い状態で通ってしまわないように


def test_overlay_widget_links_raises_when_label_is_missing_for_a_route():
    """新しいoverlayルートを追加したのに_OVERLAY_WIDGET_LABELSへの追記を忘れた場合、
    リンク集を組み立てる時点でRuntimeErrorになり、放置されないようにする。
    """
    app = FastAPI()

    @app.get("/overlay/not-yet-labeled")
    def _unlabeled_overlay():
        return {}

    with pytest.raises(RuntimeError, match="/overlay/not-yet-labeled"):
        _overlay_widget_links(app)


def test_admin_post_updates_settings_and_persists_to_env_file(tmp_path: Path, monkeypatch):
    env_path = tmp_path / ".env"
    _write_admin_env_file(env_path)
    monkeypatch.setattr("nss_tracker.config.find_dotenv", lambda: str(env_path))
    monkeypatch.setenv("ALLOWED_PLAYERS", "OldName")
    monkeypatch.setenv("GOAL_RECORD_MODE", "all")
    monkeypatch.setenv("RANK_GRAPH_MATCH_LIMIT", "all")
    monkeypatch.setenv("RANK_DELTA_DISTRIBUTION_SCOPE", "all")
    monkeypatch.setenv("OBS_SCENE_SWITCHING_ENABLED", "true")
    client = TestClient(create_app(tmp_path / "test.db"))

    response = client.post(
        "/admin",
        data={
            "allowed_players": "NewName",
            "goal_record_mode": "allowlist",
            "rank_graph_match_limit": "10",
            "rank_delta_distribution_scope": "session",
            "obs_scene_switching_enabled": "false",
        },
    )

    assert response.status_code == 200
    assert response.url.path == "/admin"
    assert response.url.params["status"] == "updated"
    assert os.environ["ALLOWED_PLAYERS"] == "NewName"
    assert os.environ["RANK_GRAPH_MATCH_LIMIT"] == "10"
    assert os.environ["OBS_SCENE_SWITCHING_ENABLED"] == "false"


def test_admin_post_with_invalid_value_shows_error_and_does_not_update(tmp_path: Path, monkeypatch):
    env_path = tmp_path / ".env"
    _write_admin_env_file(env_path)
    monkeypatch.setattr("nss_tracker.config.find_dotenv", lambda: str(env_path))
    monkeypatch.setenv("ALLOWED_PLAYERS", "OldName")
    monkeypatch.setenv("GOAL_RECORD_MODE", "all")
    monkeypatch.setenv("RANK_GRAPH_MATCH_LIMIT", "all")
    monkeypatch.setenv("RANK_DELTA_DISTRIBUTION_SCOPE", "all")
    monkeypatch.setenv("OBS_SCENE_SWITCHING_ENABLED", "true")
    client = TestClient(create_app(tmp_path / "test.db"))

    response = client.post(
        "/admin",
        data={
            "allowed_players": "NewName",
            "goal_record_mode": "not-a-real-mode",
            "rank_graph_match_limit": "10",
            "rank_delta_distribution_scope": "session",
            "obs_scene_switching_enabled": "true",
        },
    )

    assert response.status_code == 200
    assert response.url.params["error"]
    assert "GOAL_RECORD_MODE" in response.url.params["error"]
    assert os.environ["ALLOWED_PLAYERS"] == "OldName"


def test_admin_post_with_invalid_obs_scene_switching_enabled_shows_error_and_does_not_update(
    tmp_path: Path, monkeypatch
):
    env_path = tmp_path / ".env"
    _write_admin_env_file(env_path)
    monkeypatch.setattr("nss_tracker.config.find_dotenv", lambda: str(env_path))
    monkeypatch.setenv("ALLOWED_PLAYERS", "OldName")
    monkeypatch.setenv("GOAL_RECORD_MODE", "all")
    monkeypatch.setenv("RANK_GRAPH_MATCH_LIMIT", "all")
    monkeypatch.setenv("RANK_DELTA_DISTRIBUTION_SCOPE", "all")
    monkeypatch.setenv("OBS_SCENE_SWITCHING_ENABLED", "true")
    client = TestClient(create_app(tmp_path / "test.db"))

    response = client.post(
        "/admin",
        data={
            "allowed_players": "NewName",
            "goal_record_mode": "allowlist",
            "rank_graph_match_limit": "10",
            "rank_delta_distribution_scope": "session",
            "obs_scene_switching_enabled": "yes",
        },
    )

    assert response.status_code == 200
    assert response.url.params["error"]
    assert "OBS_SCENE_SWITCHING_ENABLED" in response.url.params["error"]
    assert os.environ["OBS_SCENE_SWITCHING_ENABLED"] == "true"


def test_admin_post_logs_info_message_on_success(tmp_path: Path, monkeypatch, caplog):
    """Issue #129: 更新内容はINFOレベルで記録する(DEBUGモードでも表示されるため)。"""
    env_path = tmp_path / ".env"
    _write_admin_env_file(env_path)
    monkeypatch.setattr("nss_tracker.config.find_dotenv", lambda: str(env_path))
    monkeypatch.setenv("ALLOWED_PLAYERS", "OldName")
    monkeypatch.setenv("GOAL_RECORD_MODE", "all")
    monkeypatch.setenv("RANK_GRAPH_MATCH_LIMIT", "all")
    monkeypatch.setenv("RANK_DELTA_DISTRIBUTION_SCOPE", "all")
    monkeypatch.setenv("OBS_SCENE_SWITCHING_ENABLED", "true")
    client = TestClient(create_app(tmp_path / "test.db"))

    with caplog.at_level("INFO", logger="nss_tracker.web"):
        client.post(
            "/admin",
            data={
                "allowed_players": "NewName",
                "goal_record_mode": "allowlist",
                "rank_graph_match_limit": "10",
                "rank_delta_distribution_scope": "session",
                "obs_scene_switching_enabled": "true",
            },
        )

    messages = [record.message for record in caplog.records if record.name == "nss_tracker.web"]
    assert any("NewName" in message and "OldName" in message for message in messages)


def test_admin_post_logs_warning_message_on_invalid_value(tmp_path: Path, monkeypatch, caplog):
    """Issue #129: 拒否された更新もWARNINGレベルで記録する。"""
    env_path = tmp_path / ".env"
    _write_admin_env_file(env_path)
    monkeypatch.setattr("nss_tracker.config.find_dotenv", lambda: str(env_path))
    monkeypatch.setenv("ALLOWED_PLAYERS", "OldName")
    monkeypatch.setenv("GOAL_RECORD_MODE", "all")
    monkeypatch.setenv("RANK_GRAPH_MATCH_LIMIT", "all")
    monkeypatch.setenv("RANK_DELTA_DISTRIBUTION_SCOPE", "all")
    monkeypatch.setenv("OBS_SCENE_SWITCHING_ENABLED", "true")
    client = TestClient(create_app(tmp_path / "test.db"))

    with caplog.at_level("WARNING", logger="nss_tracker.web"):
        client.post(
            "/admin",
            data={
                "allowed_players": "NewName",
                "goal_record_mode": "not-a-real-mode",
                "rank_graph_match_limit": "10",
                "rank_delta_distribution_scope": "session",
                "obs_scene_switching_enabled": "true",
            },
        )

    messages = [record.message for record in caplog.records if record.name == "nss_tracker.web"]
    assert any("GOAL_RECORD_MODE" in message for message in messages)


def test_rank_entry_get_shows_no_pending_message_when_nothing_pending(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(server_module, "DEFAULT_CLIPS_DIR", tmp_path / "clips")
    db_path = tmp_path / "test.db"
    db.connect(db_path).close()
    client = TestClient(create_app(db_path))

    response = client.get("/rank-entry")

    assert response.status_code == 200
    assert "未確定の試合はありません" in response.text


def test_rank_entry_get_shows_admin_link(tmp_path: Path):
    """Issue #314: /adminへのリンクを表示する。"""
    db_path = tmp_path / "test.db"
    db.connect(db_path).close()
    client = TestClient(create_app(db_path))

    response = client.get("/rank-entry")

    assert response.status_code == 200
    assert '<a href="/admin" target="_blank" rel="noopener">' in response.text


def test_rank_entry_get_shows_oldest_pending_match(tmp_path: Path, monkeypatch):
    """クリップが1件も無い場合、Issue #306/#308までと同じくfetch_oldest_pending_manual_rank_match()
    の結果1件(has_clip=False)が埋め込みJSONに含まれることを確認する。

    DEFAULT_CLIPS_DIRを空のtmp_pathに差し替えて隔離する(実際にmain.pyで生成された
    本物のクリップ(clips/rank_entry_clips/配下)を誤って拾わないようにするため)。
    """
    monkeypatch.setattr(server_module, "DEFAULT_CLIPS_DIR", tmp_path / "clips")
    db_path = tmp_path / "test.db"
    conn = db.connect(db_path)
    db.save_match_result(
        conn,
        MatchResult(result="lose", rank_before=38.62, rank_after=38.40, league_changed=None, detected_at=now_jst()),
    )
    conn.close()
    client = TestClient(create_app(db_path))

    response = client.get("/rank-entry")

    assert response.status_code == 200
    clips = _extract_rank_entry_clips(response.text)
    assert len(clips) == 1
    assert clips[0]["result_text"] == "負け"
    assert clips[0]["rank_before"] == 38.62
    assert clips[0]["rank_after_ocr"] == 38.4
    assert clips[0]["has_clip"] is False
    assert clips[0]["recency_label"] == "最新"


def test_rank_entry_post_saves_rank_after_and_league_changed(tmp_path: Path):
    db_path = tmp_path / "test.db"
    conn = db.connect(db_path)
    match_id = db.save_match_result(
        conn,
        MatchResult(result="win", rank_before=38.62, rank_after=None, league_changed=None, detected_at=now_jst()),
    )
    conn.close()
    client = TestClient(create_app(db_path))

    response = client.post("/rank-entry", data={"match_id": str(match_id), "rank_after": "39.10"}, follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/rank-entry?status=saved"
    conn = db.connect(db_path)
    row = conn.execute("SELECT * FROM matches WHERE id = ?", (match_id,)).fetchone()
    conn.close()
    assert row["rank_after"] == 39.10
    assert row["league_changed"] == "up"


def test_rank_entry_post_with_non_numeric_value_shows_error(tmp_path: Path):
    db_path = tmp_path / "test.db"
    conn = db.connect(db_path)
    match_id = db.save_match_result(
        conn,
        MatchResult(result="win", rank_before=38.62, rank_after=None, league_changed=None, detected_at=now_jst()),
    )
    conn.close()
    client = TestClient(create_app(db_path))

    response = client.post(
        "/rank-entry", data={"match_id": str(match_id), "rank_after": "not-a-number"}, follow_redirects=False
    )

    assert response.status_code == 303
    assert response.headers["location"].startswith("/rank-entry?error=")
    conn = db.connect(db_path)
    row = conn.execute("SELECT * FROM matches WHERE id = ?", (match_id,)).fetchone()
    conn.close()
    assert row["rank_after"] is None


def test_rank_entry_post_with_unranked_match_shows_error(tmp_path: Path):
    """ランクを賭けていない試合(rank_beforeがNULL)のmatch_idを指定した場合、
    save_manual_rank_after()のValueErrorがエラー文言として表示されることを確認する。
    """
    db_path = tmp_path / "test.db"
    conn = db.connect(db_path)
    match_id = db.save_match_result(
        conn,
        MatchResult(result="win", rank_before=None, rank_after=None, league_changed=None, detected_at=now_jst()),
    )
    conn.close()
    client = TestClient(create_app(db_path))

    response = client.post("/rank-entry", data={"match_id": str(match_id), "rank_after": "39.10"}, follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"].startswith("/rank-entry?error=")


def test_rank_entry_get_shows_pending_count_when_multiple_pending(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(server_module, "DEFAULT_CLIPS_DIR", tmp_path / "clips")
    db_path = tmp_path / "test.db"
    conn = db.connect(db_path)
    for _ in range(3):
        db.save_match_result(
            conn,
            MatchResult(result="win", rank_before=38.62, rank_after=38.50, league_changed=None, detected_at=now_jst()),
        )
    conn.close()
    client = TestClient(create_app(db_path))

    response = client.get("/rank-entry")

    assert "未確定の試合: 3件" in response.text


def test_rank_entry_get_does_not_show_pending_count_when_nothing_pending(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(server_module, "DEFAULT_CLIPS_DIR", tmp_path / "clips")
    db_path = tmp_path / "test.db"
    conn = db.connect(db_path)
    match_id = db.save_match_result(
        conn,
        MatchResult(result="win", rank_before=38.62, rank_after=None, league_changed=None, detected_at=now_jst()),
    )
    db.save_manual_rank_after(conn, match_id, 38.90)
    conn.close()
    client = TestClient(create_app(db_path))

    response = client.get("/rank-entry")

    assert "未確定の試合:" not in response.text


def test_rank_entry_get_shows_blocked_message_when_rank_before_chain_unresolved(tmp_path: Path, monkeypatch):
    """Issue #308: rank_beforeのチェーンがまだ解決できていない試合が返ってきた
    場合、入力フォームの代わりに案内文言を表示することを確認する。

    通常の運用では「最古の未確定試合」のrank_beforeは常に解決済みのはずだが
    (それより古い未確定試合が無いという前提が成り立つ)、念のための防御的
    表示なので、直接DBを操作してこの状態を人為的に再現する。
    """
    monkeypatch.setattr(server_module, "DEFAULT_CLIPS_DIR", tmp_path / "clips")
    db_path = tmp_path / "test.db"
    conn = db.connect(db_path)
    match_id = db.save_match_result(
        conn,
        MatchResult(result="win", rank_before=38.62, rank_after=None, league_changed=None, detected_at=now_jst()),
    )
    conn.execute("UPDATE matches SET rank_before = NULL WHERE id = ?", (match_id,))
    conn.commit()
    conn.close()
    client = TestClient(create_app(db_path))

    response = client.get("/rank-entry")

    assert response.status_code == 200
    clips = _extract_rank_entry_clips(response.text)
    assert len(clips) == 1
    assert clips[0]["rank_before"] is None
    assert clips[0]["rank_after"] is None


def test_rank_entry_clips_api_returns_empty_list_when_no_clips(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(server_module, "DEFAULT_CLIPS_DIR", tmp_path / "clips")
    db_path = tmp_path / "test.db"
    db.connect(db_path).close()
    client = TestClient(create_app(db_path))

    response = client.get("/api/rank-entry-clips")

    assert response.status_code == 200
    assert response.json() == {"clips": [], "pending_count": 0}


def test_rank_entry_clips_api_returns_clips_newest_first_with_recency_labels(tmp_path: Path, monkeypatch):
    clips_dir = tmp_path / "clips"
    monkeypatch.setattr(server_module, "DEFAULT_CLIPS_DIR", clips_dir)
    clips_dir.mkdir()
    db_path = tmp_path / "test.db"
    conn = db.connect(db_path)
    older_id = db.save_match_result(
        conn,
        MatchResult(
            result="win",
            rank_before=38.62,
            rank_after=38.50,
            league_changed=None,
            detected_at=datetime(2026, 8, 9, 20, 0, tzinfo=timezone.utc),
        ),
    )
    newer_id = db.save_match_result(
        conn,
        MatchResult(
            result="lose",
            rank_before=38.62,
            rank_after=38.10,
            league_changed=None,
            detected_at=datetime(2026, 8, 9, 21, 0, tzinfo=timezone.utc),
        ),
    )
    conn.close()
    (clips_dir / f"{older_id}.mp4").write_bytes(b"dummy")
    (clips_dir / f"{newer_id}.mp4").write_bytes(b"dummy")
    client = TestClient(create_app(db_path))

    response = client.get("/api/rank-entry-clips")

    assert response.status_code == 200
    body = response.json()
    assert body["pending_count"] == 2
    clips = body["clips"]
    assert [clip["match_id"] for clip in clips] == [newer_id, older_id]
    assert [clip["recency_label"] for clip in clips] == ["最新", "1つ前"]
    assert clips[0]["result_text"] == "負け"
    assert clips[1]["result_text"] == "勝ち"
    assert all(clip["has_clip"] for clip in clips)


def test_rank_entry_clips_api_labels_third_clip_as_two_before(tmp_path: Path, monkeypatch):
    clips_dir = tmp_path / "clips"
    monkeypatch.setattr(server_module, "DEFAULT_CLIPS_DIR", clips_dir)
    clips_dir.mkdir()
    db_path = tmp_path / "test.db"
    conn = db.connect(db_path)
    ids = []
    for i in range(3):
        match_id = db.save_match_result(
            conn,
            MatchResult(
                result="win",
                rank_before=38.62,
                rank_after=38.50,
                league_changed=None,
                detected_at=datetime(2026, 8, 9, 20 + i, 0, tzinfo=timezone.utc),
            ),
        )
        ids.append(match_id)
        (clips_dir / f"{match_id}.mp4").write_bytes(b"dummy")
    conn.close()
    client = TestClient(create_app(db_path))

    response = client.get("/api/rank-entry-clips")

    clips = response.json()["clips"]
    assert [clip["match_id"] for clip in clips] == list(reversed(ids))
    assert [clip["recency_label"] for clip in clips] == ["最新", "1つ前", "2つ前"]


def test_rank_entry_clips_api_marks_confirmed_matches(tmp_path: Path, monkeypatch):
    clips_dir = tmp_path / "clips"
    monkeypatch.setattr(server_module, "DEFAULT_CLIPS_DIR", clips_dir)
    clips_dir.mkdir()
    db_path = tmp_path / "test.db"
    conn = db.connect(db_path)
    match_id = db.save_match_result(
        conn,
        MatchResult(result="win", rank_before=38.62, rank_after=None, league_changed=None, detected_at=now_jst()),
    )
    db.save_manual_rank_after(conn, match_id, 39.00)
    conn.close()
    (clips_dir / f"{match_id}.mp4").write_bytes(b"dummy")
    client = TestClient(create_app(db_path))

    response = client.get("/api/rank-entry-clips")

    clips = response.json()["clips"]
    assert clips[0]["rank_after"] == 39.00


def test_rank_entry_clips_api_skips_clip_without_matching_db_row(tmp_path: Path, monkeypatch):
    clips_dir = tmp_path / "clips"
    monkeypatch.setattr(server_module, "DEFAULT_CLIPS_DIR", clips_dir)
    clips_dir.mkdir()
    db_path = tmp_path / "test.db"
    db.connect(db_path).close()
    (clips_dir / "999.mp4").write_bytes(b"dummy")  # DBに対応する試合が無いidを持つファイル
    client = TestClient(create_app(db_path))

    response = client.get("/api/rank-entry-clips")

    assert response.json() == {"clips": [], "pending_count": 0}


def test_rank_entry_clip_file_serves_existing_file(tmp_path: Path, monkeypatch):
    clips_dir = tmp_path / "clips"
    monkeypatch.setattr(server_module, "DEFAULT_CLIPS_DIR", clips_dir)
    clips_dir.mkdir()
    (clips_dir / "5.mp4").write_bytes(b"dummy video bytes")
    db_path = tmp_path / "test.db"
    db.connect(db_path).close()
    client = TestClient(create_app(db_path))

    response = client.get("/rank-entry/clips/5.mp4")

    assert response.status_code == 200
    assert response.content == b"dummy video bytes"
    assert response.headers["content-type"] == "video/mp4"


def test_rank_entry_clip_file_404_when_missing(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(server_module, "DEFAULT_CLIPS_DIR", tmp_path / "clips")
    db_path = tmp_path / "test.db"
    db.connect(db_path).close()
    client = TestClient(create_app(db_path))

    response = client.get("/rank-entry/clips/5.mp4")

    assert response.status_code == 404


def test_rank_entry_clips_api_marks_has_gauge_clip_when_present(tmp_path: Path, monkeypatch):
    """Issue #312: 画面全体クリップとゲージクローズアップ動画は別ファイル・別ディレクトリ
    のため、has_gauge_clipは対応するゲージ動画が実際に存在する場合のみtrueになる。
    """
    clips_dir = tmp_path / "clips"
    gauge_clips_dir = tmp_path / "gauge_clips"
    monkeypatch.setattr(server_module, "DEFAULT_CLIPS_DIR", clips_dir)
    monkeypatch.setattr(server_module, "GAUGE_CLIPS_DIR", gauge_clips_dir)
    clips_dir.mkdir()
    gauge_clips_dir.mkdir()
    db_path = tmp_path / "test.db"
    conn = db.connect(db_path)
    with_gauge_id = db.save_match_result(
        conn,
        MatchResult(result="win", rank_before=38.62, rank_after=38.50, league_changed=None, detected_at=now_jst()),
    )
    without_gauge_id = db.save_match_result(
        conn,
        MatchResult(result="lose", rank_before=38.62, rank_after=38.10, league_changed=None, detected_at=now_jst()),
    )
    conn.close()
    (clips_dir / f"{with_gauge_id}.mp4").write_bytes(b"dummy")
    (clips_dir / f"{without_gauge_id}.mp4").write_bytes(b"dummy")
    (gauge_clips_dir / f"{with_gauge_id}.mp4").write_bytes(b"dummy")
    client = TestClient(create_app(db_path))

    response = client.get("/api/rank-entry-clips")

    clips_by_id = {clip["match_id"]: clip for clip in response.json()["clips"]}
    assert clips_by_id[with_gauge_id]["has_gauge_clip"] is True
    assert clips_by_id[without_gauge_id]["has_gauge_clip"] is False


def test_rank_entry_gauge_clip_file_serves_existing_file(tmp_path: Path, monkeypatch):
    gauge_clips_dir = tmp_path / "gauge_clips"
    monkeypatch.setattr(server_module, "GAUGE_CLIPS_DIR", gauge_clips_dir)
    gauge_clips_dir.mkdir()
    (gauge_clips_dir / "5.mp4").write_bytes(b"dummy gauge video bytes")
    db_path = tmp_path / "test.db"
    db.connect(db_path).close()
    client = TestClient(create_app(db_path))

    response = client.get("/rank-entry/gauge-clips/5.mp4")

    assert response.status_code == 200
    assert response.content == b"dummy gauge video bytes"
    assert response.headers["content-type"] == "video/mp4"


def test_rank_entry_gauge_clip_file_404_when_missing(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(server_module, "GAUGE_CLIPS_DIR", tmp_path / "gauge_clips")
    db_path = tmp_path / "test.db"
    db.connect(db_path).close()
    client = TestClient(create_app(db_path))

    response = client.get("/rank-entry/gauge-clips/5.mp4")

    assert response.status_code == 404


def test_start_web_server_thread_serves_requests_and_stops_cleanly(tmp_path: Path):
    """Issue #80のPoC: 別スレッドで起動したuvicornが実際にHTTPリクエストに
    応答し、stop()でスレッドごと正常終了できることを確認する
    (検知ループと同一プロセス内で共存できるかの技術検証)。
    """
    db_path = tmp_path / "test.db"
    db.connect(db_path).close()

    handle = start_web_server_thread(create_app(db_path), host="127.0.0.1", port=8766)
    try:
        response = httpx.get("http://127.0.0.1:8766/api/health", timeout=2.0)
        assert response.status_code == 200
        assert response.json() == {"status": "ok"}
    finally:
        handle.stop()

    assert not handle.thread.is_alive()
