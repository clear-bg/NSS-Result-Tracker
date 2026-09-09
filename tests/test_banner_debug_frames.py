"""Issue #423: 「試合終了」確認後のフレームを静止画で残す仕組みの検証。"""

from pathlib import Path

import cv2
import numpy as np
import pytest

from nss_tracker.banner_debug_frames import DEBUG_FRAME_ROI, BannerDebugFrameSaver


def _frame(value: int = 40) -> np.ndarray:
    return np.full((1080, 1920, 3), value, dtype=np.uint8)


def test_saves_nothing_before_start(tmp_path: Path):
    saver = BannerDebugFrameSaver(output_dir=tmp_path)

    assert not saver.is_active
    saver.observe(_frame(), now=0.0)

    assert list(tmp_path.glob("*.png")) == []


def test_saves_frames_at_the_configured_interval(tmp_path: Path):
    """間隔より短い呼び出しは保存せず、間隔を満たしたときだけ保存する。"""
    saver = BannerDebugFrameSaver(output_dir=tmp_path, interval_seconds=1.0)

    saver.start(match_no=3, now=0.0)
    saver.observe(_frame(), now=0.0)  # 1枚目(前回保存が無いので即保存)
    saver.observe(_frame(), now=0.5)  # 間隔不足
    saver.observe(_frame(), now=0.9)  # 間隔不足
    saver.observe(_frame(), now=1.0)  # 2枚目

    assert len(list(tmp_path.glob("*.png"))) == 2


def test_stops_saving_after_max_frames(tmp_path: Path):
    saver = BannerDebugFrameSaver(output_dir=tmp_path, interval_seconds=0.0, max_frames=3)

    saver.start(match_no=1, now=0.0)
    for step in range(10):
        saver.observe(_frame(), now=float(step))

    assert len(list(tmp_path.glob("*.png"))) == 3


def test_stop_ends_the_window(tmp_path: Path):
    saver = BannerDebugFrameSaver(output_dir=tmp_path, interval_seconds=0.0)

    saver.start(match_no=1, now=0.0)
    saver.observe(_frame(), now=0.0)
    saver.stop()
    assert not saver.is_active
    saver.observe(_frame(), now=1.0)

    assert len(list(tmp_path.glob("*.png"))) == 1


def test_filename_contains_match_number_and_elapsed_seconds(tmp_path: Path):
    """ログ(「n試合目」)と保存ファイルを突き合わせられるようにする。"""
    saver = BannerDebugFrameSaver(output_dir=tmp_path, interval_seconds=1.0)

    saver.start(match_no=7, now=100.0)
    saver.observe(_frame(), now=100.0)
    saver.observe(_frame(), now=102.5)

    names = sorted(path.name for path in tmp_path.glob("*.png"))
    assert names == ["match007_00_00.0s.png", "match007_01_02.5s.png"]


def test_saves_only_the_top_strip_that_classify_banner_reads(tmp_path: Path):
    """保存するのは画面上部の帯だけ(判定に使う領域は全て含む、モジュールdocstring参照)。"""
    saver = BannerDebugFrameSaver(output_dir=tmp_path)

    saver.start(match_no=1, now=0.0)
    saver.observe(_frame(), now=0.0)

    saved = cv2.imread(str(next(iter(tmp_path.glob("*.png")))))
    x1, y1, x2, y2 = DEBUG_FRAME_ROI
    assert saved.shape == (y2 - y1, x2 - x1, 3)


def test_saved_image_reproduces_the_same_banner_classification(tmp_path: Path):
    """保存した帯を黒画像に貼り戻せば、元のフレームと同じ判定結果を再現できる。

    これが成り立つため、切り出しだけを残しても閾値の再較正・回帰確認に使える
    (モジュールdocstring参照)。
    """
    from nss_tracker.detection.banner import banner_roi_stats

    # 実際の負けバナーに近い色(2026-09-08の専用部屋配信の実測: H101.5 S40.7 V84.3)を
    # 上部の帯だけに置き、下半分は無関係な色にしておく
    frame = np.full((1080, 1920, 3), 200, dtype=np.uint8)
    frame[0:260, :] = cv2.cvtColor(
        np.full((260, 1920, 3), (101, 41, 84), dtype=np.uint8), cv2.COLOR_HSV2BGR
    )
    saver = BannerDebugFrameSaver(output_dir=tmp_path)
    saver.start(match_no=1, now=0.0)
    saver.observe(frame, now=0.0)

    saved = cv2.imread(str(next(iter(tmp_path.glob("*.png")))))
    restored = np.zeros((1080, 1920, 3), dtype=np.uint8)
    x1, y1, x2, y2 = DEBUG_FRAME_ROI
    restored[y1:y2, x1:x2] = saved

    assert banner_roi_stats(restored) == banner_roi_stats(frame)


def test_retention_deletes_oldest_files_beyond_the_cap(tmp_path: Path):
    saver = BannerDebugFrameSaver(output_dir=tmp_path, interval_seconds=0.0, max_frames=10, max_files=4)

    saver.start(match_no=1, now=0.0)
    for step in range(10):
        saver.observe(_frame(), now=float(step))

    assert len(list(tmp_path.glob("*.png"))) == 4


def test_ignores_frames_smaller_than_the_roi(tmp_path: Path):
    """想定解像度より小さいフレームでも例外にせず、単に保存しない。"""
    saver = BannerDebugFrameSaver(output_dir=tmp_path)

    saver.start(match_no=1, now=0.0)
    saver.observe(np.zeros((10, 10, 3), dtype=np.uint8), now=0.0)

    assert list(tmp_path.glob("*.png")) == []


def test_write_failure_is_logged_but_does_not_raise(tmp_path: Path, monkeypatch, caplog):
    """保存は調査用の付加機能のため、失敗しても検知ループを止めない。"""
    saver = BannerDebugFrameSaver(output_dir=tmp_path)
    monkeypatch.setattr(
        "nss_tracker.banner_debug_frames.cv2.imwrite",
        lambda path, image: (_ for _ in ()).throw(OSError("書き込み失敗")),
    )

    saver.start(match_no=1, now=0.0)
    with caplog.at_level("WARNING", logger="nss_tracker.banner_debug_frames"):
        saver.observe(_frame(), now=0.0)

    assert "静止画を保存できませんでした" in caplog.text


@pytest.mark.parametrize("roi_name,roi", [("BANNER_ROIS", None), ("DRAW_TEXT_ROI", None)])
def test_debug_roi_covers_every_region_classify_banner_reads(roi_name, roi):
    """判定に使うROIがすべてDEBUG_FRAME_ROIに収まっていることを保証する。

    banner.py側のROIを動かした際に、保存範囲の更新漏れへ気づけるようにする。
    """
    from nss_tracker.detection.banner import BANNER_ROIS, DRAW_TEXT_ROI

    regions = list(BANNER_ROIS) if roi_name == "BANNER_ROIS" else [DRAW_TEXT_ROI]
    dx1, dy1, dx2, dy2 = DEBUG_FRAME_ROI
    for x1, y1, x2, y2 in regions:
        assert dx1 <= x1 and x2 <= dx2, f"{roi_name}の{(x1, y1, x2, y2)}が保存範囲の外(横方向)"
        assert dy1 <= y1 and y2 <= dy2, f"{roi_name}の{(x1, y1, x2, y2)}が保存範囲の外(縦方向)"
