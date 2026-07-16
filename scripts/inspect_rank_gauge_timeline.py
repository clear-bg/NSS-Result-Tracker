"""実動画中のランクバッジ・ゲージがどう変化しているかをフレーム単位で調べる診断スクリプト。

fixtures/videos/metadata.json のmatch_result_frame_range前後を対象に、
StabilityMonitorのis_stable状態とread_precise_rankの結果をフレームごとに
出力する。read_precise_rank(OCR)は重いため、全フレームではなく間引いて呼ぶ。

経緯: 00_lose_red_2-3.mp4・03_lose_blue_2-3.mp4で、MatchStateMachineが
ゲージの遷移途中の値をrank_afterとして確定してしまう不具合の原因調査に使った。
StabilityMonitorは直前フレームとの差分しか見ないため、1フレームあたりの
変化量が閾値未満のままゲージが数十フレームかけて緩やかに動き続けても
「安定」の判定が崩れないことがこのスクリプトの出力で判明した
(state/match_state.pyのGRACE中の定期再読み取りで対処済み)。
使い方: `uv run python scripts/inspect_rank_gauge_timeline.py <動画名> <開始フレーム> <終了フレーム>`
"""

import sys
from pathlib import Path

import cv2

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from nss_tracker.detection.motion import StabilityMonitor, region_diff
from nss_tracker.detection.rank_ocr import GAUGE_ROI_ENLARGED, RANK_ROI, read_precise_rank

VIDEOS_DIR = Path(__file__).parent.parent / "fixtures" / "videos"
TARGET_SIZE = (1920, 1080)


def read_frames(path: Path):
    cap = cv2.VideoCapture(str(path))
    try:
        while True:
            ok, frame = cap.read()
            if not ok:
                return
            if frame.shape[1::-1] != TARGET_SIZE:
                frame = cv2.resize(frame, TARGET_SIZE)
            yield frame
    finally:
        cap.release()


def main(video_name: str, start: int, end: int, ocr_every: int = 5) -> None:
    path = VIDEOS_DIR / video_name
    monitor = StabilityMonitor(roi=RANK_ROI, stable_frames_required=15)
    prev_frame = None
    for idx, frame in enumerate(read_frames(path)):
        if idx < start:
            prev_frame = frame
            continue
        if idx > end:
            break
        diff = region_diff(prev_frame, frame, RANK_ROI) if prev_frame is not None else -1.0
        is_stable = monitor.update(frame)
        prev_frame = frame

        ocr = ""
        if idx % ocr_every == 0:
            # TRACKING_RANK区間の調査用スクリプトのため、常に拡大表示のROIを使う
            result = read_precise_rank(frame, GAUGE_ROI_ENLARGED)
            ocr = f" ocr={result}"
        print(f"frame={idx:5d} diff={diff:6.2f} stable={is_stable}{ocr}")


if __name__ == "__main__":
    name = sys.argv[1]
    start = int(sys.argv[2])
    end = int(sys.argv[3])
    main(name, start, end)
