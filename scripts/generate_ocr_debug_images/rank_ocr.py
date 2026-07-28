"""`detection/rank_ocr.py`のROI確認用画像・READMEを生成する診断スクリプト
(fixtures/ocr_debug/rank_ocr/、Issue #135、自動テストではない)。

下のROI変数を書き換えてこのスクリプトを直接実行すれば
(`uv run python scripts/generate_ocr_debug_images/rank_ocr.py`)、変更後の枠で
`*_annotated.png`(全画面表示、fixture本体と同じ解像度)・`roi_mask_*.png`
(ROI枠のみで他は透過、任意の画像に重ねて確認する用)・`README.md`
(枠色・種別・実測ピクセル座標のテーブル込み)を再生成できる。画像ファイル自体を
手で編集する想定はない。デフォルト値は`detection/rank_ocr.py`の現在値と同じ。
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

from nss_tracker.detection.rank_ocr import GAUGE_ROI_COMPACT as _GAUGE_ROI_COMPACT
from nss_tracker.detection.rank_ocr import GAUGE_ROI_ENLARGED as _GAUGE_ROI_ENLARGED
from nss_tracker.detection.rank_ocr import RANK_NUMBER_ROI_COMPACT as _RANK_NUMBER_ROI_COMPACT
from nss_tracker.detection.rank_ocr import RANK_NUMBER_ROI_ENLARGED as _RANK_NUMBER_ROI_ENLARGED
from nss_tracker.detection.rank_ocr import RANK_ROI as _RANK_ROI

# ROI変数 — ここを書き換えてこのスクリプトを実行すると、変更後の枠で
# annotated画像・READMEを再生成できる(デフォルトはdetection/rank_ocr.pyの現在値と同じ)
RANK_ROI = _RANK_ROI
RANK_NUMBER_ROI_COMPACT = _RANK_NUMBER_ROI_COMPACT
RANK_NUMBER_ROI_ENLARGED = _RANK_NUMBER_ROI_ENLARGED
GAUGE_ROI_COMPACT = _GAUGE_ROI_COMPACT
GAUGE_ROI_ENLARGED = _GAUGE_ROI_ENLARGED

# annotated画像を生成する対象fixture(fixtures/screenshots/配下)。
# 84/85は同一試合のコンパクト表示(結果バナー確定直後)/拡大表示(ランク変動
# アニメーション中)のペア(Issue #158で収集)。78は別試合のコンパクト表示の
# 参考例として残す。
#
# 77は当初コンパクト表示として扱われていたが、調査の結果バッジが一回り大きく
# 描画される拡大表示だったことが判明したため(Issue #143、RANK_ROIがコンパクト・
# 拡大どちらのサイズも1つの領域でカバーする設計だったため誤分類に長らく
# 気付かなかった)、拡大表示側の参考例として移した
COMPACT_SOURCE_FILENAMES = [
    "84_result_win_with_rank_blue_hdr_off.png",
    "78_result_lose_with_rank_blue_hdr_off.png",
]
ENLARGED_SOURCE_FILENAMES = [
    "85_result_win_with_rank_enlarged_blue_hdr_off.png",
    "77_result_win_with_rank_red_hdr_off.png",
]


def main() -> None:
    output_dir = OUTPUT_ROOT / "rank_ocr"
    print("rank_ocr:")

    compact_categories = [
        Category("rank_badge (RANK_ROI)", "OCR", (255, 200, 0), [RANK_ROI]),
        Category("rank_number_compact (RANK_NUMBER_ROI_COMPACT)", "OCR", (255, 0, 200), [RANK_NUMBER_ROI_COMPACT]),
        Category("gauge_compact (GAUGE_ROI_COMPACT)", "color/brightness", (0, 200, 255), [GAUGE_ROI_COMPACT]),
    ]
    for source in COMPACT_SOURCE_FILENAMES:
        image = load_fixture(source)
        write_annotated(output_dir, source, draw_categories(image, compact_categories))
    write_mask(output_dir, "roi_mask_compact", draw_categories_mask(image.shape[:2], compact_categories))

    enlarged_categories = [
        Category("rank_badge (RANK_ROI)", "OCR", (255, 200, 0), [RANK_ROI]),
        Category("rank_number_enlarged (RANK_NUMBER_ROI_ENLARGED)", "OCR", (255, 0, 200), [RANK_NUMBER_ROI_ENLARGED]),
        Category("gauge_enlarged (GAUGE_ROI_ENLARGED)", "color/brightness", (0, 120, 255), [GAUGE_ROI_ENLARGED]),
    ]
    for source in ENLARGED_SOURCE_FILENAMES:
        enlarged_image = load_fixture(source)
        write_annotated(output_dir, source, draw_categories(enlarged_image, enlarged_categories))
    write_mask(output_dir, "roi_mask_enlarged", draw_categories_mask(enlarged_image.shape[:2], enlarged_categories))

    readme = output_dir / "README.md"
    readme.write_text(
        f"""# rank_ocr.py のROI

