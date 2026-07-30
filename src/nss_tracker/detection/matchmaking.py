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

【2026-07-31追記・下記Issue #144/#189対応で状況が変わった】上記は色判定
単体が主判定だった当時の話。レターボックス判定(is_letterboxed、下記)を
主判定にした後は、fixtures/screenshots内の実際のVS画面スクリーンショット
(`72_matching_hdr_off_1.png`等、"matching"を含む5枚)がcv2.imread経由でも
`is_vs_screen()`でTrueと判定されることを確認した。輝度(黒帯かどうか)は
色相ほど読み込み経路の違いに敏感ではなく、かつ色判定側もこの対応で
大まかな除外フィルタへ緩めたため、両方が揃って初めてcv2経由でも真陽性の
検証ができるようになった。この5枚は`tests/test_matchmaking.py`で真陽性の
regressionテストとして使っている(cv2.imread経由でのVS画面検知が壊れて
いないことを継続的に検証できる、という副次的な効果)。

ROIの妥当性(他の画面状態との重複が無いこと)自体は旧fixture収集時に
fixtures/screenshots全44枚・fixtures/videos全動画で確認済み。

なお試合中の稀なフレームで、プレイヤー頭上のミント色アイコン(スキル発動
などの演出)がROIにちょうど重なり単発フレームだけ誤検知することを確認した
(fixtures/videos/12_win_red_vs_screen_to_result.mp4のframe 4397)。この
誤検知は1フレーム(30fps換算で約33ms)しか継続せず、本物のVS画面は
150フレーム(5秒)以上安定して表示され続けるため、banner.pyのバナー判定
同様、呼び出し側でmotion.find_confirmed_value等によるデバウンス
(数百ms〜1秒程度の連続を要求)と組み合わせて使うことを前提とする。

Issue #144/#189対応(2026-07-31、実配信でVS画面が最後まで一度も確定しなかった
セッションの調査): 録画(`tmp/2026-07-31 00-03-29.mkv`)とログを突き合わせたところ、
実際のVS画面表示中(ユーザーが目視で特定した2試合分、動画内0:01:38-0:01:44・
0:06:24-0:06:38)のHSVは**H≈82-84/S≈89-90/V≈222-224**で、Issue #116時点の
閾値(H95-104/S88-98/V230+)からHueが約11〜22ポイント・Valueが約6〜8ポイント
外れていた(Saturationのみ閾値内)。Issue #68・#116に続き3度目の発色ドリフト。

この調査の過程で、VS画面には他の画面には無い構造的な特徴があることが分かった:
**画面上部(row 0-27)と下部(row 985-1079)が完全に黒い帯になり、中央部分は
通常どおり背景・キャラクターが表示され続ける**(レターボックス状のクロップ)。
この録画全編(約11分・42352フレーム、実プレイ中・試合間の暗転を含む)を
「上帯・下帯がLETTERBOX_MAX_BRIGHTNESS未満、かつ中央帯が
LETTERBOX_MIDDLE_MIN_BRIGHTNESS以上」という条件でスキャンしたところ、
該当したのは目視で確認した2試合分のVS画面区間のみで、誤検知は0件だった
(試合間の暗転は中央帯も含め画面全体が暗くなるため、中央帯の明るさ条件で
自然に区別できる)。輝度(黒かどうか)はYUV→BGR変換の経路が変わっても
Hueほどブレないと考えられるため、今回の発色ドリフトに対してロゴの色判定
単体より頑健な信号として、`is_letterboxed()`を`is_vs_screen()`の主判定に
採用した(ユーザーとの相談で決定)。

