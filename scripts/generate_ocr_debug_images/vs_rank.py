"""VS画面(マッチング完了直後)で検知しているROIをまとめて確認する診断
スクリプト(fixtures/ocr_debug/vs_rank/、Issue #135・#140、自動テストではない)。

同じVS画面という1つの画面状態に対して、`detection/vs_rank.py`(ランクバッジ
OCR)・`detection/matchmaking.py`(VS画面自体の検知)・`detection/team_color.py`
(チームカラーのサンプリング)と3つの別モジュールがそれぞれ別のROIで判定して
いるため、画面単位でまとめて1つのフォルダに置いている(モジュール単位で
フォルダを分けている他の画面とは異なる構成)。

下のROI変数を書き換えてこのスクリプトを直接実行すれば
(`uv run python scripts/generate_ocr_debug_images/vs_rank.py`)、変更後の枠で
`*_annotated.png`(全画面表示、fixture本体と同じ解像度)・`roi_mask.png`
(ROI枠のみで他は透過、任意の画像に重ねて確認する用)・`README.md`
(枠色・種別・実測ピクセル座標のテーブル込み)を再生成できる。画像ファイル自体を
手で編集する想定はない。デフォルト値は各モジュールの現在値と同じ。

vs_rank.py側の各ROI変数は4スロット分(自チーム/相手チームそれぞれ最大4人)を
まとめたタプルで、スロット0が画面手前・スロット3が最も奥。
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

from nss_tracker.detection.matchmaking import LETTERBOX_BOTTOM_ROI as _LETTERBOX_BOTTOM_ROI
from nss_tracker.detection.matchmaking import LETTERBOX_MIDDLE_ROI as _LETTERBOX_MIDDLE_ROI
from nss_tracker.detection.matchmaking import LETTERBOX_TOP_ROI as _LETTERBOX_TOP_ROI
from nss_tracker.detection.matchmaking import VS_ROI as _VS_ROI
from nss_tracker.detection.team_color import MINE_ROI as _TEAM_COLOR_MINE_ROI
from nss_tracker.detection.team_color import OPPONENT_ROI as _TEAM_COLOR_OPPONENT_ROI
from nss_tracker.detection.vs_rank import MINE_ICON_ROIS as _MINE_ICON_ROIS
from nss_tracker.detection.vs_rank import MINE_NUM_ROIS as _MINE_NUM_ROIS
from nss_tracker.detection.vs_rank import OPPONENT_ICON_ROIS as _OPPONENT_ICON_ROIS
from nss_tracker.detection.vs_rank import OPPONENT_NUM_ROIS as _OPPONENT_NUM_ROIS

# ROI変数 — ここを書き換えてこのスクリプトを実行すると、変更後の枠で
# annotated画像・READMEを再生成できる(デフォルトは各モジュールの現在値と同じ)
MINE_ICON_ROIS = _MINE_ICON_ROIS
MINE_NUM_ROIS = _MINE_NUM_ROIS
OPPONENT_ICON_ROIS = _OPPONENT_ICON_ROIS
OPPONENT_NUM_ROIS = _OPPONENT_NUM_ROIS
VS_ROI = _VS_ROI
TEAM_COLOR_MINE_ROI = _TEAM_COLOR_MINE_ROI
TEAM_COLOR_OPPONENT_ROI = _TEAM_COLOR_OPPONENT_ROI
LETTERBOX_TOP_ROI = _LETTERBOX_TOP_ROI
LETTERBOX_BOTTOM_ROI = _LETTERBOX_BOTTOM_ROI
LETTERBOX_MIDDLE_ROI = _LETTERBOX_MIDDLE_ROI

# annotated画像を生成する対象fixture(fixtures/screenshots/配下)
SOURCE_FILENAMES = [
    "82_matching_with_rank_4v3_hdr_off.png",
    "86_matching_with_rank_4v4_hdr_off.png",
]


def main() -> None:
    output_dir = OUTPUT_ROOT / "vs_rank"
    print("vs_rank:")

    categories = [
        Category("mine_icon (MINE_ICON_XYWH)", "OCR", (255, 200, 0), list(MINE_ICON_ROIS)),
        Category("mine_num (MINE_NUM_XYWH)", "OCR", (0, 140, 255), list(MINE_NUM_ROIS)),
        Category("opponent_icon (OPPONENT_ICON_ROIS)", "OCR", (255, 0, 200), list(OPPONENT_ICON_ROIS)),
        Category("opponent_num (OPPONENT_NUM_ROIS)", "OCR", (0, 220, 0), list(OPPONENT_NUM_ROIS)),
        Category("vs_logo (VS_ROI, matchmaking.py)", "color", (255, 255, 0), [VS_ROI]),
        Category("team_color_mine (TEAM_COLOR_MINE_ROI, team_color.py)", "color", (255, 0, 0), [TEAM_COLOR_MINE_ROI]),
        Category(
            "team_color_opponent (TEAM_COLOR_OPPONENT_ROI, team_color.py)",
            "color",
            (0, 0, 255),
            [TEAM_COLOR_OPPONENT_ROI],
        ),
        Category(
            "letterbox_top (LETTERBOX_TOP_ROI, matchmaking.py)",
            "brightness",
            (0, 255, 255),
            [LETTERBOX_TOP_ROI],
        ),
        Category(
            "letterbox_bottom (LETTERBOX_BOTTOM_ROI, matchmaking.py)",
            "brightness",
            (0, 255, 255),
            [LETTERBOX_BOTTOM_ROI],
        ),
        Category(
            "letterbox_middle (LETTERBOX_MIDDLE_ROI, matchmaking.py)",
            "brightness",
            (128, 128, 128),
            [LETTERBOX_MIDDLE_ROI],
        ),
    ]

    for source in SOURCE_FILENAMES:
        image = load_fixture(source)
        write_annotated(output_dir, source, draw_categories(image, categories))
    write_mask(output_dir, "roi_mask", draw_categories_mask(image.shape[:2], categories))

    readme = output_dir / "README.md"
    readme.write_text(
        f"""# VS画面のROI(vs_rank.py / matchmaking.py / team_color.py)

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

