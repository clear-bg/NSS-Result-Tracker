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

録画区間が異常に長引いた場合(暗転検知を逃した、プレイヤーが長時間離席した等)
に備え、`MAX_DURATION_SECONDS`(既定60秒)を超えたら暗転を待たずに強制的に
その時点までのフレームでクリップを確定する安全策を持つ。

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

## 保持数管理

直近`max_clips`件(既定3件、ユーザー確認済み)のみをディスクに保持し、
新しいクリップが出来るたびに古いものを削除する。ファイル名は
`{match_id}.mp4`とし、保持数の判定はファイル名(match_id、数値)の大小で行う
(mtimeより確実なため)。rank_afterの確定状況とは無関係に「直近3試合分」を
機械的に保持する(ユーザー確認済み、確定したら消すのではなく単純なローリング
ウィンドウ)。

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

TARGET_SAMPLE_FPS = 8.0
TARGET_WIDTH = 960
MAX_DURATION_SECONDS = 60.0
DEFAULT_MAX_CLIPS = 3
# Issue #312: ゲージのROI(実測290x32px程度)をどの幅まで拡大して見せるか
GAUGE_TARGET_WIDTH = 1160
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
    ) -> None:
        self._output_dir = output_dir
        self._max_clips = max_clips
        self._target_sample_fps = target_sample_fps
        self._target_width = target_width
        self._max_duration_seconds = max_duration_seconds
        self._ffmpeg_path = ffmpeg_path or imageio_ffmpeg.get_ffmpeg_exe()
        self._crop_roi = crop_roi
        self._overlay_fn = overlay_fn

        self._frames: list[np.ndarray] = []
        self._sample_interval = 1
        self._frame_counter = 0
        self._recording = False
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

    def add_frame(self, frame: np.ndarray) -> bool:
        """録画中でなければ何もしない。MAX_DURATION_SECONDS相当のフレーム数を
        超えた場合はTrueを返す(呼び出し側はこれを合図に強制的にfinish()すること)。

        Issue #312: crop_roi/overlay_fnによるフレーム加工で例外が起きても
        検知ループを止めないよう、この1フレーム分だけ読み捨ててログに残す
        (モジュールdocstring参照)。
        """
        if not self._recording:
            return False
        if self._frame_counter % self._sample_interval == 0:
            try:
                self._frames.append(self._process(frame))
            except Exception:
                logger.exception("動画クリップ用フレームの加工に失敗したため、このフレームを読み捨てます")
        self._frame_counter += 1
        elapsed_sampled_seconds = len(self._frames) / self._target_sample_fps
        return elapsed_sampled_seconds >= self._max_duration_seconds

    def _process(self, frame: np.ndarray) -> np.ndarray:
        if self._crop_roi is not None:
            x1, y1, x2, y2 = self._crop_roi
            frame = frame[y1:y2, x1:x2]
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
        height, width = frames[0].shape[:2]
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
        for frame in frames:
            process.stdin.write(frame.tobytes())
        process.stdin.close()
        _stdout, stderr = process.communicate()
        if process.returncode != 0:
            raise RuntimeError(f"ffmpegがエラー終了しました(returncode={process.returncode}): {stderr.decode(errors='replace')}")
        logger.info("試合(match_id=%d)の動画クリップを生成しました: %s(%d フレーム)", match_id, output_path, len(frames))

    def _apply_retention(self) -> None:
        clip_files = sorted(
            (p for p in self._output_dir.glob("*.mp4") if p.stem.isdigit()),
            key=lambda p: int(p.stem),
        )
        while len(clip_files) > self._max_clips:
            oldest = clip_files.pop(0)
            oldest.unlink(missing_ok=True)
            logger.info("古い動画クリップを削除しました: %s", oldest)
