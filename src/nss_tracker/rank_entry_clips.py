"""試合終了区間(結果バナー確定〜暗転)の動画クリップ生成(Issue #307)。

`web/rank-entry`(Issue #306)でランク数値を手動入力する際、ゲーム画面は
既にマッチング待機画面等へ進んでしまっていることが多く、結果バナー〜ランク確定
までの区間を見返す手段が無いと入力そのものが困難になる。本モジュールは
`main.py`の検知ループから呼ばれ、この区間のフレームをバッファし、区間が
終わった時点(暗転検知)でmp4クリップとしてディスクに書き出す。

## 録画区間の決め方

録画開始のタイミングは`MatchStateMachine.current_state`が"watching"から
"tracking_rank"へ遷移した瞬間(ランクを賭けた試合の結果バナー確定〜GRACE
フェーズ突入)。ランクを賭けない試合はこの遷移自体が起こらない
(state/match_state.pyのIssue #235参照、結果バナー確定時点で直ちに確定し
TRACKING_RANKを経由しない)ため、自然に録画対象外になる(#307のIssue本文にあった
未確定事項の1つ)。

録画終了のタイミングは、Issue本文どおり`detection.motion.is_full_blackout()`が
真になった瞬間。これは`state/match_state.py`内部でOBSシーン切替のトリガーに
使っている暗転検知(`_check_pending_obs_switch`、Issue #224)とは別に、
このモジュール専用に`main.py`の検知ループから直接呼ぶ(検知ロジック自体を
`match_state.py`に持たせず、疎結合を保つため)。

## フレームの間引き・縮小

1920x1080のフレームをそのまま(実測60fpsで)数秒〜数十秒分バッファすると
容量が大きくなりすぎる(例: 10秒×60fps×約6MB/フレーム≒3.6GB)ため、
`TARGET_SAMPLE_FPS`(既定8fps)に間引き、かつ`TARGET_WIDTH`(既定960px、
元解像度の約半分)に縮小してから保持する。ランク数値を目視確認できれば
十分な用途のため、この程度の間引き・縮小で実用上問題ない想定。

録画区間が長引いた場合(暗転検知を逃した、プレイヤーが長時間離席した等)に備え、
`MAX_DURATION_SECONDS`(既定18秒、Issue #395で60秒から短縮)に達したら
それ以降のフレームは追加しない。

Issue #395: このとき**録画状態とバッファは保持したまま**にし、`match_id`が
判明した時点(`main.py`が`_finalize()`の`MatchResult`を受け取った時点)で
書き出す。以前は`main.py`側が「上限に達したのに`match_id`が未判明」を異常系と
みなしてクリップごと破棄・リセットしていたが、`_finalize()`はクリップ開始から
3.1〜36.5秒とばらつき、実配信3セッション25試合のうち13試合が上限(18秒)より
遅かった。そのまま上限だけ短くするとその13試合のクリップが1本も残らず、
手動入力(`/rank-entry`)そのものが成立しなくなるため。上限到達後は
`add_frame()`がフレームを追加しないので、待っている間にバッファが増え続けることも
メモリを圧迫することもない。

Issue #399: ゲージクローズアップ側は上記の間引きを8fps→`GAUGE_SAMPLE_FPS`(30fps)へ
上げた。8fpsではランク変動が止まった瞬間の値を目盛りに合わせて読み取れず、
手動入力の役に立たなかったため(実配信でのユーザー報告)。あわせて
`resize_on_encode=True`を指定し、拡大(290x32→1160px)と目盛り描画は書き出し時に
行い、バッファには切り出したままの生クロップだけを保持する。加工後を保持すると
1枚約600KBだが、生クロップなら約28KBで済むため、枚数を3.75倍にしてもメモリは
約1/6になる(実測: 8fps/加工後=144枚82.2MB → 30fps/生クロップ=540枚14.3MB)。
画面全体のクリップは元が1920x1080で生のまま持つ方が重くなるため、従来どおり
`add_frame()`の時点で960pxへ縮小して保持する。

## エンコード

`imageio-ffmpeg`(既存依存、`capture/ffmpeg_capture.py`と同じ調達方法)で
ffmpeg本体のパスを取得し、サブプロセスへ生フレーム(bgr24, rawvideo)を
標準入力経由で流し込んでmp4にエンコードする。エンコード自体はCPUバウンドで
数百ミリ秒〜数秒かかりうるが、実体はffmpegサブプロセス側の処理のため
(Issue #303で判明したPaddleOCRのようにPythonのGILを占有する処理ではない)、
標準入力への書き込み待ち(I/Oバウンド)だけがPython側のスレッドを塞ぐ。
検知ループ本体をブロックしないよう、エンコード自体はバックグラウンドスレッドで
行う(Issue #189/#303と同じ考え方)。`_last_encode_thread`はテスト・シャットダウン時に
完了を待てるようにするためのフックで、通常の検知ループはこれを待たない。

## ランク数値拡大クリップ(Issue #417)

画面全体・ゲージ拡大に加えて、帯番号だけを切り出した3本目のクリップを生成する
(`RANK_NUMBER_CLIPS_DIR`)。`/rank-entry`をモニターの片側半分に寄せて使うと、
画面全体のクリップでは帯番号が約8px幅にしかならず目視で読み間違えるため。
切り出し領域は`detection/rank_ocr.py`の`RANK_NUMBER_CLIP_ROI`で、ゲージ拡大と
同じく`crop_roi`+`resize_on_encode`の仕組みにそのまま乗せている。

## 保持数管理

直近`max_clips`件(既定50件)を基本の保持数とし、新しいクリップが出来るたびに
これを超えた古いものを削除する。ファイル名は`{match_id}.mp4`とする。

Issue #409で既定を3件→50件に増やした。当初は「手動入力に必要な直近3件だけ
残せばよい」という前提だったが、2026-09-08に記録済みのランク値を配信映像と
突き合わせて検証したところ4件の誤りが見つかり(matches id=1/3/15/21)、その
どれもクリップが既に削除されていて見返せなかった。Issue #407で入力値の矛盾を
自動検知するようにしても、指摘された試合のクリップが残っていなければ結局
アーカイブを探しに行くことになるため、保持件数を増やした。

容量は実測で1試合あたり平均約1.6MB・最大約4.3MB(画面全体+ゲージの2本合計)
だったが、これはIssue #395で録画上限を60秒→18秒に短縮する前の値。18秒上限が
効いた後は1試合あたり最大でも約1.3MB程度になる見込みで、50件でも100MB以下に
収まる(仮に旧サイズのままでも最悪約220MB)。

保持数の判定(「古い」の基準)は、当初はファイル名(match_id、数値)の大小
(mtimeより確実なため)で行っていたが、Issue #381でファイルの実際の更新日時
(mtime)へ変更した。match_idは`matches`テーブルのAUTOINCREMENTのため、DBファイル
を作り直す(または空にする)とmatch_idが1から振り直される。一方このフォルダは
DBとは別のライフサイクルで残り続けるため、DBリセット直後は「番号は小さいが
実際には直前に作られたばかりの最新クリップ」が、リセット前の番号が大きい
古いファイルより先に削除されてしまう不具合が実機で見つかった。通常運用(DBを
作り直さない限り)ではmatch_idの昇順と生成順は一致するため、この変更で
既存の動作は変わらない。

### 未確定試合のクリップは削除しない(Issue #389)

`rank_before`のチェーン(直近の「ランクを賭けた試合」から`rank_after`を古い順に
連鎖して解決する仕組み、`database.db`のモジュールdocstring参照)は、途中の1試合が
未確定(`/rank-entry`での入力待ち)のままだと、それ以降の試合すべてが連続して
`rank_before`を解決できないまま止まってしまう。この状態で`max_clips`件による
機械的なローリングウィンドウ削除をそのまま適用すると、チェーンを塞いでいる
最古の未確定試合のクリップが`/rank-entry`のUIから選べる範囲(直近3件)の外に
押し出されて削除されてしまい、二度とその試合を確定できなくなる(=以降の
試合も永久に未確定のまま)デッドロックが実配信で見つかった。

対策として、`max_clips`件を超えて削除の対象になった古いクリップのうち、
対応する試合が未確定(`matches.rank_before_ocr`が非NULLかつ`rank_after`が
NULL、`database.db.fetch_match`で判定)のものは削除せずそのまま残す。
`/rank-entry`側(`web/server.py`)はディスク上に残っている全クリップを表示
対象にするため、UIからは`max_clips`件に加えて未確定の試合の分だけ選択肢が
伸びる(表示側の実装は`_build_rank_entry_context`参照)。基本の見せ方・削除
ポリシー自体は変更せず、未確定の試合だけが例外的に残り続ける。

削除対象の判定にはDBへの接続が要る(`RankEntryClipRecorder`はコンストラクタで
`db_path`を受け取る)。`_apply_retention()`はバックグラウンドスレッド
(`_encode_and_apply_retention`)から呼ばれるため、`main.py`側の検知ループが
使っているコネクションは(sqlite3のcheck_same_thread制約により)使い回せず、
`web/server.py`と同じ「呼び出しのたびに新規コネクションを開いて閉じる」方式にした。

このポリシーだと、配信者が確定作業を何セッションも放置すると未確定クリップが
際限なく溜まりディスクを圧迫しうる。あえて上限は設けず(ユーザーとの相談で
決定、個人利用のため実害が出るケースは考えにくい)、代わりに未確定のまま
保持されているクリップが`PENDING_CLIP_WARNING_THRESHOLD`(既定5件)を超えたら
WARNINGログを出し、配信者が確定作業を溜め込みすぎていることに気付けるようにする。

## ゲージクローズアップ動画(Issue #312)

上記の画面全体クリップと全く同じ録画区間・トリガー(main.pyから同じ
start()/add_frame()/finish()呼び出し)を使い、`RankEntryClipRecorder`を
もう1つ(`crop_roi`/`overlay_fn`付きで)構築するだけで、ランクゲージ部分
だけを切り出した別動画も並行して生成できるようにした。`crop_roi`が
指定されている場合、`add_frame()`は各サンプリングフレームからそのROIを
切り出してから(`_resize`によるサイズ調整・`overlay_fn`によるオーバーレイ
合成を経て)バッファする。`_resize`は当初「大きい画面全体フレームを
`TARGET_WIDTH`まで縮小する」用途のみだったが、ゲージのROI(実測290x32px程度、
`detection/rank_ocr.py`の`GAUGE_ROI_ENLARGED`参照)は逆に「小さい切り出しを
見やすく拡大する」必要があるため、拡大方向にも対応させた(`vs_rank.py`の
数字OCR前処理と同じ、`cv2.INTER_CUBIC`で拡大する考え方)。

`overlay_fn`(`_draw_gauge_ticks`)は、ゲージ幅を20分割(0.5刻みで0〜10まで、
Issue #312のIssue本文で決定)する目盛り線を各フレームに描画する。ゲージは
横方向に左から右へ塗りつぶされる仕様(`read_rank_gauge_fill`参照)のため、
目盛りは縦線として引く。手入力時にゲージの溜まり具合(小数部)を目視で
より精密に読み取れるようにするための補助線で、1.0刻み(偶数番目の線)は
少し太く目立たせている。

`crop_roi`/`overlay_fn`を伴うフレーム処理で例外が起きても検知ループ全体を
止めないよう、`add_frame()`内で例外を握りつぶしログに残すだけにしている
(ユーザー確認済み。キャプチャループへの影響を最小限にする設計方針)。

### 目盛り線の見やすさ改善(Issue #334)

実際の配信環境で見ると、当初の黄色の目盛り線(0.5刻み・1.0刻みとも実線)は
見にくいというフィードバックを受けて改善した。

- 色は黄色からマゼンタ(`GAUGE_TICK_COLOR`)に変更した。ゲージは明るい
  塗りつぶし部分(グラデーション)と暗い未塗りつぶし部分の両方を持つため、
  単色だと片方の背景でコントラストが弱くなる問題があった(黒は暗い背景で
  ほぼ同化、白は明るい背景で弱いことを実データで確認済み)。マゼンタは
  どちらの背景でもはっきり視認できたため、複数の候補色をユーザーと
  見比べて選んだ。線の太さ自体(0.5刻み1px・1.0刻み2px)は変更していない
- 0.5刻みの補助線は実線から点線(`_draw_dashed_vline`)に変更した。この
  点線はゲージ本体の高さ内にとどめ、下記の白い余白側へは伸ばさない
  (ゲージ内部の目安であり、軸目盛りではないため)
- 1.0刻みの線は実線のまま維持し、ゲージ本体の下に追加した白い余白
  (`GAUGE_LABEL_PADDING_HEIGHT`)側へ`GAUGE_TICK_LABEL_EXTENSION`分だけ
  短く伸ばした上で、その下に整数の目盛り数値(0〜10)を黒字で描画する。
  数値をゲージ本体に重ねると溜まり具合の色と被って見えづらいため、
  余白側に逃がした(Issue本文の対応方針どおり)。ゲージ本体の左右の端
  (0・10)には元々線を引いていない(境界線と紛らわしいため)が、数値
  ラベル自体は0・10とも表示する
- 余白の高さ・点線の間隔は初期実装の値のまま確定した。数値の文字サイズは
  初期実装時(scale=0.5、高さ12px)だと小さすぎるというフィードバックを受け、
  実際に生成したゲージクローズアップ動画を元に複数のサイズ候補を実寸大で
  比較し、scale=0.8(高さ18px、太字は使わない)に変更した。文字が大きくなった
  分、余白の高さも40px→44pxに広げている
"""

