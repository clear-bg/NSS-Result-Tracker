"""MatchStateMachineの状態遷移タイムラインを把握するための診断スクリプト。

動画を1本指定すると、フレームを流しながらcurrent_stateが切り替わった
フレーム番号と、process_frame()がMatchResultを返したフレーム番号を出力する。

注意: この出力はMatchStateMachine自身の判定結果であり、それをそのまま
fixtures/videos/metadata.json の banner_confirmed_frame_range /
match_result_frame_range に転記してはいけない(実装のバグをそのまま
「正解」として固定してしまい、テストとして意味がなくなるため)。
あくまで「だいたいこのあたりのフレームを見ればよい」という当たりを
つけるための補助として使い、実際の期待値は動画を目で見て確認すること。

使い方: uv run python scripts/inspect_match_state_timeline.py <動画ファイル名>
       (fixtures/videos/ 配下のファイル名を指定する)
"""

import sys
from pathlib import Path

import cv2

from nss_tracker.detection.motion import StabilityMonitor
from nss_tracker.detection.rank_ocr import RANK_ROI
from nss_tracker.state.match_state import MatchStateMachine

VIDEOS_DIR = Path(__file__).parent.parent / "fixtures" / "videos"
TARGET_SIZE = (1920, 1080)


def main() -> None:
    if len(sys.argv) != 2:
        print("usage: uv run python scripts/inspect_match_state_timeline.py <動画ファイル名>")
        raise SystemExit(1)

    video_path = VIDEOS_DIR / sys.argv[1]
    cap = cv2.VideoCapture(str(video_path))
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    print(f"fps={fps:.2f}")

    confirm_frames = round(fps * 1.0)
    machine = MatchStateMachine(
        banner_confirm_frames=confirm_frames,
        banner_absence_confirm_frames=confirm_frames,
        league_change_grace_frames=round(fps * 5.0),
        rank_stability_monitor=StabilityMonitor(roi=RANK_ROI, stable_frames_required=round(fps * 0.5)),
    )

    idx = 0
    prev_state = machine.current_state
    print(f"frame=0 state={prev_state}")
    while True:
        ok, frame = cap.read()
        if not ok:
            break
        if frame.shape[1::-1] != TARGET_SIZE:
            frame = cv2.resize(frame, TARGET_SIZE)
        result = machine.process_frame(frame)
        if machine.current_state != prev_state:
            print(f"frame={idx:5d} state={prev_state} -> {machine.current_state}")
            prev_state = machine.current_state
        if result is not None:
            print(f"frame={idx:5d} MatchResult={result}")
        idx += 1
    cap.release()


if __name__ == "__main__":
    main()
