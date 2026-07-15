from nss_tracker import capture, detection, state, storage

from conftest import requires_fixtures


def test_package_importable():
    assert capture is not None
    assert detection is not None
    assert state is not None
    assert storage is not None


@requires_fixtures
def test_fixtures_dir_has_lobby_screenshot(fixtures_dir):
    assert (fixtures_dir / "00_lobby.png").is_file()
