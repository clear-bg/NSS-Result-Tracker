from pathlib import Path

import httpx
from fastapi.testclient import TestClient

from nss_tracker.database import db
from nss_tracker.state.match_state import MatchResult
from nss_tracker.timeutil import now_jst
from nss_tracker.web.runner import start_web_server_thread
from nss_tracker.web.server import (
    _RANK_GRAPH_LEFT_PADDING,
    _RANK_GRAPH_MARGIN_LEFT,
    _RANK_GRAPH_MARGIN_RIGHT,
    _RANK_GRAPH_VIEWBOX_WIDTH,
    _aggregate_goal_stats,
    _rank_graph_x_axis_max,
    _rank_graph_x_tick_values,
    _rank_graph_y_bounds,
    _render_rank_graph_svg,
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


def test_overlay_winrate_page_shows_readable_summary(tmp_path: Path):
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


def test_overlay_winrate_page_links_transparent_background_stylesheet(tmp_path: Path):
    """OBSのブラウザソースに重ねて配置する想定のため、文字の無い部分が
    背後の他の部品を隠さないよう背景を明示的に透過にしていることを確認する。
    """
    db_path = tmp_path / "test.db"
    db.connect(db_path).close()

    client = TestClient(create_app(db_path))

    page_response = client.get("/overlay/winrate")
    assert '<link rel="stylesheet" href="/static/overlay.css">' in page_response.text

    css_response = client.get("/static/overlay.css")
    assert css_response.status_code == 200
    assert "background: transparent" in css_response.text


def test_overlay_winrate_page_shows_dash_when_no_matches(tmp_path: Path):
    db_path = tmp_path / "test.db"
    db.connect(db_path).close()

    client = TestClient(create_app(db_path))

    response = client.get("/overlay/winrate")

    assert response.status_code == 200
    assert "勝率 -" in response.text


def test_rank_history_returns_recent_matches_oldest_first(tmp_path: Path):
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


def test_rank_history_skips_matches_without_rank_after(tmp_path: Path):
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


def test_rank_history_returns_all_matches_when_limit_env_unset(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("RANK_GRAPH_MATCH_LIMIT", raising=False)
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


def test_render_rank_graph_svg_draws_vertical_gridlines_at_x_ticks():
    """横軸の目盛り位置(1, 5, 10試合目)にも縦軸と同様の薄いグリッド線を引く。"""
    history = [{"rank_after": value, "league_changed": None} for value in [10, 20, 15, 25, 12]]

    svg = _render_rank_graph_svg(history)

    # 縦軸のグリッド線(横向き)+横軸のグリッド線(縦向き、1試合目・5試合目・拡張後の10試合目分)
    assert svg.count('class="rank-graph-gridline"') >= 3 + 3


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


def test_overlay_rank_graph_page_links_transparent_background_stylesheet(tmp_path: Path):
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
