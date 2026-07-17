import re
import sys
from pathlib import Path

import pytest

# main.pyはプロジェクトルート直下(パッケージ外)にあるため、
# tests/test_main.pyから`import main`できるようにルートをsys.pathへ追加する
sys.path.insert(0, str(Path(__file__).parent.parent))

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

# docs/screen_states.md記載の命名規則(`{番号}_{説明}.png`)に沿ったファイルのみを
# 対象にする。fixtures/screenshots/はローカル環境で手動管理しており、この規則に
# 沿わないファイル(OS標準のスクリーンショットツール等が別目的で保存したもの)が
# 混ざることがあるため、「配下の全pngを無条件にfixtureとして扱う」テストでは
# このヘルパーで事前にフィルタする
_FIXTURE_NAME_PATTERN = re.compile(r"^\d+_")


def list_screenshot_fixtures(fixtures_dir: Path) -> list[Path]:
    return sorted(p for p in fixtures_dir.glob("*.png") if _FIXTURE_NAME_PATTERN.match(p.name))


@pytest.fixture
def fixtures_dir() -> Path:
    return FIXTURES_DIR


@pytest.fixture
def videos_dir() -> Path:
    return VIDEOS_DIR
