# goal.py のROI

`detection/goal.py` が使う切り抜き領域。

- **goal_banner (BANNER_ROI)** — 色判定。「ゴール!」の斜めバナーが表示されて
  いるかどうかを、チームカラーを問わず色ベースで判定する(`is_goal_event()`)
- **name_panel (NAME_PANEL_ROI)** — OCR。得点者名・アシスト名の両方を含む
  1つの大きい領域で、`read_scorer_name()`/`read_assist_name()`はどちらも
  同じこの領域をOCRし、結果の中から「アシスト」というラベル文字列の位置で
  得点者名とアシスト名を区別する(得点者用・アシスト用に別々のROIがあるわけ
  ではない)

| ROI | 枠色 | 種別 | 座標 (x1, y1)–(x2, y2) | サイズ (w×h px) |
| --- | --- | --- | --- | --- |
| goal_banner (BANNER_ROI) | #FFC800 | color | (100, 280)–(400, 350) | 300×70 |
| name_panel (NAME_PANEL_ROI) | #00C8FF | OCR | (700, 780)–(1250, 1030) | 550×250 |

いずれのROIもチームカラーに依存しないため、赤チームでアシスト有りの例
(`74_goal_with_assist_red_hdr_off_annotated.png`)を基本例として置いている。
アシストが無い場合の例として、青チームのオウンゴール
(`75_goal_blue_owngoal_hdr_off_annotated.png`)も置いている(オウンゴールは
性質上アシストが付かないため、この場合name_panel内には得点者名のみが表示され、
「アシスト」ラベル自体が現れず`read_assist_name()`はNoneを返す)。
