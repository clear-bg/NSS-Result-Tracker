"""結果バナー(勝ち/負け)の色判定。

CLAUDE.md記載の方針どおり、OCRではなく画面上部に出る斜めの結果バナーの
色で勝敗を判定する(形状判定は今後の精度向上時に追加検討)。

閾値は fixtures/screenshots の全状態画像に加え、実際の配信からのクリップ
(fixtures/videos/00〜03、scripts/inspect_banner_colors.py と同様の手法で採取)
を実測して決めている。同じOBS経由のキャプチャでも配信間でバナーの色味・
帯の太さ/角度に無視できない差があることが分かったため(勝ちはH80〜83で
ほぼ一致するが、負けはH84〜101まで幅がある)、ROIはバナー帯が細くなっても
確実に帯の内側に収まるよう画面最上部寄りの薄い帯にし、閾値も両者の実測
クラスタを包含する範囲にしている。

この判定は単体では「試合中のゴール演出」等でも稀に誤検知しうることを
確認済み(誤検知は最大でも0.5秒未満しか継続しない一方、本物のバナーは
1秒以上継続する)。呼び出し側で複数フレームにわたり同じ判定が一定時間
連続した場合のみ採用する運用を想定している(motion.find_confirmed_value参照)。

上記のデバウンスだけでは防げない誤検知として、マッチング待機画面
(「対戦相手をさがしています...」等)で背景のスタジアム建造物がBANNER_ROI内に
写り込み、その平均色がLOSE_HUE_RANGE等の閾値に偶然一致するケースが実データで
見つかった(fixtures/videos/14_matching_wait_1.mp4で2.5秒以上・3.4秒以上
持続、Issue #45参照)。この画面はカメラアングルがほぼ静止するため、
0.5秒未満という前提が崩れる。本物のバナーはリボン状の単色帯(白文字が
乗る程度のばらつき)なのに対し、背景の建造物は写実的で色相のばらつきが
大きいことを利用し、ROI内のHue標準偏差が閾値以下であることも判定条件に
追加した(実測: 本物のバナーはH標準偏差1〜3程度、マッチング画面の誤検知は
16〜52程度で明確に分離できる。fixtures/videos/00-03,10-16の全既知ケースで
検証済み)。

Issue #67: 実プレイ配信のアーカイブ映像で、上記いずれとも異なる新しい誤検知
パターンが見つかった。マッチング待機画面ではなく**通常プレイ中**(ゴール演出とも
無関係)に、カメラアングルの都合でスタジアムの背景がBANNER_ROIに写り込み、
1.3秒程度連続して"lose"と誤判定された(fixtures/videos/
21_goal_event_false_positive_win_blue_4-3.mp4で実測)。この背景はIssue #45の
建造物ケースと異なりHue標準偏差が5〜10程度と低く、本物のバナー(1〜3程度)に
近いため、既存のBANNER_HUE_STD_MAXフィルタでは区別できない。HUD要素(タイマー/
スコア)の有無や複数ROIでの整合性など追加の判定条件も検討したが、本物のバナーが
表示直後にアニメーションで変形するため確定に必要な連続区間の途中で条件を満たさなく
なり、いずれも本物の確定を壊してしまうことが実データ検証で判明した。この種の
誤検知の参照サンプルが現時点で1件のみで、閾値を「範囲+マージン」で決められるだけの
データが無いため、本モジュール側の根本的な検知改善は見送り、呼び出し側
(state.match_state)のデバウンス時間を1秒から2秒に延長する対症療法で対応した
(state.match_state参照)。誤検知が最大でも0.5秒程度、Issue #45のケースでも
2.5秒以上、というこの節冒頭の記述は、Issue #67のケース(1.3秒)も踏まえると
「デバウンスの秒数によっては単体のHueベース判定だけでは防ぎきれない誤検知が
起こりうる」というより慎重な理解に修正すること。

引き分け(draw)判定について(Issue #26、fixtures/videos/
20_draw_blue_without_rank_1-1.mp4実測): 引き分けバナー「引き分け」の帯
自体は負けバナーと似た暗い配色で、BANNER_ROIの帯色だけではLOSE_HUE_RANGE
等の条件に一致してしまい負けと区別できない。一方、文字「引き分け」の
縁取りには勝ちバナーと同じミントグリーンが使われており(負けバナー
「負け」の文字は白一色でグリーンの縁取りが無い)、これを区別に使える
ことが実測で判明した。文字部分のROI(DRAW_TEXT_ROI、画面左上の時計を
避けた範囲)のうちミントグリーン(H75-95, S>100, V>140)の画素が占める
割合は、勝ち約55%・引き分け約25-27%・負け0%と明確に分離できる
(fixtures/screenshots 8枚+fixtures/videos/20_draw_blue_without_rank_1-1.mp4
で実測)。そのため、帯色がLOSE_HUE_RANGE等に一致した場合のみ追加でこの
文字ROIを確認し、閾値(DRAW_TEXT_TEAL_FRACTION_MIN)以上なら"draw"、
未満なら"lose"と判定する。

なお、現時点で入手できている引き分けの参照素材はランクを賭けない対戦の
ものだけで、この場合ランクバッジ自体が表示されない(バナー消灯後は暗転
演出を挟まず直接メニュー画面に遷移する)。これはバッジ非表示のwin/lose
ケース(`*_without_rank_*.png`,
fixtures/videos/13_win_red_vs_screen_without_rank.mp4)と見た目上区別が
つかないため、state/match_state.py側は無改修のまま既存の「バッジが
読み取れない場合はNoneのまま記録する」フォールバックで正しく扱えている
(動画メタデータ駆動テストで確認済み)。ランクを賭けた対戦での引き分け
(バッジが表示されつつ、勝敗が無いのでバッジ自体は動かないと想定される
ケース)の参照素材はまだ無く未検証。この場合もバッジが静止しているだけ
であれば既存のTRACKING_RANK側のロジック(安定を待って確定)でそのまま
扱えるはずだが、実データで確認できるまでは推測に留める。

Issue #159対応(BANNER_ROIの複数矩形化): 従来の単一ROI(1300,5,1750,35、
帯の右上寄りをまとめて薄く横断する1本の細長い矩形)は、配信ごとの帯の
太さ・角度の差(モジュール冒頭参照)を吸収しきれず、(1)矩形の右下端が
帯からはみ出して背景色を拾う(負けバナーの一部の配信で実測)、(2)細長い
1本のみのサンプルのため、背景の建造物・天蓋等がこの帯にちょうど重なる
角度だと平均色・Hue標準偏差ごと誤検知側の閾値に一致しやすい(Issue #45・
#67・fixtures/videos/25_inplay_false_positive_win_blue_teal_canopy.mp4)、
という2つの弱点があった。帯を横断する形で5つの矩形に分割し(各矩形は
帯の傾きに沿って高さ・y座標を変え、どの区間でも帯の内側に収まるように
実測して配置)、5つを`goal.py`の`is_goal_event()`と同じ「まとめて1つの
サンプルとして平均を取る」方式で合算するよう変更した。

実際にfixtures/videos全フレームを旧ROI・新ROIの両方でスキャンして比較した
ところ(scripts/inspect_banner_colors.py参照)、既知の誤検知動画
(21_goal_event_false_positive_win_blue_4-3.mp4、
25_inplay_false_positive_win_blue_teal_canopy.mp4)で新ROIは旧ROIが検知して
いた誤検知区間をほぼ全て(25番は完全に0件まで)除去した一方、本物の勝ち/
負けバナー動画(28・29・30・31番)では旧ROIとほぼ同じ区間(数フレームの
起動ラグのみ)を検知できており、真陽性を壊していないことを確認済み。
なお新ROIでは勝ちバナーの実測Hueがわずかに下がる副次的な効果も見られたが
(旧87.1〜87.3→新86.3〜86.9)、WIN_HUE_RANGEの再較正はIssue #118/#143の
スコープのためここでは変更していない(Issue #143は別途、WIN_VAL_MINも
絡む形で再調査する。79_result_rank_up_hdr_off.png(ランク昇格オーバーレイ)
がWIN_VAL_MINを緩めると誤検知することが判明したため、単純な閾値緩和では
済まないことが分かっている)。

Issue #193対応(2026-07-31、LOSE_VAL_RANGE再較正): 降格ラベル付きの負け
バナー(fixtures/screenshots/98)はV53.7-59.2がLOSE_VAL_RANGEの下限65を
下回り、classify_banner()がNoneを返すことがIssue #147/#192で判明していた。
参照素材が1件のみのため一旦xfail扱いとしていたが、Issue #176/#201の調査で
2件目の降格素材(106、動画43から切り出し)を収集した際、BANNER_ROIS平均
V53.7-56.0が別セッション同士で高い一貫性を示し、かつ動画40・43それぞれで
2秒以上安定していることを確認した(単発フレームのノイズではない)。この
一貫性を踏まえ、下限を65→50に再較正した。降格ラベル・演出表示中は通常の
負けバナーより画面全体がやや暗めに描画される(降格ラベル自体の半透明
オーバーレイの影響と推測、根本原因は未確定)ことが、これで2セッション分の
実データにより裏付けられた形になる。

再較正にあたり、fixtures/screenshots全45枚・fixtures/videos全24本を新しい
下限(50)でスキャンし、新たな誤検知が発生しないことを確認済み。該当した
動画(29・33・39番)はいずれも本物の"lose"シーンの一部区間で、緩和後も
正しく"lose"であり続けるべきフレームだった。Issue #172(WIN_HUE_RANGE/
WIN_VAL_MIN、リーグ昇格演出と衝突するため単純な閾値緩和では解決しない)
とは異なり、今回は衝突するfixtureが見つからなかったため安全に緩和できた。

Issue #172/#211(WIN_VAL_MIN再調査の結論、2026-07-31): 77・85番(勝ちバナー、
V146.1〜148.5)がWIN_VAL_MIN=165を満たせずclassify_banner()がNoneを返す件を、
当初はWIN_HUE_RANGE/WIN_VAL_MINを緩めて解消しようとしていたが(Issue #172)、
79_result_rank_up_hdr_off.png(ランク昇格オーバーレイ、V150.18)と安全に分離
できず断念していた。改めて画像を目視確認したところ、77・85番は両方とも
**拡大表示**(ランク変動アニメーション開始後〜暗転までの間に表示されるサイズ)
のタイミングのフレームであり、正しく"win"判定できている84・88・93〜96・99・
100番(いずれもコンパクト表示=結果バナー確定直後)とは撮影タイミングが異なる
ことが判明した。ランクゲージが動き始めた後(拡大表示以降)は画面全体がやや
暗くなる演出が入り、これがバナー帯のVを押し下げている。79・101番のVが近い
値になるのも、同じ「拡大表示以降の暗くなった局面」で右上にバナーの残光が
透けて見えているためで偶然ではない。

state/match_state.pyを確認すると、拡大表示以降のフェーズ(_track_rankの
GRACE中)でclassify_banner()を呼んでいる箇所は「バナーが消えたか(is None)」
の確認のみで、"win"かどうかを再確認する箇所は無い。実際のwin/lose確定
(_watch_for_banner)はコンパクト表示のうち(V≈218前後)に完了しているため、
77・85が"win"を返さないことは運用上のバグではなかった。つまりWIN_HUE_RANGE/
WIN_VAL_MINの閾値変更は不要(現状のVの下限が拡大表示の残光・昇格オーバーレイ
双方を正しく除外できている)。真に誤っていたのはtests/test_banner.py側の
ground truth("win"を期待していた)であり、77・85の期待値をNoneに訂正して
解決した(詳細はtests/test_banner.py参照)。
"""