`detection/rank_ocr.py` が使う切り抜き領域。ランクバッジは結果バナー確定直後の
**コンパクト表示**とランク変動アニメーション安定後の**拡大表示**でサイズが
明確に異なるため、ゲージ用ROIは表示サイズごとに別々に用意されている
(CLAUDE.md参照)。

## `84_result_win_with_rank_blue_hdr_off_annotated.png`(コンパクト表示の例)

- **rank_badge (RANK_ROI)** — OCR。バッジ全体(アイコン+数値)を包む余裕のある
  領域。`read_rank_tier()`がこの領域をallowlist無しでOCRしてアイコン部分
  (`'∞'`/`'S'`/`'A'`)を判定するほか、`state/match_state.py`の
  `StabilityMonitor`(ランク変動アニメーションが安定したかのピクセル差分監視)
  にも使われる
- **rank_number_compact (RANK_NUMBER_ROI_COMPACT)** — OCR。数値ピル部分だけに
  絞った領域。`read_rank()`が数字のみ(allowlist)でOCRして帯内の数値を読む
  (Issue #143でRANK_ROIから分離した)
- **gauge_compact (GAUGE_ROI_COMPACT)** — 色/明度判定。バッジ下部の横長ゲージの
  塗りつぶし割合を、列ごとの明度(HSVのV)平均が閾値を超えるかで判定する
  (`read_rank_gauge_fill()`)。結果バナー確定直後(rank_before)にのみ使う

{roi_table_markdown(compact_categories)}

`78_result_lose_with_rank_blue_hdr_off_annotated.png`は別試合でのコンパクト表示の
参考例。

## `85_result_win_with_rank_enlarged_blue_hdr_off_annotated.png`(拡大表示の例)

`84`と同一試合で、ランク変動アニメーション中(バッジが一回り大きく描画される)
を切り出したもの(Issue #158で収集)。

`77_result_win_with_rank_red_hdr_off_annotated.png`も拡大表示の参考例として
置いている。**当初はコンパクト表示として扱われていたが、実際にはバッジが
一回り大きく描画される拡大表示だったことが判明した**(Issue #143。RANK_ROI
自体はコンパクト・拡大どちらのサイズも1つの領域でカバーする設計のため誤検知
はしないが、この誤分類には長らく気付かなかった。数値ピルだけに絞った
RANK_NUMBER_ROI_COMPACT/ENLARGEDを追加して初めて、コンパクト用の枠が77の
数値ピルと噛み合わないことから表面化した)。

- **rank_badge (RANK_ROI)** — 上と同じROI・同じ判定(バッジが一回り大きく
  描画されるが、RANK_ROI自体は両サイズをカバーできる余裕を持たせてある)
- **rank_number_enlarged (RANK_NUMBER_ROI_ENLARGED)** — OCR。上の
  rank_number_compactと同じ考え方だが、拡大表示ではバッジの実寸(位置・幅)が
  異なるため別領域を使う(Issue #143)
- **gauge_enlarged (GAUGE_ROI_ENLARGED)** — 色/明度判定。上のGAUGE_ROI_COMPACTと
  同じ考え方だが、拡大表示ではバーの実寸(幅・位置)が異なるため別領域を使う。
  ランク変動アニメーションが安定した後(rank_after)にのみ使う

{roi_table_markdown(enlarged_categories)}

呼び出し元(`state/match_state.py`)では、どちらのROIを使うべきかは読み取り
タイミングによって一意に決まる(結果バナー確定直後=常にコンパクト、
アニメーション安定後=常に拡大)。

`roi_mask_compact.png`/`roi_mask_enlarged.png`はROI枠のみを描画し、それ以外は
透過にした画像(fixture本体の画像データは含まない)。手元の任意の画像に重ねて、
現在のROIがどの位置に来るかを画像編集ソフト等で確認する用途に使う(Issue #140)。
""",
        encoding="utf-8",
    )
    print(f"  wrote {readme.relative_to(OUTPUT_ROOT.parent.parent)}")


if __name__ == "__main__":
    main()
