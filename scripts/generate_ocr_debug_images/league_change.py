"""`detection/league_change.py`の確認用画像・READMEを生成する診断スクリプト
(fixtures/ocr_debug/league_change/、Issue #140・#150・#160・#176、自動テストではない)。

昇格判定(`is_league_change_screen()`)の`PROMOTION_TOP_BAND_ROI`/
`PROMOTION_BOTTOM_BAND_ROI`/`PROMOTION_CONTENT_ROI`、降格ラベル判定
(`is_demotion_label_candidate()`)の`DEMOTION_LABEL_ROI`は、いずれも通常のROIを
持つため、vs_rank.py等と同じ`common.py`のCategory機構でannotated画像・
roi_mask_*.pngを生成する。

下の変数を書き換えてこのスクリプトを直接実行すれば
(`uv run python scripts/generate_ocr_debug_images/league_change.py`)、変更後の値で
annotated画像・roi_mask_*.png・README側の記載を再生成できる。デフォルト値は
`detection/league_change.py`の現在値と同じ。
"""

from common import (
    Category,
    OUTPUT_ROOT,
    draw_categories,
    draw_categories_mask,
    load_fixture,
    roi_table_markdown,
    write_annotated,
    write_mask,
)

from nss_tracker.detection.league_change import DEMOTION_LABEL_ROI as _DEMOTION_LABEL_ROI
from nss_tracker.detection.league_change import PROMOTION_BAND_MAX_BRIGHTNESS_STD as _PROMOTION_BAND_MAX_BRIGHTNESS_STD
from nss_tracker.detection.league_change import PROMOTION_BAND_MIN_BRIGHTNESS as _PROMOTION_BAND_MIN_BRIGHTNESS
from nss_tracker.detection.league_change import PROMOTION_BOTTOM_BAND_ROI as _PROMOTION_BOTTOM_BAND_ROI
from nss_tracker.detection.league_change import PROMOTION_CONTENT_HUE_RANGE as _PROMOTION_CONTENT_HUE_RANGE
from nss_tracker.detection.league_change import PROMOTION_CONTENT_ROI as _PROMOTION_CONTENT_ROI
from nss_tracker.detection.league_change import PROMOTION_CONTENT_SAT_MIN as _PROMOTION_CONTENT_SAT_MIN
from nss_tracker.detection.league_change import PROMOTION_TOP_BAND_ROI as _PROMOTION_TOP_BAND_ROI

# 閾値・ROI変数 — ここを書き換えてこのスクリプトを実行すると、変更後の値で
# 画像・READMEを再生成できる(デフォルトはdetection/league_change.pyの現在値と同じ)
DEMOTION_LABEL_ROI = _DEMOTION_LABEL_ROI
PROMOTION_TOP_BAND_ROI = _PROMOTION_TOP_BAND_ROI
PROMOTION_BOTTOM_BAND_ROI = _PROMOTION_BOTTOM_BAND_ROI
PROMOTION_CONTENT_ROI = _PROMOTION_CONTENT_ROI
PROMOTION_BAND_MIN_BRIGHTNESS = _PROMOTION_BAND_MIN_BRIGHTNESS
PROMOTION_BAND_MAX_BRIGHTNESS_STD = _PROMOTION_BAND_MAX_BRIGHTNESS_STD
PROMOTION_CONTENT_HUE_RANGE = _PROMOTION_CONTENT_HUE_RANGE
PROMOTION_CONTENT_SAT_MIN = _PROMOTION_CONTENT_SAT_MIN

# 昇格演出(全画面オーバーレイ)の参照用fixture。79は既存のHDR無効化後fixture、
# 101は2件目のサンプル(Issue #160のROI実測に使用)
PROMOTION_SOURCE_FILENAMES = [
    "79_result_rank_up_hdr_off.png",
    "101_result_rank_up_hdr_off_2.png",
]

# 降格ラベルのannotated画像を生成する対象fixture(fixtures/screenshots/配下)
DEMOTION_SOURCE_FILENAMES = [
    "98_result_lose_with_rank_demotion_red_hdr_off.png",
    "106_result_lose_with_rank_demotion_red_hdr_off_2.png",
]


