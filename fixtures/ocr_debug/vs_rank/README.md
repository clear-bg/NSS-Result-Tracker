# VS画面のROI(vs_rank.py / matchmaking.py / team_color.py)

VS画面(マッチング完了直後)という1つの画面状態に対して、3つの別モジュールが
それぞれ別のROIで判定している。モジュール単位でフォルダを分けている他の
画面とは異なり、画面単位でこのフォルダにまとめている。

## vs_rank.py(自チーム/相手チームそれぞれ最大4スロット分のランクバッジ、OCR)

スロット0が画面手前(自チーム側は自分自身)、スロット3が最も奥。各カテゴリとも
4スロット分あり、画像内の枠の中の小さい数字(0〜3)がスロット番号に対応する。

- **mine_icon (MINE_ICON_XYWH)** — 自チームのバッジのアイコン部分。allowlist無しで
  OCRし、結果が空でなく全て数字なら`'∞'`、`'S'`/`'A'`の文字と一致すればその帯と
  判定する(全て数字=∞アイコンの誤読、という前提。B~E帯は未対応)
- **mine_num (MINE_NUM_XYWH)** — 自チームのバッジの数値ピル部分。数字のみでOCRし、
  帯内の数値を読み取る
- **opponent_icon (OPPONENT_ICON_ROIS)** — 相手チーム版のmine_icon。y座標・幅・
  高さはmine_icon側の対応スロットと同じで、x座標(OPPONENT_X1)のみ個別に実測した値
- **opponent_num (OPPONENT_NUM_ROIS)** — 相手チーム版のmine_num

## matchmaking.py(VS画面自体の検知、色判定)

- **vs_logo (VS_ROI)** — 画面中央に一瞬表示される「VS」ロゴの文字部分だけを
  狙った領域。`is_vs_screen()`が使う。**fixture画像(cv2.imread経由)では
  真陽性の検証ができない**(実際の検知ループはffmpeg経由でフレームを読んでおり
  色変換経路が異なるため、`detection/matchmaking.py`のモジュールdocstring
  参照)。この画像で示しているのはあくまでROIの位置のみ

## team_color.py(チームカラーのサンプリング、色判定)

- **team_color_mine (TEAM_COLOR_MINE_ROI)** / **team_color_opponent
  (TEAM_COLOR_OPPONENT_ROI)** — 自チーム/相手チームの名前タグ背景(スロット0、
  文字やバッジが重ならない帯)から実際の描画色をそのまま平均RGBでサンプリング
  する`read_team_colors()`が使う領域

| ROI | 枠色 | 種別 | 座標 (x1, y1)–(x2, y2) | サイズ (w×h px) |
| --- | --- | --- | --- | --- |
| mine_icon (MINE_ICON_XYWH) [0] | #00C8FF | OCR | (83, 830)–(116, 863) | 33×33 |
| mine_icon (MINE_ICON_XYWH) [1] | #00C8FF | OCR | (305, 752)–(333, 778) | 28×26 |
| mine_icon (MINE_ICON_XYWH) [2] | #00C8FF | OCR | (465, 686)–(493, 708) | 28×22 |
| mine_icon (MINE_ICON_XYWH) [3] | #00C8FF | OCR | (649, 629)–(673, 645) | 24×16 |
| mine_num (MINE_NUM_XYWH) [0] | #FF8C00 | OCR | (83, 871)–(116, 890) | 33×19 |
| mine_num (MINE_NUM_XYWH) [1] | #FF8C00 | OCR | (305, 788)–(333, 805) | 28×17 |
| mine_num (MINE_NUM_XYWH) [2] | #FF8C00 | OCR | (465, 714)–(493, 728) | 28×14 |
| mine_num (MINE_NUM_XYWH) [3] | #FF8C00 | OCR | (649, 652)–(673, 664) | 24×12 |
| opponent_icon (OPPONENT_ICON_ROIS) [0] | #C800FF | OCR | (1448, 830)–(1481, 863) | 33×33 |
| opponent_icon (OPPONENT_ICON_ROIS) [1] | #C800FF | OCR | (1270, 752)–(1298, 778) | 28×26 |
| opponent_icon (OPPONENT_ICON_ROIS) [2] | #C800FF | OCR | (1151, 686)–(1179, 708) | 28×22 |
| opponent_icon (OPPONENT_ICON_ROIS) [3] | #C800FF | OCR | (991, 629)–(1015, 645) | 24×16 |
| opponent_num (OPPONENT_NUM_ROIS) [0] | #00DC00 | OCR | (1448, 871)–(1481, 890) | 33×19 |
| opponent_num (OPPONENT_NUM_ROIS) [1] | #00DC00 | OCR | (1270, 788)–(1298, 805) | 28×17 |
| opponent_num (OPPONENT_NUM_ROIS) [2] | #00DC00 | OCR | (1151, 714)–(1179, 728) | 28×14 |
| opponent_num (OPPONENT_NUM_ROIS) [3] | #00DC00 | OCR | (991, 652)–(1015, 664) | 24×12 |
| vs_logo (VS_ROI, matchmaking.py) | #00FFFF | color | (880, 495)–(1050, 600) | 170×105 |
| team_color_mine (TEAM_COLOR_MINE_ROI, team_color.py) | #0000FF | color | (150, 878)–(400, 887) | 250×9 |
| team_color_opponent (TEAM_COLOR_OPPONENT_ROI, team_color.py) | #FF0000 | color | (1515, 878)–(1765, 887) | 250×9 |

`82_matching_with_rank_4v3_hdr_off_annotated.png`(Issue #147で収集)は
4vs3の変則試合で、mine[1]・opponent[0]がS帯バッジ、opponent[3]は相手が
3人しかいないため不在(SlotRank(None, None))という、∞帯以外のケースを
複数まとめて確認できる例(`tests/test_vs_rank.py`のEXPECTED_SCREENSHOTS参照)。

`86_matching_with_rank_4v4_hdr_off_annotated.png`は両チームとも4人揃った
通常編成(4vs4)の例。mine[2]・mine[3]がA帯バッジで、82には無かったA帯の
参照としても使える。

`roi_mask.png`はROI枠のみを描画し、それ以外は透過にした画像(fixture本体の
画像データは含まない)。手元の任意の画像に重ねて、現在のROIがどの位置に
来るかを画像編集ソフト等で確認する用途に使う(Issue #140)。