import logging
import subprocess
import threading
from pathlib import Path
from typing import Callable, Optional

import cv2
import imageio_ffmpeg
import numpy as np

from nss_tracker.database import db

logger = logging.getLogger("nss_tracker.rank_entry_clips")

# main.py(生成側)・web/server.py(配信側)の両方から参照する、クリップの
# 保存先ディレクトリ。リポジトリルート直下の専用ディレクトリ(.gitignore対象)を使う。
# 当初はtmp/配下の専用サブディレクトリだったが、tmp/はユーザーが手動キャプチャした
# 動画等も置く共有の作業用フォルダでもあり、ユーザーがtmp/を整理した際に誤って
# クリップごと削除してしまう事故が起きたため、Issue #342でtmp/の外(clips/)に
# 独立させた(ユーザーとの相談で決定、.envでの設定は行わず固定パスのままとする)
DEFAULT_CLIPS_DIR = Path("clips/rank_entry_clips")
# Issue #312: ゲージクローズアップ動画の保存先(画面全体クリップとは別ディレクトリ)
GAUGE_CLIPS_DIR = Path("clips/rank_gauge_clips")
# Issue #417: ランク数値だけを拡大したクリップの保存先
RANK_NUMBER_CLIPS_DIR = Path("clips/rank_number_clips")

