"""OBS Virtual Cameraからのフレーム継続取得。

CLAUDE.md記載の方針どおり、cv2.VideoCaptureは使わずffmpegをサブプロセスとして
起動し、dshow経由で"OBS Virtual Camera"から生フレーム(bgr24, rawvideo)を
標準出力から読み取る。単発の1フレーム取得はscripts/capture_test_frame.pyで
動作確認済みだが、常時稼働する検知プロセスとしては継続的な読み取り・
バッファリング・フレーム抜け対策が必要(このモジュールで対応する)。

検知処理(特にOCR)が重く、フレーム取得が追いつかないことがありうる。
そのため、バックグラウンドスレッドで継続的にffmpegの標準出力を読み取り、
「最新フレームだけ」を保持する(処理が追いつかない間に届いた古いフレームは
破棄する)。検知は常に最新の画面状態が分かればよく、取りこぼしたフレームを
後から遡って処理する必要はないため、この設計で問題ない。

OBS Virtual Camera自体(dshow経由の実デバイス)を使った動作確認はこの開発
環境では行えないため未検証。input_argsを差し替えることで、実ファイルを
入力にした継続読み取り自体はテストで検証している
(tests/test_ffmpeg_capture.py参照)。実機でのOBS Virtual Camera疎通確認は
別途行うこと。
"""

import subprocess
import threading
from types import TracebackType
from typing import Optional

import imageio_ffmpeg
import numpy as np

DEFAULT_DEVICE_NAME = "OBS Virtual Camera"
DEFAULT_WIDTH = 1920
DEFAULT_HEIGHT = 1080


def dshow_input_args(device_name: str, width: int, height: int) -> list[str]:
    """OBS Virtual Camera(dshow)からの入力を指定するffmpeg引数を組み立てる。"""
    return ["-f", "dshow", "-video_size", f"{width}x{height}", "-i", f"video={device_name}"]


class FfmpegFrameReader:
    """ffmpegサブプロセスから継続的にBGR24フレームを読み取るリーダー。

    start()でサブプロセスとバックグラウンド読み取りスレッドを起動し、
    read()を呼ぶたびに「その時点で最新のフレーム」を返す。
    """

    def __init__(
        self,
        input_args: Optional[list[str]] = None,
        width: int = DEFAULT_WIDTH,
        height: int = DEFAULT_HEIGHT,
        device_name: str = DEFAULT_DEVICE_NAME,
        ffmpeg_path: Optional[str] = None,
    ) -> None:
        self._width = width
        self._height = height
        self._frame_size = width * height * 3
        self._ffmpeg_path = ffmpeg_path or imageio_ffmpeg.get_ffmpeg_exe()
        self._input_args = input_args if input_args is not None else dshow_input_args(device_name, width, height)

        self._process: Optional[subprocess.Popen] = None
        self._reader_thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()
        self._latest_frame: Optional[np.ndarray] = None
        self._new_frame_event = threading.Event()
        self._stopped = threading.Event()
        self.error: Optional[Exception] = None

    @property
    def is_running(self) -> bool:
        return self._process is not None and self._process.poll() is None

    def start(self) -> None:
        if self._process is not None:
            raise RuntimeError("already started")

        cmd = [self._ffmpeg_path, *self._input_args, "-pix_fmt", "bgr24", "-f", "rawvideo", "-"]
        self._process = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, bufsize=self._frame_size * 2
        )
        self._stopped.clear()
        self._reader_thread = threading.Thread(target=self._read_loop, daemon=True)
        self._reader_thread.start()

    def _read_loop(self) -> None:
        assert self._process is not None and self._process.stdout is not None
        try:
            while not self._stopped.is_set():
                raw = self._process.stdout.read(self._frame_size)
                if len(raw) < self._frame_size:
                    # パイプが閉じた(入力の終端、またはffmpegの異常終了)
                    break
                frame = np.frombuffer(raw, dtype=np.uint8).reshape((self._height, self._width, 3))
                with self._lock:
                    self._latest_frame = frame
                self._new_frame_event.set()
        except Exception as exc:  # noqa: BLE001 - スレッド内例外を呼び出し側に伝える
            self.error = exc
        finally:
            self._stopped.set()
            self._new_frame_event.set()

    def read(self, timeout: Optional[float] = None) -> Optional[np.ndarray]:
        """最新フレームを返す。呼び出し後は既読とし、次の新しいフレームまでread()は待機する。

        入力が終端・エラーで終了しており新しいフレームがもう来ない場合はNoneを返す。
        """
        if self._stopped.is_set() and not self._new_frame_event.is_set():
            return None
        self._new_frame_event.wait(timeout=timeout)
        with self._lock:
            frame = self._latest_frame
            self._new_frame_event.clear()
        return frame

    def stop(self) -> None:
        self._stopped.set()
        if self._process is not None:
            self._process.terminate()
            try:
                self._process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self._process.kill()
        if self._reader_thread is not None:
            self._reader_thread.join(timeout=5)

    def __enter__(self) -> "FfmpegFrameReader":
        self.start()
        return self

    def __exit__(
        self,
        exc_type: Optional[type[BaseException]],
        exc_value: Optional[BaseException],
        traceback: Optional[TracebackType],
    ) -> None:
        self.stop()
