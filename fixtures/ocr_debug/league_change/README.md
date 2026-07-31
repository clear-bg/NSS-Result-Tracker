# league_change.py のROI

`detection/league_change.py`は昇格・降格それぞれ別方式で判定する。

## 昇格: is_league_change_screen()(現状の実装、ROI無し・画面全体の平均HSV)

現在の実装はROI(部分領域)を持たず、**フレーム全体**の平均HSVで判定する。
リーグ**昇格**時のみ表示される半透明の白っぽいオーバーレイが画面全体に
かぶるため。

判定に使う閾値:

| 閾値 | 値 |
| --- | --- |
| HUE_RANGE | (95, 108) |
| SAT_RANGE | (55, 80) |
| VAL_MIN | 180 |

Issue #150: この「画面全体」判定は、スタジアムのミント色天蓋が画面の
大部分を占めるだけで誤検知しうる欠点がある(fixtures/videos/29_lose_blue_hdr_off.mp4
のframe 280〜294で実際に発生)。

## 昇格: Issue #160で検討中のROIベース案(未実装)

Issue #150の根本対応として、VS画面のレターボックス判定(Issue #144/#189、
`detection/matchmaking.py`の`is_letterboxed()`)と同じ考え方の候補ROIを検討中。
昇格演出は画面上下に**白い横長の帯**が出る(`PROMOTION_TOP_BAND_ROI`/
`PROMOTION_BOTTOM_BAND_ROI`、下記annotated画像の水色枠)。これを主判定とし、
帯の内側のラベンダー色パネル(`PROMOTION_CONTENT_ROI`、下記のピンク枠)の
色を補助判定として組み合わせることで、白帯だけなら他の要因(画面が単に
真っ白になった等)でも誤検知しうるケースを弾く狙い。

| ROI | 枠色 | 種別 | 座標 (x1, y1)–(x2, y2) | サイズ (w×h px) |
| --- | --- | --- | --- | --- |
| promotion_top_band (PROMOTION_TOP_BAND_ROI, 検討中) | #FFFF00 | brightness | (0, 122)–(1920, 158) | 1920×36 |
| promotion_bottom_band (PROMOTION_BOTTOM_BAND_ROI, 検討中) | #FFFF00 | brightness | (0, 924)–(1920, 957) | 1920×33 |
| promotion_content (PROMOTION_CONTENT_ROI, 検討中) | #C800FF | color | (0, 183)–(1920, 893) | 1920×710 |

実測(fixtures/screenshots/79・101、別セッション):

| 領域 | H | S | V | 輝度(グレースケール)平均 | 輝度の標準偏差 |
| --- | --- | --- | --- | --- | --- |
| 上帯(79) | 84.7 | 2.5 | 245.2 | 244.0 | 6.8 |
| 上帯(101) | 80.2 | 3.1 | 246.2 | 245.0 | 6.8 |
| 下帯(79) | 85.7 | 2.5 | 245.2 | 244.0 | 6.8 |
| 下帯(101) | 80.0 | 2.8 | 246.2 | 245.1 | 6.8 |
| 中身(79) | 115.3 | 60.0 | 249.0 | 202.8 | 27.4 |
| 中身(101) | 115.6 | 60.0 | 249.8 | 203.2 | 27.4 |

比較として、Issue #150の天蓋誤検知区間(fixtures/videos/29_lose_blue_hdr_off.mp4
のframe 275〜298)における同じ上下帯の実測: 彩度39〜77・輝度の標準偏差29〜53と、
昇格演出時(彩度2.5〜3.1・標準偏差6.8)から明確に外れている。この候補ROIなら
天蓋誤検知を分離できる見込みが高い(詳細な閾値決定・実装はIssue #160で
別途行う。**PROMOTION_*_ROIはまだdetection/league_change.py側に実装されて
いない検討用の値**、モジュールdocstring参照)。

`79_result_rank_up_hdr_off_annotated.png`・`101_result_rank_up_hdr_off_2_annotated.png`
がこの候補ROIを重ねた画像。`roi_mask_promotion.png`はROI枠のみのマスク画像。

## 降格: is_demotion_label_candidate() / confirm_demotion_label_text()(Issue #176)

降格時は昇格と異なり全画面オーバーレイが出ず、ランクバッジ上に小さな
「降格」ラベル(白背景の吹き出し+黒文字+下向きの三角ポインター)が乗るだけ。
このラベルの画面上の固定領域(DEMOTION_LABEL_ROI)内で、輝度200以上の
画素数がDEMOTION_LABEL_WHITE_COUNT_RANGEに収まるかで候補判定し
(`is_demotion_label_candidate()`)、候補と判定された場合にPaddleOCRで
「降格」の文字を確認する(`confirm_demotion_label_text()`)。

| ROI | 枠色 | 種別 | 座標 (x1, y1)–(x2, y2) | サイズ (w×h px) |
| --- | --- | --- | --- | --- |
| demotion_label (DEMOTION_LABEL_ROI) | #FFFF00 | color/brightness | (190, 530)–(420, 680) | 230×150 |

実測(fixtures/screenshots/98・106、fixtures/videos/40のframe 450、
いずれも別セッション): ラベル表示中はDEMOTION_LABEL_ROI内の輝度200以上の
画素数が10589〜10890に収束。非該当のfixtures/screenshots全43枚は4456以下、
または昇格演出等の画面全体が明るい特殊画面で25701以上(詳細は
detection/league_change.pyのモジュールdocstring参照)。

`98_result_lose_with_rank_demotion_red_hdr_off_annotated.png`・
`106_result_lose_with_rank_demotion_red_hdr_off_2_annotated.png`はいずれも
実際に降格した試合の結果画面(別セッション)。`106`はユーザー提供の
`tmp/赤_負け_降格.mp4`のframe 360から切り出したもの(同名の`.png`は
YouTube再生画面のブラウザUIが写り込んでいたため使わなかった)。
`roi_mask_demotion.png`はROI枠のみのマスク画像(Issue #160対応で昇格用の
`roi_mask_promotion.png`が増えたため、従来`roi_mask.png`という名前だった
ものをこちらにリネームした)。
