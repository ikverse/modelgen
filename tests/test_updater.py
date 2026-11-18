"""Auto-update, with GitHub, the download and the setup all faked: nothing leaves this PC. The
signatures are real Ed25519 signatures, made with a key made for the tests."""

import base64
import hashlib
import urllib.error

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fastapi.testclient import TestClient

from modelgen import __version__, config, server, settings_store, updater

KEY = Ed25519PrivateKey.generate()
PUBLIC = base64.b64encode(KEY.public_key().public_bytes(serialization.Encoding.Raw,
                                                        serialization.PublicFormat.Raw)).decode()


def release(tag, assets=(updater.ASSET, updater.SUMS, updater.SIG), host=None):
    host = host or f"https://github.com/{updater.repo()}/releases/download/{tag}"
    return {"tag_name": tag, "html_url": f"https://github.com/x/y/releases/{tag}",
            "assets": [{"name": n, "browser_download_url": f"{host}/{n}"} for n in assets]}


def signed(body, version, key=KEY):
    return base64.b64encode(key.sign(updater.signed_message(version, hashlib.sha256(body).hexdigest()))).decode()


@pytest.fixture(autouse=True)
def fresh_state(monkeypatch):
    monkeypatch.setitem(updater.STATE, "latest", None)
    monkeypatch.setitem(updater.STATE, "available", False)
    monkeypatch.setitem(updater.STATE, "state", "idle")
    monkeypatch.setitem(updater.STATE, "error", None)
    monkeypatch.setitem(updater.STATE, "_setup_url", None)
    monkeypatch.setitem(updater.STATE, "_sig_url", None)


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


def test_a_newer_release_with_its_setup_and_signature_is_offered():
    state = updater.check(fetch=lambda url: release(newer()))
    assert state["available"] and state["latest"] == newer()[1:] and state["error"] is None


def test_the_same_version_or_a_release_without_its_files_is_not_offered():
    assert not updater.check(fetch=lambda url: release(f"v{__version__}"))["available"]
    assert not updater.check(fetch=lambda url: release(newer(), assets=(updater.ASSET,)))["available"]
    unsigned = (updater.ASSET, updater.SUMS)  # a fingerprint alone is not enough any more
    assert not updater.check(fetch=lambda url: release(newer(), assets=unsigned))["available"]


def test_files_from_anywhere_but_this_repositorys_releases_are_not_offered():
    elsewhere = "https://example.invalid/releases/download/x"
    assert not updater.check(fetch=lambda url: release(newer(), host=elsewhere))["available"]
    other_repo = "https://github.com/someone/else/releases/download/x"
    assert not updater.check(fetch=lambda url: release(newer(), host=other_repo))["available"]


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


def downloader(body, kept=None):
    def download(url, dest):
        dest.write_bytes(body)
        if kept is not None:
            kept.append(dest)
        return hashlib.sha256(body).hexdigest()
    return download


def test_installing_checks_the_signature_then_starts_the_setup_and_closes(installed):
    updater.check(fetch=lambda url: release(newer()))
    body = b"the setup"
    started, closed = [], []
    out = updater.install(download=downloader(body), signature=lambda url: signed(body, newer()),
                          public_key=PUBLIC, launch=started.append, exit_soon=lambda: closed.append(True))
    assert out == {"action": "installing", "version": newer()[1:]}
    assert started and started[0].read_bytes() == body and closed == [True]


@pytest.mark.parametrize("why, sig", [
    ("a tampered setup", lambda: signed(b"the real setup", newer())),
    ("another key", lambda: signed(b"tampered", newer(), key=Ed25519PrivateKey.generate())),
    ("an older version's signature", lambda: signed(b"tampered", f"v{__version__}")),
    ("no signature at all", lambda: "not a signature"),
])
def test_a_setup_without_a_good_signature_is_deleted_and_not_started(installed, why, sig):
    updater.check(fetch=lambda url: release(newer()))
    kept = []
    with pytest.raises(updater.UpdateError, match="not signed with ModelGen's key"):
        updater.install(download=downloader(b"tampered", kept), signature=lambda url: sig(), public_key=PUBLIC,
                        launch=lambda p: pytest.fail("must not start a setup"), exit_soon=lambda: None)
    assert not kept[0].exists() and "not signed" in updater.public_state()["error"]


def test_the_built_in_key_is_a_real_ed25519_public_key():
    from modelgen.update_key import PUBLIC_KEY
    assert len(base64.b64decode(PUBLIC_KEY)) == 32
    assert not updater.verify(PUBLIC_KEY, "1.0.0", "0" * 64, signed(b"x", "1.0.0"))  # the test key is not it


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
