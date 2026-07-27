# rank_ocr.py のROI

`detection/rank_ocr.py` が使う切り抜き領域。ランクバッジは結果バナー確定直後の
**コンパクト表示**とランク変動アニメーション安定後の**拡大表示**でサイズが
明確に異なるため、ゲージ用ROIは表示サイズごとに別々に用意されている
(CLAUDE.md参照)。

## `44_result_lose_with_rank_blue_annotated.png`(コンパクト表示の例)

- **rank_badge (RANK_ROI)** — OCR。バッジ全体(アイコン+数値)を包む余裕のある
  領域。`read_rank()`は数字のみ(allowlist)でOCRして帯内の数値を、
  `read_rank_tier()`は同じ領域をallowlist無しでOCRしてアイコン部分
  (`'∞'`/`'S'`/`'A'`)を判定する(同じ切り抜きを2種類のOCR設定で読んでいる)
- **gauge_compact (GAUGE_ROI_COMPACT)** — 色/明度判定。バッジ下部の横長ゲージの
  塗りつぶし割合を、列ごとの明度(HSVのV)平均が閾値を超えるかで判定する
  (`read_rank_gauge_fill()`)。結果バナー確定直後(rank_before)にのみ使う

## `46_result_lose_after_rank_decrease_blue_annotated.png`(拡大表示の例)

- **rank_badge (RANK_ROI)** — 上と同じROI・同じ判定(バッジが一回り大きく
  描画されるが、RANK_ROI自体は両サイズをカバーできる余裕を持たせてある)
- **gauge_enlarged (GAUGE_ROI_ENLARGED)** — 色/明度判定。上のGAUGE_ROI_COMPACTと
  同じ考え方だが、拡大表示ではバーの実寸(幅・位置)が異なるため別領域を使う。
  ランク変動アニメーションが安定した後(rank_after)にのみ使う

呼び出し元(`state/match_state.py`)では、どちらのROIを使うべきかは読み取り
タイミングによって一意に決まる(結果バナー確定直後=常にコンパクト、
アニメーション安定後=常に拡大)。
