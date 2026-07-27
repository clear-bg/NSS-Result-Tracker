# rank_ocr.py のROI

`detection/rank_ocr.py` が使う切り抜き領域。ランクバッジは結果バナー確定直後の
**コンパクト表示**とランク変動アニメーション安定後の**拡大表示**でサイズが
明確に異なるため、ゲージ用ROIは表示サイズごとに別々に用意されている
(CLAUDE.md参照)。

## `78_result_lose_with_rank_blue_hdr_off_annotated.png`(コンパクト表示の例)

- **rank_badge (RANK_ROI)** — OCR。バッジ全体(アイコン+数値)を包む余裕のある
  領域。`read_rank()`は数字のみ(allowlist)でOCRして帯内の数値を、
  `read_rank_tier()`は同じ領域をallowlist無しでOCRしてアイコン部分
  (`'∞'`/`'S'`/`'A'`)を判定する(同じ切り抜きを2種類のOCR設定で読んでいる)
- **gauge_compact (GAUGE_ROI_COMPACT)** — 色/明度判定。バッジ下部の横長ゲージの
  塗りつぶし割合を、列ごとの明度(HSVのV)平均が閾値を超えるかで判定する
  (`read_rank_gauge_fill()`)。結果バナー確定直後(rank_before)にのみ使う

| ROI | 枠色 | 種別 | 座標 (x1, y1)–(x2, y2) | サイズ (w×h px) |
| --- | --- | --- | --- | --- |
| rank_badge (RANK_ROI) | `#00C8FF` | OCR | (90, 600)–(420, 930) | 330×330 |
| gauge_compact (GAUGE_ROI_COMPACT) | `#FFC800` | color/brightness | (125, 970)–(345, 990) | 220×20 |

## 拡大表示(ランク変動アニメーション安定後の例)

- **rank_badge (RANK_ROI)** — 上と同じROI・同じ判定(バッジが一回り大きく
  描画されるが、RANK_ROI自体は両サイズをカバーできる余裕を持たせてある)
- **gauge_enlarged (GAUGE_ROI_ENLARGED)** — 色/明度判定。上のGAUGE_ROI_COMPACTと
  同じ考え方だが、拡大表示ではバーの実寸(幅・位置)が異なるため別領域を使う。
  ランク変動アニメーションが安定した後(rank_after)にのみ使う

| ROI | 枠色 | 種別 | 座標 (x1, y1)–(x2, y2) | サイズ (w×h px) |
| --- | --- | --- | --- | --- |
| rank_badge (RANK_ROI) | `#00C8FF` | OCR | (90, 600)–(420, 930) | 330×330 |
| gauge_enlarged (GAUGE_ROI_ENLARGED) | `#FF7800` | color/brightness | (130, 966)–(420, 998) | 290×32 |

拡大表示のHDR無効化後fixtureがまだ無いため(Issue #147)、この表示に対応する
`*_annotated.png`は生成していない(座標のみ上表に記載)。

呼び出し元(`state/match_state.py`)では、どちらのROIを使うべきかは読み取り
タイミングによって一意に決まる(結果バナー確定直後=常にコンパクト、
アニメーション安定後=常に拡大)。
