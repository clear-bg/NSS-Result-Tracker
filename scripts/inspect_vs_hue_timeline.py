"""実プレイ録画でVS_ROIのHSVがどう推移しているかを把握するための診断スクリプト(Issue #68)。

2026-07-19・2026-07-20の実プレイ検証(YouTubeアーカイブ経由の分析)に続き、
OBSのローカル録画(再エンコード無し)を直接分析することで、YouTube再エンコードの
影響を切り分ける。動画全体を舐めて、is_vs_screen()の現行閾値に近い
Hue帯(WIDE_HUE_RANGE)に入っている区間をまとめ、区間ごとに:
- 開始・終了フレーム番号/秒
- 区間内で現行の狭い閾値(is_vs_screen())を満たしていた最大連続フレーム数
- 区間内のH/S/Vの最小・最大・平均
を出力する。動画のfpsを自動検出するため、録画のfpsが30fps想定と異なっていても
そのまま実時間で解釈できる。

使い方: uv run python scripts/inspect_vs_hue_timeline.py <動画ファイルへのパス>
"""

import sys
from pathlib import Path

import cv2

from nss_tracker.detection.matchmaking import VS_ROI, is_vs_screen, read_vs_roi_hsv

# 現行の狭い閾値(detection/matchmaking.pyのVS_HUE_RANGE等)を大きく上回って
# 広めに取り、パルスで閾値を外れている区間そのものを見失わないようにする
WIDE_HUE_RANGE = (40, 100)
WIDE_SAT_RANGE = (30, 100)
WIDE_VAL_MIN = 120


def _is_wide_candidate(h: float, s: float, v: float) -> bool:
    return WIDE_HUE_RANGE[0] <= h <= WIDE_HUE_RANGE[1] and WIDE_SAT_RANGE[0] <= s <= WIDE_SAT_RANGE[1] and v >= WIDE_VAL_MIN


def main() -> None:
    if len(sys.argv) != 2:
        print("usage: uv run python scripts/inspect_vs_hue_timeline.py <動画ファイルへのパス>")
        raise SystemExit(1)

    video_path = Path(sys.argv[1])
    cap = cv2.VideoCapture(str(video_path))
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    print(f"fps={fps:.2f}")

    idx = 0
    in_episode = False
    episode_start = 0
    strict_streak = 0
    max_strict_streak = 0
    h_values: list[float] = []
    s_values: list[float] = []
    v_values: list[float] = []

    def flush_episode(end_idx: int) -> None:
        nonlocal max_strict_streak, h_values, s_values, v_values
        duration = (end_idx - episode_start) / fps
        print(
            f"episode frame={episode_start:6d}-{end_idx:6d} "
            f"t={episode_start / fps:6.2f}s-{end_idx / fps:6.2f}s duration={duration:5.2f}s "
            f"H[{min(h_values):.1f},{max(h_values):.1f}] avg={sum(h_values) / len(h_values):.1f} "
            f"S[{min(s_values):.1f},{max(s_values):.1f}] V[{min(v_values):.1f},{max(v_values):.1f}] "
            f"max_strict_streak={max_strict_streak}({max_strict_streak / fps:.2f}s)"
        )

    while True:
        ok, frame = cap.read()
        if not ok:
            break
        if frame.shape[1::-1] != (1920, 1080):
            frame = cv2.resize(frame, (1920, 1080))

        h, s, v = read_vs_roi_hsv(frame, VS_ROI)
        wide_hit = _is_wide_candidate(h, s, v)
        strict_hit = is_vs_screen(frame, VS_ROI)

        if wide_hit:
            if not in_episode:
                in_episode = True
                episode_start = idx
                max_strict_streak = 0
                strict_streak = 0
                h_values, s_values, v_values = [], [], []
            h_values.append(h)
            s_values.append(s)
            v_values.append(v)
            if strict_hit:
                strict_streak += 1
                max_strict_streak = max(max_strict_streak, strict_streak)
            else:
                strict_streak = 0
        else:
            if in_episode:
                flush_episode(idx)
                in_episode = False

        idx += 1
        if idx % 10000 == 0:
            print(f"...processing frame={idx} ({idx / fps:.1f}s)", file=sys.stderr)

    if in_episode:
        flush_episode(idx)

    cap.release()


if __name__ == "__main__":
    main()