TARGET_SAMPLE_FPS = 8.0
# Issue #399: ゲージクローズアップ動画だけは、ランク変動が止まった瞬間の値を
# 目盛りに合わせて読み取る用途のため8fpsでは足りない(実配信で「動画がある意味が
# ほとんど無い」というフィードバックを受けた)。保持形式を「拡大・目盛り描画後」から
# 「拡大前の生クロップ」へ変えたことで1枚あたり約28KB(拡大後は約600KB)に
# なったため、この解像度でもfpsを上げられる(18秒×30fpsで約15MB)
GAUGE_SAMPLE_FPS = 30.0
TARGET_WIDTH = 960
# Issue #395: 録画区間の上限。以前は「暗転を見逃した/長時間離席した」場合の
# 安全策としての60秒だったが、実配信3セッション25試合の実測で9試合がこの上限に
# 張り付いており(暗転の取りこぼし、#383)、60秒のクリップは中身のほとんどが
# 試合と無関係な画面で手動入力の役に立たないうえ、加工後フレームを保持する都合で
# 画面クリップ約747MB+ゲージクリップ約287MBをRAMに抱える状態になっていた。
# 「クリップ開始(tracking_rank突入)→暗転検知」は正常に検知できた13試合で
# 2.9〜13.0秒だったため、正常ケースを1件も切らない18秒に短縮した
# (#395のOBSシーン切替タイムアウト30秒の約3秒前に相当、ユーザーとの相談で決定)
MAX_DURATION_SECONDS = 18.0
# Issue #409: 3件→50件。誤入力に後から気付いたときに見返せるようにするため
# (モジュールdocstringの「保持数管理」節参照)
DEFAULT_MAX_CLIPS = 50
# Issue #389: 未確定のため削除せず残っているクリップがこの件数を超えたら
# WARNINGログを出す(上限として削除するわけではない、モジュールdocstring参照)
PENDING_CLIP_WARNING_THRESHOLD = 5
# Issue #312: ゲージのROI(実測290x32px程度)をどの幅まで拡大して見せるか
GAUGE_TARGET_WIDTH = 1160
# Issue #417: ランク数値クリップ(RANK_NUMBER_CLIP_ROI、158px幅)をどこまで
# 拡大して書き出すか。ゲージ(290px→1160px)と同じ4倍にした
RANK_NUMBER_TARGET_WIDTH = 632
GAUGE_TICK_SEGMENTS = 20
# Issue #334: 黄色は明るい塗りつぶし部分・暗い未塗りつぶし部分の両方で見にくかった
# ため、どちらの背景でもはっきり視認できたマゼンタに変更した(複数候補をユーザーと
# 見比べて選んだ、モジュールdocstring参照)
GAUGE_TICK_COLOR = (255, 0, 255)  # BGR: マゼンタ
# Issue #334: 0.5刻みの点線の、線分の長さ・隙間の長さ(px)。実際の見た目を
# ユーザーと確認し、この値のまま確定した
GAUGE_TICK_DASH_LENGTH = 6
GAUGE_TICK_DASH_GAP = 4
# Issue #334: 整数の目盛り数値(0〜10)を描画するため、ゲージ本体の下に追加する
# 白い余白の高さ(px)。文字サイズをGAUGE_LABEL_FONT_SCALE=0.5→0.8に拡大した分、
# 40→44に広げた
GAUGE_LABEL_PADDING_HEIGHT = 44
# Issue #334: 1.0刻みの目盛り線を、上記の白い余白側へどれだけ伸ばすか(px)。
# 線をそのまま数値まで伸ばすとうるさいため、短い「目盛り」として少しだけ伸ばす
GAUGE_TICK_LABEL_EXTENSION = 8
GAUGE_LABEL_FONT = cv2.FONT_HERSHEY_SIMPLEX
# Issue #334: 初期実装時は0.5(高さ12px)だったが、実際に生成した動画で見ると
# 小さすぎるというフィードバックを受け、複数のサイズ候補を実寸大で比較して
# 0.8(高さ18px)に変更した。太字(GAUGE_LABEL_FONT_THICKNESS)は候補にもあったが、
# 通常の太さの方が読みやすいというユーザーの判断で見送り、1のまま維持している
GAUGE_LABEL_FONT_SCALE = 0.8
GAUGE_LABEL_FONT_THICKNESS = 1
GAUGE_LABEL_COLOR = (0, 0, 0)  # BGR: 黒


