import logging

import numpy as np
import pytest

from nss_tracker import config
from nss_tracker.database import db
from nss_tracker.rank_entry_clips import (
    GAUGE_LABEL_PADDING_HEIGHT,
    GAUGE_TICK_LABEL_EXTENSION,
    GAUGE_TICK_SEGMENTS,
    PENDING_CLIP_WARNING_THRESHOLD,
    RankEntryClipRecorder,
    _draw_gauge_ticks,
)
from nss_tracker.state.match_state import MatchResult
from nss_tracker.timeutil import now_jst


def _insert_match(conn, *, rank_before: float | None, confirmed: bool) -> int:
    """テスト用に試合を1件保存し、match_idを返す(Issue #389)。

    rank_before=Noneならランクを賭けない試合(rank_before_ocrがNULL、常に
    保持数管理の対象=削除してよい)。confirmed=Falseならrank_afterをNULLの
    ままにする(未確定、rank_before_ocrが非NULLの場合のみ意味を持つ)。
    save_match_result()はランクを賭けない試合でconfig.get_room_type()
    (/adminで選択するまでNoneのまま、Issue #379)を参照するため、
    matches.room_typeのNOT NULL制約に落ちないよう先に'random'を設定しておく。
    """
    config.set_room_type("random")
    match = MatchResult(result="win", rank_before=rank_before, rank_after=None, league_changed=None, detected_at=now_jst())
    match_id = db.save_match_result(conn, match)
    if confirmed and rank_before is not None:
        db.save_manual_rank_after(conn, match_id, rank_before)
    return match_id


def _make_frame(width: int = 64, height: int = 48, value: int = 128) -> np.ndarray:
    return np.full((height, width, 3), value, dtype=np.uint8)


def test_add_frame_before_start_does_nothing():
    recorder = RankEntryClipRecorder(output_dir=None, target_sample_fps=8.0)

    exceeded = recorder.add_frame(_make_frame())

    assert exceeded is False
    assert recorder.is_recording is False


def test_start_enables_recording():
    recorder = RankEntryClipRecorder(output_dir=None)

    recorder.start(source_fps=60.0)

    assert recorder.is_recording is True


def test_add_frame_samples_at_target_fps():
    """60fps入力・目標8fpsなら、間引き間隔は round(60/8)=8フレームに1回のはず。"""
    recorder = RankEntryClipRecorder(output_dir=None, target_sample_fps=8.0)
    recorder.start(source_fps=60.0)

    for _ in range(24):  # 24フレーム分 = 24/8 = 3回サンプルされるはず
        recorder.add_frame(_make_frame())

    assert len(recorder._frames) == 3


def test_add_frame_resizes_wide_frames_to_target_width():
    recorder = RankEntryClipRecorder(output_dir=None, target_sample_fps=60.0, target_width=32)
    recorder.start(source_fps=60.0)

    recorder.add_frame(_make_frame(width=64, height=48))

    resized = recorder._frames[0]
    assert resized.shape[1] == 32
    assert resized.shape[0] == 24  # アスペクト比を維持(48 * 32/64 = 24)


def test_add_frame_keeps_frame_as_is_when_already_at_target_width():
    recorder = RankEntryClipRecorder(output_dir=None, target_sample_fps=60.0, target_width=64)
    recorder.start(source_fps=60.0)

    recorder.add_frame(_make_frame(width=64, height=48))

    assert recorder._frames[0].shape == (48, 64, 3)


def test_add_frame_upscales_narrow_frames_to_target_width():
    """Issue #312: ゲージのROIのような小さい切り出しを、見やすいサイズまで
    拡大できることを確認する(画面全体クリップの縮小方向とは逆)。
    """
    recorder = RankEntryClipRecorder(output_dir=None, target_sample_fps=60.0, target_width=128)
    recorder.start(source_fps=60.0)

    recorder.add_frame(_make_frame(width=64, height=48))

    resized = recorder._frames[0]
    assert resized.shape[1] == 128
    assert resized.shape[0] == 96  # アスペクト比を維持(48 * 128/64 = 96)


