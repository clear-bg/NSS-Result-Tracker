"""`detection/vs_rank.py`のROI確認用画像・READMEを生成する診断スクリプト
(fixtures/ocr_debug/vs_rank/、Issue #135、自動テストではない)。

下のROI変数を書き換えてこのスクリプトを直接実行すれば
(`uv run python scripts/generate_ocr_debug_images/vs_rank.py`)、変更後の枠で
`*_annotated.png`(全画面表示、fixture本体と同じ解像度)と`README.md`
(枠色・種別・実測ピクセル座標のテーブル込み)を再生成できる。画像ファイル自体を
手で編集する想定はない。デフォルト値は`detection/vs_rank.py`の現在値と同じ。

各ROI変数は4スロット分(自チーム/相手チームそれぞれ最大4人)をまとめた
タプルで、スロット0が画面手前・スロット3が最も奥。
"""

from common import Category, OUTPUT_ROOT, draw_categories, load_fixture, roi_table_markdown, write_annotated

from nss_tracker.detection.vs_rank import MINE_ICON_ROIS as _MINE_ICON_ROIS
from nss_tracker.detection.vs_rank import MINE_NUM_ROIS as _MINE_NUM_ROIS
from nss_tracker.detection.vs_rank import OPPONENT_ICON_ROIS as _OPPONENT_ICON_ROIS
from nss_tracker.detection.vs_rank import OPPONENT_NUM_ROIS as _OPPONENT_NUM_ROIS

# ROI変数 — ここを書き換えてこのスクリプトを実行すると、変更後の枠で
# annotated画像・READMEを再生成できる(デフォルトはdetection/vs_rank.pyの現在値と同じ)
MINE_ICON_ROIS = _MINE_ICON_ROIS
MINE_NUM_ROIS = _MINE_NUM_ROIS
OPPONENT_ICON_ROIS = _OPPONENT_ICON_ROIS
OPPONENT_NUM_ROIS = _OPPONENT_NUM_ROIS

# annotated画像を生成する対象fixture(fixtures/screenshots/配下)
SOURCE_FILENAME = "82_matching_with_rank_4v3_hdr_off.png"


def main() -> None:
    output_dir = OUTPUT_ROOT / "vs_rank"
    print("vs_rank:")

    categories = [
        Category("mine_icon (MINE_ICON_XYWH)", "OCR", (255, 200, 0), list(MINE_ICON_ROIS)),
        Category("mine_num (MINE_NUM_XYWH)", "OCR", (0, 140, 255), list(MINE_NUM_ROIS)),
        Category("opponent_icon (OPPONENT_ICON_ROIS)", "OCR", (255, 0, 200), list(OPPONENT_ICON_ROIS)),
        Category("opponent_num (OPPONENT_NUM_ROIS)", "OCR", (0, 220, 0), list(OPPONENT_NUM_ROIS)),
    ]

    image = load_fixture(SOURCE_FILENAME)
    write_annotated(output_dir, SOURCE_FILENAME, draw_categories(image, categories))

    readme = output_dir / "README.md"
    readme.write_text(
        f"""# vs_rank.py のROI

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

{roi_table_markdown(categories)}

`82_matching_with_rank_4v3_hdr_off_annotated.png`(Issue #147で収集)は
4vs3の変則試合で、mine[1]・opponent[0]がS帯バッジ、opponent[3]は相手が
3人しかいないため不在(SlotRank(None, None))という、∞帯以外のケースを
複数まとめて確認できる例(`tests/test_vs_rank.py`のEXPECTED_SCREENSHOTS参照)。
""",
        encoding="utf-8",
    )
    print(f"  wrote {readme.relative_to(OUTPUT_ROOT.parent.parent)}")


if __name__ == "__main__":
    main()
