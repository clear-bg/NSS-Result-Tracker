from pathlib import Path

import pytest

FIXTURES_DIR = Path(__file__).parent.parent / "fixtures" / "screenshots"
VIDEOS_DIR = Path(__file__).parent.parent / "fixtures" / "videos"

requires_fixtures = pytest.mark.skipif(
    not FIXTURES_DIR.is_dir(),
    reason="fixtures/screenshots が存在しません(.gitignore対象のためローカルにのみ配置)",
)

requires_video_fixtures = pytest.mark.skipif(
    not VIDEOS_DIR.is_dir(),
    reason="fixtures/videos が存在しません(.gitignore対象のためローカルにのみ配置)",
)


@pytest.fixture
def fixtures_dir() -> Path:
    return FIXTURES_DIR


@pytest.fixture
def videos_dir() -> Path:
    return VIDEOS_DIR
