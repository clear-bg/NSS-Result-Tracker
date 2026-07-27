"""`detection/match_end.py`のROI確認用画像・READMEを生成する診断スクリプト
(fixtures/ocr_debug/match_end/、Issue #135、自動テストではない)。

下のROI変数を書き換えてこのスクリプトを直接実行すれば
(`uv run python scripts/generate_ocr_debug_images/match_end.py`)、変更後の枠で
`*_annotated.png`(全画面表示、fixture本体と同じ解像度)と`README.md`
(枠色・種別・実測ピクセル座標のテーブル込み)を再生成できる。画像ファイル自体を
手で編集する想定はない。デフォルト値は`detection/match_end.py`の現在値と同じ。
"""

from common import Category, OUTPUT_ROOT, draw_categories, load_fixture, roi_table_markdown, write_annotated

from nss_tracker.detection.match_end import MATCH_END_LEFT_ROI as _MATCH_END_LEFT_ROI
from nss_tracker.detection.match_end import MATCH_END_RIGHT_ROI as _MATCH_END_RIGHT_ROI
from nss_tracker.detection.match_end import MATCH_END_TEXT_ROI as _MATCH_END_TEXT_ROI

# ROI変数 — ここを書き換えてこのスクリプトを実行すると、変更後の枠で
# annotated画像・READMEを再生成できる(デフォルトはdetection/match_end.pyの現在値と同じ)
MATCH_END_LEFT_ROI = _MATCH_END_LEFT_ROI
MATCH_END_RIGHT_ROI = _MATCH_END_RIGHT_ROI
MATCH_END_TEXT_ROI = _MATCH_END_TEXT_ROI

# annotated画像を生成する対象fixture(fixtures/screenshots/配下)
SOURCE_FILENAME = "80_match_end_hdr_off_2.png"


def main() -> None:
    output_dir = OUTPUT_ROOT / "match_end"
    print("match_end:")

    image = load_fixture(SOURCE_FILENAME)
    categories = [
        Category("candidate_left (MATCH_END_LEFT_ROI)", "color", (0, 200, 255), [MATCH_END_LEFT_ROI]),
        Category("candidate_right (MATCH_END_RIGHT_ROI)", "color", (0, 140, 255), [MATCH_END_RIGHT_ROI]),
        Category("text_confirm (MATCH_END_TEXT_ROI)", "OCR", (255, 200, 0), [MATCH_END_TEXT_ROI]),
    ]
    write_annotated(output_dir, SOURCE_FILENAME, draw_categories(image, categories))

    readme = output_dir / "README.md"
    readme.write_text(
        f"""# match_end.py のROI

`detection/match_end.py` が使う切り抜き領域。「試合終了」バナーは、色味が
非常によく似た「延長戦」「キックオフ」バナーと区別するため、軽量な色判定
(候補判定)→OCRによる文字確認、の2段構成になっている。

- **candidate_left (MATCH_END_LEFT_ROI)** / **candidate_right (MATCH_END_RIGHT_ROI)** —
  色判定。文字を避けた帯の左右2箇所の色が「試合終了」帯の色と一致するかを見る
  (`is_match_end_screen()`)。「延長戦」は帯の横幅が異なるためRIGHT側が背景色に
  なり除外できるが、「キックオフ」はこの2点だけでは区別できない
- **text_confirm (MATCH_END_TEXT_ROI)** — OCR。上記の色判定が一定時間連続した
  タイミングで1回だけ、実際に「試合終了」の文字を読んで確定させる
  (`confirm_match_end_text()`)。「延長戦」「キックオフ」との最終的な区別は
  この文字確認で行う

{roi_table_markdown(categories)}

`80_match_end_hdr_off_2.png`は色判定・文字確認とも問題なく通る例。もう1つの
HDR無効化後fixture`76_match_end_hdr_off.png`は目視では同じ「試合終了」帯だが、
帯の実際の描画位置がわずかにズレておりcandidate_left(MATCH_END_LEFT_ROI)の
左上角が帯の丸み端にかかるため、色判定(hue_std)が閾値をわずかに超えて
候補判定自体を通過できない既知の問題がある(`tests/test_match_end.py`で
xfail、Issue #142でROI再較正予定)。
""",
        encoding="utf-8",
    )
    print(f"  wrote {readme.relative_to(OUTPUT_ROOT.parent.parent)}")


if __name__ == "__main__":
    main()
