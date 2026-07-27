"""OCR/色判定でROIを切り出して判定しているdetectionモジュールについて、
fixture画像にROI枠を重ねた確認用画像と、各ROIが何を判定しているかを
説明するMarkdownを生成する診断スクリプト(Issue #135、自動テストではない)。

生成物は fixtures/ocr_debug/<モジュール名>/ 配下に置く:
- `*_annotated.png`: ROI枠を重ねた画像(fixture本体同様プレイヤーの実名・
  容姿を含むため.gitignore対象。都度このスクリプトで再生成する想定)。
  ゲーム画面(fixture本体)をそのままの解像度・全画面表示で使い、枠と
  (複数インスタンスがある場合のみ)枠内の小さな連番だけを重ねる。
  ROIが実際に何ピクセルの位置にあるかを画像上でそのまま確認できるように
  するため、画像に余白パネルなどは一切追加しない(Issue #140)
- `README.md`: 各ROIの役割(文字認識か色/明度判定か等)と、枠色・実測
  ピクセル座標を説明する(個人情報を含まないためgit管理対象)

以前手作りで作っていた合成画像(tmp/composite_masked.png)は、各ROI枠の
すぐ右側にラベル文字を直接書き込んでおり、プレイヤー名やキャラクターと
重なって読みにくかった。この反省から、ラベル文字は画像内には一切置かず、
枠色とラベル・座標の対応はREADME.md側にまとめている(以前は画像右側に
固定幅の凡例パネルを追加していたが、それだとゲーム画面が全画面表示に
ならずROIの実際のピクセル位置が分かりにくいという指摘を受けて撤廃した)。
"""

from pathlib import Path

import cv2
import numpy as np

from nss_tracker.detection.goal import BANNER_ROI as GOAL_BANNER_ROI
from nss_tracker.detection.goal import NAME_PANEL_ROI
from nss_tracker.detection.match_end import MATCH_END_LEFT_ROI, MATCH_END_RIGHT_ROI, MATCH_END_TEXT_ROI
from nss_tracker.detection.rank_ocr import GAUGE_ROI_COMPACT, GAUGE_ROI_ENLARGED, RANK_ROI
from nss_tracker.detection.vs_rank import MINE_ICON_ROIS, MINE_NUM_ROIS, OPPONENT_ICON_ROIS, OPPONENT_NUM_ROIS

FIXTURES_DIR = Path(__file__).parent.parent / "fixtures" / "screenshots"
OUTPUT_ROOT = Path(__file__).parent.parent / "fixtures" / "ocr_debug"

_LEGEND_FONT = cv2.FONT_HERSHEY_SIMPLEX
_BOX_THICKNESS = 2
_INDEX_FONT_SCALE = 0.4


class _Category:
    """1種類のROI(1個または複数インスタンス)をまとめたもの。"""

    def __init__(self, label: str, kind: str, color: tuple[int, int, int], rois: list[tuple[int, int, int, int]]):
        self.label = label
        self.kind = kind  # "OCR" または "color/brightness"
        self.color = color  # BGR
        self.rois = rois


def _draw_categories(image: np.ndarray, categories: list[_Category]) -> np.ndarray:
    """画像にROI枠(+複数インスタンスなら枠内に小さい連番)を描いた画像を返す。

    ラベル文字・凡例は画像に含めない(README.md側の`_roi_table_markdown`に
    任せる)ため、返す画像は入力fixtureと同じ解像度・全画面表示のまま。
    """
    annotated = image.copy()
    for category in categories:
        multi = len(category.rois) > 1
        for index, (x1, y1, x2, y2) in enumerate(category.rois):
            cv2.rectangle(annotated, (x1, y1), (x2, y2), category.color, _BOX_THICKNESS)
            if multi:
                # 枠の内側左上に小さい連番だけを書く(短い数字なので枠内に収まり、
                # ゲーム画面の文字・キャラクターと重ならない)
                cv2.putText(
                    annotated,
                    str(index),
                    (x1 + 2, y1 + 14),
                    _LEGEND_FONT,
                    _INDEX_FONT_SCALE,
                    category.color,
                    1,
                    cv2.LINE_AA,
                )
    return annotated


def _bgr_to_hex(color: tuple[int, int, int]) -> str:
    b, g, r = color
    return f"#{r:02X}{g:02X}{b:02X}"