from typing import Literal, Optional

import cv2
import numpy as np

from nss_tracker.detection_config import get_detection_value

BannerResult = Optional[Literal["win", "lose", "draw"]]

# バナー帯を横断する5つの矩形(x1, y1, x2, y2)。帯の傾きに沿って高さ・y座標を
# 変え、配信ごとの帯の太さ・角度の差があってもどの区間でも帯の内側に収まる
# ように実測して配置している(Issue #159、モジュールdocstring参照)。
# 解像度1920x1080のフレームを前提とする
# (config/detection.tomlの[banner]で上書き可能。以下同様)
BANNER_ROIS = get_detection_value(
    "banner",
    "BANNER_ROIS",
    (
        (696, 71, 879, 149),
        (879, 44, 1057, 128),
        (1057, 8, 1245, 86),
        (1245, 5, 1543, 39),
        (1543, 5, 1722, 24),
    ),
)

# 実測(scripts/inspect_banner_colors.py, fixtures/screenshots + fixtures/videos/00-03):
# 勝ち: H80.7-83.2 / 負け: H89.0-99.0(配信間の差が大きい)
# Issue #172: HDR無効化後、84・88番(H86.86)がこの上限からわずかに外れていた。
# WIN_VAL_MINには触れずHue上限のみ77→87→88(境界含む)に広げても、
# 79_result_rank_up_hdr_off.png(V150.18)はWIN_VAL_MIN=165を満たさないため
# 引き続き弾かれることをfixtures/screenshots・fixtures/videos全件で確認済み
# (77・85番はV146-148がWIN_VAL_MIN未満のため今回のHue側の緩和だけでは直らず、
# xfailのまま残す。詳細はIssue #172参照)
WIN_HUE_RANGE = get_detection_value("banner", "WIN_HUE_RANGE", (77, 87))
WIN_SAT_MIN = get_detection_value("banner", "WIN_SAT_MIN", 120)
WIN_VAL_MIN = get_detection_value("banner", "WIN_VAL_MIN", 165)
LOSE_HUE_RANGE = get_detection_value("banner", "LOSE_HUE_RANGE", (87, 103))
LOSE_SAT_RANGE = get_detection_value("banner", "LOSE_SAT_RANGE", (35, 65))
# Issue #193: 降格ラベル付きの負けバナーはV53.7-59.2と下限65を下回っていたが、
# 別セッション2件(98・106、モジュールdocstring参照)で2秒以上安定した実測値
# だったため、下限を65→50に再較正した(実測最小値53.7に対しマージン約4)。
# fixtures/screenshots全45枚・fixtures/videos全24本を緩和後の範囲でスキャンし、
# 新たな誤検知が無いことを確認済み(該当したのはいずれも本物のlose動画の
# 一部区間のみ)
LOSE_VAL_RANGE = get_detection_value("banner", "LOSE_VAL_RANGE", (50, 130))

