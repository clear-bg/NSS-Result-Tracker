"""中央スコア表示(Issue #493)の数字テンプレートを作る。

スコア表示が止まった状態のスクリーンショット(1920x1080)から左右の数字の形を
切り出し、`src/nss_tracker/detection/center_score_templates/{数字}_{連番}.png`に保存する。
切り出し方は`detection/center_score.py`の読み取りと全く同じ(白い最大連結成分を
外接矩形で切り出して48x64に縮める)。

元のスクリーンショットは`fixtures/screenshots/`に`{番号}_center_score_{自チーム}-{相手チーム}_hdr_off.png`
(同じスコアの2枚目以降は末尾に`_2`等)の名前で置いてある。画像のスコアはこの名前から読む。

    uv run python scripts/build_center_score_templates.py fixtures/screenshots/*_center_score_*.png

既存のテンプレートはすべて消してから作り直す(同じ画像を2回取り込まないため)。
5・6のサンプルが手に入ったら、これまでの画像と一緒に渡して作り直す。

テンプレートは数字の形だけの2値画像で、プレイヤー名等は含まないためgitで管理する
(元のスクリーンショットはfixtures/と同じ理由でコミットしない)。
"""

import argparse
import re
import sys
from pathlib import Path

import cv2

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from nss_tracker.detection.center_score import (  # noqa: E402
    LEFT_ROI,
    RIGHT_ROI,
    TEMPLATE_DIR,
    extract_digit_mask,
)

_NAME_PATTERN = re.compile(r"^\d+_center_score_(\d)-(\d)_")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("images", nargs="+", type=Path, help="{番号}_center_score_{自}-{相手}_*.png 形式のスクリーンショット")
    args = parser.parse_args()

    samples = []
    for path in sorted(args.images):
        match = _NAME_PATTERN.match(path.name)
        if match is None:
            print(f"ファイル名からスコアを読めません({{番号}}_center_score_{{自}}-{{相手}}_の形にしてください): {path}")
            return 1
        frame = cv2.imread(str(path))
        if frame is None:
            print(f"画像を読み込めません: {path}")
            return 1
        for roi, digit in ((LEFT_ROI, int(match.group(1))), (RIGHT_ROI, int(match.group(2)))):
            mask = extract_digit_mask(frame, roi)
            if mask is None:
                print(f"止まった表示の数字を切り出せません({path.name}、{digit}の側)")
                return 1
            samples.append((digit, mask))

    TEMPLATE_DIR.mkdir(parents=True, exist_ok=True)
    for old in TEMPLATE_DIR.glob("*.png"):
        old.unlink()
    counts: dict[int, int] = {}
    for digit, mask in samples:
        counts[digit] = counts.get(digit, 0) + 1
        cv2.imwrite(str(TEMPLATE_DIR / f"{digit}_{counts[digit]:02d}.png"), mask.astype("uint8") * 255)
    print(f"{len(samples)}個のテンプレートを保存しました: {TEMPLATE_DIR}")
    print("数字ごとの数:", dict(sorted(counts.items())))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
