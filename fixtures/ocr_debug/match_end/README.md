# match_end.py のROI

`detection/match_end.py` が使う切り抜き領域。「試合終了」バナーは、色味が
非常によく似た「延長戦」「キックオフ」バナーと区別するため、軽量な色判定
(候補判定)→OCRによる文字確認、の2段構成になっている。

- **candidate_left (MATCH_END_LEFT_ROI)** / **candidate_right (MATCH_END_RIGHT_ROI)** —
  色判定。文字を避けた帯の左右2箇所の色が「試合終了」帯の色と一致するかを見る
  (`is_match_end_screen()`)。「延長戦」は帯の横幅が異なるためRIGHT側が背景色に
  なり除外できるが、「キックオフ」はこの2点だけでは区別できない
- **text_confirm (MATCH_END_TEXT_ROI)** — OCR。上記の色判定が一定時間連続した
  タイミングで1回だけ、実際に「試合終了」の文字を読んで確定させる
  (`confirm_match_end_text()`)。「延長戦」「キックオフ」との最終的な区別は
  この文字確認で行う

| ROI | 枠色 | 種別 | 座標 (x1, y1)–(x2, y2) | サイズ (w×h px) |
| --- | --- | --- | --- | --- |
| candidate_left (MATCH_END_LEFT_ROI) | `#FFC800` | color | (630, 405)–(750, 465) | 120×60 |
| candidate_right (MATCH_END_RIGHT_ROI) | `#FF8C00` | color | (1200, 415)–(1270, 450) | 70×35 |
| text_confirm (MATCH_END_TEXT_ROI) | `#00C8FF` | OCR | (600, 385)–(1330, 480) | 730×95 |

`80_match_end_hdr_off_2.png`は色判定・文字確認とも問題なく通る例。もう1つの
HDR無効化後fixture`76_match_end_hdr_off.png`は目視では同じ「試合終了」帯だが、
帯の実際の描画位置がわずかにズレておりcandidate_left(MATCH_END_LEFT_ROI)の
左上角が帯の丸み端にかかるため、色判定(hue_std)が閾値をわずかに超えて
候補判定自体を通過できない既知の問題がある(`tests/test_match_end.py`で
xfail、Issue #142でROI再較正予定)。