def main() -> None:
    output_dir = OUTPUT_ROOT / "league_change"
    output_dir.mkdir(parents=True, exist_ok=True)
    print("league_change:")

    promotion_categories = [
        Category("promotion_top_band (PROMOTION_TOP_BAND_ROI)", "brightness", (0, 255, 255), [PROMOTION_TOP_BAND_ROI]),
        Category(
            "promotion_bottom_band (PROMOTION_BOTTOM_BAND_ROI)",
            "brightness",
            (0, 255, 255),
            [PROMOTION_BOTTOM_BAND_ROI],
        ),
        Category("promotion_content (PROMOTION_CONTENT_ROI)", "color", (255, 0, 200), [PROMOTION_CONTENT_ROI]),
    ]
    promotion_image = None
    for source in PROMOTION_SOURCE_FILENAMES:
        promotion_image = load_fixture(source)
        write_annotated(output_dir, source, draw_categories(promotion_image, promotion_categories))
    if promotion_image is not None:
        write_mask(output_dir, "roi_mask_promotion", draw_categories_mask(promotion_image.shape[:2], promotion_categories))

    demotion_categories = [
        Category("demotion_label (DEMOTION_LABEL_ROI)", "color/brightness", (0, 255, 255), [DEMOTION_LABEL_ROI]),
    ]
    demotion_image = None
    for source in DEMOTION_SOURCE_FILENAMES:
        demotion_image = load_fixture(source)
        write_annotated(output_dir, source, draw_categories(demotion_image, demotion_categories))
    if demotion_image is not None:
        write_mask(output_dir, "roi_mask_demotion", draw_categories_mask(demotion_image.shape[:2], demotion_categories))

    readme = output_dir / "README.md"
    readme.write_text(
        f"""# league_change.py のROI

`detection/league_change.py`は昇格・降格それぞれ別方式で判定する。

## 昇格: is_league_change_screen()(Issue #160/#150、ROIベース)

昇格演出は画面上下に**白い横長の帯**が出る(`PROMOTION_TOP_BAND_ROI`/
`PROMOTION_BOTTOM_BAND_ROI`、下記annotated画像の水色枠)。輝度が非常に均一で
明るいことを主判定とし、帯の内側のラベンダー色パネル(`PROMOTION_CONTENT_ROI`、
下記のピンク枠)の色相を補助判定として組み合わせる。

以前はフレーム全体の平均HSVで判定していたが、スタジアムのミント色天蓋が
画面の大部分を占めるだけで誤検知する欠点があった(Issue #150)。VS画面の
レターボックス判定(Issue #144/#189、`detection/matchmaking.py`の
`is_letterboxed()`)と同じ、構造的な特徴(帯の均一性)を主判定にする方式へ
切り替えて解消した。詳細な経緯は`detection/league_change.py`のモジュール
docstring参照。

{roi_table_markdown(promotion_categories)}

判定に使う閾値:

| 閾値 | 値 |
| --- | --- |
| PROMOTION_BAND_MIN_BRIGHTNESS | {PROMOTION_BAND_MIN_BRIGHTNESS} |
| PROMOTION_BAND_MAX_BRIGHTNESS_STD | {PROMOTION_BAND_MAX_BRIGHTNESS_STD} |
| PROMOTION_CONTENT_HUE_RANGE | {PROMOTION_CONTENT_HUE_RANGE} |
| PROMOTION_CONTENT_SAT_MIN | {PROMOTION_CONTENT_SAT_MIN} |

実測(fixtures/screenshots/79・101、別セッション):

| 領域 | H | S | 輝度(グレースケール)平均 | 輝度の標準偏差 |
| --- | --- | --- | --- | --- |
| 上帯(79) | 84.7 | 2.5 | 244.0 | 6.8 |
| 上帯(101) | 80.2 | 3.1 | 245.0 | 6.8 |
| 下帯(79) | 85.7 | 2.5 | 244.0 | 6.8 |
| 下帯(101) | 80.0 | 2.8 | 245.1 | 6.8 |
| 中身(79) | 115.3 | 60.0 | 202.8 | 27.4 |
| 中身(101) | 115.6 | 60.0 | 203.2 | 27.4 |

比較として、Issue #150の天蓋誤検知区間(fixtures/videos/29_lose_blue_hdr_off.mp4
のframe 275〜298)における同じ上下帯の実測: 彩度39〜77・輝度の標準偏差29〜53と、
昇格演出時(彩度2.5〜3.1・標準偏差6.8)から明確に分離できている。

再較正後、fixtures/screenshots全45枚・fixtures/videos全24本(既知の天蓋誤検知
2本(25・29番)を含む)をこのROIでスキャンし、天蓋誤検知は完全に解消(0件)、
既知の昇格演出動画(30・42番)は引き続き正しく検知できることを確認した。
副産物として、`21_goal_event_false_positive_win_blue_4-3.mp4`(Issue #67の
banner.py誤検知動画として収集されたもの)にも、これまで気付かれていなかった
本物の昇格演出区間が含まれていたことが判明した(frame 8532以降)。

`79_result_rank_up_hdr_off_annotated.png`・`101_result_rank_up_hdr_off_2_annotated.png`
がこのROIを重ねた画像。`roi_mask_promotion.png`はROI枠のみのマスク画像。

## 降格: is_demotion_label_candidate() / confirm_demotion_label_text()(Issue #176)

降格時は昇格と異なり全画面オーバーレイが出ず、ランクバッジ上に小さな
「降格」ラベル(白背景の吹き出し+黒文字+下向きの三角ポインター)が乗るだけ。
このラベルの画面上の固定領域(DEMOTION_LABEL_ROI)内で、輝度200以上の
画素数がDEMOTION_LABEL_WHITE_COUNT_RANGEに収まるかで候補判定し
(`is_demotion_label_candidate()`)、候補と判定された場合にPaddleOCRで
「降格」の文字を確認する(`confirm_demotion_label_text()`)。

{roi_table_markdown(demotion_categories)}

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