def test_add_frame_returns_true_when_max_duration_exceeded():
    recorder = RankEntryClipRecorder(
        output_dir=None, target_sample_fps=10.0, max_duration_seconds=0.2
    )
    recorder.start(source_fps=10.0)  # sample_interval=1なので毎フレームサンプルされる

    results = [recorder.add_frame(_make_frame()) for _ in range(5)]

    # 0.2秒 * 10fps = 2フレームでちょうど超える
    assert results == [False, True, True, True, True]


def test_add_frame_crops_to_crop_roi_before_resizing():
    """Issue #312: crop_roiを指定すると、画面全体ではなく指定したROIだけを
    切り出してからバッファすることを確認する。
    """
    recorder = RankEntryClipRecorder(
        output_dir=None, target_sample_fps=60.0, target_width=20, crop_roi=(10, 5, 30, 15)
    )
    recorder.start(source_fps=60.0)

    recorder.add_frame(_make_frame(width=64, height=48))

    # crop_roi(10,5,30,15) -> 20x10、target_width=20と一致するため拡大縮小なし
    assert recorder._frames[0].shape == (10, 20, 3)


def test_add_frame_applies_overlay_fn():
    recorder = RankEntryClipRecorder(
        output_dir=None,
        target_sample_fps=60.0,
        target_width=64,
        overlay_fn=lambda frame: np.zeros_like(frame),
    )
    recorder.start(source_fps=60.0)

    recorder.add_frame(_make_frame(width=64, height=48, value=200))

    assert (recorder._frames[0] == 0).all()


def test_add_frame_skips_frame_when_processing_raises(caplog):
    """crop_roi/overlay_fnによる加工で例外が起きても、add_frame()自体は
    例外を投げず、そのフレームだけ読み捨てて録画を継続することを確認する
    (Issue #312、検知ループへの影響を防ぐ設計)。
    """

    def _raising_overlay(frame: np.ndarray) -> np.ndarray:
        raise RuntimeError("boom")

    recorder = RankEntryClipRecorder(
        output_dir=None, target_sample_fps=60.0, target_width=64, overlay_fn=_raising_overlay
    )
    recorder.start(source_fps=60.0)

    with caplog.at_level("ERROR", logger="nss_tracker.rank_entry_clips"):
        exceeded = recorder.add_frame(_make_frame(width=64, height=48))

    assert exceeded is False
    assert recorder._frames == []
    assert "加工に失敗" in caplog.text


def test_draw_gauge_ticks_draws_expected_number_of_vertical_lines():
    """Issue #312: ゲージ幅をGAUGE_TICK_SEGMENTS(20)分割する目盛り線が
    実際に描画されることを確認する(各列の色が変化する回数で数える)。

    Issue #334でゲージ本体の下に白い余白を追加したため、ゲージ本体の高さの
    範囲(0:height)だけを見る(余白側は目盛り数値の黒字で別途非背景色になるため)。
    """
    height, width = 40, 200
    frame = np.zeros((height, width, 3), dtype=np.uint8)

    result = _draw_gauge_ticks(frame)

    gauge_area = result[:height, :]
    non_black_columns = [x for x in range(width) if (gauge_area[:, x] != 0).any()]
    # 両端(0, 20)には線を引かないため、GAUGE_TICK_SEGMENTS - 1本の線があるはず
    # (線の太さが2pxの箇所もあるため、列数は本数以上になりうる)
    assert len(non_black_columns) >= GAUGE_TICK_SEGMENTS - 1


def test_draw_gauge_ticks_adds_white_padding_for_labels():
    """Issue #334: 整数の目盛り数値を描画するため、ゲージ本体の下に
    GAUGE_LABEL_PADDING_HEIGHT分の白い余白を追加する(横幅は変えない)。
    """
    height, width = 40, 200
    frame = np.zeros((height, width, 3), dtype=np.uint8)

    result = _draw_gauge_ticks(frame)

    assert result.shape == (height + GAUGE_LABEL_PADDING_HEIGHT, width, 3)
    # 余白部分の背景は白(数値の黒字・目盛り線の伸び以外)
    padding_area = result[height + GAUGE_TICK_LABEL_EXTENSION + 1 :, :]
    assert (padding_area == 255).any()