def _roi_table_markdown(categories: list[_Category]) -> str:
    """各ROIの枠色・種別・実測ピクセル座標を一覧化したMarkdownテーブルを返す。

    座標は画像に描画しているのと同じ`category.rois`の値をそのまま使うため、
    ここに書かれた数値と画像上の枠は常に一致する。
    """
    lines = [
        "| ROI | 枠色 | 種別 | 座標 (x1, y1)–(x2, y2) | サイズ (w×h px) |",
        "| --- | --- | --- | --- | --- |",
    ]
    for category in categories:
        hex_color = _bgr_to_hex(category.color)
        multi = len(category.rois) > 1
        for index, (x1, y1, x2, y2) in enumerate(category.rois):
            name = f"{category.label} [{index}]" if multi else category.label
            lines.append(
                f"| {name} | `{hex_color}` | {category.kind} | ({x1}, {y1})–({x2}, {y2}) | {x2 - x1}×{y2 - y1} |"
            )
    return "\n".join(lines)


def _load(filename: str) -> np.ndarray:
    path = FIXTURES_DIR / filename
    image = cv2.imread(str(path))
    if image is None:
        raise FileNotFoundError(f"fixture not found: {path}")
    return image


def _write(output_dir: Path, source_filename: str, image: np.ndarray) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    stem = Path(source_filename).stem
    out_path = output_dir / f"{stem}_annotated.png"
    cv2.imwrite(str(out_path), image)
    print(f"  wrote {out_path.relative_to(OUTPUT_ROOT.parent.parent)}")


def generate_rank_ocr() -> None:
    output_dir = OUTPUT_ROOT / "rank_ocr"
    print("rank_ocr:")

    # コンパクト表示(結果バナー確定直後、rank_before用)
    compact_source = "78_result_lose_with_rank_blue_hdr_off.png"
    image = _load(compact_source)
    compact_categories = [
        _Category("rank_badge (RANK_ROI)", "OCR", (255, 200, 0), [RANK_ROI]),
        _Category("gauge_compact (GAUGE_ROI_COMPACT)", "color/brightness", (0, 200, 255), [GAUGE_ROI_COMPACT]),
    ]
    _write(output_dir, compact_source, _draw_categories(image, compact_categories))

    # 拡大表示(ランク変動アニメーション安定後、rank_after用)のHDR無効化後fixtureは
    # まだ無い(Issue #147参照、`30_win_blue_league_up_hdr_off.mp4`内に該当区間は
    # 含まれているが静止画としては未切り出し)。GAUGE_ROI_ENLARGED自体の座標は
    # READMEに載せるが、枠を重ねた画像は生成しない
    enlarged_categories = [
        _Category("rank_badge (RANK_ROI)", "OCR", (255, 200, 0), [RANK_ROI]),
        _Category("gauge_enlarged (GAUGE_ROI_ENLARGED)", "color/brightness", (0, 120, 255), [GAUGE_ROI_ENLARGED]),
    ]

    readme = output_dir / "README.md"
    readme.write_text(
        f"""# rank_ocr.py のROI

`detection/rank_ocr.py` が使う切り抜き領域。ランクバッジは結果バナー確定直後の
**コンパクト表示**とランク変動アニメーション安定後の**拡大表示**でサイズが
明確に異なるため、ゲージ用ROIは表示サイズごとに別々に用意されている
(CLAUDE.md参照)。

## `78_result_lose_with_rank_blue_hdr_off_annotated.png`(コンパクト表示の例)

- **rank_badge (RANK_ROI)** — OCR。バッジ全体(アイコン+数値)を包む余裕のある
  領域。`read_rank()`は数字のみ(allowlist)でOCRして帯内の数値を、
  `read_rank_tier()`は同じ領域をallowlist無しでOCRしてアイコン部分
  (`'∞'`/`'S'`/`'A'`)を判定する(同じ切り抜きを2種類のOCR設定で読んでいる)
- **gauge_compact (GAUGE_ROI_COMPACT)** — 色/明度判定。バッジ下部の横長ゲージの
  塗りつぶし割合を、列ごとの明度(HSVのV)平均が閾値を超えるかで判定する
  (`read_rank_gauge_fill()`)。結果バナー確定直後(rank_before)にのみ使う

{_roi_table_markdown(compact_categories)}

## 拡大表示(ランク変動アニメーション安定後の例)

- **rank_badge (RANK_ROI)** — 上と同じROI・同じ判定(バッジが一回り大きく
  描画されるが、RANK_ROI自体は両サイズをカバーできる余裕を持たせてある)
- **gauge_enlarged (GAUGE_ROI_ENLARGED)** — 色/明度判定。上のGAUGE_ROI_COMPACTと
  同じ考え方だが、拡大表示ではバーの実寸(幅・位置)が異なるため別領域を使う。
  ランク変動アニメーションが安定した後(rank_after)にのみ使う

{_roi_table_markdown(enlarged_categories)}

拡大表示のHDR無効化後fixtureがまだ無いため(Issue #147)、この表示に対応する
`*_annotated.png`は生成していない(座標のみ上表に記載)。

呼び出し元(`state/match_state.py`)では、どちらのROIを使うべきかは読み取り
タイミングによって一意に決まる(結果バナー確定直後=常にコンパクト、
アニメーション安定後=常に拡大)。
""",
        encoding="utf-8",
    )
    print(f"  wrote {readme.relative_to(OUTPUT_ROOT.parent.parent)}")


