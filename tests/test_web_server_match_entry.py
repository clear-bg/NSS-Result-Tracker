"""記録漏れ試合の手動追加ページ(/match-entry、Issue #489)のテスト。"""

from datetime import datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from nss_tracker import match_activity
from nss_tracker.database.db import connect, create_session, fetch_match
from nss_tracker.timeutil import JST
from nss_tracker.web import server as server_module
from nss_tracker.web.server import create_app


def _at(day: int, hour: int, minute: int) -> datetime:
    return datetime(2026, 11, day, hour, minute, tzinfo=JST)


def _insert(conn, session_id, detected_at, rank_before, rank_after, result="win") -> int:
    stamp = detected_at.isoformat()
    cursor = conn.execute(
        "INSERT INTO matches (session_id, detected_at, result, rank_before, rank_after, rank_staked, "
        "created_at, updated_at) VALUES (?, ?, ?, ?, ?, 1, ?, ?)",
        (session_id, stamp, result, rank_before, rank_after, stamp, stamp),
    )
    conn.commit()
    return cursor.lastrowid


@pytest.fixture()
def setup(tmp_path: Path, monkeypatch):
    """前回の配信(1試合)と、1試合目の前を記録し損ねた今回の配信(2試合)。クリップは今回の2試合分。"""
    clips_dir = tmp_path / "clips"
    gauge_dir = tmp_path / "gauge_clips"
    for directory in (clips_dir, gauge_dir):
        directory.mkdir()
    monkeypatch.setattr(server_module, "DEFAULT_CLIPS_DIR", clips_dir)
    monkeypatch.setattr(server_module, "GAUGE_CLIPS_DIR", gauge_dir)
    monkeypatch.setattr(match_activity, "_in_progress", False)

    db_path = tmp_path / "test.db"
    conn = connect(db_path)
    previous_session = create_session(conn)
    current_session = create_session(conn)
    ids = [
        _insert(conn, previous_session, _at(20, 21, 10), 43.10, 43.27),
        _insert(conn, current_session, _at(22, 21, 9), 43.27, 43.78),
        _insert(conn, current_session, _at(22, 21, 15), 43.78, 43.60, result="lose"),
    ]
    conn.close()
    for match_id in ids[1:]:
        (clips_dir / f"{match_id}.mp4").write_bytes(f"clip{match_id}".encode())
        (gauge_dir / f"{match_id}.mp4").write_bytes(f"gauge{match_id}".encode())
    return TestClient(create_app(db_path)), db_path, ids, clips_dir, gauge_dir


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
    for side in ("mine", "opponent"):
        for index in range(4):
            form[f"vs_{side}_{index}_label"] = "∞"
            form[f"vs_{side}_{index}_value"] = ""
    form.update(overrides)
    return form


def test_input_page_prefills_rank_and_self_vs_rank_from_the_chain(setup):
    client, *_ = setup
    response = client.get("/match-entry")
    assert response.status_code == 200
    html = response.text
    assert 'name="rank_tier" value="43"' in html
    assert 'name="rank_fraction" value="27"' in html
    assert 'name="vs_mine_0_value" id="vs_mine_0_value" value="43"' in html
    assert 'name="vs_mine_1_value" id="vs_mine_1_value" value=""' in html
    assert "試合中のため" not in html


def test_input_page_is_disabled_while_a_match_is_in_progress(setup, monkeypatch):
    client, *_ = setup
    monkeypatch.setattr(match_activity, "_in_progress", True)
    html = client.get("/match-entry").text
    assert "試合中のため、いまは追加できません" in html
    assert '<fieldset class="match-entry-fieldset" disabled>' in html


def test_admin_links_to_match_entry(setup):
    client, *_ = setup
    assert 'href="/match-entry"' in client.get("/admin").text


def test_confirm_page_shows_what_will_change_without_writing(setup):
    client, db_path, ids, *_ = setup
    response = client.post("/match-entry/confirm", data=_form())
    assert response.status_code == 200
    html = response.text
    assert "DBを直接書き換えます" in html
    assert f"id={ids[1]} 以降の2試合の試合IDが1つずつ後ろにずれます(クリップ4件のファイル名も変わります)" in html
    assert f"直後の試合(新id={ids[1] + 1})の試合前ランクが書き換わります" in html
    assert "43.27 → 43.51(+0.24)" in html
    assert f"{ids[1]} → {ids[1] + 1}" in html
    # 確認しましたにチェックするまで押せない
    assert 'id="match-entry-execute" disabled' in html
    conn = connect(db_path)
    try:
        assert conn.execute("SELECT COUNT(*) AS n FROM matches").fetchone()["n"] == 3
    finally:
        conn.close()


