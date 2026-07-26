"""VS画面(マッチング完了直後)での自チーム/相手チームの色のサンプリング。

VS画面では自チーム・相手チームそれぞれの名前タグ背景が丸ごとチームカラー
(青系/ピンク系等)で塗りつぶされている。「青/赤/ピンク」のような固定色への
分類はせず、実際に描画されている色をそのまま平均RGBでサンプリングしてhex
文字列(例: "#64bde2")として返す(色閾値による分類ロジックを作らずに済み、
分類誤りのリスクも無いため。用途がWebダッシュボードでの装飾表示のみで、
勝敗判定等のロジックには使わないことからもこの方式で十分)。

サンプリング領域はvs_rank.pyと同じスロット0(画面手前、常に表示される選手)の
名前タグバーのうち、文字やランクバッジが重ならない右寄りの帯を使う。
fixtures/screenshots(11/12番=青系、14/15番=ピンク系、いずれもVS画面)で実測し、
同じ色系統ならRGB各chの差が数値にして10未満に収まることを確認済み(自チーム側は
どの回も同じ色になるはずなので、これは色そのものの再現性を示す)。
"""

import numpy as np

from nss_tracker.detection_config import get_detection_value

# (x1, y1, x2, y2)。y方向はタグバーの下端寄り(文字の下端より下)の薄い帯
MINE_ROI = get_detection_value("team_color", "MINE_ROI", (150, 878, 400, 887))
OPPONENT_ROI = get_detection_value("team_color", "OPPONENT_ROI", (1515, 878, 1765, 887))


def _average_hex(frame: np.ndarray, roi: tuple[int, int, int, int]) -> str:
    x1, y1, x2, y2 = roi
    crop = frame[y1:y2, x1:x2]
    if crop.size == 0:
        # 想定解像度(1920x1080)より小さいフレームが渡された場合(テスト用の
        # ダミーフレーム等)にROIがフレーム範囲外になるとcropが空になり、
        # 平均計算がnanになってクラッシュする。実運用では起こらない想定だが、
        # 呼び出し側を落とさないためニュートラルな黒を返す
        return "#000000"
    b, g, r = crop.reshape(-1, 3).mean(axis=0)
    return f"#{int(round(r)):02x}{int(round(g)):02x}{int(round(b)):02x}"


def read_team_colors(frame: np.ndarray) -> tuple[str, str]:
    """VS画面のフレームから(自チームの色, 相手チームの色)をhex文字列で返す。"""
    return _average_hex(frame, MINE_ROI), _average_hex(frame, OPPONENT_ROI)
