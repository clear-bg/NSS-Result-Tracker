# league_change.py のROI

`detection/league_change.py`の`is_league_change_screen()`はROI(部分領域)を
持たず、**フレーム全体**の平均HSVで判定する。リーグ**昇格**時のみ表示される
半透明の白っぽいオーバーレイが画面全体にかぶるため(降格時はこの全画面
オーバーレイ自体が出ない、モジュールdocstring参照)。

領域を絞った判定ではないため、他モジュール用のスクリプトと違いROI枠を
重ねた画像やマスク画像は生成していない(画面全体を囲む枠を描いても
位置の情報にならないため)。`79_result_rank_up_hdr_off_reference.png`は
fixture本体をそのまま置いたもの。

判定に使う閾値:

| 閾値 | 値 |
| --- | --- |
| HUE_RANGE | (95, 108) |
| SAT_RANGE | (55, 80) |
| VAL_MIN | 180 |

`79_result_rank_up_hdr_off.png`(昇格演出、実測: H≈100-103, S≈66-70, V≈183-194)が
唯一のHDR無効化後の参照fixture。Issue #160でOCR確認の追加を検討予定。
