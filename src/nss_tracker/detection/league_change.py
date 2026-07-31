"""リーグ**昇格**演出画面(全画面の半透明オーバーレイ)の検知。

CLAUDE.md記載のとおり、この演出はランクの帯(数値)が実際に変化した場合のみ
表示される特別イベント。

**この関数が検知するのは昇格側の全画面オーバーレイのみ**。降格時は全画面
オーバーレイが表示されず、ランクバッジの上に小さな「降格」ラベルが乗るだけで
バッジ自体は隠れない(実データで確認済み、fixtures/videos/10_RankDown_red.mp4
参照)。そのため降格の演出中もis_league_change_screen()は常にFalseを返すのが
正しい挙動であり、これはバグではない。降格の検知はこの関数に頼らず、
state.match_state側のバナー消灯時フォールバック確定・rank_recheck機構で
別途対応している(state/match_state.py参照)。

Issue #160/#150対応(2026-07-31、画面全体判定からROIベースへの移行):
従来はフレーム全体の平均HSVで判定していたが(オーバーレイ: H≈100-103,
S≈66-70, V≈183-194)、Issue #150でスタジアムのミント色天蓋が画面の
大部分を占めるだけで誤検知することが判明した(fixtures/videos/
29_lose_blue_hdr_off.mp4のframe 280〜294)。「画面全体」判定という設計自体が
天蓋の映り込み面積に弱いことが根本原因のため、VS画面のレターボックス判定
(Issue #144/#189、detection/matchmaking.pyのis_letterboxed())と同じ考え方の
構造的な特徴に切り替えた。

昇格演出は、画面の上下に**輝度が非常に均一で明るい白い横帯**が出る
(PROMOTION_TOP_BAND_ROI/PROMOTION_BOTTOM_BAND_ROI)。実測(fixtures/screenshots/
79・101、別セッション)では、この帯の輝度標準偏差は6.8で、天蓋誤検知区間
(fixtures/videos/29のframe 275〜298、同じ帯領域を輝度標準偏差29.2〜52.8で
実測)と比べて明確に分離できた。輝度標準偏差(≒帯の内側が単色でどれだけ
均一か)は彩度・色相よりもYUV→BGR変換経路の違いの影響を受けにくいと
考えられる(Issue #144のVS画面レターボックス判定でも同じ理由を採用した)。

ただし白帯だけだと、画面が単に真っ白になっただけの別の状況(理論上)でも
満たしてしまいうるため、白帯の内側にあるラベンダー色のパネル(PROMOTION_CONTENT_ROI)
の色相も補助条件として確認する(実測: H≈115.3〜115.6で2セッションとも
ほぼ一致)。

この再較正後、fixtures/screenshots全45枚・fixtures/videos全24本(既知の
天蓋誤検知2本(25・29番)を含む)を新しいROIでスキャンし、天蓋誤検知は
完全に解消(0件)、既知の昇格演出動画(30・42番)は引き続き正しく検知
できることを確認した。副産物として、21_goal_event_false_positive_win_blue_4-3.mp4
(Issue #67のbanner.py誤検知動画として収集されたもの)にも、これまで
気付かれていなかった本物の昇格演出区間が含まれていたことが判明した
(frame 8532以降)。

Issue #176対応(2026-07-31): 降格を独立した信号で検知できないか調査した結果、
検知可能と判明したため`is_demotion_label_candidate()`/`confirm_demotion_label_text()`を
追加した。降格時にランクバッジの上に乗る小さな「降格」ラベル(白背景の吹き出し
+黒文字+下向きの小さな三角ポインター、fixtures/screenshots/98参照)は、
Issue #73で断念したS/A帯バッジのOCR(信頼度0.00〜0.16、バッジが小さすぎる)とは
異なり、ラベル自体が十分な大きさ・高コントラストのため両方の手法で安定して
検知できることを確認した:

- 形状(色): ラベルは白背景(輝度200以上)の割合が高い矩形として画面上の
  固定位置に現れる。fixtures/screenshots/98・106(降格2件、別セッション)・
  fixtures/videos/40のframe 450で、DEMOTION_LABEL_ROI内の輝度200以上の
  画素数を実測したところ10589〜10890に収まり(3件とも同じ範囲に収束)、
  非該当のfixtures/screenshots全43枚(通常の勝敗結果画面・promotion演出・
  試合終了・マッチング等)はいずれも4456以下、またはpromotion演出等の
  全画面が明るい特殊画面で25701以上と、明確なギャップがあった
  (DEMOTION_LABEL_WHITE_COUNT_RANGE参照)
- OCR: 同じ3件をPaddleOCR(goal.pyの_get_name_readerを再利用)で読み取ったところ、
  いずれも信頼度0.99以上で「降格」と正しく読み取れた(EasyOCRでも信頼度
  0.75〜0.91で読み取れることを確認したが、プロジェクト内の日本語文字OCRは
  PaddleOCRに統一する方針のためこちらを採用)

ラベルの画面上の位置は、バッジ自体がコンパクト表示か拡大表示かに関わらず
一定だった(video 40の同一クリップ内で複数フレームを比較して確認済み)。
そのためgauge_ocr.pyのGAUGE_ROI_COMPACT/ENLARGEDのような表示サイズ別の
ROI分割は不要と判断した。

is_match_end_screen()/confirm_match_end_text()やis_goal_event()/
confirm_goal_text()と同じ2段構成(色/形状の軽量な候補判定→デバウンス確定時に
1回だけ重いOCRで確認)を踏襲している(呼び出し側はstate/match_state.py参照)。
"""

