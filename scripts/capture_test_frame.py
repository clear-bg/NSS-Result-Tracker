import subprocess
import numpy as np
import cv2
import imageio_ffmpeg

ffmpeg_path = imageio_ffmpeg.get_ffmpeg_exe()
WIDTH, HEIGHT = 1920, 1080
FRAME_SIZE = WIDTH * HEIGHT * 3  # bgr24

cmd = [
    ffmpeg_path,
    "-f", "dshow",
    "-video_size", f"{WIDTH}x{HEIGHT}",
    "-i", "video=OBS Virtual Camera",
    "-pix_fmt", "bgr24",
    "-f", "rawvideo",
    "-"
]

proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, bufsize=10**8)
raw = proc.stdout.read(FRAME_SIZE)

if len(raw) == FRAME_SIZE:
    frame = np.frombuffer(raw, dtype=np.uint8).reshape((HEIGHT, WIDTH, 3))
    cv2.imwrite("test_frame_ffmpeg.png", frame)
    print("成功:", frame.shape)
else:
    print("バイト数が足りません:", len(raw), "/", FRAME_SIZE)

proc.terminate()
