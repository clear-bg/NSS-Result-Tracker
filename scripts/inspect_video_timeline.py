"""動画の状態遷移タイムラインを把握するための診断スクリプト。

fixtures/videos/ 配下の動画を1920x1080にリサイズしつつ5フレームごとに
サンプリングし、全体平均輝度(暗転検知用)・ランクROIの直前サンプルとの
差分(ピクセル差分監視用)・バナー判定を出力する。motion.py の閾値決定の
ための一次データ収集用(自動テストではない)。

MatchStateMachine自体の状態遷移(current_state・MatchResult)を見たい場合は
inspect_match_state_timeline.pyを使うこと(本スクリプトは一段階下の生信号のみ)。

使い方: uv run python scripts/inspect_video_timeline.py <動画ファイル名>
       (fixtures/videos/ 配下のファイル名を指定する)
"""

import sys
from pathlib import Path

import cv2
import numpy as np

from nss_tracker.detection.banner import classify_banner

VIDEOS_DIR = Path(__file__).parent.parent / "fixtures" / "videos"
TARGET_SIZE = (1920, 1080)
RANK_ROI = (90, 600, 420, 930)
SAMPLE_STEP = 5


def main() -> None:
    if len(sys.argv) != 2:
        print("usage: uv run python scripts/inspect_video_timeline.py <動画ファイル名>")
        raise SystemExit(1)

    video_path = VIDEOS_DIR / sys.argv[1]
    cap = cv2.VideoCapture(str(video_path))
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
