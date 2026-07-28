"""`detection/banner.py`のROI確認用画像・READMEを生成する診断スクリプト
(fixtures/ocr_debug/banner/、Issue #140、自動テストではない)。

下のROI変数を書き換えてこのスクリプトを直接実行すれば
(`uv run python scripts/generate_ocr_debug_images/banner.py`)、変更後の枠で
`*_annotated.png`(全画面表示、fixture本体と同じ解像度)・`roi_mask.png`
(ROI枠のみで他は透過、任意の画像に重ねて確認する用)・`README.md`
(枠色・種別・実測ピクセル座標のテーブル込み)を再生成できる。画像ファイル自体を
手で編集する想定はない。デフォルト値は`detection/banner.py`の現在値と同じ。
"""

from common import (
    Category,
    OUTPUT_ROOT,
    draw_categories,
    draw_categories_mask,
    load_fixture,
    roi_table_markdown,
    write_annotated,
    write_mask,
)

from nss_tracker.detection.banner import BANNER_ROI as _BANNER_ROI
from nss_tracker.detection.banner import DRAW_TEXT_ROI as _DRAW_TEXT_ROI

# ROI変数 — ここを書き換えてこのスクリプトを実行すると、変更後の枠で
# annotated画像・READMEを再生成できる(デフォルトはdetection/banner.pyの現在値と同じ)
BANNER_ROI = _BANNER_ROI
DRAW_TEXT_ROI = _DRAW_TEXT_ROI

# annotated画像を生成する対象fixture(fixtures/screenshots/配下)。
# 引き分け(draw)のHDR無効化後fixtureはまだ無いため、勝ち/負けの2例のみ
# (DRAW_TEXT_ROI自体の位置はどちらの画像でも変わらないため、内容が引き分けで
# なくても枠の位置確認には使える)
SOURCE_FILENAMES = [
    "77_result_win_with_rank_red_hdr_off.png",
    "78_result_lose_with_rank_blue_hdr_off.png",
]


def main() -> None:
    output_dir = OUTPUT_ROOT / "banner"
    print("banner:")

    categories = [
        Category("banner (BANNER_ROI)", "color", (0, 200, 255), [BANNER_ROI]),
        Category("draw_text (DRAW_TEXT_ROI)", "color", (255, 0, 200), [DRAW_TEXT_ROI]),
    ]
    for source in SOURCE_FILENAMES:
        image = load_fixture(source)
        write_annotated(output_dir, source, draw_categories(image, categories))
    write_mask(output_dir, "roi_mask", draw_categories_mask(image.shape[:2], categories))

    readme = output_dir / "README.md"
    readme.write_text(
        f"""# banner.py のROI

`detection/banner.py` が使う切り抜き領域。勝敗結果バナーの色判定に使う
(Issue #159で見直し予定)。

- **banner (BANNER_ROI)** — 色判定。斜めの結果バナー帯のうち、テキストや
  選手モデルにかぶらない右上寄りの薄い領域。`WIN_HUE_RANGE`/`LOSE_HUE_RANGE`と
  平均Hueを比較し、`BANNER_HUE_STD_MAX`でHueの標準偏差(背景の建造物等による
  誤検知除外)も確認する(`classify_banner()`)
- **draw_text (DRAW_TEXT_ROI)** — 色判定。引き分け時のみ、帯の色だけでは
  負けと区別できないため、「引き分け」の文字の縁取り色(ミントグリーン)の
  画素割合を追加で確認する(`_is_draw_text()`)。時計表示(画面左上)を避けた
  文字部分のみを狙っている

{roi_table_markdown(categories)}

`77_result_win_with_rank_red_hdr_off_annotated.png`(勝ち)・
`78_result_lose_with_rank_blue_hdr_off_annotated.png`(負け)を参考として
置いている。**引き分け(draw)のHDR無効化後fixtureは現時点で無い**ため、
draw_text (DRAW_TEXT_ROI)の枠は位置の参考のみで、これらの画像では
「引き分け」の文字自体は写っていない。

`roi_mask.png`はROI枠のみを描画し、それ以外は透過にした画像(fixture本体の
画像データは含まない)。手元の任意の画像に重ねて、現在のROIがどの位置に
来るかを画像編集ソフト等で確認する用途に使う(Issue #140)。
""",
        encoding="utf-8",
    )
    print(f"  wrote {readme.relative_to(OUTPUT_ROOT.parent.parent)}")


if __name__ == "__main__":
    main()
