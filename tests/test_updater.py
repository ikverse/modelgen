"""Auto-update, with GitHub, the download and the setup all faked: nothing leaves this PC."""

import hashlib
import urllib.error

import pytest
from fastapi.testclient import TestClient

from modelgen import __version__, config, server, settings_store, updater


def release(tag, assets=(updater.ASSET, updater.SUMS)):
    return {"tag_name": tag, "html_url": f"https://github.com/x/y/releases/{tag}",
            "assets": [{"name": n, "browser_download_url": f"https://example.invalid/{n}"} for n in assets]}


@pytest.fixture(autouse=True)
def fresh_state(monkeypatch):
    monkeypatch.setitem(updater.STATE, "latest", None)
    monkeypatch.setitem(updater.STATE, "available", False)
    monkeypatch.setitem(updater.STATE, "state", "idle")
    monkeypatch.setitem(updater.STATE, "error", None)
    monkeypatch.setitem(updater.STATE, "_setup_url", None)
    monkeypatch.setitem(updater.STATE, "_sums_url", None)


@pytest.fixture
def installed(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "ROOT", tmp_path)
    (tmp_path / updater.MARKER).write_text("{}", encoding="utf-8")
    return tmp_path


def newer():
    major, minor, patch = updater.parse_version(__version__)
    return f"v{major}.{minor}.{patch + 1}"


def test_versions_compare_as_numbers():
    assert updater.parse_version("v1.10.0") > updater.parse_version("1.9.3")
    assert updater.parse_version("nightly") is None


def test_a_newer_release_with_its_setup_and_fingerprint_is_offered():
    state = updater.check(fetch=lambda url: release(newer()))
    assert state["available"] and state["latest"] == newer()[1:] and state["error"] is None


def test_the_same_version_or_a_release_without_its_files_is_not_offered():
    assert not updater.check(fetch=lambda url: release(f"v{__version__}"))["available"]
    assert not updater.check(fetch=lambda url: release(newer(), assets=(updater.ASSET,)))["available"]


def test_no_release_yet_is_not_an_error():
    def none(url):
        raise urllib.error.HTTPError(url, 404, "Not Found", {}, None)
    state = updater.check(fetch=none)
    assert not state["available"] and state["error"] is None


def test_github_out_of_reach_is_shown_not_raised():
    def offline(url):
        raise OSError("no network")
    assert "could not reach GitHub" in updater.check(fetch=offline)["error"]


def test_only_the_version_is_asked_for_from_the_named_repository(monkeypatch):
    asked = []
    updater.check(fetch=lambda url: asked.append(url) or release(newer()))
    assert asked == [f"https://api.github.com/repos/{updater.repo()}/releases/latest"]


def test_a_source_folder_never_installs_an_update(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "ROOT", tmp_path)  # no installed.json
    updater.check(fetch=lambda url: release(newer()))
    with pytest.raises(updater.UpdateError, match="source folder"):
        updater.install(launch=lambda p: pytest.fail("must not start a setup"))


def test_installing_checks_the_fingerprint_then_starts_the_setup_and_closes(installed):
    updater.check(fetch=lambda url: release(newer()))
    body = b"the setup"
    started, closed = [], []

    def download(url, dest):
        dest.write_bytes(body)
        return hashlib.sha256(body).hexdigest()

    out = updater.install(download=download, expected=lambda url: hashlib.sha256(body).hexdigest(),
                          launch=started.append, exit_soon=lambda: closed.append(True))
    assert out == {"action": "installing", "version": newer()[1:]}
    assert started and started[0].read_bytes() == body and closed == [True]


def test_a_setup_that_does_not_match_its_fingerprint_is_deleted_and_not_started(installed):
    updater.check(fetch=lambda url: release(newer()))
    kept = []

    def download(url, dest):
        dest.write_bytes(b"tampered")
        kept.append(dest)
        return hashlib.sha256(b"tampered").hexdigest()

    with pytest.raises(updater.UpdateError, match="fingerprint"):
        updater.install(download=download, expected=lambda url: "0" * 64,
                        launch=lambda p: pytest.fail("must not start a setup"), exit_soon=lambda: None)
    assert not kept[0].exists() and "fingerprint" in updater.public_state()["error"]


def test_a_running_build_asks_first(installed, monkeypatch):
    updater.check(fetch=lambda url: release(newer()))
    monkeypatch.setattr(updater, "running_builds", lambda: ["oak-stool"])
    out = updater.install(launch=lambda p: pytest.fail("must not start yet"))
    assert out == {"action": "confirm", "running": ["oak-stool"]}


def test_the_switch_on_the_settings_page_is_saved():
    assert settings_store.load()["auto_update"] is True
    assert settings_store.save({"auto_update": False})["auto_update"] is False
    with pytest.raises(ValueError):
        settings_store.save({"auto_update": "yes"})


def test_the_page_can_read_the_update_state_and_a_source_folder_refuses_to_install():
    with TestClient(server.app) as c:
        state = c.get("/api/update").json()
        assert state["version"] == __version__ and state["installed"] is False
        assert c.post("/api/update/install", json={}).status_code == 409
