# goal.py のROI

`detection/goal.py` が使う切り抜き領域。

- **goal_banner (BANNER_ROI)** — 色判定。「ゴール！」の斜めバナーが表示されて
  いるかどうかを、チームカラーを問わず色ベースで判定する(`is_goal_event()`)
- **name_panel (NAME_PANEL_ROI)** — OCR。得点者名・アシスト名の両方を含む
  1つの大きい領域で、`read_scorer_name()`/`read_assist_name()`はどちらも
  同じこの領域をOCRし、結果の中から「アシスト」というラベル文字列の位置で
  得点者名とアシスト名を区別する(得点者用・アシスト用に別々のROIがあるわけ
  ではない)

いずれのROIもチームカラーに依存しないため、青チーム
(`21_goal_with_assist_blue_annotated.png`)・赤チーム
(`31_goal_with_assist_red_annotated.png`)の両方を参考として置いている。
アシストが無い場合の例として`22_goal_without_assist_blue_annotated.png`も
置いている(この場合name_panel内には得点者名のみが表示され、「アシスト」
ラベル自体が現れないため、`read_assist_name()`はNoneを返す)。
