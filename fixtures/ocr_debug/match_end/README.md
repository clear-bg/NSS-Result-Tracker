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
| candidate_left (MATCH_END_LEFT_ROI) | #FFC800 | color | (638, 422)–(719, 509) | 81×87 |
| candidate_right (MATCH_END_RIGHT_ROI) | #FF8C00 | color | (1187, 422)–(1265, 509) | 78×87 |
| text_confirm (MATCH_END_TEXT_ROI) | #00C8FF | OCR | (746, 383)–(1180, 501) | 434×118 |

`80_match_end_hdr_off_2.png`・`76_match_end_hdr_off.png`とも色判定・文字確認
問題なく通る(Issue #142でROIを再実測して差し替えた。以前はcandidate_left
(MATCH_END_LEFT_ROI)の左上角が76番の帯の丸み端にかかりhue_stdが閾値を
わずかに超えて候補判定を通過できない既知の問題があったが、境界を避けた
完全に単色の位置に再実測して解消した)。

「試合終了」の文字は消える直前に一瞬だけ拡大しながら消えるアニメーションが
あるため(継続時間が非常に短く気づきにくい)、動画から抜き出すフレームに
よってはtext_confirm(MATCH_END_TEXT_ROI)の範囲から文字がはみ出すことがある
(`fixtures/videos/29_lose_blue_hdr_off.mp4`のframe 958で実際に発生)。ただし
実運用のconfirm_match_end_text()は表示開始から短いデバウンス後の1回しか
呼ばれないため、このアニメーション区間に実際に当たることはない
(`tests/test_match_end.py`の`_confirmed_match_end()`参照)。

`roi_mask.png`はROI枠のみを描画し、それ以外は透過にした画像(fixture本体の
画像データは含まない)。手元の任意の画像に重ねて、現在のROIがどの位置に
来るかを画像編集ソフト等で確認する用途に使う(Issue #140)。
