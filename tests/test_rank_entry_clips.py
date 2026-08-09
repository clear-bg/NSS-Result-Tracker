import numpy as np
import pytest

from nss_tracker.rank_entry_clips import RankEntryClipRecorder


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


def test_add_frame_keeps_frame_as_is_when_already_narrower_than_target():
    recorder = RankEntryClipRecorder(output_dir=None, target_sample_fps=60.0, target_width=1000)
    recorder.start(source_fps=60.0)

    recorder.add_frame(_make_frame(width=64, height=48))

    assert recorder._frames[0].shape == (48, 64, 3)


def test_add_frame_returns_true_when_max_duration_exceeded():
    recorder = RankEntryClipRecorder(
        output_dir=None, target_sample_fps=10.0, max_duration_seconds=0.2
    )
    recorder.start(source_fps=10.0)  # sample_interval=1なので毎フレームサンプルされる

    results = [recorder.add_frame(_make_frame()) for _ in range(5)]

    # 0.2秒 * 10fps = 2フレームでちょうど超える
    assert results == [False, True, True, True, True]


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
