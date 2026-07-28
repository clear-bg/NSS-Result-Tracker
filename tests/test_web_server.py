import os
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from nss_tracker.database import db
from nss_tracker.detection.vs_rank import SlotRank
from nss_tracker.state.match_state import MatchResult
from nss_tracker.timeutil import now_jst
from nss_tracker.web.runner import start_web_server_thread
from nss_tracker.web.server import (
    _BOX_PLOT_TITLE,
    _OVERLAY_REFRESH_INTERVAL_MS,
    _RANK_GRAPH_LEFT_PADDING,
    _RANK_GRAPH_MARGIN_LEFT,
    _RANK_GRAPH_MARGIN_RIGHT,
    _RANK_GRAPH_TITLE,
    _RANK_GRAPH_VIEWBOX_WIDTH,
    _VS_RANK_COMPARISON_REFRESH_INTERVAL_MS,
    _aggregate_goal_stats,
    _compute_box_stats,
    _convert_rank_tier_to_unified_scale,
    _fetch_goal_assist_totals,
    _format_vs_rank_value,
    _percentile,
    _rank_delta_axis_max,
    _rank_graph_x_axis_max,
    _rank_graph_x_tick_values,
    _rank_graph_y_bounds,
    _rank_graph_y_tick_step,
    _render_rank_delta_box_plot_svg,
    _render_rank_graph_svg,
    _summarize_vs_slot_ranks,
    create_app,
)


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
        db.save_match_result(
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
        db.save_match_result(
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


def test_winrate_with_no_sessions_returns_empty_session_counts(tmp_path: Path):
    db_path = tmp_path / "test.db"
    conn = db.connect(db_path)
    db.save_match_result(
        conn,
        MatchResult(result="win", rank_before=1, rank_after=1, league_changed=None, detected_at=now_jst()),
    )
    conn.close()

    client = TestClient(create_app(db_path))

    response = client.get("/api/winrate")

    assert response.status_code == 200
    assert response.json() == {
        "session": {"total": 0, "win": 0, "lose": 0, "draw": 0},
        "cumulative": {"total": 1, "win": 1, "lose": 0, "draw": 0},
    }


def test_winrate_splits_session_and_cumulative_counts(tmp_path: Path):
    db_path = tmp_path / "test.db"
    conn = db.connect(db_path)
    first_session_id = db.create_session(conn)
    db.save_match_result(
        conn,
        MatchResult(result="lose", rank_before=1, rank_after=1, league_changed=None, detected_at=now_jst()),
        session_id=first_session_id,
    )
    second_session_id = db.create_session(conn)
    for result in ["win", "win"]:
        db.save_match_result(
            conn,
            MatchResult(result=result, rank_before=1, rank_after=1, league_changed=None, detected_at=now_jst()),
            session_id=second_session_id,
        )
    conn.close()

    client = TestClient(create_app(db_path))

    response = client.get("/api/winrate")

    assert response.status_code == 200
    assert response.json() == {
        "session": {"total": 2, "win": 2, "lose": 0, "draw": 0},
        "cumulative": {"total": 3, "win": 2, "lose": 1, "draw": 0},
    }


def test_overlay_winrate_page_shows_readable_summary(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("GOAL_ASSIST_TOTALS_SCOPE", "session")
    db_path = tmp_path / "test.db"
    conn = db.connect(db_path)
    session_id = db.create_session(conn)
    for result in ["win", "win", "lose"]:
        db.save_match_result(
            conn,
            MatchResult(result=result, rank_before=1, rank_after=1, league_changed=None, detected_at=now_jst()),
            session_id=session_id,
        )
    conn.close()

    client = TestClient(create_app(db_path))

    response = client.get("/overlay/winrate")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert "配信セッション: 3試合" in response.text
    assert "win 2 / lose 1 / draw 0" in response.text
    assert "勝率 66.7%" in response.text
    assert "累計: 3試合" in response.text


def test_overlay_winrate_page_shows_goal_assist_totals(tmp_path: Path, monkeypatch):
    """Issue #132: 許可リストプレイヤー全員分の得点・アシスト合計を追加表示する。"""
    monkeypatch.setenv("ALLOWED_PLAYERS", "Alice,Bob")
    monkeypatch.setenv("GOAL_RECORD_MODE", "all")
    monkeypatch.setenv("GOAL_ASSIST_TOTALS_SCOPE", "session")
    db_path = tmp_path / "test.db"
    conn = db.connect(db_path)
    session_id = db.create_session(conn)
    match_id = db.save_match_result(
        conn,
        MatchResult(result="win", rank_before=1, rank_after=1, league_changed=None, detected_at=now_jst()),
        session_id=session_id,
    )
    db.save_goal(conn, match_id, "Alice", "Bob", now_jst())
    db.save_goal(conn, match_id, "Bob", None, now_jst())
    conn.close()

    client = TestClient(create_app(db_path))

    response = client.get("/overlay/winrate")

    assert response.status_code == 200
    assert "配信セッション: 得点2 / アシスト1" in response.text


def test_overlay_winrate_page_goal_assist_totals_uses_cumulative_scope(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("ALLOWED_PLAYERS", "Alice")
    monkeypatch.setenv("GOAL_RECORD_MODE", "all")
    monkeypatch.setenv("GOAL_ASSIST_TOTALS_SCOPE", "all")
    db_path = tmp_path / "test.db"
    conn = db.connect(db_path)
    old_session_id = db.create_session(conn)
    old_match_id = db.save_match_result(
        conn,
        MatchResult(result="win", rank_before=1, rank_after=1, league_changed=None, detected_at=now_jst()),
        session_id=old_session_id,
    )
    db.save_goal(conn, old_match_id, "Alice", None, now_jst())

    current_session_id = db.create_session(conn)
    current_match_id = db.save_match_result(
        conn,
        MatchResult(result="win", rank_before=1, rank_after=1, league_changed=None, detected_at=now_jst()),
        session_id=current_session_id,
    )
    db.save_goal(conn, current_match_id, "Alice", None, now_jst())
    conn.close()

    client = TestClient(create_app(db_path))

    response = client.get("/overlay/winrate")

    assert "累計: 得点2 / アシスト0" in response.text


def test_overlay_winrate_page_links_transparent_background_stylesheet(tmp_path: Path, monkeypatch):
    """OBSのブラウザソースに重ねて配置する想定のため、文字の無い部分が
    背後の他の部品を隠さないよう背景を明示的に透過にしていることを確認する。
    """
    monkeypatch.setenv("GOAL_ASSIST_TOTALS_SCOPE", "session")
    db_path = tmp_path / "test.db"
    db.connect(db_path).close()

    client = TestClient(create_app(db_path))

    page_response = client.get("/overlay/winrate")
    assert '<link rel="stylesheet" href="/static/overlay.css">' in page_response.text

    css_response = client.get("/static/overlay.css")
    assert css_response.status_code == 200
    assert "background: transparent" in css_response.text


def test_overlay_winrate_page_shows_dash_when_no_matches(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("GOAL_ASSIST_TOTALS_SCOPE", "session")
    db_path = tmp_path / "test.db"
    db.connect(db_path).close()

    client = TestClient(create_app(db_path))

    response = client.get("/overlay/winrate")

    assert response.status_code == 200
    assert "勝率 -" in response.text
    assert "配信セッション: 得点0 / アシスト0" in response.text


def test_rank_history_returns_recent_matches_oldest_first(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("RANK_GRAPH_MATCH_LIMIT", "all")
    db_path = tmp_path / "test.db"
    conn = db.connect(db_path)
    for i, league_changed in enumerate([None, "up", None]):
        db.save_match_result(
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
    db.save_match_result(
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


def test_rank_history_returns_all_matches_when_limit_env_is_all(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("RANK_GRAPH_MATCH_LIMIT", "all")
    db_path = tmp_path / "test.db"
    conn = db.connect(db_path)
    for i in range(35):
        db.save_match_result(
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
        db.save_match_result(
            conn,
            MatchResult(result="win", rank_before=i, rank_after=i + 1, league_changed=None, detected_at=now_jst()),
        )
    conn.close()

    client = TestClient(create_app(db_path))

    response = client.get("/api/rank-history")

    matches = response.json()["matches"]
    assert [m["rank_after"] for m in matches] == [3.0, 4.0, 5.0]


def test_render_rank_graph_svg_with_no_data_shows_empty_message():
    svg = _render_rank_graph_svg([])

    assert "データがありません" in svg
    assert "<polyline" not in svg


def test_render_rank_graph_svg_always_shows_title():
    history = [{"rank_after": value, "league_changed": None} for value in [10, 20, 15]]

    assert f'class="rank-graph-title">{_RANK_GRAPH_TITLE}<' in _render_rank_graph_svg(history)
    assert f'class="rank-graph-title">{_RANK_GRAPH_TITLE}<' in _render_rank_graph_svg([])


def test_render_rank_graph_svg_always_shows_panel_behind_other_elements():
    """Issue #113: 配信画面での視認性対策の半透明パネルは、他の要素より先(=一番背面)に描画する。"""
    history = [{"rank_after": value, "league_changed": None} for value in [10, 20, 15]]

    svg = _render_rank_graph_svg(history)

    assert 'class="rank-graph-panel"' in svg
    assert svg.index('class="rank-graph-panel"') < svg.index('class="rank-graph-title"')
    assert 'class="rank-graph-panel"' in _render_rank_graph_svg([])


def test_render_rank_graph_svg_with_single_point_does_not_divide_by_zero():
    svg = _render_rank_graph_svg([{"rank_after": 10, "league_changed": None}])

    assert svg.count("<circle") == 1


def test_render_rank_graph_svg_with_flat_values_does_not_divide_by_zero():
    history = [{"rank_after": 5, "league_changed": None} for _ in range(3)]

    svg = _render_rank_graph_svg(history)

    assert svg.count("<circle") == 3


def test_render_rank_graph_svg_points_are_always_white_regardless_of_league_changed():
    """ユーザーとの相談で、昇格/降格による点の色分けはしない(全て白)方針にした。"""
    history = [
        {"rank_after": 1, "league_changed": None},
        {"rank_after": 2, "league_changed": "up"},
        {"rank_after": 1, "league_changed": "down"},
    ]

    svg = _render_rank_graph_svg(history)

    assert svg.count('class="rank-graph-point"') == 3
    assert "rank-graph-point-up" not in svg
    assert "rank-graph-point-down" not in svg


def test_render_rank_graph_svg_draws_frame_and_axis_ticks():
    history = [{"rank_after": value, "league_changed": None} for value in [10, 20, 15, 25, 12]]

    svg = _render_rank_graph_svg(history)

    assert 'class="rank-graph-frame"' in svg
    # 縦軸: 最小値10・最大値25を含む整数の目盛りラベルが表示されること
    assert ">10<" in svg
    assert ">25<" in svg
    # 横軸: 1試合目と、5試合しか無くても軸を上回るまで拡張した10試合目分の目盛りが
    # 表示されること(_rank_graph_x_axis_max(5) == 10)
    assert ">1<" in svg
    assert ">5<" in svg
    assert ">10<" in svg


def test_render_rank_graph_svg_outlier_value_does_not_flood_y_axis_ticks():
    """Issue #123: OCR誤読等で1件だけ極端な外れ値が混ざり縦軸の範囲が広がっても、
    目盛りラベルの本数が一定数以下に収まることを確認する(修正前は412本の目盛りが
    密集して描画が崩れていた)。
    """
    history = [{"rank_after": value, "league_changed": None} for value in [40, 41, 40, 2, 41, 40, 411, 40]]

    svg = _render_rank_graph_svg(history)

    assert svg.count('class="rank-graph-tick-label"') <= 25


def test_render_rank_graph_svg_draws_vertical_gridlines_at_x_ticks():
    """横軸の目盛り位置(1, 5, 10試合目)にも縦軸と同様の薄いグリッド線を引く。"""
    history = [{"rank_after": value, "league_changed": None} for value in [10, 20, 15, 25, 12]]

    svg = _render_rank_graph_svg(history)

    # 縦軸のグリッド線(横向き)+横軸のグリッド線(縦向き、1試合目・5試合目・拡張後の10試合目分)
    assert svg.count('class="rank-graph-gridline"') >= 3 + 3


def test_render_rank_graph_svg_adds_half_step_minor_gridlines_when_step_is_one():
    """Issue #146: 目盛り間隔が1(通常運用)のとき、整数目盛りの間に0.5刻みの
    補助グリッド線を追加する。ラベル・目盛り線自体は増やさない。
    """
    history = [{"rank_after": value, "league_changed": None} for value in [10, 12]]

    svg = _render_rank_graph_svg(history)

    axis_min, axis_max = _rank_graph_y_bounds(10, 12)
    assert _rank_graph_y_tick_step(axis_max - axis_min) == 1
    assert svg.count('class="rank-graph-gridline-minor"') == axis_max - axis_min
    # 0.5刻みの補助線に対応するラベル(小数)は追加しない
    assert ".5<" not in svg


def test_render_rank_graph_svg_omits_minor_gridlines_when_step_widens():
    """Issue #146: 外れ値で目盛り間隔が1以外に広がった場合、0.5刻みの補助線は追加しない
    (ユーザーとの相談で決定、issue #146のコメント参照)。
    """
    history = [{"rank_after": value, "league_changed": None} for value in [40, 41, 40, 2, 41, 40, 411, 40]]

    svg = _render_rank_graph_svg(history)

    axis_min, axis_max = _rank_graph_y_bounds(2, 411)
    assert _rank_graph_y_tick_step(axis_max - axis_min) != 1
    assert 'class="rank-graph-gridline-minor"' not in svg


def test_render_rank_graph_svg_last_point_stops_short_of_right_edge():
    """一番右の点は、実際の試合数を上回るまで拡張した横軸(x_axis_max)を使うことで
    枠の右端に接しないようにする(縦軸のbounds拡張と同じ考え方、ユーザーとの相談で決定)。
    """
    history = [{"rank_after": value, "league_changed": None} for value in [10, 20, 30]]

    svg = _render_rank_graph_svg(history)

    plot_left = _RANK_GRAPH_MARGIN_LEFT
    plot_right = _RANK_GRAPH_VIEWBOX_WIDTH - _RANK_GRAPH_MARGIN_RIGHT
    points_left = plot_left + _RANK_GRAPH_LEFT_PADDING
    x_axis_max_index = _rank_graph_x_axis_max(len(history)) - 1
    expected_right_x = points_left + (plot_right - points_left) * (len(history) - 1) / x_axis_max_index

    assert expected_right_x < plot_right
    assert f'cx="{expected_right_x:.1f}"' in svg


def test_render_rank_graph_svg_first_point_stops_short_of_left_edge():
    """一番左の点が枠の左端に接しないよう、左側にRANK_GRAPH_LEFT_PADDING分の余白を空ける。"""
    history = [{"rank_after": value, "league_changed": None} for value in [10, 20, 30]]

    svg = _render_rank_graph_svg(history)

    expected_left_x = _RANK_GRAPH_MARGIN_LEFT + _RANK_GRAPH_LEFT_PADDING
    assert f'cx="{expected_left_x:.1f}"' in svg


def test_rank_graph_x_axis_max_extends_beyond_uneven_match_count():
    # ユーザーの例: 23試合なら25まで表示する
    assert _rank_graph_x_axis_max(23) == 25


def test_rank_graph_x_axis_max_widens_further_when_count_is_exact_multiple():
    # ちょうど20試合でも、一番右の点が軸の端に接してしまうためさらに1段広げる
    assert _rank_graph_x_axis_max(20) == 25


def test_rank_graph_x_axis_max_small_count():
    assert _rank_graph_x_axis_max(3) == 5


def test_rank_graph_x_tick_values_always_includes_one_and_steps_of_five():
    assert _rank_graph_x_tick_values(25) == [1, 5, 10, 15, 20, 25]


def test_rank_graph_x_tick_values_small_axis_max():
    assert _rank_graph_x_tick_values(5) == [1, 5]


def test_render_rank_graph_svg_flat_values_widens_y_axis_around_the_value():
    """全試合が同じランク値でも、軸の下限・上限を1つ広げて点が端に接しないようにする。"""
    history = [{"rank_after": 42, "league_changed": None} for _ in range(3)]

    svg = _render_rank_graph_svg(history)

    assert ">41<" in svg
    assert ">42<" in svg
    assert ">43<" in svg


def test_overlay_rank_graph_page_links_transparent_background_stylesheet(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("RANK_GRAPH_MATCH_LIMIT", "all")
    db_path = tmp_path / "test.db"
    conn = db.connect(db_path)
    db.save_match_result(
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


def test_aggregate_goal_stats_returns_empty_list_for_no_rows():
    assert _aggregate_goal_stats([]) == []


def test_goal_stats_endpoint_scoped_to_current_session(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("ALLOWED_PLAYERS", "Alice,Bob")
    monkeypatch.setenv("GOAL_RECORD_MODE", "all")
    db_path = tmp_path / "test.db"
    conn = db.connect(db_path)

    old_session_id = db.create_session(conn)
    old_match_id = db.save_match_result(
        conn,
        MatchResult(result="win", rank_before=1, rank_after=1, league_changed=None, detected_at=now_jst()),
        session_id=old_session_id,
    )
    db.save_goal(conn, old_match_id, "Alice", None, now_jst())

    current_session_id = db.create_session(conn)
    current_match_id = db.save_match_result(
        conn,
        MatchResult(result="win", rank_before=1, rank_after=1, league_changed=None, detected_at=now_jst()),
        session_id=current_session_id,
    )
    db.save_goal(conn, current_match_id, "Bob", "Alice", now_jst())
    conn.close()

    client = TestClient(create_app(db_path))

    response = client.get("/api/goal-stats")

    assert response.status_code == 200
    assert response.json() == {
        "players": [
            {"name": "Bob", "goals": 1, "assists": 0, "involvement": 1},
            {"name": "Alice", "goals": 0, "assists": 1, "involvement": 1},
        ]
    }


def test_goal_stats_endpoint_empty_when_no_sessions(tmp_path: Path):
    db_path = tmp_path / "test.db"
    db.connect(db_path).close()

    client = TestClient(create_app(db_path))

    response = client.get("/api/goal-stats")

    assert response.json() == {"players": []}


def test_overlay_goal_stats_page_shows_readable_summary(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("ALLOWED_PLAYERS", "Alice,Bob")
    monkeypatch.setenv("GOAL_RECORD_MODE", "all")
    db_path = tmp_path / "test.db"
    conn = db.connect(db_path)
    session_id = db.create_session(conn)
    match_id = db.save_match_result(
        conn,
        MatchResult(result="win", rank_before=1, rank_after=1, league_changed=None, detected_at=now_jst()),
        session_id=session_id,
    )
    db.save_goal(conn, match_id, "Alice", None, now_jst())
    conn.close()

    client = TestClient(create_app(db_path))

    response = client.get("/overlay/goal-stats")

    assert response.status_code == 200
    assert '<link rel="stylesheet" href="/static/overlay.css">' in response.text
    assert "Alice: 得点 1 / アシスト 0 (関与 1)" in response.text

    css_response = client.get("/static/overlay.css")
    assert "background: transparent" in css_response.text


def test_overlay_goal_stats_page_hides_name_when_single_allowed_player(tmp_path: Path, monkeypatch):
    """許可リストが1名だけの場合(=配信者本人が自明)は、名前を出さず得点/アシストのみ表示する。"""
    monkeypatch.setenv("ALLOWED_PLAYERS", "Alice")
    monkeypatch.setenv("GOAL_RECORD_MODE", "all")
    db_path = tmp_path / "test.db"
    conn = db.connect(db_path)
    session_id = db.create_session(conn)
    match_id = db.save_match_result(
        conn,
        MatchResult(result="win", rank_before=1, rank_after=1, league_changed=None, detected_at=now_jst()),
        session_id=session_id,
    )
    db.save_goal(conn, match_id, "Alice", None, now_jst())
    conn.close()

    client = TestClient(create_app(db_path))

    response = client.get("/overlay/goal-stats")

    assert "Alice" not in response.text
    assert "得点 1 / アシスト 0 (関与 1)" in response.text


def test_overlay_goal_stats_page_shows_empty_message_when_no_data(tmp_path: Path):
    db_path = tmp_path / "test.db"
    db.connect(db_path).close()

    client = TestClient(create_app(db_path))

    response = client.get("/overlay/goal-stats")

    assert "データがありません" in response.text


def test_fetch_goal_assist_totals_sums_across_allowed_players(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("ALLOWED_PLAYERS", "Alice,Bob")
    monkeypatch.setenv("GOAL_RECORD_MODE", "all")
    db_path = tmp_path / "test.db"
    conn = db.connect(db_path)
    session_id = db.create_session(conn)
    match_id = db.save_match_result(
        conn,
        MatchResult(result="win", rank_before=1, rank_after=1, league_changed=None, detected_at=now_jst()),
        session_id=session_id,
    )
    db.save_goal(conn, match_id, "Alice", "Bob", now_jst())
    db.save_goal(conn, match_id, "Bob", None, now_jst())
    conn.close()

    assert _fetch_goal_assist_totals(db_path, "session") == {"goals": 2, "assists": 1}


def test_fetch_goal_assist_totals_excludes_disallowed_players(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("ALLOWED_PLAYERS", "Alice")
    monkeypatch.setenv("GOAL_RECORD_MODE", "all")
    db_path = tmp_path / "test.db"
    conn = db.connect(db_path)
    session_id = db.create_session(conn)
    match_id = db.save_match_result(
        conn,
        MatchResult(result="win", rank_before=1, rank_after=1, league_changed=None, detected_at=now_jst()),
        session_id=session_id,
    )
    db.save_goal(conn, match_id, "Stranger", None, now_jst())
    conn.close()

    assert _fetch_goal_assist_totals(db_path, "session") == {"goals": 0, "assists": 0}


def test_fetch_goal_assist_totals_returns_zero_when_no_sessions(tmp_path: Path):
    db_path = tmp_path / "test.db"
    db.connect(db_path).close()

    assert _fetch_goal_assist_totals(db_path, "session") == {"goals": 0, "assists": 0}


def test_goal_assist_totals_endpoint_uses_configured_scope(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("ALLOWED_PLAYERS", "Alice")
    monkeypatch.setenv("GOAL_RECORD_MODE", "all")
    monkeypatch.setenv("GOAL_ASSIST_TOTALS_SCOPE", "all")
    db_path = tmp_path / "test.db"
    conn = db.connect(db_path)
    old_session_id = db.create_session(conn)
    old_match_id = db.save_match_result(
        conn,
        MatchResult(result="win", rank_before=1, rank_after=1, league_changed=None, detected_at=now_jst()),
        session_id=old_session_id,
    )
    db.save_goal(conn, old_match_id, "Alice", None, now_jst())

    current_session_id = db.create_session(conn)
    current_match_id = db.save_match_result(
        conn,
        MatchResult(result="win", rank_before=1, rank_after=1, league_changed=None, detected_at=now_jst()),
        session_id=current_session_id,
    )
    conn.close()

    client = TestClient(create_app(db_path))

    response = client.get("/api/goal-assist-totals")

    assert response.status_code == 200
    assert response.json() == {"goals": 1, "assists": 0}


def test_match_log_returns_recent_results_oldest_first(tmp_path: Path):
    db_path = tmp_path / "test.db"
    conn = db.connect(db_path)
    for result in ["win", "lose", "draw", "win"]:
        db.save_match_result(
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
        db.save_match_result(
            conn,
            MatchResult(result="win", rank_before=i, rank_after=i, league_changed=None, detected_at=now_jst()),
        )
    conn.close()

    client = TestClient(create_app(db_path))

    response = client.get("/api/match-log")

    assert len(response.json()["results"]) == 10


def test_overlay_match_log_page_shows_win_lose_draw_letters(tmp_path: Path):
    db_path = tmp_path / "test.db"
    conn = db.connect(db_path)
    for result in ["win", "win", "lose", "draw"]:
        db.save_match_result(
            conn,
            MatchResult(result=result, rank_before=1, rank_after=1, league_changed=None, detected_at=now_jst()),
        )
    conn.close()

    client = TestClient(create_app(db_path))

    response = client.get("/overlay/match-log")

    assert response.status_code == 200
    assert '<link rel="stylesheet" href="/static/overlay.css">' in response.text
    assert "WWLD" in response.text

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


def test_format_vs_rank_value_returns_none_when_total_missing():
    """Issue #113: 直近試合自体が無い場合の表示(none VS none)と表記を揃える。"""
    assert _format_vs_rank_value({"total": None, "known_count": 0, "unknown_count": 4}) == "none"


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


def test_overlay_vs_rank_comparison_page_shows_none_placeholders_when_no_data(tmp_path: Path):
    """データが無い場合も表示形式は崩さず、値をnoneにするだけにする。"""
    db_path = tmp_path / "test.db"
    db.connect(db_path).close()

    client = TestClient(create_app(db_path))

    response = client.get("/overlay/vs-rank-comparison")

    assert '<span class="vs-rank-pill" style="background-color: #666666;">none</span>' in response.text
    assert '<div class="vs-rank-caption">Rank</div>' in response.text


def test_overlay_vs_rank_comparison_page_shows_none_for_side_with_only_unknown_members(tmp_path: Path):
    """Issue #113: VS画面自体は検知できているが片側が全員不明人数の場合も、noneで表記を揃える。"""
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
    assert '<span class="vs-rank-pill" style="background-color: #f87abe;">none</span>' in response.text


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
    db.save_match_result(
        conn,
        MatchResult(result="win", rank_before=10, rank_after=12, league_changed=None, detected_at=now_jst()),
        session_id=session_id,
    )
    db.save_match_result(
        conn,
        MatchResult(result="lose", rank_before=10, rank_after=8, league_changed=None, detected_at=now_jst()),
        session_id=session_id,
    )
    db.save_match_result(
        conn,
        MatchResult(result="draw", rank_before=10, rank_after=10, league_changed=None, detected_at=now_jst()),
        session_id=session_id,
    )
    db.save_match_result(
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
    db.save_match_result(
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
    db.save_match_result(
        conn,
        MatchResult(result="win", rank_before=10, rank_after=13, league_changed=None, detected_at=now_jst()),
        session_id=old_session_id,
    )
    current_session_id = db.create_session(conn)
    db.save_match_result(
        conn,
        MatchResult(result="lose", rank_before=10, rank_after=9, league_changed=None, detected_at=now_jst()),
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
    db.save_match_result(
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
        "/overlay/winrate",
        "/overlay/rank-graph",
        "/overlay/goal-stats",
        "/overlay/match-log",
        "/overlay/rank-delta-distribution",
    ],
)
def test_overlay_pages_include_refresh_script_with_default_interval(tmp_path: Path, path: str, monkeypatch):
    monkeypatch.setenv("RANK_DELTA_DISTRIBUTION_SCOPE", "session")
    monkeypatch.setenv("RANK_GRAPH_MATCH_LIMIT", "all")
    monkeypatch.setenv("GOAL_ASSIST_TOTALS_SCOPE", "session")
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
        "RANK_DELTA_DISTRIBUTION_SCOPE=all\n",
        encoding="utf-8",
    )


def test_admin_get_shows_current_settings(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("ALLOWED_PLAYERS", "Alice,Bob")
    monkeypatch.setenv("GOAL_RECORD_MODE", "allowlist")
    monkeypatch.setenv("RANK_GRAPH_MATCH_LIMIT", "30")
    monkeypatch.setenv("RANK_DELTA_DISTRIBUTION_SCOPE", "session")
    client = TestClient(create_app(tmp_path / "test.db"))

    response = client.get("/admin")

    assert response.status_code == 200
    assert 'value="Alice,Bob"' in response.text
    assert 'value="30"' in response.text


def test_admin_post_updates_settings_and_persists_to_env_file(tmp_path: Path, monkeypatch):
    env_path = tmp_path / ".env"
    _write_admin_env_file(env_path)
    monkeypatch.setattr("nss_tracker.config.find_dotenv", lambda: str(env_path))
    monkeypatch.setenv("ALLOWED_PLAYERS", "OldName")
    monkeypatch.setenv("GOAL_RECORD_MODE", "all")
    monkeypatch.setenv("RANK_GRAPH_MATCH_LIMIT", "all")
    monkeypatch.setenv("RANK_DELTA_DISTRIBUTION_SCOPE", "all")
    client = TestClient(create_app(tmp_path / "test.db"))

    response = client.post(
        "/admin",
        data={
            "allowed_players": "NewName",
            "goal_record_mode": "allowlist",
            "rank_graph_match_limit": "10",
            "rank_delta_distribution_scope": "session",
        },
    )

    assert response.status_code == 200
    assert response.url.path == "/admin"
    assert response.url.params["status"] == "updated"
    assert os.environ["ALLOWED_PLAYERS"] == "NewName"
    assert os.environ["RANK_GRAPH_MATCH_LIMIT"] == "10"


def test_admin_post_with_invalid_value_shows_error_and_does_not_update(tmp_path: Path, monkeypatch):
    env_path = tmp_path / ".env"
    _write_admin_env_file(env_path)
    monkeypatch.setattr("nss_tracker.config.find_dotenv", lambda: str(env_path))
    monkeypatch.setenv("ALLOWED_PLAYERS", "OldName")
    monkeypatch.setenv("GOAL_RECORD_MODE", "all")
    monkeypatch.setenv("RANK_GRAPH_MATCH_LIMIT", "all")
    monkeypatch.setenv("RANK_DELTA_DISTRIBUTION_SCOPE", "all")
    client = TestClient(create_app(tmp_path / "test.db"))

    response = client.post(
        "/admin",
        data={
            "allowed_players": "NewName",
            "goal_record_mode": "not-a-real-mode",
            "rank_graph_match_limit": "10",
            "rank_delta_distribution_scope": "session",
        },
    )

    assert response.status_code == 200
    assert response.url.params["error"]
    assert "GOAL_RECORD_MODE" in response.url.params["error"]
    assert os.environ["ALLOWED_PLAYERS"] == "OldName"


def test_admin_post_logs_info_message_on_success(tmp_path: Path, monkeypatch, caplog):
    """Issue #129: 更新内容はINFOレベルで記録する(DEBUGモードでも表示されるため)。"""
    env_path = tmp_path / ".env"
    _write_admin_env_file(env_path)
    monkeypatch.setattr("nss_tracker.config.find_dotenv", lambda: str(env_path))
    monkeypatch.setenv("ALLOWED_PLAYERS", "OldName")
    monkeypatch.setenv("GOAL_RECORD_MODE", "all")
    monkeypatch.setenv("RANK_GRAPH_MATCH_LIMIT", "all")
    monkeypatch.setenv("RANK_DELTA_DISTRIBUTION_SCOPE", "all")
    client = TestClient(create_app(tmp_path / "test.db"))

    with caplog.at_level("INFO", logger="nss_tracker.web"):
        client.post(
            "/admin",
            data={
                "allowed_players": "NewName",
                "goal_record_mode": "allowlist",
                "rank_graph_match_limit": "10",
                "rank_delta_distribution_scope": "session",
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
    client = TestClient(create_app(tmp_path / "test.db"))

    with caplog.at_level("WARNING", logger="nss_tracker.web"):
        client.post(
            "/admin",
            data={
                "allowed_players": "NewName",
                "goal_record_mode": "not-a-real-mode",
                "rank_graph_match_limit": "10",
                "rank_delta_distribution_scope": "session",
            },
        )

    messages = [record.message for record in caplog.records if record.name == "nss_tracker.web"]
    assert any("GOAL_RECORD_MODE" in message for message in messages)


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
