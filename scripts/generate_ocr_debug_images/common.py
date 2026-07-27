"""goal.py / match_end.py / rank_ocr.py / vs_rank.py が共有するヘルパー
(Issue #135で作成、Issue #140でモジュールごとのスクリプトに分割)。

各スクリプトはimport直後にROIの値を平置きの変数として持っており、その値を
書き換えてスクリプトを直接実行すれば(例: `uv run python
scripts/generate_ocr_debug_images/goal.py`)、変更後の枠で
fixtures/ocr_debug/<モジュール名>/ 配下の`*_annotated.png`・`README.md`を
再生成できる。

各スクリプトはこれに加えて、ROI枠だけを描画しそれ以外は完全に透過な
`roi_mask.png`(BGRA)も生成する(`draw_categories_mask`/`write_mask`参照、
Issue #140)。fixture本体の画像データを含まないため、手元の任意の画像
(実プレイのスクリーンショット等)に重ねて、現在のROIがその画像のどの位置に
来るかを画像編集ソフト等で目視確認する用途に使う。

生成物の位置づけ・画像に凡例パネルを追加しない理由等はこのファイル自体の
docstringではなく各スクリプトのdocstringを参照。
"""

from pathlib import Path

import cv2
import numpy as np

FIXTURES_DIR = Path(__file__).parent.parent.parent / "fixtures" / "screenshots"
OUTPUT_ROOT = Path(__file__).parent.parent.parent / "fixtures" / "ocr_debug"

_LEGEND_FONT = cv2.FONT_HERSHEY_SIMPLEX
_BOX_THICKNESS = 2
_INDEX_FONT_SCALE = 0.4


class Category:
    """1種類のROI(1個または複数インスタンス)をまとめたもの。"""

    def __init__(self, label: str, kind: str, color: tuple[int, int, int], rois: list[tuple[int, int, int, int]]):
        self.label = label
        self.kind = kind  # "OCR" または "color/brightness"
        self.color = color  # BGR
        self.rois = rois


def draw_categories(image: np.ndarray, categories: list[Category]) -> np.ndarray:
    """画像にROI枠(+複数インスタンスなら枠内に小さい連番)を描いた画像を返す。

    ラベル文字・凡例は画像に含めない(README.md側の`roi_table_markdown`に
    任せる)ため、返す画像は入力fixtureと同じ解像度・全画面表示のまま。
    """
    annotated = image.copy()
    for category in categories:
        multi = len(category.rois) > 1
        for index, (x1, y1, x2, y2) in enumerate(category.rois):
            cv2.rectangle(annotated, (x1, y1), (x2, y2), category.color, _BOX_THICKNESS)
            if multi:
                # 枠の内側左上に小さい連番だけを書く(短い数字なので枠内に収まり、
                # ゲーム画面の文字・キャラクターと重ならない)
                cv2.putText(
                    annotated,
                    str(index),
                    (x1 + 2, y1 + 14),
                    _LEGEND_FONT,
                    _INDEX_FONT_SCALE,
                    category.color,
                    1,
                    cv2.LINE_AA,
                )
    return annotated


def draw_categories_mask(shape: tuple[int, int], categories: list[Category]) -> np.ndarray:
    """ROI枠(+複数インスタンスなら枠内に小さい連番)だけを描き、それ以外は
    完全に透過な(アルファ0の)BGRA画像を返す。

    shapeは(height, width)。fixture本体の画像データは一切含まないため、
    手元の任意の画像に重ねてROIの位置を確認する用途に使う(Issue #140)。
    """
    height, width = shape
    mask = np.zeros((height, width, 4), dtype=np.uint8)
    for category in categories:
        multi = len(category.rois) > 1
        color_bgra = (*category.color, 255)
        for index, (x1, y1, x2, y2) in enumerate(category.rois):
            cv2.rectangle(mask, (x1, y1), (x2, y2), color_bgra, _BOX_THICKNESS)
            if multi:
                cv2.putText(
                    mask,
                    str(index),
                    (x1 + 2, y1 + 14),
                    _LEGEND_FONT,
                    _INDEX_FONT_SCALE,
                    color_bgra,
                    1,
                    cv2.LINE_AA,
                )
    return mask


def _bgr_to_hex(color: tuple[int, int, int]) -> str:
    b, g, r = color
    return f"#{r:02X}{g:02X}{b:02X}"


def roi_table_markdown(categories: list[Category]) -> str:
    """各ROIの枠色・種別・実測ピクセル座標を一覧化したMarkdownテーブルを返す。

    座標は画像に描画しているのと同じ`category.rois`の値をそのまま使うため、
    ここに書かれた数値と画像上の枠は常に一致する。
    """
    lines = [
        "| ROI | 枠色 | 種別 | 座標 (x1, y1)–(x2, y2) | サイズ (w×h px) |",
        "| --- | --- | --- | --- | --- |",
    ]
    for category in categories:
        hex_color = _bgr_to_hex(category.color)
        multi = len(category.rois) > 1
        for index, (x1, y1, x2, y2) in enumerate(category.rois):
            name = f"{category.label} [{index}]" if multi else category.label
            lines.append(
                f"| {name} | {hex_color} | {category.kind} | ({x1}, {y1})–({x2}, {y2}) | {x2 - x1}×{y2 - y1} |"
            )
    return "\n".join(lines)


def load_fixture(filename: str) -> np.ndarray:
    path = FIXTURES_DIR / filename
    image = cv2.imread(str(path))
    if image is None:
        raise FileNotFoundError(f"fixture not found: {path}")
    return image


def write_annotated(output_dir: Path, source_filename: str, image: np.ndarray) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    stem = Path(source_filename).stem
    out_path = output_dir / f"{stem}_annotated.png"
    cv2.imwrite(str(out_path), image)
    print(f"  wrote {out_path.relative_to(OUTPUT_ROOT.parent.parent)}")


def write_mask(output_dir: Path, name: str, mask: np.ndarray) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    out_path = output_dir / f"{name}.png"
    cv2.imwrite(str(out_path), mask)
    print(f"  wrote {out_path.relative_to(OUTPUT_ROOT.parent.parent)}")
