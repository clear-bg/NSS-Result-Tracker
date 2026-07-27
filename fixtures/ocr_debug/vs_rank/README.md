# vs_rank.py のROI

`detection/vs_rank.py` が使う切り抜き領域。VS画面(マッチング完了直後)の
自チーム/相手チームそれぞれ最大4スロット分のランクバッジを読み取る。
スロット0が画面手前(自チーム側は自分自身)、スロット3が最も奥。

全て**OCR**による判定(色/明度判定は無い)。各カテゴリとも4スロット分あり、
画像内の枠の中の小さい数字(0〜3)がスロット番号に対応する。

- **mine_icon (MINE_ICON_XYWH)** — 自チームのバッジのアイコン部分。allowlist無しで
  OCRし、結果が空でなく全て数字なら`'∞'`、`'S'`/`'A'`の文字と一致すればその帯と
  判定する(全て数字=∞アイコンの誤読、という前提。B~E帯は未対応)
- **mine_num (MINE_NUM_XYWH)** — 自チームのバッジの数値ピル部分。数字のみでOCRし、
  帯内の数値を読み取る
- **opponent_icon (OPPONENT_ICON_ROIS)** — 相手チーム版のmine_icon。y座標・幅・
  高さはmine_icon側の対応スロットと同じで、x座標(OPPONENT_X1)のみ個別に実測した値
- **opponent_num (OPPONENT_NUM_ROIS)** — 相手チーム版のmine_num

| ROI | 枠色 | 種別 | 座標 (x1, y1)–(x2, y2) | サイズ (w×h px) |
| --- | --- | --- | --- | --- |
| mine_icon (MINE_ICON_XYWH) [0] | `#00C8FF` | OCR | (83, 830)–(116, 863) | 33×33 |
| mine_icon (MINE_ICON_XYWH) [1] | `#00C8FF` | OCR | (305, 752)–(333, 778) | 28×26 |
| mine_icon (MINE_ICON_XYWH) [2] | `#00C8FF` | OCR | (465, 686)–(493, 708) | 28×22 |
| mine_icon (MINE_ICON_XYWH) [3] | `#00C8FF` | OCR | (649, 629)–(673, 645) | 24×16 |
| mine_num (MINE_NUM_XYWH) [0] | `#FF8C00` | OCR | (83, 871)–(116, 890) | 33×19 |
| mine_num (MINE_NUM_XYWH) [1] | `#FF8C00` | OCR | (305, 788)–(333, 805) | 28×17 |
| mine_num (MINE_NUM_XYWH) [2] | `#FF8C00` | OCR | (465, 714)–(493, 728) | 28×14 |
| mine_num (MINE_NUM_XYWH) [3] | `#FF8C00` | OCR | (649, 652)–(673, 664) | 24×12 |
| opponent_icon (OPPONENT_ICON_ROIS) [0] | `#C800FF` | OCR | (1448, 830)–(1481, 863) | 33×33 |
| opponent_icon (OPPONENT_ICON_ROIS) [1] | `#C800FF` | OCR | (1270, 752)–(1298, 778) | 28×26 |
| opponent_icon (OPPONENT_ICON_ROIS) [2] | `#C800FF` | OCR | (1151, 686)–(1179, 708) | 28×22 |
| opponent_icon (OPPONENT_ICON_ROIS) [3] | `#C800FF` | OCR | (991, 629)–(1015, 645) | 24×16 |
| opponent_num (OPPONENT_NUM_ROIS) [0] | `#00DC00` | OCR | (1448, 871)–(1481, 890) | 33×19 |
| opponent_num (OPPONENT_NUM_ROIS) [1] | `#00DC00` | OCR | (1270, 788)–(1298, 805) | 28×17 |
| opponent_num (OPPONENT_NUM_ROIS) [2] | `#00DC00` | OCR | (1151, 714)–(1179, 728) | 28×14 |
| opponent_num (OPPONENT_NUM_ROIS) [3] | `#00DC00` | OCR | (991, 652)–(1015, 664) | 24×12 |

`82_matching_with_rank_4v3_hdr_off_annotated.png`(Issue #147で収集)は
4vs3の変則試合で、mine[1]・opponent[0]がS帯バッジ、opponent[3]は相手が
3人しかいないため不在(SlotRank(None, None))という、∞帯以外のケースを
複数まとめて確認できる例(`tests/test_vs_rank.py`のEXPECTED_SCREENSHOTS参照)。
