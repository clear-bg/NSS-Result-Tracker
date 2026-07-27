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

`70_rank_tier_s_annotated.png`はS帯バッジを含む例、
`11_matching_with_rank_blue_annotated.png`は全スロット∞帯の例。
