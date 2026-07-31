# banner.py のROI

`detection/banner.py` が使う切り抜き領域。勝敗結果バナーの色判定に使う
(Issue #159で見直し済み)。

- **banner (BANNER_ROIS)** — 色判定。斜めの結果バナー帯を横断する5つの矩形
  (帯の傾きに沿って高さ・y座標を変え、配信ごとの帯の太さ・角度の差があっても
  内側に収まるように配置)を`goal.py`の`is_goal_event()`と同様まとめて1つの
  サンプルとして平均を取る。`WIN_HUE_RANGE`/`LOSE_HUE_RANGE`と平均Hueを比較し、
  `BANNER_HUE_STD_MAX`でHueの標準偏差(背景の建造物等による誤検知除外)も
  確認する(`classify_banner()`)
- **draw_text (DRAW_TEXT_ROI)** — 色判定。引き分け時のみ、帯の色だけでは
  負けと区別できないため、「引き分け」の文字の縁取り色(ミントグリーン)の
  画素割合を追加で確認する(`_is_draw_text()`)。時計表示(画面左上)を避けた
  文字部分のみを狙っている

| ROI | 枠色 | 種別 | 座標 (x1, y1)–(x2, y2) | サイズ (w×h px) |
| --- | --- | --- | --- | --- |
| banner (BANNER_ROIS) [0] | #FFC800 | color | (696, 71)–(879, 149) | 183×78 |
| banner (BANNER_ROIS) [1] | #FFC800 | color | (879, 44)–(1057, 128) | 178×84 |
| banner (BANNER_ROIS) [2] | #FFC800 | color | (1057, 8)–(1245, 86) | 188×78 |
| banner (BANNER_ROIS) [3] | #FFC800 | color | (1245, 5)–(1543, 39) | 298×34 |
| banner (BANNER_ROIS) [4] | #FFC800 | color | (1543, 5)–(1722, 24) | 179×19 |
| draw_text (DRAW_TEXT_ROI) | #C800FF | color | (270, 45)–(650, 250) | 380×205 |

`77_result_win_with_rank_red_hdr_off_annotated.png`(勝ち)・
`78_result_lose_with_rank_blue_hdr_off_annotated.png`(負け)を参考として
置いている(draw_text (DRAW_TEXT_ROI)の枠は位置の参考のみで、これらの画像では
「引き分け」の文字自体は写っていない)。

`89_result_draw_without_rank_hdr_off_annotated.png`はHDR無効化後で初めて
収集した引き分けの参照素材(Issue #182)。「引き分け」の文字自体が実際に
写っている唯一のfixtureで、DRAW_TEXT_ROIの枠が文字にかかっていないか、
BANNER_ROISの枠が帯にきちんと収まっているかを確認できる。

`roi_mask.png`はROI枠のみを描画し、それ以外は透過にした画像(fixture本体の
画像データは含まない)。手元の任意の画像に重ねて、現在のROIがどの位置に
来るかを画像編集ソフト等で確認する用途に使う(Issue #140)。
