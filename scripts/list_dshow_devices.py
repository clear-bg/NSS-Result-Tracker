import subprocess
import imageio_ffmpeg

ffmpeg_path = imageio_ffmpeg.get_ffmpeg_exe()

result = subprocess.run(
    [ffmpeg_path, "-list_devices", "true", "-f", "dshow", "-i", "dummy"],
    capture_output=True  # text=Trueを外す
)

output = result.stderr.decode("utf-8", errors="replace")
print(output)
