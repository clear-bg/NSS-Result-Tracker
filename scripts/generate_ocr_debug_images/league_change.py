"""`detection/league_change.py`の確認用画像・READMEを生成する診断スクリプト
(fixtures/ocr_debug/league_change/、Issue #140・#176、自動テストではない)。

`is_league_change_screen()`(昇格の全画面オーバーレイ判定)はROI(部分領域)を
持たず、フレーム全体の平均HSVで判定するため、他モジュール用のスクリプトと
異なりROI枠を重ねた`*_annotated.png`は生成せず、fixture本体をそのまま
`*_reference.png`として置き、判定に使う閾値をREADME側にまとめる。

一方、Issue #176で追加した降格ラベル判定(`is_demotion_label_candidate()`)は
DEMOTION_LABEL_ROIという通常のROIを持つため、こちらはvs_rank.py等と同じ
`common.py`のCategory機構でannotated画像・roi_mask.pngを生成する。

下の変数を書き換えてこのスクリプトを直接実行すれば
(`uv run python scripts/generate_ocr_debug_images/league_change.py`)、変更後の値で
annotated画像・roi_mask.png・README側の記載を再生成できる。デフォルト値は
`detection/league_change.py`の現在値と同じ。
"""

import shutil

from common import (
    Category,
    FIXTURES_DIR,
    OUTPUT_ROOT,
    draw_categories,
    draw_categories_mask,
    load_fixture,
    roi_table_markdown,
    write_annotated,
    write_mask,
)

from nss_tracker.detection.league_change import DEMOTION_LABEL_ROI as _DEMOTION_LABEL_ROI
from nss_tracker.detection.league_change import HUE_RANGE as _HUE_RANGE
from nss_tracker.detection.league_change import SAT_RANGE as _SAT_RANGE
from nss_tracker.detection.league_change import VAL_MIN as _VAL_MIN

# 閾値・ROI変数 — ここを書き換えてこのスクリプトを実行すると、変更後の値で
# 画像・READMEを再生成できる(デフォルトはdetection/league_change.pyの現在値と同じ)
HUE_RANGE = _HUE_RANGE
SAT_RANGE = _SAT_RANGE
VAL_MIN = _VAL_MIN
DEMOTION_LABEL_ROI = _DEMOTION_LABEL_ROI

# 昇格演出(全画面オーバーレイ)の参照用fixture。リーグ昇格演出が写っている
# 唯一のHDR無効化後fixture
PROMOTION_SOURCE_FILENAME = "79_result_rank_up_hdr_off.png"

# 降格ラベルのannotated画像を生成する対象fixture(fixtures/screenshots/配下)
DEMOTION_SOURCE_FILENAMES = [
    "98_result_lose_with_rank_demotion_red_hdr_off.png",
    "106_result_lose_with_rank_demotion_red_hdr_off_2.png",
]


def main() -> None:
    output_dir = OUTPUT_ROOT / "league_change"
    output_dir.mkdir(parents=True, exist_ok=True)
    print("league_change:")

    src_path = FIXTURES_DIR / PROMOTION_SOURCE_FILENAME
    if not src_path.is_file():
        raise FileNotFoundError(f"fixture not found: {src_path}")
    dst_path = output_dir / f"{src_path.stem}_reference.png"
    shutil.copyfile(src_path, dst_path)
    print(f"  wrote {dst_path.relative_to(OUTPUT_ROOT.parent.parent)}")

    categories = [
        Category("demotion_label (DEMOTION_LABEL_ROI)", "color/brightness", (0, 255, 255), [DEMOTION_LABEL_ROI]),
    ]
    image = None
    for source in DEMOTION_SOURCE_FILENAMES:
        image = load_fixture(source)
        write_annotated(output_dir, source, draw_categories(image, categories))
    if image is not None:
        write_mask(output_dir, "roi_mask", draw_categories_mask(image.shape[:2], categories))

    readme = output_dir / "README.md"
    readme.write_text(
        f"""# league_change.py のROI

`detection/league_change.py`は昇格・降格それぞれ別方式で判定する。

## 昇格: is_league_change_screen()(ROI無し、画面全体の平均HSV)

ROI(部分領域)を持たず、**フレーム全体**の平均HSVで判定する。リーグ**昇格**時
のみ表示される半透明の白っぽいオーバーレイが画面全体にかぶるため。
領域を絞った判定ではないため、annotated画像・マスク画像は生成していない
(画面全体を囲む枠を描いても位置の情報にならないため)。
`{PROMOTION_SOURCE_FILENAME.replace(".png", "_reference.png")}`はfixture本体を
そのまま置いたもの。

判定に使う閾値:

| 閾値 | 値 |
| --- | --- |
| HUE_RANGE | {HUE_RANGE} |
| SAT_RANGE | {SAT_RANGE} |
| VAL_MIN | {VAL_MIN} |

`{PROMOTION_SOURCE_FILENAME}`(昇格演出、実測: H≈100-103, S≈66-70, V≈183-194)が
唯一のHDR無効化後の参照fixture。

## 降格: is_demotion_label_candidate() / confirm_demotion_label_text()(Issue #176)

降格時は昇格と異なり全画面オーバーレイが出ず、ランクバッジ上に小さな
「降格」ラベル(白背景の吹き出し+黒文字+下向きの三角ポインター)が乗るだけ。
このラベルの画面上の固定領域(DEMOTION_LABEL_ROI)内で、輝度200以上の
画素数がDEMOTION_LABEL_WHITE_COUNT_RANGEに収まるかで候補判定し
(`is_demotion_label_candidate()`)、候補と判定された場合にPaddleOCRで
「降格」の文字を確認する(`confirm_demotion_label_text()`)。

{roi_table_markdown(categories)}

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
""",
        encoding="utf-8",
    )
    print(f"  wrote {readme.relative_to(OUTPUT_ROOT.parent.parent)}")


if __name__ == "__main__":
    main()
