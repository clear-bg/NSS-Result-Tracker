"""`detection/goal.py`のROI確認用画像・READMEを生成する診断スクリプト
(fixtures/ocr_debug/goal/、Issue #135、自動テストではない)。

下のROI変数を書き換えてこのスクリプトを直接実行すれば
(`uv run python scripts/generate_ocr_debug_images/goal.py`)、変更後の枠で
`*_annotated.png`(全画面表示、fixture本体と同じ解像度)と`README.md`
(枠色・種別・実測ピクセル座標のテーブル込み)を再生成できる。画像ファイル自体を
手で編集する想定はない。デフォルト値は`detection/goal.py`の現在値と同じ。
"""

from common import Category, OUTPUT_ROOT, draw_categories, load_fixture, roi_table_markdown, write_annotated

from nss_tracker.detection.goal import BANNER_ROI as _BANNER_ROI
from nss_tracker.detection.goal import NAME_PANEL_ROI as _NAME_PANEL_ROI

# ROI変数 — ここを書き換えてこのスクリプトを実行すると、変更後の枠で
# annotated画像・READMEを再生成できる(デフォルトはdetection/goal.pyの現在値と同じ)
BANNER_ROI = _BANNER_ROI
NAME_PANEL_ROI = _NAME_PANEL_ROI

# annotated画像を生成する対象fixture(fixtures/screenshots/配下)
SOURCE_FILENAMES = [
    "74_goal_with_assist_red_hdr_off.png",
    "75_goal_blue_owngoal_hdr_off.png",
]


def main() -> None:
    output_dir = OUTPUT_ROOT / "goal"
    print("goal:")

    categories = [
        Category("goal_banner (BANNER_ROI)", "color", (0, 200, 255), [BANNER_ROI]),
        Category("name_panel (NAME_PANEL_ROI)", "OCR", (255, 200, 0), [NAME_PANEL_ROI]),
    ]
    for source in SOURCE_FILENAMES:
        image = load_fixture(source)
        write_annotated(output_dir, source, draw_categories(image, categories))

    readme = output_dir / "README.md"
    readme.write_text(
        f"""# goal.py のROI

`detection/goal.py` が使う切り抜き領域。

- **goal_banner (BANNER_ROI)** — 色判定。「ゴール!」の斜めバナーが表示されて
  いるかどうかを、チームカラーを問わず色ベースで判定する(`is_goal_event()`)
- **name_panel (NAME_PANEL_ROI)** — OCR。得点者名・アシスト名の両方を含む
  1つの大きい領域で、`read_scorer_name()`/`read_assist_name()`はどちらも
  同じこの領域をOCRし、結果の中から「アシスト」というラベル文字列の位置で
  得点者名とアシスト名を区別する(得点者用・アシスト用に別々のROIがあるわけ
  ではない)

{roi_table_markdown(categories)}

いずれのROIもチームカラーに依存しないため、赤チームでアシスト有りの例
(`74_goal_with_assist_red_hdr_off_annotated.png`)を基本例として置いている。
アシストが無い場合の例として、青チームのオウンゴール
(`75_goal_blue_owngoal_hdr_off_annotated.png`)も置いている(オウンゴールは
性質上アシストが付かないため、この場合name_panel内には得点者名のみが表示され、
「アシスト」ラベル自体が現れず`read_assist_name()`はNoneを返す)。
""",
        encoding="utf-8",
    )
    print(f"  wrote {readme.relative_to(OUTPUT_ROOT.parent.parent)}")


if __name__ == "__main__":
    main()