def test_draw_gauge_ticks_half_step_lines_are_dashed_and_stay_within_gauge():
    """Issue #334: 0.5刻みの線は点線になり、ゲージ本体の高さ内(0:height)に
    とどまる(白い余白側へは伸びない)ことを確認する。
    """
    height, width = 40, 200
    frame = np.zeros((height, width, 3), dtype=np.uint8)

    result = _draw_gauge_ticks(frame)

    # i=1(0.5刻み)の列: x = round(200 * 1 / 20) = 10
    x = 10
    column = result[:height, x]
    magenta = np.array([255, 0, 255], dtype=np.uint8)
    is_magenta = (column == magenta).all(axis=1)
    # 点線のため、色が乗っている行・乗っていない行の両方が存在するはず
    assert is_magenta.any()
    assert not is_magenta.all()
    # ゲージ本体の高さを超えた行(余白側)には点線を伸ばさない
    assert not (result[height:, x] == magenta).all(axis=1).any()


def test_draw_gauge_ticks_full_step_lines_stay_solid_and_extend_into_padding():
    """Issue #334: 1.0刻みの線は実線のまま、白い余白側へGAUGE_TICK_LABEL_EXTENSION分
    だけ短く伸ばすことを確認する。
    """
    height, width = 40, 200
    frame = np.zeros((height, width, 3), dtype=np.uint8)

    result = _draw_gauge_ticks(frame)

    # i=2(1.0刻み)の列: x = round(200 * 2 / 20) = 20
    x = 20
    magenta = np.array([255, 0, 255], dtype=np.uint8)
    gauge_column = result[:height, x]
    assert (gauge_column == magenta).all(axis=1).all()
    extension_column = result[height : height + GAUGE_TICK_LABEL_EXTENSION, x]
    assert (extension_column == magenta).all(axis=1).all()
    # 伸ばすのはGAUGE_TICK_LABEL_EXTENSION分だけで、そこから先(数値の行)には伸ばさない
    assert not (result[height + GAUGE_TICK_LABEL_EXTENSION + 5, x] == magenta).all()


def test_draw_gauge_ticks_does_not_mutate_input_frame():
    frame = np.zeros((10, 200, 3), dtype=np.uint8)

    _draw_gauge_ticks(frame)

    assert (frame == 0).all()


def test_finish_without_start_is_noop(tmp_path):
    recorder = RankEntryClipRecorder(output_dir=tmp_path)

    recorder.finish(match_id=1)  # 例外を投げないことだけ確認

    assert list(tmp_path.glob("*.mp4")) == []


def test_finish_with_no_frames_does_not_create_file(tmp_path):
    recorder = RankEntryClipRecorder(output_dir=tmp_path, target_sample_fps=8.0)
    recorder.start(source_fps=60.0)
    # add_frameを一度も呼ばずにfinish

    recorder.finish(match_id=1)

    assert list(tmp_path.glob("*.mp4")) == []


def test_finish_encodes_clip_and_stops_recording(tmp_path):
    recorder = RankEntryClipRecorder(output_dir=tmp_path, target_sample_fps=10.0)
    recorder.start(source_fps=10.0)
    for _ in range(5):
        recorder.add_frame(_make_frame(width=64, height=48))

    recorder.finish(match_id=42)
    recorder._last_encode_thread.join(timeout=10)

    output_path = tmp_path / "42.mp4"
    assert output_path.exists()
    assert output_path.stat().st_size > 0
    assert recorder.is_recording is False


def test_finish_applies_retention_keeping_only_max_clips(tmp_path):
    recorder = RankEntryClipRecorder(
        output_dir=tmp_path, target_sample_fps=10.0, max_clips=3, db_path=tmp_path / "test.db"
    )

    for match_id in [10, 11, 12, 13]:
        recorder.start(source_fps=10.0)
        recorder.add_frame(_make_frame())
        recorder.finish(match_id=match_id)
        recorder._last_encode_thread.join(timeout=10)

    remaining = sorted(int(p.stem) for p in tmp_path.glob("*.mp4"))
    assert remaining == [11, 12, 13], "直近3件(11,12,13)のみ残り、最古の10は削除されるはず"