# 実測(Issue #45): 本物のバナーはH標準偏差1〜3程度(単色のリボン状の帯に白文字が
# 乗る程度のばらつき)。マッチング待機画面の誤検知は背景の建造物が写実的なため
# 16〜52程度と大きく上回る。余裕を持って10を閾値とする
BANNER_HUE_STD_MAX = get_detection_value("banner", "BANNER_HUE_STD_MAX", 10.0)

# 「引き分け」の文字の縁取り(ミントグリーン)を判定するための領域。
# 時計表示(画面左上)を避けた文字部分のみを狙う (x1, y1, x2, y2)
# 解像度1920x1080のフレームを前提とする
# (config/detection.tomlの[banner]で上書き可能。以下同様)
DRAW_TEXT_ROI = get_detection_value("banner", "DRAW_TEXT_ROI", (270, 45, 650, 250))

# 実測(fixtures/screenshots + fixtures/videos/20_draw_blue_without_rank_1-1.mp4):
# 勝ち55% / 引き分け25-27% / 負け0%(DRAW_TEXT_ROI内でのミントグリーン画素の割合)
DRAW_TEXT_HUE_RANGE = get_detection_value("banner", "DRAW_TEXT_HUE_RANGE", (75, 95))
DRAW_TEXT_SAT_MIN = get_detection_value("banner", "DRAW_TEXT_SAT_MIN", 100)
DRAW_TEXT_VAL_MIN = get_detection_value("banner", "DRAW_TEXT_VAL_MIN", 140)
DRAW_TEXT_TEAL_FRACTION_MIN = get_detection_value("banner", "DRAW_TEXT_TEAL_FRACTION_MIN", 0.10)


