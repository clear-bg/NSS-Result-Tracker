"""`detection/goal.py`のROI確認用画像・READMEを生成する診断スクリプト
(fixtures/ocr_debug/goal/、Issue #135、自動テストではない)。

下のROI変数を書き換えてこのスクリプトを直接実行すれば
(`uv run python scripts/generate_ocr_debug_images/goal.py`)、変更後の枠で
`*_annotated.png`(全画面表示、fixture本体と同じ解像度)・`roi_mask.png`
(ROI枠のみで他は透過、任意の画像に重ねて確認する用)・`README.md`
(枠色・種別・実測ピクセル座標のテーブル込み)を再生成できる。画像ファイル自体を
手で編集する想定はない。デフォルト値は`detection/goal.py`の現在値と同じ。
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

from nss_tracker.detection.goal import ASSIST_LABEL_ROI as _ASSIST_LABEL_ROI
from nss_tracker.detection.goal import ASSIST_NAME_ROI as _ASSIST_NAME_ROI
from nss_tracker.detection.goal import BANNER_ROI_LEFT as _BANNER_ROI_LEFT
from nss_tracker.detection.goal import BANNER_ROI_RIGHT as _BANNER_ROI_RIGHT
from nss_tracker.detection.goal import GOAL_LABEL_ROI as _GOAL_LABEL_ROI
from nss_tracker.detection.goal import OWN_GOAL_LABEL_ROI as _OWN_GOAL_LABEL_ROI
from nss_tracker.detection.goal import SCORER_NAME_ROI as _SCORER_NAME_ROI

# ROI変数 — ここを書き換えてこのスクリプトを実行すると、変更後の枠で
# annotated画像・READMEを再生成できる(デフォルトはdetection/goal.pyの現在値と同じ)
BANNER_ROI_LEFT = _BANNER_ROI_LEFT
BANNER_ROI_RIGHT = _BANNER_ROI_RIGHT
GOAL_LABEL_ROI = _GOAL_LABEL_ROI
SCORER_NAME_ROI = _SCORER_NAME_ROI
ASSIST_LABEL_ROI = _ASSIST_LABEL_ROI
ASSIST_NAME_ROI = _ASSIST_NAME_ROI
OWN_GOAL_LABEL_ROI = _OWN_GOAL_LABEL_ROI

# annotated画像を生成する対象fixture(fixtures/screenshots/配下)
SOURCE_FILENAMES = [
    "74_goal_with_assist_red_hdr_off.png",
    "75_goal_blue_owngoal_hdr_off.png",
    "83_goal_without_assist_blue_hdr_off.png",
]


def main() -> None:
    output_dir = OUTPUT_ROOT / "goal"
    print("goal:")

    categories = [
        Category("goal_banner_left (BANNER_ROI_LEFT)", "color", (0, 200, 255), [BANNER_ROI_LEFT]),
        Category("goal_banner_right (BANNER_ROI_RIGHT)", "color", (0, 140, 255), [BANNER_ROI_RIGHT]),
        Category("goal_label (GOAL_LABEL_ROI)", "OCR", (255, 200, 0), [GOAL_LABEL_ROI]),
        Category("scorer_name (SCORER_NAME_ROI)", "OCR", (255, 0, 200), [SCORER_NAME_ROI]),
        Category("assist_label (ASSIST_LABEL_ROI)", "OCR", (0, 220, 0), [ASSIST_LABEL_ROI]),
        Category("assist_name (ASSIST_NAME_ROI)", "OCR", (220, 0, 0), [ASSIST_NAME_ROI]),
        Category("own_goal_label (OWN_GOAL_LABEL_ROI)", "OCR", (0, 0, 255), [OWN_GOAL_LABEL_ROI]),
    ]
    for source in SOURCE_FILENAMES:
        image = load_fixture(source)
        write_annotated(output_dir, source, draw_categories(image, categories))

    write_mask(output_dir, "roi_mask", draw_categories_mask(image.shape[:2], categories))

    readme = output_dir / "README.md"
    readme.write_text(
        f"""# goal.py のROI

`detection/goal.py` が使う切り抜き領域(Issue #141でROI・閾値を見直した)。

- **goal_banner_left / goal_banner_right (BANNER_ROI_LEFT / BANNER_ROI_RIGHT)** —
  色判定。「ゴール!」の斜めバナーが表示されているかどうかを、チームカラーを
  問わず色ベースで判定する(`is_goal_event()`)。バナーの斜め境界・文字・
  キャラクターを避けた完全に単色の領域を左右2箇所実測し、まとめて1つの
  サンプルとして平均を取る方式にした(以前の単一ROIはバナーの斜め境界に
  わずかにかかっており、背景色が混ざった状態で閾値が調整されていたことが
  判明したため)
- **goal_label (GOAL_LABEL_ROI)** / **scorer_name (SCORER_NAME_ROI)** /
  **assist_label (ASSIST_LABEL_ROI)** / **assist_name (ASSIST_NAME_ROI)** — OCR。
  得点者名パネルは画面下部に上から「ゴール」ラベル→得点者名→「アシスト」
  ラベル→アシスト者名、という4行の固定グリッドで構成される。以前は4行全体を
  1つの広いROI(NAME_PANEL_ROI)でまとめてOCRし、検出順から役割を推測していたが、
  行ごとに個別のROIへ分割し、各ラベル行が実際に「ゴール」/「アシスト」の
  どちらかを読んで役割を判定する方式にした(`read_scorer_name()`/
  `read_assist_name()`。アシスト無しの単独ゴールでは「ゴール」ラベル+得点者名の
  組がアシスト側の位置にそのままずれて表示されるため、ASSIST_LABEL_ROIに
  「ゴール」が読めた場合はASSIST_NAME_ROIを得点者名として扱う。詳細は
  `detection/goal.py`のモジュールdocstring参照)
- **own_goal_label (OWN_GOAL_LABEL_ROI)** — OCR。オウンゴールは上記4行の
  グリッドとは別に「オウンゴール」という単独ラベルのみが表示され、得点者名は
  表示されない(`is_own_goal_event()`。実データで確認済み)

{roi_table_markdown(categories)}

チームカラーに依存するのはgoal_banner_left/rightの色閾値のみで、それ以外の
ROIはチームカラーに依存しないため、赤チームでアシスト有りの例
(`74_goal_with_assist_red_hdr_off_annotated.png`)を基本例として置いている。
オウンゴールの例として、青チームのオウンゴール
(`75_goal_blue_owngoal_hdr_off_annotated.png`)も置いている(この場合
goal_label/scorer_name/assist_label/assist_nameはいずれも空で、
own_goal_labelのみに「オウンゴール」が表示される)。

アシスト無しの単独ゴール(オウンゴールではない通常のゴール)の例として、
青チームの`83_goal_without_assist_blue_hdr_off_annotated.png`も置いている
(Issue #153で収集。goal_labelは空で、assist_label側に「ゴール」が、
assist_name側に得点者名が表示される)。

`roi_mask.png`はROI枠のみを描画し、それ以外は透過にした画像(fixture本体の
画像データは含まない)。手元の任意の画像に重ねて、現在のROIがどの位置に
来るかを画像編集ソフト等で確認する用途に使う(Issue #140)。
""",
        encoding="utf-8",
    )
    print(f"  wrote {readme.relative_to(OUTPUT_ROOT.parent.parent)}")


if __name__ == "__main__":
    main()
