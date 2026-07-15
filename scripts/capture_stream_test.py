"""OBS Virtual Cameraからの継続フレーム取得を実機で確認するための手動検証スクリプト。

capture_test_frame.py(単発1フレーム取得)の継続版。OBS StudioでVirtual
Cameraを起動した状態で実行し、指定秒数の間フレームを読み取り続けて、
取得できたフレーム数・実測fps・最後のフレームを表示する。

使い方: uv run python scripts/capture_stream_test.py [秒数(デフォルト5)]
"""

import sys
import time

import cv2

from nss_tracker.capture.ffmpeg_capture import FfmpegFrameReader


def main() -> None:
    duration_sec = float(sys.argv[1]) if len(sys.argv) > 1 else 5.0

    reader = FfmpegFrameReader()
    reader.start()
    print("OBS Virtual Cameraへの接続を試みています...")

    frame_count = 0
    last_frame = None
    start = time.monotonic()
    try:
        while time.monotonic() - start < duration_sec:
            frame = reader.read(timeout=5)
            if frame is None:
                print("フレームが取得できませんでした(接続失敗、またはOBS側で停止した可能性があります)")
                break
            frame_count += 1
            last_frame = frame
    finally:
        reader.stop()

    elapsed = time.monotonic() - start
    print(f"取得フレーム数: {frame_count} ({elapsed:.1f}秒, 実測 {frame_count / elapsed:.1f}fps)")

    if last_frame is not None:
        cv2.imwrite("capture_stream_test_last_frame.png", last_frame)
        print("最後のフレームを capture_stream_test_last_frame.png に保存しました")


if __name__ == "__main__":
    main()
