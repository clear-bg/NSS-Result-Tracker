"""結果バナー検知の調査用に、「試合終了」確認後のフレームを静止画として保存する(Issue #423)。

2026-09-08の専用部屋配信で、18試合中8試合(すべて負け)が丸ごと記録されなかった。
`detection/match_end.py`の「試合終了」OCR確認は8試合すべてで成功しているのに、
`classify_banner()`が一度もwin/lose/drawを返さないまま次のVS画面が来て破棄されていた。

原因究明の妨げになったのは、**Pythonが実際に読んでいるOBS Virtual Camera経由の
フレームがどこにも残っていない**ことだった。OBSローカル録画(mkv)から同じ区間を
`classify_banner()`に食わせると5〜6.5秒間ちゃんと`"lose"`を返すため、Virtual Camera
経由の映像との色味の差が疑わしい(Issue #373で`DRAW_TEXT_VAL_MIN`を140→115へ
再較正したのと同種の問題)が、それを裏付ける実測値が無かった。

`clips/rank_entry_clips/`(Issue #307)は`main.py`が`watching -> tracking_rank`の
遷移で録画を開始する仕組みのため、この不具合のケースでは**構造的に1本も残らない**
(結果バナーが確定しないとその遷移自体が起こらず、さらに専用部屋はランクを賭けない
試合なので二重に対象外)。そのため別の保存経路としてこのモジュールを用意した。

保存するもの:

- **画面上部の帯だけ**(`DEBUG_FRAME_ROI`、フル解像度・可逆のPNG)。`classify_banner()`が
  読む`BANNER_ROIS`(y5〜149)と`DRAW_TEXT_ROI`(y45〜250)はすべてこの範囲に収まるため、
  黒画像に貼り戻せば`classify_banner()`に渡して同じ判定結果を再現できる。フレーム全体を
  1920x1080のPNGで残すと1枚2〜3MBになるのに対し、この切り出しなら1枚0.5MB程度で済む
- 「試合終了」OCR確認から`MAX_FRAMES`枚まで、`SAVE_INTERVAL_SECONDS`間隔で保存する。
  結果バナーが出るのは実測で「試合終了」確認の5〜8秒後、表示が消えるのがその5〜6.5秒後
  なので、既定値(1秒間隔・12枚)なら表示前・表示中・表示後がすべて残る

縮小せずフル解像度で保存するのは、この不具合が閾値の境界(実測H=101.50に対し上限103、
S=40.73に対し下限35)で起きており、縮小によるわずかな色の変化が判断を誤らせうるため。
非可逆圧縮(JPEG等)を使わないのも同じ理由。

保存先の`clips/`は`.gitignore`対象のため、結果バナー画面に写る他プレイヤーの名前が
誤ってコミットされることはない(`fixtures/`と同じ扱い)。

**この仕組みは閾値の再較正が終わるまでの調査用。** 再較正後も残すかどうかは、
Issue #423の対応が完了した時点で改めて判断する。
"""

import logging
from pathlib import Path
from typing import Optional

import cv2
import numpy as np

logger = logging.getLogger("nss_tracker.banner_debug_frames")

# 保存先。clips/はrank_entry_clips(Issue #342)と同じく.gitignore対象のため、
# 結果バナー画面に写る他プレイヤーの名前が誤ってコミットされることはない
BANNER_DEBUG_FRAMES_DIR = Path("clips/banner_debug_frames")

# 切り出す領域(x1, y1, x2, y2)。detection/banner.pyのBANNER_ROIS(最大y=149)と
# DRAW_TEXT_ROI(最大y=250)の両方を含む、画面上部の全幅の帯。判定に使う領域を
# すべて含みつつ、フレーム全体を保存するより1枚あたりのサイズを小さく抑えられる
DEBUG_FRAME_ROI = (0, 0, 1920, 260)
# 1回の「試合終了」確認あたりに保存する最大枚数と、保存の間隔(秒)。
# 結果バナーは「試合終了」確認の5〜8秒後に出て5〜6.5秒表示されるため、
# 1秒間隔で12枚あれば表示前・表示中・表示後がすべて残る
MAX_FRAMES = 12
SAVE_INTERVAL_SECONDS = 1.0
# 保存先に残す最大ファイル数。1枚0.5MB程度・1試合あたり最大12枚のため、
# 240枚(=20試合分)でも120MB程度に収まる
MAX_FILES = 240


