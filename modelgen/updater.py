"""Auto-update for an installed ModelGen, from the releases of its GitHub repository.

An installed copy (the installer leaves installed.json in its folder) asks GitHub for the latest
release when it starts and every few hours (updates: in config.yaml; the Settings page can switch
it off). Only the release's version is read: nothing about this PC or its user is sent. When a
newer one is there, the top bar offers it; installing downloads the release's ModelGen-Setup.exe,
checks it against the SHA-256 fingerprint published with it, starts it quietly and closes
ModelGen. The setup replaces the program files, keeps the person's models, library and settings,
and starts ModelGen again.

A source folder (no installed.json, such as the one ModelGen is developed in) never installs an
update: the page says it runs from the source folder.
"""

import hashlib
import json
import os
import re
import subprocess
import tempfile
import threading
import urllib.error
import urllib.request
from pathlib import Path

from . import __version__, config, db, pipeline, settings_store

ASSET = "ModelGen-Setup.exe"
SUMS = ASSET + ".sha256"
MARKER = "installed.json"
USER_AGENT = "ModelGen-updater"
EXIT_DELAY_S = 1.5  # time for the page to get its answer before ModelGen closes for the setup


class UpdateError(Exception):
    pass


_lock = threading.Lock()
_listeners = []
STATE = {"version": __version__, "latest": None, "available": False, "state": "idle", "checked_at": None,
         "error": None, "notes_url": None, "_setup_url": None, "_sums_url": None}


def repo():
    return str(config.get("updates", "repo", "ikverse/modelgen"))


def check_hours():
    return max(1.0, float(config.get("updates", "check_hours", 6)))


def installed():
    return (config.ROOT / MARKER).is_file()


def enabled():
    return bool(settings_store.load().get("auto_update", True))


def public_state():
    with _lock:
        out = {k: v for k, v in STATE.items() if not k.startswith("_")}
    out["installed"] = installed()
    out["enabled"] = enabled()
    return out


def subscribe(fn):
    """fn(public_state) is called, from any thread, whenever the update state changes."""
    _listeners.append(fn)


def unsubscribe(fn):
    if fn in _listeners:
        _listeners.remove(fn)


def _set(**changes):
    with _lock:
        STATE.update(changes)
    state = public_state()
    for fn in list(_listeners):
        try:
            fn(state)
        except Exception:  # a page that went away must not stop the update
            pass


def parse_version(text):
    """(1, 2, 3) from "v1.2.3" or "1.2.3"; None for anything else."""
    m = re.fullmatch(r"v?(\d+)\.(\d+)\.(\d+)", str(text or "").strip())
    return tuple(int(x) for x in m.groups()) if m else None


def _request(url, timeout=20):
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/vnd.github+json"})
    return urllib.request.urlopen(req, timeout=timeout)


def _fetch_json(url):
    with _request(url) as resp:
        return json.loads(resp.read().decode("utf-8"))


def check(fetch=None):
    """Ask GitHub for the latest release. Returns the public state."""
    fetch = fetch or _fetch_json
    _set(state="checking", error=None)
    try:
        release = fetch(f"https://api.github.com/repos/{repo()}/releases/latest")
    except urllib.error.HTTPError as exc:
        if exc.code == 404:  # the repository has no release yet
            _set(state="idle", latest=None, available=False, checked_at=db.now(), error=None)
            return public_state()
        _set(state="idle", checked_at=db.now(), error=f"GitHub answered {exc.code}")
        return public_state()
    except (OSError, ValueError) as exc:
        _set(state="idle", checked_at=db.now(), error=f"could not reach GitHub ({type(exc).__name__})")
        return public_state()
    tag = str(release.get("tag_name") or "")
    latest = parse_version(tag)
    assets = {a.get("name"): a.get("browser_download_url") for a in release.get("assets") or []}
    newer = bool(latest and latest > parse_version(__version__) and assets.get(ASSET) and assets.get(SUMS))
    _set(state="idle", latest=tag.lstrip("v") if latest else None, available=newer, checked_at=db.now(),
         error=None, notes_url=release.get("html_url"), _setup_url=assets.get(ASSET), _sums_url=assets.get(SUMS))
    return public_state()


def _download(url, dest):
    """Streams url to dest and returns its SHA-256."""
    digest = hashlib.sha256()
    tmp = dest.with_suffix(".part")
    with _request(url, timeout=60) as resp, open(tmp, "wb") as out:
        while True:
            chunk = resp.read(1 << 20)
            if not chunk:
                break
            digest.update(chunk)
            out.write(chunk)
    tmp.replace(dest)
    return digest.hexdigest()


def _expected_sum(url):
    with _request(url) as resp:
        text = resp.read().decode("utf-8", "replace")
    m = re.search(r"\b[0-9a-fA-F]{64}\b", text)
    if not m:
        raise UpdateError("the release has no readable SHA-256 fingerprint")
    return m.group(0).lower()


def _launch(setup):
    flags = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    subprocess.Popen([str(setup), "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART"], creationflags=flags,
                     close_fds=True)


def _exit_soon():
    threading.Timer(EXIT_DELAY_S, lambda: os._exit(0)).start()


def running_builds():
    return [p["name"] for p in db.list_projects() if pipeline.is_running(p["name"])]


def install(force=False, download=None, expected=None, launch=None, exit_soon=None):
    """Download, check and start the newer setup, then close ModelGen. With a build running and
    force False, nothing happens and the answer asks the page to confirm first. The caller stops
    the running builds (on the server's event loop) before calling with force True."""
    if not installed():
        raise UpdateError("This copy of ModelGen runs from its source folder, so it is not updated automatically.")
    with _lock:
        ready = STATE["available"] and STATE["_setup_url"] and STATE["_sums_url"]
        busy = STATE["state"] in ("downloading", "installing")
        latest, setup_url, sums_url = STATE["latest"], STATE["_setup_url"], STATE["_sums_url"]
    if busy:
        raise UpdateError("The update is already on its way.")
    if not ready:
        raise UpdateError("There is no newer version to install.")
    # jobs other programs queued (the /v1 API) count too: they would wait out the restart, so ask first
    running = running_builds() + [f"{j['client']}: {j['object']}" for j in db.queued_jobs()]
    if running and not force:
        return {"action": "confirm", "running": running}
    download, expected = download or _download, expected or _expected_sum
    folder = Path(tempfile.gettempdir()) / "modelgen-update"
    folder.mkdir(parents=True, exist_ok=True)
    setup = folder / f"ModelGen-Setup-{latest}.exe"
    _set(state="downloading", error=None)
    try:
        want = expected(sums_url)
        got = download(setup_url, setup)
        if got.lower() != want:
            setup.unlink(missing_ok=True)
            raise UpdateError("the downloaded setup does not match its fingerprint, so it was deleted")
        _set(state="installing")
        (launch or _launch)(setup)
    except UpdateError as exc:
        _set(state="idle", error=str(exc))
        raise
    except (OSError, ValueError) as exc:
        _set(state="idle", error=f"the download failed ({type(exc).__name__})")
        raise UpdateError(f"The download failed ({type(exc).__name__}). Try again later.") from exc
    (exit_soon or _exit_soon)()
    return {"action": "installing", "version": latest}


async def loop(sleep):
    """The background check of an installed copy: shortly after start, then every check_hours."""
    import asyncio
    await sleep(10)
    while True:
        if installed() and enabled():
            try:
                await asyncio.to_thread(check)
            except Exception:
                pass
        await sleep(check_hours() * 3600)
