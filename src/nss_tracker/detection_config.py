"""detection/配下の各モジュールが使うROI・色閾値をconfig/detection.tomlから読み込む。

config/detection.toml(git追跡対象、デフォルト値入り)にモジュールごとのテーブル
([banner]・[rank_ocr]・[league_change]・[goal])としてキーを持たせる。fixture実測
根拠のコメントは各detectionモジュールのPython定数側に残し、このモジュールは値の
読み込みだけを担当する。ファイルが存在しない、またはテーブル・キーが無い場合は
呼び出し側が渡したデフォルト値(=元々のPython定数値)にフォールバックする。
"""

import tomllib
from functools import lru_cache
from pathlib import Path
from typing import TypeVar

DETECTION_CONFIG_PATH = Path("config/detection.toml")

_T = TypeVar("_T")


@lru_cache(maxsize=None)
def _load(path: Path) -> dict:
    if not path.is_file():
        return {}
    with path.open("rb") as f:
        return tomllib.load(f)


def get_detection_value(section: str, key: str, default: _T, path: Path = DETECTION_CONFIG_PATH) -> _T:
    """config/detection.tomlの`[section]`テーブルから`key`を読み取る。

    無ければ(ファイル自体が無い場合を含む)defaultを返す。defaultがtupleの場合、
    TOML側は配列として書かれる(例: [1300, 5, 1750, 35])ため、tupleへ変換して返す。
    """
    value = _load(path).get(section, {}).get(key, default)
    if isinstance(default, tuple) and isinstance(value, list):
        return tuple(value)
    return value