class BannerDebugFrameSaver:
    """「試合終了」確認後のフレームを、間隔を空けて静止画として保存する。

    `main.py`が`MatchStateMachine.match_end_seen`の立ち上がりで`start()`を呼び、
    その区間の毎フレームで`observe()`を呼ぶ。`observe()`は間隔・枚数の上限を
    自分で管理するため、呼び出し側は条件を持たない。

    保存は配信演出でも検知でもない調査用の付加機能のため、書き込みに失敗しても
    WARNINGログを出すだけで例外を伝播させない(`obs_control.ObsSceneController`と
    同じ考え方)。検知ループを止める理由にはならない。
    """

    def __init__(
        self,
        output_dir: Path,
        roi: tuple[int, int, int, int] = DEBUG_FRAME_ROI,
        max_frames: int = MAX_FRAMES,
        interval_seconds: float = SAVE_INTERVAL_SECONDS,
        max_files: int = MAX_FILES,
    ) -> None:
        self._output_dir = output_dir
        self._roi = roi
        self._max_frames = max_frames
        self._interval_seconds = interval_seconds
        self._max_files = max_files
        self._match_no: Optional[int] = None
        self._saved_count = 0
        self._last_saved_at: Optional[float] = None
        self._started_at: Optional[float] = None

    @property
    def is_active(self) -> bool:
        return self._match_no is not None

    def start(self, match_no: int, now: float) -> None:
        """「試合終了」を確認した時点で呼ぶ。この区間の保存枚数・間隔をリセットする。"""
        self._match_no = match_no
        self._saved_count = 0
        self._last_saved_at = None
        self._started_at = now

    def stop(self) -> None:
        """結果バナーが確定した(または破棄された)時点で呼ぶ。"""
        self._match_no = None

    def observe(self, frame: np.ndarray, now: float) -> None:
        """区間中の毎フレームで呼ぶ。間隔・枚数の上限を満たす場合だけ保存する。"""
        if self._match_no is None or self._saved_count >= self._max_frames:
            return
        if self._last_saved_at is not None and now - self._last_saved_at < self._interval_seconds:
            return
        x1, y1, x2, y2 = self._roi
        crop = frame[y1:y2, x1:x2]
        if crop.shape[:2] != (y2 - y1, x2 - x1):
            # 想定解像度(1920x1080)より小さいフレーム(テスト用のダミー等)。numpyの
            # スライスは範囲外を切り詰めるだけで例外にならないため、空かどうかではなく
            # 切り出せた大きさで判定する(切れた画像を保存しても調査に使えないため)
            return
        elapsed = now - self._started_at if self._started_at is not None else 0.0
        path = self._output_dir / f"match{self._match_no:03d}_{self._saved_count:02d}_{elapsed:04.1f}s.png"
        try:
            self._output_dir.mkdir(parents=True, exist_ok=True)
            if not cv2.imwrite(str(path), crop):
                raise OSError(f"cv2.imwriteが失敗しました: {path}")
        except (OSError, cv2.error) as exc:
            logger.warning("バナー調査用の静止画を保存できませんでした: %s (%s)", path, exc)
            return
        self._saved_count += 1
        self._last_saved_at = now
        self._apply_retention()

    def _apply_retention(self) -> None:
        """保存先のファイル数が上限を超えたら、更新時刻の古いものから削除する。

        rank_entry_clips._apply_retention()と同じ考え方。こちらは「未確定なら
        消さない」といった例外を持たない(調査用の使い捨てのため)。
        """
        try:
            files = sorted(self._output_dir.glob("*.png"), key=lambda path: path.stat().st_mtime)
            for path in files[: max(0, len(files) - self._max_files)]:
                path.unlink()
        except OSError as exc:
            logger.warning("バナー調査用の静止画の整理に失敗しました: %s", exc)
