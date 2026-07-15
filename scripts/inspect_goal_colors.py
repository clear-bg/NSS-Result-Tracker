"""ゴール演出バナーの色域を調べるための診断スクリプト。

fixtures/screenshots のゴール関連状態・非該当状態について、バナー帯が
通ると想定される左寄りの領域(テキストにかぶらない位置)の平均HSVを出力する。
goal.py の色閾値を決めるための一次データ収集用(自動テストではない)。
"""

from pathlib import Path

import cv2

FIXTURES_DIR = Path(__file__).parent.parent / "fixtures" / "screenshots"

# バナー帯のうち、中央の白文字にかぶらない左寄りの矩形領域 (x1, y1, x2, y2)
ROI = (100, 280, 400, 350)

TARGETS = [
    "21_goal_with_assist_blue.png",
    "22_goal_without_assist_blue.png",
    "23_assist_blue.png",
    "24_GA_without_me_blue.png",
    "31_goal_with_assist_red.png",
    "32_goal_without_assist_red.png",
    "33_assist_red.png",
    "34_GA_without_me_red.png",
    "20_in_game_blue.png",
    "30_in_game_red.png",
    "25_resume_game_blue.png",
    "35_resume_game_red.png",
    "00_lobby.png",
]


def main() -> None:
    x1, y1, x2, y2 = ROI
    for name in TARGETS:
        path = FIXTURES_DIR / name
        img = cv2.imread(str(path))
        if img is None:
            print(f"[skip] {name} not found")
            continue
        crop = img[y1:y2, x1:x2]
        hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)
        h, s, v = hsv.reshape(-1, 3).mean(axis=0)
        print(f"{name:40s} H={h:6.1f} S={s:6.1f} V={v:6.1f}")


if __name__ == "__main__":
    main()
