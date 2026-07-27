"""ランクゲージの塗りつぶし割合を調べるための診断スクリプト。

fixtures/screenshots のランクバッジが写る状態について、GAUGE_ROI_COMPACT/
GAUGE_ROI_ENLARGEDそれぞれの塗りつぶし割合を出力する。
rank_ocr.read_rank_gauge_fill() の閾値・ROIを決めるための一次データ収集用
(自動テストではない)。

コンパクト表示(結果バナー確定直後)・拡大表示(ランク変動アニメーション
開始後〜暗転まで)でバーの実寸が異なるため(rank_ocr.pyのdocstring参照)、
対応するfixtureをそれぞれ別グループとして出力する。
"_in_rank_increase_"/"_in_rank_decrease_"のfixtureは遷移演出の途中
(ゲージの見た目がグラデーション表示になり値として意味を持たない状態)
のため参考値として出力するのみで、tests/test_rank_ocr.pyのground truthには
含めていない。
"""

from pathlib import Path

import cv2

from nss_tracker.detection.rank_ocr import GAUGE_ROI_COMPACT, GAUGE_ROI_ENLARGED, read_rank_gauge_fill

FIXTURES_DIR = Path(__file__).parent.parent / "fixtures" / "screenshots"

# Issue #148: HDR無効化前fixture削除に伴い張り替え。拡大表示・遷移演出中の
# HDR無効化後fixtureはまだ無い(Issue #147参照)ため空のまま
COMPACT_TARGETS = [
    "77_result_win_with_rank_red_hdr_off.png",
    "78_result_lose_with_rank_blue_hdr_off.png",
]

ENLARGED_TARGETS: list[str] = []

# 遷移演出中(参考値のみ、ground truthには使わない)。拡大表示と同じ座標系のはず
TRANSITIONAL_TARGETS: list[str] = []


def _print_group(label: str, names: list[str], roi: tuple[int, int, int, int]) -> None:
    print(f"--- {label} ---")
    for name in names:
        path = FIXTURES_DIR / name
        img = cv2.imread(str(path))
        if img is None:
            print(f"[skip] {name} not found")
            continue
        fill = read_rank_gauge_fill(img, roi)
        print(f"{name:55s} fill={fill:.3f}")


def main() -> None:
    _print_group("コンパクト表示", COMPACT_TARGETS, GAUGE_ROI_COMPACT)
    _print_group("拡大表示", ENLARGED_TARGETS, GAUGE_ROI_ENLARGED)
    _print_group("遷移演出中(参考値のみ)", TRANSITIONAL_TARGETS, GAUGE_ROI_ENLARGED)


if __name__ == "__main__":
    main()
