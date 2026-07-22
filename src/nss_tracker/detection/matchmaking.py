"""マッチング完了(VS画面)の色判定。

CLAUDE.md記載の方針どおり、OCRではなく画面中央に一瞬表示される「VS」ロゴの
色で判定する(banner.py・league_change.pyと同様の軽量な色ベース判定)。

ROIはロゴの文字部分だけを狙った小さな矩形にしている(周囲の芝生・空を含む
広いROIだと、対戦相手の背景色の違い(晴天/曇天等)でHSVの平均が引きずられ
判定がぶれるため)。

Issue #68対応(2026-07-21、HDR無効化後の実プレイ録画): 当初の閾値
(H62-77/S60-70/V180+、fixtures/screenshots内のVS画面6枚から決定)は実プレイで
繰り返し検知に失敗し(2026-07-19・20・21の実測でそれぞれ0/5・0/4・0/14)、
YouTubeアーカイブ経由の分析では「ロゴの色がアニメーションでパルスしている」
ことが原因と推測されていた。しかし2026-07-21にOBSのローカル録画(再エンコード
無し)を直接解析したところ、本物のVS画面(72_matching_hdr_off_1.png/
73_matching_hdr_off_2.png、fixtures/videos/22・23_vs_screen_hdr_off_*.mp4で
確認)は2回ともH81.0-85.5/S92.6-93.2/V235.8-237.8に収まり、8〜14秒間
ほぼ変動せず安定していた。パルスではなく、キャプチャパイプラインの発色特性が
旧fixture収集時と全く異なる(重複ゼロ)ことが実際の原因だった。旧6枚の
fixtureは現在の環境では再現しない色のため、72/73番に置き換えた
(11/12/14/15番は「マッチング ランク有無・チーム別」という元々の収集目的を
保つため画像自体は残すが、is_vs_screenの真陽性テストからは外している。
70/71番はS/A帯バッジOCR確認という別目的のfixtureで、たまたま旧VS画面色を
捉えていただけのため同様に外した)。

**上記の値だけで一度main.pyの実パイプラインで再現テストしたところ、
それでもVS画面を検知できなかった。** 原因を追ったところ、fixtureは
すべてcv2.VideoCapture/cv2.imreadで読み込んでいるのに対し、実際の検知
ループ(capture/ffmpeg_capture.pyのFfmpegFrameReader、`--video`指定時も
実キャプチャ時も同じ)はffmpegサブプロセスを`-pix_fmt bgr24`で介して
フレームを読んでおり、両者のYUV→BGR変換が微妙に異なる(同一動画・同一
フレーム位置でもS値が5前後ずれる)ことが判明した。そのためFfmpegFrameReader
経由で同じ2回のVS画面を再計測したところ、H82.5-86.6/S98.3-98.8/V227.5-229.3
という、cv2実測値とは重ならない別のタイトな範囲になった。cv2経由(fixture
テスト用)とFfmpegFrameReader経由(実パイプライン用)の両方を満たすよう、
現在の閾値は両者の実測範囲を包含する形にしている。今後この手のROI色閾値を
実測し直す際は、cv2ではなく必ずFfmpegFrameReader経由で読んだフレームで
検証すること(cv2の実測値だけでは実際のパイプラインを代表しない)。

ROIの妥当性(他の画面状態との重複が無いこと)自体は旧fixture収集時に
fixtures/screenshots全44枚・fixtures/videos全動画で確認済みで、今回の閾値
変更後もfixtures/videos/24_no_vs_screen_hdr_off_gameplay.mp4(HDR無効化後の
通常プレイ中の録画)で誤検知が無いことを確認済み。

なお試合中の稀なフレームで、プレイヤー頭上のミント色アイコン(スキル発動
などの演出)がROIにちょうど重なり単発フレームだけ誤検知することを確認した
(fixtures/videos/12_win_red_vs_screen_to_result.mp4のframe 4397)。この
誤検知は1フレーム(30fps換算で約33ms)しか継続せず、本物のVS画面は
150フレーム(5秒)以上安定して表示され続けるため、banner.pyのバナー判定
同様、呼び出し側でmotion.find_confirmed_valueによるデバウンス
(数百ms〜1秒程度の連続を要求)と組み合わせて使うことを前提とする。
"""

import cv2
import numpy as np

from nss_tracker.detection_config import get_detection_value

# 画面中央に表示される「VS」ロゴの文字部分のみを狙った矩形 (x1, y1, x2, y2)。
# 解像度1920x1080のフレームを前提とする
# (config/detection.tomlの[matchmaking]で上書き可能。以下同様)
VS_ROI = get_detection_value("matchmaking", "VS_ROI", (880, 495, 1050, 600))

# 実測(Issue #68、2026-07-21のHDR無効化後ローカル録画・VS画面2回分)。
# cv2経由: H81.0-85.5/S92.6-93.2/V235.8-237.8、FfmpegFrameReader経由:
# H82.5-86.6/S98.3-98.8/V227.5-229.3(両者の差はモジュールdocstring参照)。
# 実際に使われるのはFfmpegFrameReader経由の値だが、fixtureテストはcv2経由の
# ため、両方を包含するようマージンを取っている(旧値62-77/60-70/180は
# モジュールdocstring参照。現在の環境では再現しないため置き換えた)
VS_HUE_RANGE = get_detection_value("matchmaking", "VS_HUE_RANGE", (78, 90))
VS_SAT_RANGE = get_detection_value("matchmaking", "VS_SAT_RANGE", (90, 101))
VS_VAL_MIN = get_detection_value("matchmaking", "VS_VAL_MIN", 220)


def read_vs_roi_hsv(frame: np.ndarray, roi: tuple[int, int, int, int] = VS_ROI) -> tuple[float, float, float]:
    """VS_ROI内の平均HSV値をそのまま返す(診断用)。

    Issue #68: 実プレイでVS画面検知が繰り返し失敗しており(2026-07-19・
    2026-07-20の実測とも0/5・0/4)、原因が未特定のまま。既存fixtureでの
    検証では色閾値自体に問題は見つからなかったため、実際のキャプチャ
    パイプラインで何が起きているかを次回セッションでDEBUGログから
    直接確認できるよう、is_vs_screen()の判定に使うHSV平均値を単体で
    呼び出せるようにしている(state.match_stateの_check_for_vs_screen参照)。
    """
    x1, y1, x2, y2 = roi
    crop = frame[y1:y2, x1:x2]
    hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV).reshape(-1, 3)
    h, s, v = hsv.mean(axis=0)
    return float(h), float(s), float(v)


def is_vs_screen(frame: np.ndarray, roi: tuple[int, int, int, int] = VS_ROI) -> bool:
    """マッチング完了(VS画面)の「VS」ロゴが表示されているかを判定する。

    単発フレームでは試合中の演出アイコン等で稀に誤検知しうる(モジュール
    docstring参照)。呼び出し側でmotion.find_confirmed_value等によるデバウンスと
    組み合わせて使うことを前提とする。
    """
    h, s, v = read_vs_roi_hsv(frame, roi)
    return VS_HUE_RANGE[0] <= h <= VS_HUE_RANGE[1] and VS_SAT_RANGE[0] <= s <= VS_SAT_RANGE[1] and v >= VS_VAL_MIN
