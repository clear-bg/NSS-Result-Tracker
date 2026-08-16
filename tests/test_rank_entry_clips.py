import numpy as np
import pytest

from nss_tracker.rank_entry_clips import (
    GAUGE_LABEL_PADDING_HEIGHT,
    GAUGE_TICK_LABEL_EXTENSION,
    GAUGE_TICK_SEGMENTS,
    RankEntryClipRecorder,
    _draw_gauge_ticks,
)


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
    recorder = RankEntryClipRecorder(output_dir=tmp_path, target_sample_fps=10.0, max_clips=3)

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
    recorder = RankEntryClipRecorder(output_dir=tmp_path, target_sample_fps=10.0, max_clips=3)

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
