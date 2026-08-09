import numpy as np
import pytest

from nss_tracker.rank_entry_clips import GAUGE_TICK_SEGMENTS, RankEntryClipRecorder, _draw_gauge_ticks


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
    """
    frame = np.zeros((10, 200, 3), dtype=np.uint8)

    result = _draw_gauge_ticks(frame)

    assert result.shape == frame.shape
    non_black_columns = [x for x in range(200) if (result[:, x] != 0).any()]
    # 両端(0, 20)には線を引かないため、GAUGE_TICK_SEGMENTS - 1本の線があるはず
    # (線の太さが2pxの箇所もあるため、列数は本数以上になりうる)
    assert len(non_black_columns) >= GAUGE_TICK_SEGMENTS - 1


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
