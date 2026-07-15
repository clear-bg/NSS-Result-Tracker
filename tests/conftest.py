from pathlib import Path

import pytest

FIXTURES_DIR = Path(__file__).parent.parent / "fixtures" / "screenshots"

requires_fixtures = pytest.mark.skipif(
    not FIXTURES_DIR.is_dir(),
    reason="fixtures/screenshots が存在しません(.gitignore対象のためローカルにのみ配置)",
)


@pytest.fixture
def fixtures_dir() -> Path:
    return FIXTURES_DIR
