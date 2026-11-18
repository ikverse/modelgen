"""Auto-update for an installed ModelGen, from the releases of its GitHub repository.

An installed copy (the installer leaves installed.json in its folder) asks GitHub for the latest
release when it starts and every few hours (updates: in config.yaml; the Settings page can switch
it off). Only the release's version is read: nothing about this PC or its user is sent. When a
newer one is there, the top bar offers it; installing downloads the release's ModelGen-Setup.exe
and its signature (ModelGen-Setup.exe.sig), only from this repository's release downloads, and
checks the signature against the public key built into ModelGen (update_key.py). The signature
covers the version and the setup's SHA-256, so neither a changed file nor an older signed setup
under a newer version gets through. Then it starts the setup quietly and closes ModelGen. The setup replaces the program files, keeps the person's models, library and settings,
and starts ModelGen again.

A source folder (no installed.json, such as the one ModelGen is developed in) never installs an
update: the page says it runs from the source folder.
"""

import base64
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
from .update_key import PUBLIC_KEY

ASSET = "ModelGen-Setup.exe"
SUMS = ASSET + ".sha256"  # still published, for copies older than 1.1.0 and for people checking by hand
SIG = ASSET + ".sig"
MARKER = "installed.json"
USER_AGENT = "ModelGen-updater"
EXIT_DELAY_S = 1.5  # time for the page to get its answer before ModelGen closes for the setup


class UpdateError(Exception):
    pass


_lock = threading.Lock()
_listeners = []
STATE = {"version": __version__, "latest": None, "available": False, "state": "idle", "checked_at": None,
         "error": None, "notes_url": None, "_setup_url": None, "_sig_url": None}


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
    setup, sig = assets.get(ASSET), assets.get(SIG)
    # only a signed setup, from this repository's own release downloads, is ever offered
    newer = bool(latest and latest > parse_version(__version__) and _ours(setup) and _ours(sig))
    _set(state="idle", latest=tag.lstrip("v") if latest else None, available=newer, checked_at=db.now(),
         error=None, notes_url=release.get("html_url"), _setup_url=setup if newer else None,
         _sig_url=sig if newer else None)
    return public_state()


def _ours(url):
    """Whether url is a download from this repository's GitHub releases."""
    return bool(url) and str(url).startswith(f"https://github.com/{repo()}/releases/download/")


# --- signatures ---------------------------------------------------------------------------

def signed_message(version, sha256_hex):
    """What a release signature covers: the version and the setup's SHA-256."""
    return f"ModelGen {str(version).lstrip('v')}\n{sha256_hex.lower()}".encode("utf-8")


def sign(setup, version, private_key):
    """The signature file's text for this setup (installer\\sign_release.py, with the owner's private key)."""
    digest = hashlib.sha256(Path(setup).read_bytes()).hexdigest()
    return base64.b64encode(private_key.sign(signed_message(version, digest))).decode("ascii") + "\n"


def verify(public_key_b64, version, sha256_hex, signature_text):
    """Whether the signature is the key's, over this version and SHA-256. Never raises."""
    try:
        from cryptography.exceptions import InvalidSignature
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
        key = Ed25519PublicKey.from_public_bytes(base64.b64decode(public_key_b64))
        key.verify(base64.b64decode(str(signature_text).strip()), signed_message(version, sha256_hex))
        return True
    except (InvalidSignature, ValueError, TypeError, ImportError):
        return False


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


def _signature(url):
    """The release's signature file, as text (a few hundred bytes at most)."""
    with _request(url) as resp:
        return resp.read(500).decode("ascii", "replace")


def _launch(setup):
    flags = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    subprocess.Popen([str(setup), "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART"], creationflags=flags,
                     close_fds=True)


def _exit_soon():
    threading.Timer(EXIT_DELAY_S, lambda: os._exit(0)).start()


def running_builds():
    return [p["name"] for p in db.list_projects() if pipeline.is_running(p["name"])]


def install(force=False, download=None, signature=None, launch=None, exit_soon=None, public_key=None):
    """Download, check the signature of, and start the newer setup, then close ModelGen. With a
    build running and force False, nothing happens and the answer asks the page to confirm first.
    The caller stops the running builds (on the server's event loop) before calling with force True."""
    if not installed():
        raise UpdateError("This copy of ModelGen runs from its source folder, so it is not updated automatically.")
    with _lock:
        ready = STATE["available"] and STATE["_setup_url"] and STATE["_sig_url"]
        busy = STATE["state"] in ("downloading", "installing")
        latest, setup_url, sig_url = STATE["latest"], STATE["_setup_url"], STATE["_sig_url"]
    if busy:
        raise UpdateError("The update is already on its way.")
    if not ready:
        raise UpdateError("There is no newer version to install.")
    # jobs other programs queued (the /v1 API) count too: they would wait out the restart, so ask first
    running = running_builds() + [f"{j['client']}: {j['object']}" for j in db.queued_jobs()]
    if running and not force:
        return {"action": "confirm", "running": running}
    download, signature = download or _download, signature or _signature
    folder = Path(tempfile.gettempdir()) / "modelgen-update"
    folder.mkdir(parents=True, exist_ok=True)
    setup = folder / f"ModelGen-Setup-{latest}.exe"
    _set(state="downloading", error=None)
    try:
        signed = signature(sig_url)
        got = download(setup_url, setup)
        if not verify(public_key or PUBLIC_KEY, latest, got, signed):
            setup.unlink(missing_ok=True)
            raise UpdateError("the downloaded setup is not signed with ModelGen's key, so it was deleted")
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