## matchmaking.py(VS画面自体の検知、色判定+レターボックス判定)

- **vs_logo (VS_ROI)** — 画面中央に一瞬表示される「VS」ロゴの文字部分だけを
  狙った領域。`is_vs_screen()`が使う大まかな除外フィルタ(Issue #144/#189で
  主判定からは格下げ、詳細は`detection/matchmaking.py`のモジュールdocstring参照)
- **letterbox_top / letterbox_bottom (LETTERBOX_TOP_ROI / LETTERBOX_BOTTOM_ROI)** —
  VS画面特有の上下黒帯を検知する領域(全幅)。`is_letterboxed()`(`is_vs_screen()`の
  主判定)が使う
- **letterbox_middle (LETTERBOX_MIDDLE_ROI)** — 試合間の暗転(画面全体が暗い)と
  区別するための中央参照領域。ここが暗いままなら暗転とみなしFalseになる

Issue #144/#189対応(2026-07-31)で、色判定単体からレターボックス判定+色判定の
組み合わせに変更した経緯・実測データは`detection/matchmaking.py`のモジュール
docstring参照。この変更に伴い、輝度ベースの主判定はcv2.imreadでも
ffmpegパイプラインでも大きくブレないため、fixture画像でも真陽性の検証が
できるようになった(`72_matching_hdr_off_1.png`等、"matching"を含む5枚で確認済み。
`tests/test_matchmaking.py`参照)。

## team_color.py(チームカラーのサンプリング、色判定)

- **team_color_mine (TEAM_COLOR_MINE_ROI)** / **team_color_opponent
  (TEAM_COLOR_OPPONENT_ROI)** — 自チーム/相手チームの名前タグ背景(スロット0、
  文字やバッジが重ならない帯)から実際の描画色をそのまま平均RGBでサンプリング
  する`read_team_colors()`が使う領域

{roi_table_markdown(categories)}

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
""",
        encoding="utf-8",
    )
    print(f"  wrote {readme.relative_to(OUTPUT_ROOT.parent.parent)}")


if __name__ == "__main__":
    main()
