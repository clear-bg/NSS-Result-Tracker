"""VS画面(マッチング完了直後)での自チーム/相手チームの色のサンプリング。

VS画面では自チーム・相手チームそれぞれの名前タグ背景が丸ごとチームカラー
(青系/ピンク系等)で塗りつぶされている。「青/赤/ピンク」のような固定色への
分類はせず、実際に描画されている色をそのまま平均RGBでサンプリングしてhex
文字列(例: "#64bde2")として返す(色閾値による分類ロジックを作らずに済み、
分類誤りのリスクも無いため。用途がWebダッシュボードでの装飾表示のみで、
勝敗判定等のロジックには使わないことからもこの方式で十分)。

サンプリング領域はvs_rank.pyと同じスロット0(画面手前、常に表示される選手)の
名前タグバーのうち、文字やランクバッジが重ならない右寄りの帯を使う。

Issue #144対応: 当初のROI(x幅250px×高さ9px、名前タグバーの下端寄りを横断する
細長い帯)は、実際には(1)名前文字のアンチエイリアス縁が上端付近にわずかに
かぶる、(2)バー自体が単色ではなく左右方向にグラデーションで塗られており
場所によって色が変わる、という2つの問題があることが実データで判明した(86番
`86_matching_with_rank_4v4_hdr_off.png`のピクセル値を直接確認して発覚)。
名前の長さに関わらず名前タグの丸い右端(グラデーションが収束し、文字も
被らない)まで届く位置に、幅20px×高さ18pxの小さな矩形を置くよう変更した。
86番・82番(`82_matching_with_rank_4v3_hdr_off.png`、別試合)の両方で標準偏差が
2未満(旧ROIは18〜72)の完全に均一な色になることを確認済み。
"""

import numpy as np

from nss_tracker.detection_config import get_detection_value

# (x1, y1, x2, y2)。名前タグバーの丸い右端付近、文字・グラデーションの影響を
# 受けない位置(モジュールdocstring参照)
MINE_ROI = get_detection_value("team_color", "MINE_ROI", (441, 868, 461, 886))
OPPONENT_ROI = get_detection_value("team_color", "OPPONENT_ROI", (1804, 868, 1824, 886))


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
