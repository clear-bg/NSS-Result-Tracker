# rank_ocr.py のROI

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

| ROI | 枠色 | 種別 | 座標 (x1, y1)–(x2, y2) | サイズ (w×h px) |
| --- | --- | --- | --- | --- |
| rank_badge (RANK_ROI) | #00C8FF | OCR | (90, 600)–(420, 930) | 330×330 |
| rank_number_compact (RANK_NUMBER_ROI_COMPACT) | #C800FF | OCR | (208, 890)–(264, 923) | 56×33 |
| gauge_compact (GAUGE_ROI_COMPACT) | #FFC800 | color/brightness | (125, 970)–(345, 990) | 220×20 |

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

| ROI | 枠色 | 種別 | 座標 (x1, y1)–(x2, y2) | サイズ (w×h px) |
| --- | --- | --- | --- | --- |
| rank_badge (RANK_ROI) | #00C8FF | OCR | (90, 600)–(420, 930) | 330×330 |
| rank_number_enlarged (RANK_NUMBER_ROI_ENLARGED) | #C800FF | OCR | (238, 866)–(306, 909) | 68×43 |
| gauge_enlarged (GAUGE_ROI_ENLARGED) | #FF7800 | color/brightness | (130, 966)–(420, 998) | 290×32 |

呼び出し元(`state/match_state.py`)では、どちらのROIを使うべきかは読み取り
タイミングによって一意に決まる(結果バナー確定直後=常にコンパクト、
アニメーション安定後=常に拡大)。

`roi_mask_compact.png`/`roi_mask_enlarged.png`はROI枠のみを描画し、それ以外は
透過にした画像(fixture本体の画像データは含まない)。手元の任意の画像に重ねて、
現在のROIがどの位置に来るかを画像編集ソフト等で確認する用途に使う(Issue #140)。