def test_apply_retention_uses_creation_order_not_match_id_after_db_reset(tmp_path):
    """Issue #381: DBファイルが作り直されてmatch_idが1から振り直されても、
    実際に新しく作られたクリップがファイル名の数字の小ささだけを理由に
    誤って削除されないことを確認する(以前はmatch_id昇順=生成順という前提が
    崩れ、リセット直後の最新クリップが即座に削除される不具合があった)。
    """
    recorder = RankEntryClipRecorder(
        output_dir=tmp_path, target_sample_fps=10.0, max_clips=3, db_path=tmp_path / "test.db"
    )

    # DBリセット前: match_id 9, 10, 11の順で生成(リセット後もフォルダに残り続ける想定)
    for match_id in [9, 10, 11]:
        recorder.start(source_fps=10.0)
        recorder.add_frame(_make_frame())
        recorder.finish(match_id=match_id)
        recorder._last_encode_thread.join(timeout=10)

    # DBリセット後: match_idが1から振り直される
    for match_id in [1, 2]:
        recorder.start(source_fps=10.0)
        recorder.add_frame(_make_frame())
        recorder.finish(match_id=match_id)
        recorder._last_encode_thread.join(timeout=10)

    remaining = sorted(int(p.stem) for p in tmp_path.glob("*.mp4"))
    assert remaining == [1, 2, 11], "リセット後に生成した1・2は残り、生成順が最も古い9・10が削除されるはず"


def test_apply_retention_keeps_clips_for_unconfirmed_matches(tmp_path):
    """Issue #389: max_clips件を超えていても、対応する試合が未確定
    (rank_before_ocrが非NULLかつrank_afterがNULL)のクリップは削除しない。
    確定済み・ランクを賭けていない試合のクリップは従来どおり削除される。
    """
    db_path = tmp_path / "test.db"
    conn = db.connect(db_path)
    try:
        confirmed_id = _insert_match(conn, rank_before=40.0, confirmed=True)
        pending_id = _insert_match(conn, rank_before=41.0, confirmed=False)
        unranked_id = _insert_match(conn, rank_before=None, confirmed=False)
    finally:
        conn.close()

    recorder = RankEntryClipRecorder(output_dir=tmp_path, target_sample_fps=10.0, max_clips=1, db_path=db_path)
    for match_id in [confirmed_id, pending_id, unranked_id]:
        recorder.start(source_fps=10.0)
        recorder.add_frame(_make_frame())
        recorder.finish(match_id=match_id)
        recorder._last_encode_thread.join(timeout=10)

    remaining = sorted(int(p.stem) for p in tmp_path.glob("*.mp4"))
    assert remaining == [pending_id, unranked_id], (
        "確定済み(confirmed_id)は削除、未確定(pending_id)は残り、"
        "最新1件(max_clips=1、unranked_id)は通常どおり残るはず"
    )


def test_apply_retention_warns_when_pending_clips_exceed_threshold(tmp_path, caplog):
    """Issue #389: 未確定のため削除せず残っているクリップがPENDING_CLIP_WARNING_
    THRESHOLDを超えたらWARNINGログを出すことを確認する(上限として削除は
    しない、モジュールdocstring参照)。
    """
    # max_clips=1のため、「未確定として残る」件数はmatch_ids総数-1になる。
    # PENDING_CLIP_WARNING_THRESHOLDを超えさせるには+2件生成する必要がある
    db_path = tmp_path / "test.db"
    match_ids = []
    conn = db.connect(db_path)
    try:
        for i in range(PENDING_CLIP_WARNING_THRESHOLD + 2):
            match_ids.append(_insert_match(conn, rank_before=40.0 + i, confirmed=False))
    finally:
        conn.close()

    recorder = RankEntryClipRecorder(output_dir=tmp_path, target_sample_fps=10.0, max_clips=1, db_path=db_path)
    with caplog.at_level(logging.WARNING, logger="nss_tracker.rank_entry_clips"):
        for match_id in match_ids:
            recorder.start(source_fps=10.0)
            recorder.add_frame(_make_frame())
            recorder.finish(match_id=match_id)
            recorder._last_encode_thread.join(timeout=10)

    remaining = sorted(int(p.stem) for p in tmp_path.glob("*.mp4"))
    assert remaining == sorted(match_ids), "全件未確定のため1件も削除されないはず"
    assert f"{PENDING_CLIP_WARNING_THRESHOLD}件超" in caplog.text


