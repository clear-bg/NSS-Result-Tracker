# goal.py のROI

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

| ROI | 枠色 | 種別 | 座標 (x1, y1)–(x2, y2) | サイズ (w×h px) |
| --- | --- | --- | --- | --- |
| goal_banner_left (BANNER_ROI_LEFT) | #FFC800 | color | (100, 305)–(650, 415) | 550×110 |
| goal_banner_right (BANNER_ROI_RIGHT) | #FF8C00 | color | (1300, 305)–(1850, 415) | 550×110 |
| goal_label (GOAL_LABEL_ROI) | #00C8FF | OCR | (915, 751)–(1009, 787) | 94×36 |
| scorer_name (SCORER_NAME_ROI) | #C800FF | OCR | (842, 835)–(1145, 878) | 303×43 |
| assist_label (ASSIST_LABEL_ROI) | #00DC00 | OCR | (900, 900)–(1020, 938) | 120×38 |
| assist_name (ASSIST_NAME_ROI) | #0000DC | OCR | (842, 986)–(1145, 1028) | 303×42 |
| own_goal_label (OWN_GOAL_LABEL_ROI) | #FF0000 | OCR | (845, 957)–(1073, 1004) | 228×47 |

チームカラーに依存するのはgoal_banner_left/rightの色閾値のみで、それ以外の
ROIはチームカラーに依存しないため、赤チームでアシスト有りの例
(`74_goal_with_assist_red_hdr_off_annotated.png`)を基本例として置いている。
オウンゴールの例として、青チームのオウンゴール
(`75_goal_blue_owngoal_hdr_off_annotated.png`)も置いている(この場合
goal_label/scorer_name/assist_label/assist_nameはいずれも空で、
own_goal_labelのみに「オウンゴール」が表示される)。

アシスト無しの単独ゴール(オウンゴールではない通常のゴール)でgoal_labelが
空になりassist_label側に「ゴール」が来るケースは、現時点でHDR無効化後の
参照fixtureが無く未検証(Issue #153で収集予定)。

`roi_mask.png`はROI枠のみを描画し、それ以外は透過にした画像(fixture本体の
画像データは含まない)。手元の任意の画像に重ねて、現在のROIがどの位置に
来るかを画像編集ソフト等で確認する用途に使う(Issue #140)。
