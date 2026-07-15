"""試合結果付き動画.mp4 の状態遷移タイムラインを把握するための診断スクリプト。

fixtures/videos/試合結果付き動画.mp4 (1280x720) を1920x1080にリサイズしつつ
5フレームごとにサンプリングし、全体平均輝度(暗転検知用)・ランクROIの
直前サンプルとの差分(ピクセル差分監視用)・バナー判定を出力する。
motion.py の閾値決定とテストのための一次データ収集用(自動テストではない)。
"""

from pathlib import Path

import cv2
import numpy as np

from nss_tracker.detection.banner import classify_banner

VIDEO_PATH = Path(__file__).parent.parent / "fixtures" / "videos" / "試合結果付き動画.mp4"
TARGET_SIZE = (1920, 1080)
RANK_ROI = (90, 600, 420, 930)
SAMPLE_STEP = 5


def main() -> None:
    cap = cv2.VideoCapture(str(VIDEO_PATH))
    prev_rank_crop = None
    idx = 0
    while True:
        ok, frame = cap.read()
        if not ok:
            break
        if idx % SAMPLE_STEP == 0:
            frame = cv2.resize(frame, TARGET_SIZE)
            mean_brightness = frame.mean()
            x1, y1, x2, y2 = RANK_ROI
            rank_crop = frame[y1:y2, x1:x2]
            if prev_rank_crop is None:
                rank_diff = 0.0
            else:
                rank_diff = float(
                    np.abs(rank_crop.astype(np.int16) - prev_rank_crop.astype(np.int16)).mean()
                )
            prev_rank_crop = rank_crop
            banner = classify_banner(frame)
            t = idx / 30.03
            print(
                f"frame={idx:4d} t={t:5.2f}s brightness={mean_brightness:6.1f} "
                f"rank_diff={rank_diff:6.2f} banner={banner}"
            )
        idx += 1
    cap.release()


if __name__ == "__main__":
    main()
