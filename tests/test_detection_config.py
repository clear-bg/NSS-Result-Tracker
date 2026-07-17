from pathlib import Path

from nss_tracker.detection_config import get_detection_value


def _write_toml(tmp_path: Path, content: str) -> Path:
    path = tmp_path / "detection.toml"
    path.write_text(content, encoding="utf-8")
    return path


def test_falls_back_to_default_when_file_missing(tmp_path):
    path = tmp_path / "does_not_exist.toml"
    assert get_detection_value("banner", "BANNER_ROI", (1, 2, 3, 4), path=path) == (1, 2, 3, 4)


def test_falls_back_to_default_when_section_missing(tmp_path):
    path = _write_toml(tmp_path, "[other_section]\nKEY = 1\n")
    assert get_detection_value("banner", "BANNER_ROI", (1, 2, 3, 4), path=path) == (1, 2, 3, 4)


def test_falls_back_to_default_when_key_missing(tmp_path):
    path = _write_toml(tmp_path, "[banner]\nOTHER_KEY = 1\n")
    assert get_detection_value("banner", "BANNER_ROI", (1, 2, 3, 4), path=path) == (1, 2, 3, 4)


def test_uses_overridden_tuple_value(tmp_path):
    path = _write_toml(tmp_path, "[banner]\nBANNER_ROI = [10, 20, 30, 40]\n")
    value = get_detection_value("banner", "BANNER_ROI", (1, 2, 3, 4), path=path)
    assert value == (10, 20, 30, 40)
    assert isinstance(value, tuple)


def test_uses_overridden_scalar_value(tmp_path):
    path = _write_toml(tmp_path, "[banner]\nWIN_SAT_MIN = 200\n")
    assert get_detection_value("banner", "WIN_SAT_MIN", 120, path=path) == 200


def test_different_sections_are_independent(tmp_path):
    path = _write_toml(tmp_path, "[banner]\nWIN_SAT_MIN = 200\n\n[goal]\nSAT_MIN = 50\n")
    assert get_detection_value("banner", "WIN_SAT_MIN", 120, path=path) == 200
    assert get_detection_value("goal", "SAT_MIN", 100, path=path) == 50
