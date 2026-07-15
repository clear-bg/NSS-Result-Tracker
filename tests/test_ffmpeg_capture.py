"""FfmpegFrameReaderのテスト。

OBS Virtual Camera(dshow)自体はこの環境で検証できないため、
fixtures/videos/ にある実動画ファイル(1920x1080)をffmpegの入力として
差し替えることで、継続読み取り・最新フレーム保持・停止処理を検証する。
dshow経由の実機疎通確認は別途OBS・Switch起動環境で行うこと。
"""

import numpy as np
import pytest

from conftest import requires_video_fixtures
from nss_tracker.capture.ffmpeg_capture import FfmpegFrameReader

VIDEO_NAME = "01_win_blue_2-1.mp4"
WIDTH, HEIGHT = 1920, 1080


def _make_reader(videos_dir):
    video_path = videos_dir / VIDEO_NAME
    return FfmpegFrameReader(input_args=["-i", str(video_path)], width=WIDTH, height=HEIGHT)


@requires_video_fixtures
def test_reads_frames_with_correct_shape_and_dtype(videos_dir):
    reader = _make_reader(videos_dir)
    reader.start()
    try:
        frame = reader.read(timeout=10)
        assert frame is not None
        assert frame.shape == (HEIGHT, WIDTH, 3)
        assert frame.dtype == np.uint8
    finally:
        reader.stop()


@requires_video_fixtures
def test_consecutive_reads_return_different_frames(videos_dir):
    reader = _make_reader(videos_dir)
    reader.start()
    try:
        frames = [reader.read(timeout=10) for _ in range(5)]
        assert all(frame is not None for frame in frames)
        # 動画が進行しているので、全フレームが同一ではないはず
        assert not all(np.array_equal(frames[0], frame) for frame in frames[1:])
    finally:
        reader.stop()


@requires_video_fixtures
def test_read_returns_none_after_input_ends(videos_dir):
    reader = _make_reader(videos_dir)
    reader.start()
    try:
        frame = reader.read(timeout=10)
        assert frame is not None
        # 動画の終端まで読み切る
        while frame is not None:
            frame = reader.read(timeout=10)
        assert frame is None
    finally:
        reader.stop()


@requires_video_fixtures
def test_stop_terminates_the_process(videos_dir):
    reader = _make_reader(videos_dir)
    reader.start()
    assert reader.is_running
    reader.read(timeout=10)

    reader.stop()
    assert not reader.is_running


@requires_video_fixtures
def test_context_manager_starts_and_stops(videos_dir):
    video_path = videos_dir / VIDEO_NAME
    with FfmpegFrameReader(input_args=["-i", str(video_path)], width=WIDTH, height=HEIGHT) as reader:
        assert reader.is_running
        frame = reader.read(timeout=10)
        assert frame is not None
    assert not reader.is_running


def test_start_twice_raises():
    reader = FfmpegFrameReader(input_args=["-f", "lavfi", "-i", "color=c=black:s=2x2"], width=2, height=2)
    reader.start()
    try:
        with pytest.raises(RuntimeError):
            reader.start()
    finally:
        reader.stop()
