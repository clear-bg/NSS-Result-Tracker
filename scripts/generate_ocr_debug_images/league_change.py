"""`detection/league_change.py`の確認用画像・READMEを生成する診断スクリプト
(fixtures/ocr_debug/league_change/、Issue #140、自動テストではない)。

`is_league_change_screen()`はROI(部分領域)を持たず、フレーム全体の平均HSVで
判定する(半透明の全画面オーバーレイのため)。そのため他モジュール用の
スクリプトと異なり、ROI枠を重ねた`*_annotated.png`やROI枠のみの`roi_mask.png`は
生成しない(枠を描いても画面全体を囲むだけで情報にならないため)。fixture本体を
そのまま`*_reference.png`として置き、判定に使う閾値をREADME側にまとめる。

下の閾値変数を書き換えてこのスクリプトを直接実行すれば
(`uv run python scripts/generate_ocr_debug_images/league_change.py`)、README側の
記載を再生成できる。デフォルト値は`detection/league_change.py`の現在値と同じ。
"""

import shutil

from common import FIXTURES_DIR, OUTPUT_ROOT

from nss_tracker.detection.league_change import HUE_RANGE as _HUE_RANGE
from nss_tracker.detection.league_change import SAT_RANGE as _SAT_RANGE
from nss_tracker.detection.league_change import VAL_MIN as _VAL_MIN

# 閾値変数 — ここを書き換えてこのスクリプトを実行すると、変更後の値で
# READMEを再生成できる(デフォルトはdetection/league_change.pyの現在値と同じ)
HUE_RANGE = _HUE_RANGE
SAT_RANGE = _SAT_RANGE
VAL_MIN = _VAL_MIN

# 参照用に置くfixture(fixtures/screenshots/配下)。リーグ昇格演出(全画面
# オーバーレイ)が写っている唯一のHDR無効化後fixture
SOURCE_FILENAME = "79_result_rank_up_hdr_off.png"


def main() -> None:
    output_dir = OUTPUT_ROOT / "league_change"
    output_dir.mkdir(parents=True, exist_ok=True)
    print("league_change:")

    src_path = FIXTURES_DIR / SOURCE_FILENAME
    if not src_path.is_file():
        raise FileNotFoundError(f"fixture not found: {src_path}")
    dst_path = output_dir / f"{src_path.stem}_reference.png"
    shutil.copyfile(src_path, dst_path)
    print(f"  wrote {dst_path.relative_to(OUTPUT_ROOT.parent.parent)}")

    readme = output_dir / "README.md"
    readme.write_text(
        f"""# league_change.py のROI

`detection/league_change.py`の`is_league_change_screen()`はROI(部分領域)を
持たず、**フレーム全体**の平均HSVで判定する。リーグ**昇格**時のみ表示される
半透明の白っぽいオーバーレイが画面全体にかぶるため(降格時はこの全画面
オーバーレイ自体が出ない、モジュールdocstring参照)。

領域を絞った判定ではないため、他モジュール用のスクリプトと違いROI枠を
重ねた画像やマスク画像は生成していない(画面全体を囲む枠を描いても
位置の情報にならないため)。`{SOURCE_FILENAME.replace(".png", "_reference.png")}`は
fixture本体をそのまま置いたもの。

判定に使う閾値:

| 閾値 | 値 |
| --- | --- |
| HUE_RANGE | {HUE_RANGE} |
| SAT_RANGE | {SAT_RANGE} |
| VAL_MIN | {VAL_MIN} |

`{SOURCE_FILENAME}`(昇格演出、実測: H≈100-103, S≈66-70, V≈183-194)が
唯一のHDR無効化後の参照fixture。Issue #160でOCR確認の追加を検討予定。
""",
        encoding="utf-8",
    )
    print(f"  wrote {readme.relative_to(OUTPUT_ROOT.parent.parent)}")


if __name__ == "__main__":
    main()