ただし今回の検証は1セッション(2試合)分のみのデータであり、このゲームには
今回観測していない画面(リプレイ演出等)で偶然同じレターボックス構図を
持つものが無いとは言い切れない。そのため、ロゴの色判定を完全に廃止はせず、
`is_vs_screen()`はレターボックス判定とのAND条件として残す。ただし主判定は
レターボックス側に移ったため、色判定はもう精密な閾値である必要が無く、
「明らかに違う色ではないことの大まかな確認」程度の緩いフィルタへ格下げした
(`VS_HUE_RANGE`/`VS_SAT_RANGE`/`VS_VAL_MIN`を今回の実測値中心に大きく
広いマージンを取って再較正)。
"""

import cv2
import numpy as np

from nss_tracker.detection_config import get_detection_value

# 画面中央に表示される「VS」ロゴの文字部分のみを狙った矩形 (x1, y1, x2, y2)。
# 解像度1920x1080のフレームを前提とする
# (config/detection.tomlの[matchmaking]で上書き可能。以下同様)
VS_ROI = get_detection_value("matchmaking", "VS_ROI", (880, 495, 1050, 600))

# Issue #144/#189: 今回実測(2026-07-31、実機ライブパイプライン・
# FfmpegFrameReader経由、2試合分)したVS画面のHSVはH≈82-84/S≈89-90/V≈222-224
# (モジュールdocstring参照)。主判定がis_letterboxed()に移ったため、ここでは
# 大まかな除外フィルタとして機能すればよく、実測値に大きく広いマージンを
# 加えている(単一セッションの実測のため、今後さらにズレても壊れにくいよう
# 意図的に広め)
VS_HUE_RANGE = get_detection_value("matchmaking", "VS_HUE_RANGE", (65, 100))
VS_SAT_RANGE = get_detection_value("matchmaking", "VS_SAT_RANGE", (70, 105))
VS_VAL_MIN = get_detection_value("matchmaking", "VS_VAL_MIN", 195)

# Issue #144/#189: VS画面だけが持つレターボックス状の黒帯(モジュールdocstring
# 参照)。上帯・下帯とも実測で境界からマージンを取った範囲(全幅)。
# 上帯: 実測で黒→通常表示の境界はrow27→28(2試合とも同じ)。下帯: 境界は
# row983→985だが、境界ぎりぎりを避けるため1000-1070とかなり内側に取っている
LETTERBOX_TOP_ROI = get_detection_value("matchmaking", "LETTERBOX_TOP_ROI", (0, 2, 1920, 10))
LETTERBOX_BOTTOM_ROI = get_detection_value("matchmaking", "LETTERBOX_BOTTOM_ROI", (0, 1020, 1920, 1070))
# 中央帯: 暗転(試合間の画面全体が暗くなる区間)との区別用。ここが暗いままなら
# レターボックスではなく単なる暗転とみなす
LETTERBOX_MIDDLE_ROI = get_detection_value("matchmaking", "LETTERBOX_MIDDLE_ROI", (0, 200, 1920, 800))
LETTERBOX_MAX_BRIGHTNESS = get_detection_value("matchmaking", "LETTERBOX_MAX_BRIGHTNESS", 10)
LETTERBOX_MIDDLE_MIN_BRIGHTNESS = get_detection_value("matchmaking", "LETTERBOX_MIDDLE_MIN_BRIGHTNESS", 30)


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


def read_letterbox_brightness(
    frame: np.ndarray,
    top_roi: tuple[int, int, int, int] = LETTERBOX_TOP_ROI,
    bottom_roi: tuple[int, int, int, int] = LETTERBOX_BOTTOM_ROI,
    middle_roi: tuple[int, int, int, int] = LETTERBOX_MIDDLE_ROI,
) -> tuple[float, float, float]:
    """上帯・下帯・中央帯それぞれの平均輝度(グレースケール)をそのまま返す(診断用)。

    read_vs_roi_hsvと同じ位置づけで、DEBUGログから直接確認できるようにしている
    (state.match_stateの_check_for_vs_screen参照)。
    """

    def _mean_gray(roi: tuple[int, int, int, int]) -> float:
        x1, y1, x2, y2 = roi
        gray = cv2.cvtColor(frame[y1:y2, x1:x2], cv2.COLOR_BGR2GRAY)
        return float(gray.mean())

    return _mean_gray(top_roi), _mean_gray(bottom_roi), _mean_gray(middle_roi)


def is_letterboxed(frame: np.ndarray) -> bool:
    """VS画面特有のレターボックス(上下黒帯・中央は通常表示)になっているかを判定する。

    モジュールdocstring参照(Issue #144/#189)。試合間の暗転(画面全体が暗くなる)
    とは、中央帯が暗いままかどうかで区別する。
    """
    top, bottom, middle = read_letterbox_brightness(frame)
    return top < LETTERBOX_MAX_BRIGHTNESS and bottom < LETTERBOX_MAX_BRIGHTNESS and middle >= LETTERBOX_MIDDLE_MIN_BRIGHTNESS


def is_vs_screen(frame: np.ndarray, roi: tuple[int, int, int, int] = VS_ROI) -> bool:
    """マッチング完了(VS画面)の「VS」ロゴが表示されているかを判定する。

    Issue #144/#189: レターボックス判定(is_letterboxed、モジュールdocstring参照)を
    主判定とし、ロゴの色判定は大まかな除外フィルタとしてAND条件で組み合わせる。

    単発フレームでは試合中の演出アイコン等で稀に誤検知しうる(モジュール
    docstring参照)。呼び出し側でmotion.find_confirmed_value等によるデバウンスと
    組み合わせて使うことを前提とする。
    """
    if not is_letterboxed(frame):
        return False
    h, s, v = read_vs_roi_hsv(frame, roi)
    return VS_HUE_RANGE[0] <= h <= VS_HUE_RANGE[1] and VS_SAT_RANGE[0] <= s <= VS_SAT_RANGE[1] and v >= VS_VAL_MIN
