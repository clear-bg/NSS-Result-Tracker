# banner.py のROI

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

| ROI | 枠色 | 種別 | 座標 (x1, y1)–(x2, y2) | サイズ (w×h px) |
| --- | --- | --- | --- | --- |
| banner (BANNER_ROI) | #FFC800 | color | (1300, 5)–(1750, 35) | 450×30 |
| draw_text (DRAW_TEXT_ROI) | #C800FF | color | (270, 45)–(650, 250) | 380×205 |

`77_result_win_with_rank_red_hdr_off_annotated.png`(勝ち)・
`78_result_lose_with_rank_blue_hdr_off_annotated.png`(負け)を参考として
置いている。**引き分け(draw)のHDR無効化後fixtureは現時点で無い**ため、
draw_text (DRAW_TEXT_ROI)の枠は位置の参考のみで、これらの画像では
「引き分け」の文字自体は写っていない。

`roi_mask.png`はROI枠のみを描画し、それ以外は透過にした画像(fixture本体の
画像データは含まない)。手元の任意の画像に重ねて、現在のROIがどの位置に
来るかを画像編集ソフト等で確認する用途に使う(Issue #140)。
