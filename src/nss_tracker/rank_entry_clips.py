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
"""

import logging
import subprocess
import threading
from pathlib import Path
from typing import Optional

import cv2
import imageio_ffmpeg
import numpy as np

logger = logging.getLogger("nss_tracker.rank_entry_clips")

# main.py(生成側)・web/server.py(配信側)の両方から参照する、クリップの
# 保存先ディレクトリ。tmp/配下は.gitignore対象かつ、ユーザーが手動キャプチャした
# 動画等を置く場所でもあるため、専用のサブディレクトリに分ける
DEFAULT_CLIPS_DIR = Path("tmp/rank_entry_clips")

TARGET_SAMPLE_FPS = 8.0
TARGET_WIDTH = 960
MAX_DURATION_SECONDS = 60.0
DEFAULT_MAX_CLIPS = 3


class RankEntryClipRecorder:
    """試合終了区間のフレームをバッファし、区間終了時にmp4クリップを生成する。

    呼び出し側(main.py)の想定する使い方:
        recorder.start(source_fps)          # "watching" -> "tracking_rank"遷移時
        recorder.add_frame(frame)            # 録画中は毎フレーム呼ぶ(内部で間引く)
        recorder.finish(match_id)            # is_full_blackout(frame)がTrueになった時点
    """

    def __init__(
        self,
        output_dir: Path,
        max_clips: int = DEFAULT_MAX_CLIPS,
        target_sample_fps: float = TARGET_SAMPLE_FPS,
        target_width: int = TARGET_WIDTH,
        max_duration_seconds: float = MAX_DURATION_SECONDS,
        ffmpeg_path: Optional[str] = None,
    ) -> None:
        self._output_dir = output_dir
        self._max_clips = max_clips
        self._target_sample_fps = target_sample_fps
        self._target_width = target_width
        self._max_duration_seconds = max_duration_seconds
        self._ffmpeg_path = ffmpeg_path or imageio_ffmpeg.get_ffmpeg_exe()

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
        """
        if not self._recording:
            return False
        if self._frame_counter % self._sample_interval == 0:
            self._frames.append(self._resize(frame))
        self._frame_counter += 1
        elapsed_sampled_seconds = len(self._frames) / self._target_sample_fps
        return elapsed_sampled_seconds >= self._max_duration_seconds

    def _resize(self, frame: np.ndarray) -> np.ndarray:
        height, width = frame.shape[:2]
        if width <= self._target_width:
            return frame.copy()
        target_height = round(height * self._target_width / width)
        return cv2.resize(frame, (self._target_width, target_height), interpolation=cv2.INTER_AREA)

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