def test_confirm_returns_to_input_with_field_errors(setup):
    client, *_ = setup
    response = client.post("/match-entry/confirm", data=_form(result="", minute="99"))
    html = response.text
    assert "勝敗を選んでください" in html
    assert "分は0〜59の範囲で入力してください" in html
    # 入力値は保ったまま戻す
    assert 'name="hour" value="21"' in html


def test_confirm_rejects_a_time_in_the_middle_of_a_session(setup):
    client, *_ = setup
    html = client.post("/match-entry/confirm", data=_form(minute="12")).text
    assert "配信の途中" in html


def test_confirm_shows_non_blocking_warnings(setup):
    client, *_ = setup
    html = client.post(
        "/match-entry/confirm", data=_form(vs_mine_0_value="41", vs_opponent_0_value="40")
    ).text
    assert "自分のランク(∞41)" in html
    assert "8人中6人読み取れていません" in html


def test_back_returns_to_input_with_values(setup):
    client, *_ = setup
    html = client.post("/match-entry/back", data=_form(second="37", player_shortage="on")).text
    assert 'name="second" value="37"' in html
    assert 'id="player_shortage" name="player_shortage" checked' in html


def test_submit_without_confirmation_is_rejected(setup):
    client, db_path, *_ = setup
    html = client.post("/match-entry", data=_form()).text
    assert "「確認しました」にチェック" in html
    conn = connect(db_path)
    try:
        assert conn.execute("SELECT COUNT(*) AS n FROM matches").fetchone()["n"] == 3
    finally:
        conn.close()


def test_submit_is_rejected_while_a_match_is_in_progress(setup, monkeypatch):
    client, db_path, *_ = setup
    monkeypatch.setattr(match_activity, "_in_progress", True)
    html = client.post("/match-entry", data=_form(confirmed="on")).text
    assert "試合中のため" in html
    conn = connect(db_path)
    try:
        assert conn.execute("SELECT COUNT(*) AS n FROM matches").fetchone()["n"] == 3
    finally:
        conn.close()


def test_submit_inserts_match_renames_clips_and_backs_up(setup):
    client, db_path, ids, clips_dir, gauge_dir = setup
    response = client.post("/match-entry", data=_form(confirmed="on", second="30"), follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"].startswith(f"/match-entry/done?match_id={ids[1]}&backup=")

    conn = connect(db_path)
    try:
        added = fetch_match(conn, ids[1])
        assert added["detected_at"] == _at(22, 21, 4).replace(second=30).isoformat()
        assert added["rank_before"] == 43.27
        assert added["rank_after"] == 43.51
        assert fetch_match(conn, ids[1] + 1)["rank_before"] == 43.51
    finally:
        conn.close()

    # クリップも試合IDと一緒にずれる
    assert sorted(p.name for p in clips_dir.iterdir()) == [f"{ids[1] + 1}.mp4", f"{ids[2] + 1}.mp4"]
    assert (clips_dir / f"{ids[1] + 1}.mp4").read_bytes() == f"clip{ids[1]}".encode()
    assert (gauge_dir / f"{ids[2] + 1}.mp4").read_bytes() == f"gauge{ids[2]}".encode()

    backups = list((db_path.parent / "backups").glob("*.db"))
    assert len(backups) == 1
    backup = connect(backups[0])
    try:
        assert backup.execute("SELECT COUNT(*) AS n FROM matches").fetchone()["n"] == 3
    finally:
        backup.close()

    done = client.get(response.headers["location"]).text
    assert f"id={ids[1]} として追加しました" in done


def test_clip_rename_failure_rolls_back_db_and_files(setup, monkeypatch):
    """付け替えの途中で失敗したら(Windowsでファイルを開いている等)、付け替え済みのクリップとDBを元に戻す。"""
    client, db_path, ids, clips_dir, gauge_dir = setup
    original_rename = Path.rename
    calls = {"count": 0}

    def flaky_rename(self, target):
        calls["count"] += 1
        if calls["count"] == 2:
            raise PermissionError("in use")
        return original_rename(self, target)

    monkeypatch.setattr(Path, "rename", flaky_rename)
    html = client.post("/match-entry", data=_form(confirmed="on")).text
    monkeypatch.setattr(Path, "rename", original_rename)

    assert "追加に失敗したため、変更を取り消しました" in html
    for match_id in ids[1:]:
        assert (clips_dir / f"{match_id}.mp4").read_bytes() == f"clip{match_id}".encode()
        assert (gauge_dir / f"{match_id}.mp4").read_bytes() == f"gauge{match_id}".encode()
    assert not (clips_dir / f"{ids[2] + 1}.mp4").exists()
    conn = connect(db_path)
    try:
        assert [r["id"] for r in conn.execute("SELECT id FROM matches ORDER BY id")] == ids
        assert fetch_match(conn, ids[1])["rank_before"] == 43.27
    finally:
        conn.close()
