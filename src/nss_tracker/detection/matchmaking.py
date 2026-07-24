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
無し)を直接解析したところ、本物のVS画面は2回ともH81.0-85.5/S92.6-93.2/
V235.8-237.8に収まり、8〜14秒間ほぼ変動せず安定していた。パルスではなく、
キャプチャパイプラインの発色特性が旧fixture収集時と全く異なる(重複ゼロ)
ことが実際の原因だった。

**上記の値だけで一度main.pyの実パイプラインで再現テストしたところ、
それでもVS画面を検知できなかった。** 原因を追ったところ、fixtureは
すべてcv2.VideoCapture/cv2.imreadで読み込んでいるのに対し、実際の検知
ループ(capture/ffmpeg_capture.pyのFfmpegFrameReader、`--video`指定時も
実キャプチャ時も同じ)はffmpegサブプロセスを`-pix_fmt bgr24`で介して
フレームを読んでおり、両者のYUV→BGR変換が異なることが判明した。cv2経由
(fixtureテスト用)とFfmpegFrameReader経由(実パイプライン用)の両方を
満たすよう、当時の閾値は両者の実測範囲を包含する形にしていた。

Issue #116対応(2026-07-24、実機end-to-endテスト): Issue #68の修正後も
実機(#83のOBSシーン自動切替テスト)でVS画面検知が2セッション連続で失敗した。
`NSS_TRACKER_LOG_LEVEL=DEBUG`で`read_vs_roi_hsv`の診断ログ(次項参照)を
確認したところ、実際のライブパイプライン(FfmpegFrameReader経由)でのVS画面の
色は**H≈99.47-99.83/S≈91.97-94.72/V≈236.14-238.28**(2026-07-24 23:26:02〜
23:26:15、750サンプル・12秒以上安定)で、Issue #68時点の閾値(H78-90/S90-101/
V220+)からHueが10ポイント以上外れていた。単純なRGBチャンネルの入れ替え
(6通りの並べ替えを総当たりで確認)では説明がつかず、YUV→BGR変換時の色空間
解釈(BT.601/BT.709、フルレンジ/リミテッドレンジ等)がIssue #68修正時から
変化した可能性がある。原因の特定は行わず、ユーザーと合意の上で**過去の
閾値決定の経緯(cv2実測値・fixtureベースの調整を含む)は一切考慮せず、
今回実測したライブパイプラインの値のみに基づいて閾値を決め直した**
(現在の`VS_HUE_RANGE`/`VS_SAT_RANGE`/`VS_VAL_MIN`参照)。

この決定に伴い、**fixtures/screenshots・fixtures/videosを使った画像/動画
ベースのfixtureテストは、is_vs_screenの真陽性判定にはもう使えない**
(tests/test_matchmaking.py参照)。cv2.imread/cv2.VideoCaptureはいずれも
上記のFfmpegFrameReaderとは異なる色変換経路のため、どんな実機録画から
切り出したfixtureを用意しても、cv2で読む限りライブパイプラインの色
(H≈99.6付近)を再現できない(これは特定のfixtureの品質の問題ではなく、
読み込み経路そのものに起因する)。そのため真陽性の検証は、実測したHSV値を
直接使う合成フレームによるテストに置き換えている。

ROIの妥当性(他の画面状態との重複が無いこと)自体は旧fixture収集時に
fixtures/screenshots全44枚・fixtures/videos全動画で確認済み。

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

# 実測(Issue #116、2026-07-24の実機ライブパイプライン・FfmpegFrameReader経由)。
# 2026-07-24 23:26:02〜23:26:15の安定区間(750サンプル)でH99.47-99.83/
# S91.97-94.72/V236.14-238.28(モジュールdocstring参照)。過去の閾値決定
# (Issue #68・cv2実測値ベース)は一切考慮せず、この実測値のみにマージンを
# 加えて決め直した(単一セッションの実測のため、実測範囲より広めにマージンを
# 取っている)
VS_HUE_RANGE = get_detection_value("matchmaking", "VS_HUE_RANGE", (95, 104))
VS_SAT_RANGE = get_detection_value("matchmaking", "VS_SAT_RANGE", (88, 98))
VS_VAL_MIN = get_detection_value("matchmaking", "VS_VAL_MIN", 230)


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
