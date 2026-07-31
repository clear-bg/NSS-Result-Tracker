import cv2
import pytest

from conftest import requires_fixtures
from nss_tracker.detection.banner import classify_banner

EXPECTED = {
    "72_matching_hdr_off_1.png": None,
    "73_matching_hdr_off_2.png": None,
    "74_goal_with_assist_red_hdr_off.png": None,
    "75_goal_blue_owngoal_hdr_off.png": None,
    "76_match_end_hdr_off.png": None,
    "77_result_win_with_rank_red_hdr_off.png": None,
    "78_result_lose_with_rank_blue_hdr_off.png": "lose",
    "79_result_rank_up_hdr_off.png": None,
    "80_match_end_hdr_off_2.png": None,
    "81_result_lose_without_rank_blue_hdr_off.png": "lose",
    "82_matching_with_rank_4v3_hdr_off.png": None,
    "83_goal_without_assist_blue_hdr_off.png": None,
    "84_result_win_with_rank_blue_hdr_off.png": "win",
    "85_result_win_with_rank_enlarged_blue_hdr_off.png": None,
    "86_matching_with_rank_4v4_hdr_off.png": None,
    "87_matching_hdr_off_3.png": None,
    "88_result_win_without_rank_blue_hdr_off.png": "win",
    "89_result_draw_without_rank_hdr_off.png": "draw",
    "90_start_overtime_hdr_off_1.png": None,
    "91_start_overtime_hdr_off_2.png": None,
    "92_start_overtime_hdr_off_3.png": None,
    "93_result_win_with_rank_red_hdr_off_2.png": "win",
    "94_result_win_with_rank_red_hdr_off_3.png": "win",
    "95_result_win_with_rank_red_hdr_off_4.png": "win",
    "96_result_win_without_rank_red_hdr_off.png": "win",
    "97_result_lose_with_rank_red_hdr_off.png": "lose",
    "98_result_lose_with_rank_demotion_red_hdr_off.png": "lose",
    "99_result_win_with_rank_blue_hdr_off_2.png": "win",
    "100_result_win_with_rank_blue_hdr_off_3.png": "win",
    "101_result_rank_up_hdr_off_2.png": None,
    "102_goal_with_assist_blue_hdr_off.png": None,
    "103_match_end_knockout_hdr_off.png": None,
    "104_goal_with_assist_red_hdr_off_2.png": None,
    "106_result_lose_with_rank_demotion_red_hdr_off_2.png": "lose",
}


# Issue #172/#211: 77・85番は当初「WIN_HUE_RANGE/WIN_VAL_MINがカバーできていない
# 既知の欠落」としてground truthを"win"のままxfail扱いにしていたが、後日の調査で
# 前提が誤っていたと判明した。両者とも実は**拡大表示**(ランク変動アニメーション
# 開始後〜暗転までの間に表示されるサイズ)のタイミングのフレームであり(85はファイル
# 名の通り、77はIssue #143対応時に「長らくコンパクト表示として誤分類されていた」と
# 判明済み。CLAUDE.mdのランクOCR節参照)、正しく"win"判定できて
# いる84・88・93〜96・99・100番(いずれもコンパクト表示=結果バナー確定直後)とは
# 撮影タイミングが異なる。
#
# ランクゲージが動き始めた後(拡大表示以降)は画面全体がやや暗くなる演出が入り、
# これがバナー帯のV(146.1〜148.5)を押し下げている。79・101番(昇格オーバーレイ、
# V≈150)がこれと近い値になるのも、同じ「拡大表示以降の暗くなった局面」で右上に
# バナーの残光が透けて見えているためで偶然ではない。
#
# state/match_state.pyを確認したところ、拡大表示以降のフェーズ(_track_rankの
# GRACE中)でclassify_banner()を呼んでいる箇所は「バナーが消えたか(is None)」の
# 確認のみで、"win"かどうかを再確認する箇所は無い。実際のwin/lose確定
# (_watch_for_banner)はコンパクト表示のうち(V≈218前後)に完了しているため、
# 77・85が"win"を返さないことは運用上のバグではなかった。WIN_HUE_RANGE/
# WIN_VAL_MINの閾値変更も不要(現状のVの下限が拡大表示の残光・昇格オーバーレイ
# 双方を正しく除外できている)。ground truthをNoneに訂正し、xfailは解消する

# Issue #182(解消済み): 89番(引き分け)はH98.6〜102.2・V82.3〜89.3はLOSE_HUE_RANGE/
# LOSE_VAL_RANGEの範囲内だが、S28.1〜42.1がLOSE_SAT_RANGE=(35, 65)の下限をわずかに
# 下回りclassify_banner()がNoneを返していた。実際の"lose"バナー(S42.7〜45.3、
# 複数fixture間で揃っている)との差が9〜12ポイントと大きく一貫しているため、単なる
# 照明条件のノイズではなく「引き分けは負けより彩度が低い」という実際の色の違いと
# 判断した。LOSE_SAT_RANGE自体を緩めると誤検知対策動画(fixtures/videos/
# 21_goal_event_false_positive_win_blue_4-3.mp4)と衝突するため、代わりに"draw"の
# 確定条件を独立した強い判定材料である_is_draw_text()の確認のみに切り離し
# (LOSE_SAT_RANGEを通らなくても良い)、classify_banner()を組み替えて解消した
# (詳細はdetection/banner.pyのモジュールdocstring参照)。89番も通常どおりEXPECTEDで
# "draw"を期待する。

# Issue #193(解消済み): 98番(降格ラベル付きの負けバナー)はV53.7〜59.2が
# LOSE_VAL_RANGEの下限をわずかに下回りclassify_banner()がNoneを返していたが、
# 2件目の参照素材(106、別セッション)でも同水準のVが確認できたため、
# LOSE_VAL_RANGEの下限を65→50に再較正して解消した(詳細はdetection/banner.pyの
# モジュールdocstring参照)。98・106とも通常どおりEXPECTEDで"lose"を期待する。


@requires_fixtures
@pytest.mark.parametrize("filename, expected", sorted(EXPECTED.items()))
def test_classify_banner(fixtures_dir, filename, expected):
    frame = cv2.imread(str(fixtures_dir / filename))
    assert frame is not None, f"failed to load {filename}"
    assert classify_banner(frame) == expected
