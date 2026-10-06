"""OBS Virtual Cameraが停止中に流すプレースホルダー画像の検知(Issue #470)。

OBSの「仮想カメラ開始」を押し忘れたまま起動すると、試合が1つも記録されないまま
配信が終わってしまう。停止中の仮想カメラはDirectShowのデバイスとしては残り続け、
OBSのロゴとカメラ禁止アイコンが描かれた固定のプレースホルダー画像を通常どおりの
フレームレートで流し続ける(2026-10-03に実機で確認。ffmpegは正常に起動し、
120フレーム/2.55秒が届き、先頭と末尾のフレームの差分は0.0だった)。そのため
「フレームが届かない」では検知できず、届いた画像の中身で判定する。

判定は、プレースホルダー画像の中で色が一様な6箇所(ロゴ内側の黒・ロゴの白い縁・
背景の紺3箇所・カメラ禁止アイコンのグレー)の平均色が、すべて実測値と一致するか
どうかで行う。実測(OBS Virtual Camera経由のbgr24、1920x1080)では各領域の
標準偏差が0〜2.3しかなく、ほぼ単色で描かれている。ゲーム画面がこの6箇所すべてで
同時に一致することは事実上起こらない。

OBSのバージョンアップでプレースホルダー画像のデザインが変わった場合は、
この判定が常にFalseを返すようになる(=警告が出なくなるだけで、検知・記録には影響しない)。
その場合はscripts等で停止中の仮想カメラから1フレーム取得し、SAMPLESを測り直すこと。
"""

import numpy as np

from nss_tracker.detection_config import get_detection_value

# (x1, y1, x2, y2, B, G, R)。2026-10-03に停止中のOBS Virtual Cameraから取得した
# フレームでの実測値(括弧内は各領域のBGR標準偏差)
_DEFAULT_SAMPLES = (
    (860, 430, 890, 460, 24, 21, 24),  # ロゴ内側の黒(2.2, 1.9, 2.3)
    (945, 140, 975, 150, 255, 253, 255),  # ロゴの白い縁(0, 0, 0)
    (1600, 40, 1760, 100, 82, 40, 32),  # 背景右上の紺(0.7, 0.4, 0.7)
    (80, 420, 280, 520, 107, 46, 35),  # 背景左の青い帯(0, 0, 0)
    (160, 1000, 400, 1060, 48, 24, 25),  # 背景下部の暗い紺(0, 0.2, 0.3)
    (890, 860, 920, 900, 143, 141, 143),  # カメラ禁止アイコンのグレー(0, 0, 0)
)
PLACEHOLDER_SAMPLES: tuple[tuple[int, ...], ...] = tuple(
    tuple(sample) for sample in get_detection_value("virtual_camera", "PLACEHOLDER_SAMPLES", _DEFAULT_SAMPLES)
)
# 各領域の平均色が実測値から各チャンネルこれ以内であれば一致とみなす。
# 実測の標準偏差(最大2.3)に対して十分広く、映像の圧縮・色変換の揺らぎを吸収できる
PLACEHOLDER_COLOR_TOLERANCE = get_detection_value("virtual_camera", "PLACEHOLDER_COLOR_TOLERANCE", 15.0)


def is_virtual_camera_placeholder(frame: np.ndarray) -> bool:
    """フレームがOBS Virtual Camera停止中のプレースホルダー画像ならTrueを返す。

    想定解像度(1920x1080)より小さいフレームで領域が範囲外になる場合はFalseを返す
    (プレースホルダーかどうか判断できないため、警告を出さない側に倒す)。
    """
    for x1, y1, x2, y2, b, g, r in PLACEHOLDER_SAMPLES:
        crop = frame[y1:y2, x1:x2]
        if crop.size == 0:
            return False
        mean = crop.reshape(-1, 3).mean(axis=0)
        if np.abs(mean - np.array((b, g, r), dtype=float)).max() > PLACEHOLDER_COLOR_TOLERANCE:
            return False
    return True