def test_finish_when_ffmpeg_fails_does_not_raise(tmp_path):
    """ffmpegの起動自体に失敗しても、バックグラウンドスレッド内で完結し
    呼び出し元(検知ループ)には例外を伝播させないことを確認する。
    """
    recorder = RankEntryClipRecorder(output_dir=tmp_path, target_sample_fps=10.0, ffmpeg_path="nonexistent-ffmpeg-binary")
    recorder.start(source_fps=10.0)
    recorder.add_frame(_make_frame())

    recorder.finish(match_id=1)  # 例外を投げずに戻ってくることを確認
    recorder._last_encode_thread.join(timeout=10)

    assert list(tmp_path.glob("*.mp4")) == []


def test_add_frame_stops_buffering_after_max_duration_but_keeps_recording(caplog):
    """Issue #395: 上限時間に達したら以降のフレームは追加しないが、録画状態と
    バッファはmatch_idが判明するまで保持することを確認する。

    以前は呼び出し側(main.py)が上限到達を異常系とみなしてクリップごと破棄して
    いたため、_finalize()が上限より遅い試合(実配信25試合中13試合)のクリップが
    1本も残らなくなる。上限は「収集の停止」であって「破棄」ではない。
    """
    recorder = RankEntryClipRecorder(
        output_dir=None, target_sample_fps=10.0, max_duration_seconds=0.3
    )
    recorder.start(source_fps=10.0)  # sample_interval=1なので毎フレームサンプルされる

    with caplog.at_level(logging.WARNING, logger="nss_tracker.rank_entry_clips"):
        for _ in range(20):
            recorder.add_frame(_make_frame())

    # 0.3秒 * 10fps = 3フレームで上限に達し、以降は何度呼んでも増えない
    assert len(recorder._frames) == 3
    assert recorder.is_recording is True, "上限到達後も録画状態は保持されるはず"
    assert caplog.text.count("上限時間") == 1, "上限到達のWARNINGは1回だけのはず"


def test_finish_after_max_duration_still_writes_clip(tmp_path):
    """Issue #395: 上限に達した後にmatch_idが判明した場合でも、それまでに
    バッファしたフレームでクリップが生成されることを確認する。
    """
    recorder = RankEntryClipRecorder(
        output_dir=tmp_path, target_sample_fps=10.0, max_duration_seconds=0.3
    )
    recorder.start(source_fps=10.0)
    for _ in range(20):
        recorder.add_frame(_make_frame(width=64, height=48))

    recorder.finish(match_id=7)
    recorder._last_encode_thread.join(timeout=10)

    output_path = tmp_path / "7.mp4"
    assert output_path.exists()
    assert output_path.stat().st_size > 0


def test_start_clears_duration_exceeded_from_previous_clip():
    """Issue #395: 上限に達したまま次の試合が始まった場合、新しい録画では
    再びフレームをバッファできることを確認する。
    """
    recorder = RankEntryClipRecorder(
        output_dir=None, target_sample_fps=10.0, max_duration_seconds=0.3
    )
    recorder.start(source_fps=10.0)
    for _ in range(10):
        recorder.add_frame(_make_frame())
    assert recorder.add_frame(_make_frame()) is True

    recorder.start(source_fps=10.0)

    assert recorder.add_frame(_make_frame()) is False
    assert len(recorder._frames) == 1
