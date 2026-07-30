# league_change.py のROI

`detection/league_change.py`は昇格・降格それぞれ別方式で判定する。

## 昇格: is_league_change_screen()(ROI無し、画面全体の平均HSV)

ROI(部分領域)を持たず、**フレーム全体**の平均HSVで判定する。リーグ**昇格**時
のみ表示される半透明の白っぽいオーバーレイが画面全体にかぶるため。
領域を絞った判定ではないため、annotated画像・マスク画像は生成していない
(画面全体を囲む枠を描いても位置の情報にならないため)。
`79_result_rank_up_hdr_off_reference.png`はfixture本体を
そのまま置いたもの。

判定に使う閾値:

| 閾値 | 値 |
| --- | --- |
| HUE_RANGE | (95, 108) |
| SAT_RANGE | (55, 80) |
| VAL_MIN | 180 |

`79_result_rank_up_hdr_off.png`(昇格演出、実測: H≈100-103, S≈66-70, V≈183-194)が
唯一のHDR無効化後の参照fixture。

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