def generate_vs_rank() -> None:
    output_dir = OUTPUT_ROOT / "vs_rank"
    print("vs_rank:")

    categories = [
        _Category("mine_icon (MINE_ICON_XYWH)", "OCR", (255, 200, 0), list(MINE_ICON_ROIS)),
        _Category("mine_num (MINE_NUM_XYWH)", "OCR", (0, 140, 255), list(MINE_NUM_ROIS)),
        _Category("opponent_icon (OPPONENT_ICON_ROIS)", "OCR", (255, 0, 200), list(OPPONENT_ICON_ROIS)),
        _Category("opponent_num (OPPONENT_NUM_ROIS)", "OCR", (0, 220, 0), list(OPPONENT_NUM_ROIS)),
    ]

    source = "82_matching_with_rank_4v3_hdr_off.png"
    image = _load(source)
    _write(output_dir, source, _draw_categories(image, categories))

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

{_roi_table_markdown(categories)}

`82_matching_with_rank_4v3_hdr_off_annotated.png`(Issue #147で収集)は
4vs3の変則試合で、mine[1]・opponent[0]がS帯バッジ、opponent[3]は相手が
3人しかいないため不在(SlotRank(None, None))という、∞帯以外のケースを
複数まとめて確認できる例(`tests/test_vs_rank.py`のEXPECTED_SCREENSHOTS参照)。
""",
        encoding="utf-8",
    )
    print(f"  wrote {readme.relative_to(OUTPUT_ROOT.parent.parent)}")


def generate_goal() -> None:
    output_dir = OUTPUT_ROOT / "goal"
    print("goal:")

    categories = [
        _Category("goal_banner (BANNER_ROI)", "color", (0, 200, 255), [GOAL_BANNER_ROI]),
        _Category("name_panel (NAME_PANEL_ROI)", "OCR", (255, 200, 0), [NAME_PANEL_ROI]),
    ]
    for source in [
        "74_goal_with_assist_red_hdr_off.png",
        "75_goal_blue_owngoal_hdr_off.png",
    ]:
        image = _load(source)
        _write(output_dir, source, _draw_categories(image, categories))

    readme = output_dir / "README.md"
    readme.write_text(
        f"""# goal.py のROI

`detection/goal.py` が使う切り抜き領域。

- **goal_banner (BANNER_ROI)** — 色判定。「ゴール!」の斜めバナーが表示されて
  いるかどうかを、チームカラーを問わず色ベースで判定する(`is_goal_event()`)
- **name_panel (NAME_PANEL_ROI)** — OCR。得点者名・アシスト名の両方を含む
  1つの大きい領域で、`read_scorer_name()`/`read_assist_name()`はどちらも
  同じこの領域をOCRし、結果の中から「アシスト」というラベル文字列の位置で
  得点者名とアシスト名を区別する(得点者用・アシスト用に別々のROIがあるわけ
  ではない)

{_roi_table_markdown(categories)}

いずれのROIもチームカラーに依存しないため、赤チームでアシスト有りの例
(`74_goal_with_assist_red_hdr_off_annotated.png`)を基本例として置いている。
アシストが無い場合の例として、青チームのオウンゴール
(`75_goal_blue_owngoal_hdr_off_annotated.png`)も置いている(オウンゴールは
性質上アシストが付かないため、この場合name_panel内には得点者名のみが表示され、
「アシスト」ラベル自体が現れず`read_assist_name()`はNoneを返す)。
""",
        encoding="utf-8",
    )
    print(f"  wrote {readme.relative_to(OUTPUT_ROOT.parent.parent)}")


def generate_match_end() -> None:
    output_dir = OUTPUT_ROOT / "match_end"
    print("match_end:")

    source = "80_match_end_hdr_off_2.png"
    image = _load(source)
    categories = [
        _Category("candidate_left (MATCH_END_LEFT_ROI)", "color", (0, 200, 255), [MATCH_END_LEFT_ROI]),
        _Category("candidate_right (MATCH_END_RIGHT_ROI)", "color", (0, 140, 255), [MATCH_END_RIGHT_ROI]),
        _Category("text_confirm (MATCH_END_TEXT_ROI)", "OCR", (255, 200, 0), [MATCH_END_TEXT_ROI]),
    ]
    _write(output_dir, source, _draw_categories(image, categories))

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

{_roi_table_markdown(categories)}

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


def main() -> None:
    generate_rank_ocr()
    generate_vs_rank()
    generate_goal()
    generate_match_end()


if __name__ == "__main__":
    main()
