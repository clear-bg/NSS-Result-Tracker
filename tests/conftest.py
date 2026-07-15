from pathlib import Path

import pytest

FIXTURES_DIR = Path(__file__).parent.parent / "fixtures" / "screenshots"
VIDEOS_DIR = Path(__file__).parent.parent / "fixtures" / "videos"

requires_fixtures = pytest.mark.skipif(
    not any(FIXTURES_DIR.glob("*.png")),
    reason="fixtures/screenshots に画像が存在しません(.gitignore対象のためローカルにのみ配置。"
    ".gitkeepのみのCI環境ではこのテストはskipされる想定)",
)

requires_video_fixtures = pytest.mark.skipif(
    not any(VIDEOS_DIR.glob("*.mp4")),
    reason="fixtures/videos に動画が存在しません(.gitignore対象のためローカルにのみ配置。"
    "metadata.jsonのみのCI環境ではこのテストはskipされる想定)",
)


@pytest.fixture
def fixtures_dir() -> Path:
    return FIXTURES_DIR


@pytest.fixture
def videos_dir() -> Path:
    return VIDEOS_DIR