import cv2
import numpy as np

from nss_tracker.detection.goal import _get_name_reader
from nss_tracker.detection_config import get_detection_value

# Issue #160/#150: 昇格演出の上下に出る白帯(主判定)。解像度1920x1080の
# フレームを前提とする(config/detection.tomlの[league_change]で上書き可能。
# 以下同様。モジュールdocstring参照)
PROMOTION_TOP_BAND_ROI = get_detection_value("league_change", "PROMOTION_TOP_BAND_ROI", (0, 122, 1920, 158))
PROMOTION_BOTTOM_BAND_ROI = get_detection_value("league_change", "PROMOTION_BOTTOM_BAND_ROI", (0, 924, 1920, 957))
# 帯が「明るく均一な単色」であることの判定閾値。実測(モジュールdocstring参照):
# 昇格演出時は輝度平均244.0〜245.1・標準偏差6.8、天蓋誤検知時は輝度平均
# 112.7〜221.8・標準偏差29.2〜52.8。標準偏差の方が分離が明確なためこちらを
# 主に使い、輝度平均は暗い一様な背景(誤検知の可能性は低いが念のため)を
# 除外する目的で緩めに設定している
PROMOTION_BAND_MIN_BRIGHTNESS = get_detection_value("league_change", "PROMOTION_BAND_MIN_BRIGHTNESS", 200)
PROMOTION_BAND_MAX_BRIGHTNESS_STD = get_detection_value("league_change", "PROMOTION_BAND_MAX_BRIGHTNESS_STD", 15.0)

# 白帯の内側にあるラベンダー色のパネル部分(補助判定)。画面が単に真っ白に
# なっただけの状況(理論上)と区別するために色相も確認する
PROMOTION_CONTENT_ROI = get_detection_value("league_change", "PROMOTION_CONTENT_ROI", (0, 183, 1920, 893))
# 実測(モジュールdocstring参照): H≈115.3〜115.6・S≈60(2セッションともほぼ一致)
PROMOTION_CONTENT_HUE_RANGE = get_detection_value("league_change", "PROMOTION_CONTENT_HUE_RANGE", (100, 130))
PROMOTION_CONTENT_SAT_MIN = get_detection_value("league_change", "PROMOTION_CONTENT_SAT_MIN", 30)

