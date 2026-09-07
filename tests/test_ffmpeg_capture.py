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

VIDEO_NAME = "27_goal_blue_owngoal_hdr_off.mp4"
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
def test_frame_counters_track_produced_and_consumed(videos_dir):
    """処理落ち(フレーム抜け)の実測用カウンタを確認する。frames_consumedは
    read()の呼び出し回数(成功分)と一致し、frames_producedはそれ以上に
    なるはず(処理が追いつかない間に生成されたが読み捨てられたフレームの分)。
    """
    reader = _make_reader(videos_dir)
    reader.start()
    try:
        for expected_consumed in range(1, 6):
            frame = reader.read(timeout=10)
            assert frame is not None
            assert reader.frames_consumed == expected_consumed
        assert reader.frames_produced >= reader.frames_consumed
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


def test_frame_observer_receives_every_decoded_frame(videos_dir):
    """Issue #398: frame_observerには、read()が返さなかったフレームも含めて
    デコードした全フレームが渡ることを確認する。

    read()は「その時点の最新フレーム」しか返さないため、呼び出し側の処理が
    止まっている間に届いたフレームは読み捨てられる。0.40秒しかない暗転を
    そこで失わないようにするためのフックなので、produced数と一致することが要件。
    """
    observed = {"n": 0}

    def observer(frame):
        observed["n"] += 1

    video_path = videos_dir / VIDEO_NAME
    with FfmpegFrameReader(
        input_args=["-i", str(video_path)], frame_observer=observer
    ) as reader:
        for _ in range(5):
            reader.read(timeout=10)
        produced = reader.frames_produced

    assert produced > 0
    assert observed["n"] >= produced, (
        f"デコードした全フレームがobserverへ渡っていない(produced={produced} observed={observed['n']})"
    )


def test_frame_observer_exception_does_not_stop_reading(videos_dir):
    """Issue #398: オブザーバが例外を投げてもフレーム取得自体は継続する。"""

    def broken_observer(frame):
        raise RuntimeError("観測側の失敗")

    video_path = videos_dir / VIDEO_NAME
    with FfmpegFrameReader(
        input_args=["-i", str(video_path)], frame_observer=broken_observer
    ) as reader:
        frame = reader.read(timeout=10)

    assert frame is not None
    assert reader.error is None