def _draw_dashed_vline(
    frame: np.ndarray,
    x: int,
    y_start: int,
    y_end: int,
    color: tuple[int, int, int],
    thickness: int,
    dash_length: int,
    gap_length: int,
) -> None:
    """(x, y_start)から(x, y_end)まで、破線の縦線をframeに直接描画する(in-place)。"""
    y = y_start
    while y < y_end:
        segment_end = min(y + dash_length, y_end)
        cv2.line(frame, (x, y), (x, segment_end), color, thickness)
        y += dash_length + gap_length


def _draw_gauge_ticks(frame: np.ndarray) -> np.ndarray:
    """ゲージクローズアップ動画に0.5刻み(0〜10、計20分割)の目盛り線を合成する(Issue #312)。

    ゲージは横方向(左から右)に塗りつぶされる仕様のため、目盛りは縦線で引く。
    1.0刻みに相当する線(偶数番目)は少し太くして目立たせ、大まかな位置の
    目安にしやすくしている。

    Issue #334: 0.5刻みの線は点線に、色はマゼンタに変更した。ゲージ本体の下に
    白い余白を追加し、1.0刻みの線をそこへ少し伸ばした上で整数の目盛り数値(0〜10)を
    黒字で描画する(詳細はモジュールdocstring参照)。戻り値はframeより縦に大きくなる。
    """
    height, width = frame.shape[:2]
    canvas = np.full((height + GAUGE_LABEL_PADDING_HEIGHT, width, 3), 255, dtype=np.uint8)
    canvas[:height, :] = frame

    for i in range(1, GAUGE_TICK_SEGMENTS):  # 両端(0, 20)には線を引かない
        x = round(width * i / GAUGE_TICK_SEGMENTS)
        if i % 2 == 0:
            cv2.line(canvas, (x, 0), (x, height + GAUGE_TICK_LABEL_EXTENSION), GAUGE_TICK_COLOR, 2)
        else:
            _draw_dashed_vline(
                canvas, x, 0, height, GAUGE_TICK_COLOR, 1, GAUGE_TICK_DASH_LENGTH, GAUGE_TICK_DASH_GAP
            )

    for value in range(0, 11):
        x = round(width * value / 10)
        text = str(value)
        (text_width, text_height), _ = cv2.getTextSize(
            text, GAUGE_LABEL_FONT, GAUGE_LABEL_FONT_SCALE, GAUGE_LABEL_FONT_THICKNESS
        )
        # 両端(0・10)は文字が枠外にはみ出さないよう、中央寄せの位置をframe内に収める
        text_x = min(max(x - text_width // 2, 0), width - text_width)
        text_y = height + GAUGE_TICK_LABEL_EXTENSION + text_height + 4
        cv2.putText(
            canvas,
            text,
            (text_x, text_y),
            GAUGE_LABEL_FONT,
            GAUGE_LABEL_FONT_SCALE,
            GAUGE_LABEL_COLOR,
            GAUGE_LABEL_FONT_THICKNESS,
            cv2.LINE_AA,
        )

    return canvas


class RankEntryClipRecorder:
    """試合終了区間のフレームをバッファし、区間終了時にmp4クリップを生成する。

    呼び出し側(main.py)の想定する使い方:
        recorder.start(source_fps)          # "watching" -> "tracking_rank"遷移時
        recorder.add_frame(frame)            # 録画中は毎フレーム呼ぶ(内部で間引く)
        recorder.finish(match_id)            # is_full_blackout(frame)がTrueになった時点

    `crop_roi`/`overlay_fn`(Issue #312)を指定すると、画面全体ではなく指定した
    ROIを切り出し、必要な拡大・縮小と任意のオーバーレイ合成を行ってから
    バッファする(モジュールdocstring参照)。
    """

    def __init__(
        self,
        output_dir: Path,
        max_clips: int = DEFAULT_MAX_CLIPS,
        target_sample_fps: float = TARGET_SAMPLE_FPS,
        target_width: int = TARGET_WIDTH,
        max_duration_seconds: float = MAX_DURATION_SECONDS,
        ffmpeg_path: Optional[str] = None,
        crop_roi: Optional[tuple[int, int, int, int]] = None,
        overlay_fn: Optional[Callable[[np.ndarray], np.ndarray]] = None,
        db_path: Optional[Path] = None,
        resize_on_encode: bool = False,
    ) -> None:
        self._output_dir = output_dir
        self._max_clips = max_clips
        self._target_sample_fps = target_sample_fps
        self._target_width = target_width
        self._max_duration_seconds = max_duration_seconds
        self._ffmpeg_path = ffmpeg_path or imageio_ffmpeg.get_ffmpeg_exe()
        self._crop_roi = crop_roi
        self._overlay_fn = overlay_fn
        # Issue #399: Trueにすると、拡大(_resize)とオーバーレイ(_overlay_fn)を
        # バッファ時ではなく書き出し時に行い、バッファには切り出したままの
        # 生クロップを保持する。小さいROIを大きく引き伸ばして見せるゲージ
        # クローズアップ動画では、保持サイズが約1/20(600KB→28KB)になり、
        # その分をfpsに回せる(モジュールdocstring参照)
        self._resize_on_encode = resize_on_encode
        # Issue #389: 未確定の試合のクリップを削除しないための判定に使う
        # (省略時はdb.connect()と同じくconfig.get_db_path()を都度評価する)
        self._db_path = db_path

        self._frames: list[np.ndarray] = []
        self._sample_interval = 1
        self._frame_counter = 0
        self._recording = False
        # Issue #395: 上限時間に到達済みかどうか。到達後はフレームの追加だけを
        # 止め、録画状態(_recording)とバッファはmatch_idが判明するまで保持する
        # (モジュールdocstring参照)
        self._duration_exceeded = False
        # テスト・シャットダウン時にバックグラウンドエンコードの完了を待てるようにする
        # フック(モジュールdocstring参照)。通常の検知ループ(main.py)はこれを待たない
        self._last_encode_thread: Optional[threading.Thread] = None

    @property
    def is_recording(self) -> bool:
        return self._recording

    def start(self, source_fps: float) -> None:
        """録画を開始する。前回分のバッファが残っていれば(finish()未到達のまま
        次の試合が始まった場合)破棄する(モジュールdocstring参照、見逃しは許容する)。
        """
        self._frames = []
        self._frame_counter = 0
        self._sample_interval = max(1, round(source_fps / self._target_sample_fps))
        self._recording = True
        self._duration_exceeded = False

    def add_frame(self, frame: np.ndarray) -> bool:
        """録画中でなければ何もしない。max_duration_seconds相当のフレーム数に
        到達済みならTrueを返す。

        Issue #395: 到達後は**フレームの追加だけを止め**、録画状態とバッファは
        そのまま保持する。呼び出し側(main.py)はmatch_idが判明した時点で
        finish()を呼べばよく、上限に達したこと自体を理由にクリップを破棄しては
        いけない(モジュールdocstring参照)。

        Issue #312: crop_roi/overlay_fnによるフレーム加工で例外が起きても
        検知ループを止めないよう、この1フレーム分だけ読み捨ててログに残す
        (モジュールdocstring参照)。
        """
        if not self._recording:
            return False
        if self._duration_exceeded:
            return True
        if self._frame_counter % self._sample_interval == 0:
            try:
                self._frames.append(self._process(frame))
            except Exception:
                logger.exception("動画クリップ用フレームの加工に失敗したため、このフレームを読み捨てます")
        self._frame_counter += 1
        elapsed_sampled_seconds = len(self._frames) / self._target_sample_fps
        if elapsed_sampled_seconds >= self._max_duration_seconds:
            self._duration_exceeded = True
            # Issue #395: 通常は暗転検知で終わるはずの区間が上限まで伸びたことを
            # 残す(暗転の取りこぼし#383が起きた回数を後から数えられるようにする)
            logger.warning(
                "動画クリップ(%s)の録画が上限時間(%.0f秒)に達したため、以降のフレームを追加しません"
                "(バッファは保持し、試合結果が確定した時点で書き出します)",
                self._output_dir,
                self._max_duration_seconds,
            )
        return self._duration_exceeded

    def _process(self, frame: np.ndarray) -> np.ndarray:
        """バッファへ積む形にフレームを加工する。

        Issue #399: resize_on_encode=Trueの場合は拡大・オーバーレイを行わず、
        切り出した生クロップだけを保持する(書き出し時に_encode_process()で
        同じ加工を行う)。元フレームのスライスをそのまま持つと1920x1080の
        バッファ全体が解放されなくなるため、必ずcopy()する。
        """
        if self._crop_roi is not None:
            x1, y1, x2, y2 = self._crop_roi
            frame = frame[y1:y2, x1:x2]
        if self._resize_on_encode:
            return frame.copy()
        frame = self._resize(frame)
        if self._overlay_fn is not None:
            frame = self._overlay_fn(frame)
        return frame

    def _encode_process(self, frame: np.ndarray) -> np.ndarray:
        """書き出し直前の加工(Issue #399)。resize_on_encode=Falseなら何もしない
        (バッファ時点で_process()が済ませている)。"""
        if not self._resize_on_encode:
            return frame
        frame = self._resize(frame)
        if self._overlay_fn is not None:
            frame = self._overlay_fn(frame)
        return frame

    def _resize(self, frame: np.ndarray) -> np.ndarray:
        height, width = frame.shape[:2]
        if width == self._target_width:
            return frame.copy()
        target_height = round(height * self._target_width / width)
        interpolation = cv2.INTER_AREA if width > self._target_width else cv2.INTER_CUBIC
        return cv2.resize(frame, (self._target_width, target_height), interpolation=interpolation)

    def finish(self, match_id: int) -> None:
        """録画を終え、バックグラウンドスレッドでエンコード・保持数管理を行う。"""
        if not self._recording:
            return
        self._recording = False
        frames = self._frames
        self._frames = []
        if not frames:
            logger.warning("試合(match_id=%d)の動画クリップ用フレームが1枚も無いため、生成をスキップします", match_id)
            return
        thread = threading.Thread(
            target=self._encode_and_apply_retention, args=(frames, match_id), daemon=True
        )
        self._last_encode_thread = thread
        thread.start()

    def _encode_and_apply_retention(self, frames: list[np.ndarray], match_id: int) -> None:
        try:
            self._encode(frames, match_id)
        except Exception:
            logger.exception("試合(match_id=%d)の動画クリップ生成に失敗しました", match_id)
            return
        try:
            self._apply_retention()
        except Exception:
            logger.exception("動画クリップの保持数管理に失敗しました")

    def _encode(self, frames: list[np.ndarray], match_id: int) -> None:
        # Issue #399: resize_on_encode=Trueの場合はここで初めて拡大・オーバーレイを
        # 行うため、出力サイズは加工後のフレームから取る
        first_frame = self._encode_process(frames[0])
        height, width = first_frame.shape[:2]
        self._output_dir.mkdir(parents=True, exist_ok=True)
        output_path = self._output_dir / f"{match_id}.mp4"
        cmd = [
            self._ffmpeg_path,
            "-y",
            "-f",
            "rawvideo",
            "-pixel_format",
            "bgr24",
            "-video_size",
            f"{width}x{height}",
            "-framerate",
            str(self._target_sample_fps),
            "-i",
            "-",
            "-pix_fmt",
            "yuv420p",
            str(output_path),
        ]
        process = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        assert process.stdin is not None
        for index, frame in enumerate(frames):
            processed = first_frame if index == 0 else self._encode_process(frame)
            process.stdin.write(processed.tobytes())
        # Issue #350: ここでprocess.stdin.close()を自前で呼んでしまうと、直後の
        # communicate()(POSIX実装)が未クローズ前提でstdinをflushしようとして
        # `ValueError: flush of closed file`になる(Windowsでは表面化せず、
        # Linux/CI環境で実際に発生することを確認済み)。フレームの書き込みまでは
        # 自前で行い、flush・close・stdout/stderr読み取り・終了待ちはcommunicate()に
        # 任せる(input未指定で呼べば、書き込み済みのstdinをそのまま閉じてくれる)。
        _stdout, stderr = process.communicate()
        if process.returncode != 0:
            raise RuntimeError(f"ffmpegがエラー終了しました(returncode={process.returncode}): {stderr.decode(errors='replace')}")
        logger.info("試合(match_id=%d)の動画クリップを生成しました: %s(%d フレーム)", match_id, output_path, len(frames))

    def _apply_retention(self) -> None:
        """max_clips件を超えた分を古いものから削除する。ただし対応する試合が
        未確定(rank_before_ocrが非NULLかつrank_afterがNULL)のクリップは
        削除しない(Issue #389、モジュールdocstring参照)。

        Issue #381: 以前はファイル名の数字(match_id)の昇順を「古い」とみなしていたが、
        match_idはmatchesテーブルのAUTOINCREMENTのため、DBファイルを作り直す(または
        空にする)とmatch_idが1から振り直される。一方このフォルダ側は別のライフサイクル
        (DBを跨いで残り続ける)のため、DBリセット直後は「番号は小さいが実際には
        直前に作られたばかりの最新クリップ」が、DBリセット前の番号が大きい古いファイル
        より先に削除されてしまう不具合が実機で見つかった。ファイルの実際の更新日時
        (mtime)でソートすることで、match_idの採番がリセットされても常に実際に古い
        ファイルから削除されるようにする。
        """
        clip_files = sorted(
            (p for p in self._output_dir.glob("*.mp4") if p.stem.isdigit()),
            key=lambda p: p.stat().st_mtime,
        )
        excess = len(clip_files) - self._max_clips
        if excess <= 0:
            return

        conn = db.connect(self._db_path)
        try:
            pending_kept = 0
            for path in clip_files[:excess]:
                match_id = int(path.stem)
                row = db.fetch_match(conn, match_id)
                if row is not None and row["rank_before_ocr"] is not None and row["rank_after"] is None:
                    # Issue #389: rank_beforeチェーンを塞いでいる可能性がある未確定
                    # 試合のクリップは、max_clips件を超えていても削除せず残す
                    pending_kept += 1
                    continue
                path.unlink(missing_ok=True)
                logger.info("古い動画クリップを削除しました: %s", path)
        finally:
            conn.close()

        if pending_kept > PENDING_CLIP_WARNING_THRESHOLD:
            logger.warning(
                "未確定のため削除せず残っている動画クリップが%d件あります"
                "(閾値%d件超)。/rank-entryでの確定作業が溜まっていないか確認してください",
                pending_kept,
                PENDING_CLIP_WARNING_THRESHOLD,
            )