# Issue #176: 降格ラベル(「降格」の吹き出し)の画面上の固定位置。解像度
# 1920x1080のフレームを前提とする(モジュールdocstring参照)
DEMOTION_LABEL_ROI = get_detection_value("league_change", "DEMOTION_LABEL_ROI", (190, 530, 420, 680))
# 上記ROI内で輝度(グレースケール)がこの値を超える画素を「白」とみなす
DEMOTION_LABEL_BRIGHTNESS_THRESHOLD = get_detection_value("league_change", "DEMOTION_LABEL_BRIGHTNESS_THRESHOLD", 200)
# 実測(モジュールdocstring参照): 降格ラベル表示中は10589〜10890、非該当は
# 4456以下または25701以上。上下に余裕を持たせた範囲にしている
DEMOTION_LABEL_WHITE_COUNT_RANGE = get_detection_value("league_change", "DEMOTION_LABEL_WHITE_COUNT_RANGE", (8000, 20000))


def _is_uniform_bright_band(frame: np.ndarray, roi: tuple[int, int, int, int]) -> bool:
    x1, y1, x2, y2 = roi
    gray = cv2.cvtColor(frame[y1:y2, x1:x2], cv2.COLOR_BGR2GRAY)
    return bool(gray.mean() >= PROMOTION_BAND_MIN_BRIGHTNESS and gray.std() <= PROMOTION_BAND_MAX_BRIGHTNESS_STD)


def is_league_change_screen(frame: np.ndarray) -> bool:
    """リーグ**昇格**の演出オーバーレイが表示されているかを判定する。

    降格時はこの全画面オーバーレイ自体が発生しないため、常にFalseを返す
    (モジュールdocstring参照)。上下の白帯(構造的な特徴)を主判定とし、
    帯の内側のラベンダー色パネルの色相を補助判定として組み合わせる
    (Issue #160/#150、モジュールdocstring参照)。
    """
    if not (
        _is_uniform_bright_band(frame, PROMOTION_TOP_BAND_ROI)
        and _is_uniform_bright_band(frame, PROMOTION_BOTTOM_BAND_ROI)
    ):
        return False
    x1, y1, x2, y2 = PROMOTION_CONTENT_ROI
    hsv = cv2.cvtColor(frame[y1:y2, x1:x2], cv2.COLOR_BGR2HSV).reshape(-1, 3)
    h, s, _v = hsv.mean(axis=0)
    return bool(s >= PROMOTION_CONTENT_SAT_MIN and PROMOTION_CONTENT_HUE_RANGE[0] <= h <= PROMOTION_CONTENT_HUE_RANGE[1])


def is_demotion_label_candidate(frame: np.ndarray, roi: tuple[int, int, int, int] = DEMOTION_LABEL_ROI) -> bool:
    """降格ラベル(「降格」の吹き出し)の候補を軽量な輝度判定で検知する(Issue #176)。

    単体では確定情報として扱わず、呼び出し側が一定時間連続したタイミングで
    confirm_demotion_label_text()を1回だけ呼んでOCRにより確認すること
    (モジュールdocstring参照)。
    """
    x1, y1, x2, y2 = roi
    gray = cv2.cvtColor(frame[y1:y2, x1:x2], cv2.COLOR_BGR2GRAY)
    white_count = int((gray > DEMOTION_LABEL_BRIGHTNESS_THRESHOLD).sum())
    return DEMOTION_LABEL_WHITE_COUNT_RANGE[0] <= white_count <= DEMOTION_LABEL_WHITE_COUNT_RANGE[1]


def confirm_demotion_label_text(frame: np.ndarray, roi: tuple[int, int, int, int] = DEMOTION_LABEL_ROI) -> bool:
    """ラベルの文字をOCR(PaddleOCR)で読み取り、「降格」かどうかを判定する(重い処理)。

    is_demotion_label_candidate()で候補と判定されたフレームに対して、呼び出し側が
    デバウンス確定時に1回だけ呼ぶことを想定している(モジュールdocstring参照)。
    """
    x1, y1, x2, y2 = roi
    crop = frame[y1:y2, x1:x2]
    results = _get_name_reader().predict(crop)
    texts: list[str] = []
    for result in results:
        texts.extend(result.get("rec_texts", []))
    return any("降格" in text for text in texts)
