import cv2
import pytest

from conftest import list_screenshot_fixtures, requires_fixtures, requires_video_fixtures
from nss_tracker.detection.league_change import is_league_change_screen

TARGET_SIZE = (1920, 1080)


def _read_frames(path):
    cap = cv2.VideoCapture(str(path))
    try:
        while True:
            ok, frame = cap.read()
            if not ok:
                break
            yield cv2.resize(frame, TARGET_SIZE)
    finally:
        cap.release()


# 79_result_rank_up_hdr_off.pngはリーグ昇格の全画面オーバーレイそのもの
# (意図的にTrueになるべき唯一のfixture)なので、以下の「非該当画面は全てFalseの
# はず」テストの対象から除く
LEAGUE_CHANGE_OVERLAY_SCREENSHOT = "79_result_rank_up_hdr_off.png"


@requires_fixtures
def test_is_league_change_screen_false_for_non_overlay_screenshots(fixtures_dir):
    """ロビー・マッチング・試合中・結果バナー等、演出画面ではない静止画では
    is_league_change_screenが常にFalseであることを確認する
    (fixtures/screenshots/*.pngのうち79_result_rank_up_hdr_off.png以外は演出画面を
    含まないため全件が非該当のはず)。
    """
    screenshots = list_screenshot_fixtures(fixtures_dir)
    assert screenshots, "fixtures/screenshots/にpngが見つからない"
    for path in screenshots:
        if path.name == LEAGUE_CHANGE_OVERLAY_SCREENSHOT:
            continue
        frame = cv2.imread(str(path))
        assert frame is not None, f"failed to load {path.name}"
        assert not is_league_change_screen(frame), f"{path.name}で誤検知した"


@requires_fixtures
def test_is_league_change_screen_true_for_promotion_overlay_screenshot(fixtures_dir):
    path = fixtures_dir / LEAGUE_CHANGE_OVERLAY_SCREENSHOT
    frame = cv2.imread(str(path))
    assert frame is not None, f"failed to load {path.name}"
    assert is_league_change_screen(frame), f"{path.name}で検知できなかった"


# 実際の映像を目視確認して決めたフレーム区間(is_league_change_screen自体の
# 判定結果をそのまま転記したものではない、60fps)。
# - OVERLAY_RANGE: 「リーグ昇格!」の全画面オーバーレイが表示されている区間
# - BEFORE_RANGE / AFTER_RANGE: オーバーレイの前後、通常の結果バナー・ランクバッジ表示中
LEAGUE_UP_VIDEO = "30_win_blue_league_up_hdr_off.mp4"
BEFORE_RANGE = range(1830, 1950)
OVERLAY_RANGE = range(2070, 2280)
AFTER_RANGE = range(2370, 2460)


@requires_video_fixtures
def test_is_league_change_screen_detects_promotion_overlay(videos_dir):
    video_path = videos_dir / LEAGUE_UP_VIDEO
    if not video_path.is_file():
        pytest.skip(f"{LEAGUE_UP_VIDEO} が見つからない")

    detected_overlay = False
    false_positive_frame = None
    for idx, frame in enumerate(_read_frames(video_path)):
        result = is_league_change_screen(frame)
        if idx in OVERLAY_RANGE:
            detected_overlay = detected_overlay or result
        elif idx in BEFORE_RANGE or idx in AFTER_RANGE:
            if result and false_positive_frame is None:
                false_positive_frame = idx

    assert detected_overlay, "昇格演出の区間で一度もTrueにならなかった"
    assert false_positive_frame is None, f"演出区間外(フレーム{false_positive_frame})で誤検知した"


# 降格時は昇格と異なり全画面演出が一切出ず、ランクバッジ上に小さい「降格」ラベルが
# 乗るだけ(バッジ自体は隠れない)。目視確認済み(state/match_state.pyのモジュール
# docstring・detection/league_change.pyのモジュールdocstring参照)。
# state/match_state.pyのrank_recheck機構・バナー消灯時フォールバック確定は
# このケースに対応するために追加された
# (test_match_state.pyのtest_track_rank_grace_recheck_catches_tier_change参照)。
# is_league_change_screenが全画面演出だけを見て判定する設計上、これらの動画では
# 全編を通じて一度もTrueにならないのが正しい挙動。
#
# 以前は実際に降格したことを確認済みの動画(03/10番)を使っていたが、Issue #148で
# HDR無効化前fixtureとして削除された。代替のHDR無効化後fixtureはいずれも降格が
# 実際に発生したことまでは確認していない(negative-caseの一般的な誤検知防止の
# 回帰としてのみ使う)
#
# 29番はフレーム270〜300付近(約0.5秒)でis_league_change_screenが誤ってTrueになる
# ことが判明した。原因はCLAUDE.mdに記載済みの「スタジアムのミント/ティール色の
# 天蓋(屋根の日除け)」がカメラアングルの都合で画面に大きく写り込むケース
# (Issue #67の2件目の参照サンプル`25_inplay_false_positive_win_blue_teal_canopy.mp4`
# と同じ現象)で、これまでbanner.py側でのみ報告されていたが、is_league_change_screen
# (画面全体の平均HSVで判定)も同じ天蓋色に反応しうることが今回新たに判明した。
# Issue #185で調査したが、本物の昇格演出(79番・30番・21番のH101.21〜104.13)と
# 天蓋誤検知(H最大103.51)のHue範囲が重なっており、単純な閾値再較正では
# 分離できないと判明したため、Issue #172・#182と同じ結論でxfailのまま残す
LEAGUE_DOWN_WITHOUT_OVERLAY_VIDEOS = [
    "29_lose_blue_hdr_off.mp4",
    "31_lose_blue_without_rank_hdr_off.mp4",
]
_KNOWN_CANOPY_FALSE_POSITIVE_VIDEOS = {"29_lose_blue_hdr_off.mp4"}


@requires_video_fixtures
@pytest.mark.parametrize(
    "video_name",
    [
        pytest.param(
            name,
            marks=pytest.mark.xfail(
                reason="スタジアムのミント色天蓋の写り込みでis_league_change_screenが誤検知する(Issue #185で調査、Hue範囲重複のため単純な閾値再較正では未解決)",
                strict=False,
            ),
        )
        if name in _KNOWN_CANOPY_FALSE_POSITIVE_VIDEOS
        else name
        for name in LEAGUE_DOWN_WITHOUT_OVERLAY_VIDEOS
    ],
)
def test_is_league_change_screen_false_throughout_when_no_dedicated_overlay(videos_dir, video_name):
    video_path = videos_dir / video_name
    if not video_path.is_file():
        pytest.skip(f"{video_name} が見つからない")

    for idx, frame in enumerate(_read_frames(video_path)):
        assert not is_league_change_screen(frame), (
            f"{video_name}のフレーム{idx}で誤検知した"
            "(この動画は降格が小さいラベル表示のみで全画面演出が出ないケース)"
        )