def _is_draw_text(frame: np.ndarray, roi: tuple[int, int, int, int] = DRAW_TEXT_ROI) -> bool:
    x1, y1, x2, y2 = roi
    crop = frame[y1:y2, x1:x2]
    hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV).reshape(-1, 3)
    mask = (
        (hsv[:, 1] > DRAW_TEXT_SAT_MIN)
        & (hsv[:, 0] >= DRAW_TEXT_HUE_RANGE[0])
        & (hsv[:, 0] <= DRAW_TEXT_HUE_RANGE[1])
        & (hsv[:, 2] > DRAW_TEXT_VAL_MIN)
    )
    return bool(mask.mean() >= DRAW_TEXT_TEAL_FRACTION_MIN)


def classify_banner(
    frame: np.ndarray, rois: tuple[tuple[int, int, int, int], ...] = BANNER_ROIS
) -> BannerResult:
    """勝敗結果バナーの色を判定する。バナーが写っていなければNoneを返す。

    複数矩形(Issue #159)はgoal.pyのis_goal_event()と同様、まとめて1つの
    サンプルとして平均・標準偏差を取る(矩形ごとの個別判定はしない)。
    """
    crops = [frame[y1:y2, x1:x2].reshape(-1, 3) for x1, y1, x2, y2 in rois]
    combined = np.concatenate(crops, axis=0)
    hsv = cv2.cvtColor(combined.reshape(1, -1, 3), cv2.COLOR_BGR2HSV).reshape(-1, 3)
    h, s, v = hsv.mean(axis=0)
    hue_std = hsv[:, 0].std()

    if hue_std > BANNER_HUE_STD_MAX:
        return None
    if WIN_HUE_RANGE[0] <= h <= WIN_HUE_RANGE[1] and s >= WIN_SAT_MIN and v >= WIN_VAL_MIN:
        return "win"
    if (
        LOSE_HUE_RANGE[0] <= h <= LOSE_HUE_RANGE[1]
        and LOSE_SAT_RANGE[0] <= s <= LOSE_SAT_RANGE[1]
        and LOSE_VAL_RANGE[0] <= v <= LOSE_VAL_RANGE[1]
    ):
        return "draw" if _is_draw_text(frame) else "lose"
    return None
