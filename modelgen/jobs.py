"""Jobs from other programs: the work behind the /v1 API (modelgen\\api_v1.py, documented in API.md).

Another program on this PC (the Village, first) asks for an object and later picks up its files.
Each request is a job in the database. One worker builds the jobs one at a time, oldest first, and
waits while any other build runs (one the person started on the page, too). A job's model is an
ordinary project, marked with the program that asked for it, and it runs unattended: no agent waits
for an answer, a spending limit stops it instead of asking, no chat report is written, and the page
never jumps to it. After each version the worker builds the next with the critic's changes until a
version passes or the job's version count is used up, then accepts the best one. When the Claude
usage limit is reached the job waits and the whole queue pauses, trying again every few minutes.
"""

import asyncio
import base64
import json
import mimetypes
import re
import secrets
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path

from . import config, db, pipeline, runner, uploads
from .tools import agent_tools as at
from .tools import asset_catalog, blender, mechanism
from .tools import design as design_tools

OUTPUTS = ("game", "full", "views", "motion", "exploded", "cutaway")
SEQUENCES = ("motion", "exploded", "cutaway")  # picture sequences of a moving model
CUTS = ("auto", "top", "side", "front")
STYLES = ("realistic", "flat", "explainer")
CAMERAS = ("persp", "ortho")
ANGLES = ("front", "front34", "side")
MOTION_TIMEOUT_S = 1800
CLIENT = re.compile(r"^[a-z0-9][a-z0-9_-]{0,39}$")
USAGE_RETRY_S = 15 * 60       # how long the queue waits after the Claude usage limit before trying again
IDLE_S = 5                    # how often an idle worker looks again (it is also woken by a new job)
TURNTABLE_FRAMES = 24
VIEWS_SIZE = 1024
VIEWS_TIMEOUT_S = 1800
MAX_REFERENCES = 4
DEFAULTS = {"outputs": ["game", "full"], "style": "realistic", "camera": "persp", "reuse": True,
            "max_versions": 2, "min_score": 70, "angle": "front", "cut": "auto"}

BASE_URL = None  # set by the server once it knows its port; file links are absolute so any program can fetch them
_wake = None


class JobError(ValueError):
    """A request the API refuses (400)."""


class UsageLimit(Exception):
    """The Claude usage limit stopped the build; the job waits and the queue pauses."""


# --- the key other programs send -----------------------------------------------------------

def key_file():
    return config.data_dir() / "api_key.txt"


def api_key():
    """The key every /v1 call but /health must carry (X-ModelGen-Key). Made on first use; a program
    on this PC reads it from data\\api_key.txt."""
    path = key_file()
    if path.is_file():
        key = path.read_text(encoding="utf-8").strip()
        if key:
            return key
    path.parent.mkdir(parents=True, exist_ok=True)
    key = secrets.token_urlsafe(32)
    path.write_text(key, encoding="utf-8")
    return key


# --- a new job -----------------------------------------------------------------------------

def _number(value, name, low, high, kind=float):
    try:
        out = kind(value)
    except (TypeError, ValueError):
        raise JobError(f"{name} must be a number")
    if not low <= out <= high:
        raise JobError(f"{name} must be between {low} and {high}")
    return out


def _size(size):
    """{w, d, h, unit} (or width/depth/height) as the page stores a size; None when none is given."""
    if size in (None, "", {}):
        return None
    if not isinstance(size, dict):
        raise JobError('size must be {"w": .., "d": .., "h": .., "unit": "mm" | "cm" | "m"}')
    unit = size.get("unit") or "cm"
    if unit not in ("mm", "cm", "m"):
        raise JobError('size unit must be "mm", "cm" or "m"')
    out = {}
    for short, key in (("w", "width"), ("d", "depth"), ("h", "height")):
        value = size.get(short, size.get(key))
        if value not in (None, ""):
            out[key] = round(_number(value, f"size {short}", 0.0001, 100000), 3)
    if not out:
        raise JobError("size gives no side; give at least one of w, d, h")
    return {**out, "unit": unit}


def clean_request(payload):
    """The job's settings from a POST /v1/jobs body, with the defaults filled in. Raises JobError."""
    if not isinstance(payload, dict):
        raise JobError("send a JSON object")
    sheet, assumed, matched = None, [], None
    if payload.get("design") not in (None, "", {}):
        if payload.get("mechanism") not in (None, "", {}):
            raise JobError("give design or mechanism, not both (the design is turned into the mechanism)")
        raw, matched = _design_mechanism(payload)
        payload = {**payload, "mechanism": raw}
    if payload.get("mechanism") not in (None, "", {}):
        try:
            sheet, assumed = mechanism.clean(payload["mechanism"])
        except mechanism.MechanismError as exc:
            raise JobError(f"mechanism{' (from the design)' if matched else ''}: {exc}")
        if matched:
            assumed = [f"from the design: {n} has {p['teeth']} teeth, module {p['module_mm']:g} mm "
                       f"({matched['mm_per_px']:g} mm a pixel)" for n, p in matched["parts"].items()] + assumed
    obj = " ".join(str(payload.get("object") or "").split())
    if not obj and sheet:
        obj = mechanism.describe(sheet)
    if not obj:
        raise JobError("object is required: what to make, in a few words (\"fire hydrant\")")
    if len(obj) > 200:
        raise JobError("object is longer than 200 characters; put the details in notes")
    client = str(payload.get("client") or "").strip().lower()
    if not CLIENT.match(client):
        raise JobError("client is required: your program's name, lowercase letters, digits, - or _ (\"village\")")
    notes = " ".join(str(payload.get("notes") or "").split())
    if len(notes) > 2000:
        raise JobError("notes are longer than 2000 characters")
    outputs = payload.get("outputs") or DEFAULTS["outputs"]
    if not isinstance(outputs, list) or not outputs or any(o not in OUTPUTS for o in outputs):
        raise JobError(f"outputs must be a list of {', '.join(OUTPUTS)}")
    style = payload.get("style") or DEFAULTS["style"]
    if style not in STYLES:
        raise JobError(f"style must be one of {', '.join(STYLES)}")
    camera = payload.get("camera") or DEFAULTS["camera"]
    if camera not in CAMERAS:
        raise JobError(f"camera must be one of {', '.join(CAMERAS)}")
    references = payload.get("references") or []
    if not isinstance(references, list) or len(references) > MAX_REFERENCES:
        raise JobError(f"references must be a list of at most {MAX_REFERENCES} file paths or http(s) links")
    key = payload.get("request_key")
    if key is not None and (not isinstance(key, str) or not 0 < len(key) <= 200):
        raise JobError("request_key must be a string of 1 to 200 characters")
    angle = payload.get("angle") or DEFAULTS["angle"]
    if angle not in ANGLES:
        raise JobError(f"angle must be one of {', '.join(ANGLES)}")
    cut = payload.get("cut") or DEFAULTS["cut"]
    if cut not in CUTS:
        raise JobError(f"cut must be one of {', '.join(CUTS)}")
    colours = payload.get("colours") or {}
    if not isinstance(colours, dict):
        raise JobError('colours must be {"part name": "#rrggbb"}')
    for name, value in colours.items():
        text = str(value).strip().lower()
        if not re.match(r"^#[0-9a-f]{6}$", text):
            raise JobError(f"colours: {name} must be #rrggbb")
        colours[name] = text
    if sheet:
        unknown = sorted(set(colours) - {p["name"] for p in sheet["parts"]})
        if unknown:
            raise JobError(f"colours names {', '.join(unknown)}, which is not a part of the mechanism")
        sheet["colours"] = {**sheet["colours"], **colours}
    req = {"object": obj, "notes": notes, "size": _size(payload.get("size")),
           "outputs": list(dict.fromkeys(outputs)), "style": style, "camera": camera,
           "reuse": bool(payload.get("reuse", DEFAULTS["reuse"])),
           "max_versions": _number(payload.get("max_versions", DEFAULTS["max_versions"]), "max_versions", 1, 5, int),
           "min_score": _number(payload.get("min_score", DEFAULTS["min_score"]), "min_score", 0, 100),
           "budget_usd": (None if payload.get("budget_usd") in (None, "") else
                          round(_number(payload["budget_usd"], "budget_usd", 0.01, 100), 2)),
           "references": [str(r) for r in references], "angle": angle, "colours": colours, "cut": cut}
    if sheet:
        req["mechanism"], req["assumed"] = sheet, assumed
    if matched:
        req["design_match"] = matched
    return client, key, req


def _design_mechanism(payload):
    """A Village vector design (the object itself, or the full path of its design.json on this PC) read
    into a mechanism, with design_module (mm) or design_mm_per_px setting the scale when given."""
    raw = payload["design"]
    if isinstance(raw, str):
        path = Path(raw)
        if not path.is_absolute() or not path.is_file():
            raise JobError(f"design: no file at {raw[:200]} (send the design's JSON, or the full path of its "
                           "design.json on this PC)")
        if path.stat().st_size > design_tools.MAX_BYTES:
            raise JobError("design: the file is over 2 MB")
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except ValueError:
            raise JobError("design: the file is not JSON")
    if len(json.dumps(raw)) > design_tools.MAX_BYTES:
        raise JobError("design: over 2 MB")
    module = payload.get("design_module")
    scale = payload.get("design_mm_per_px")
    try:
        return design_tools.to_mechanism(
            raw, module=None if module in (None, "") else _number(module, "design_module", 0.3, 50),
            mm_per_px=None if scale in (None, "") else _number(scale, "design_mm_per_px", 0.001, 1000))
    except design_tools.DesignError as exc:
        raise JobError(f"design: {exc}")


def _reference(ref):
    """Store one reference image (a file on this PC or an http(s) link) as an upload; its id."""
    if re.match(r"^https?://", ref, re.I):
        try:
            request = urllib.request.Request(ref, headers={"User-Agent": "ModelGen"})
            with urllib.request.urlopen(request, timeout=20) as r:
                raw = r.read(uploads.MAX_BYTES + 1)
                mime = (r.headers.get_content_type() or "image/png")
        except Exception as exc:
            raise JobError(f"could not download the reference {ref[:120]} ({type(exc).__name__})")
    else:
        path = Path(ref)
        if not path.is_absolute() or not path.is_file():
            raise JobError(f"no image file at {ref[:200]} (give a full path on this PC or an http(s) link)")
        raw = path.read_bytes()[:uploads.MAX_BYTES + 1]
        mime = mimetypes.guess_type(path.name)[0] or "image/png"
    try:
        return uploads.save(f"data:{mime};base64,{base64.b64encode(raw).decode('ascii')}")["id"]
    except ValueError as exc:
        raise JobError(f"the reference {ref[:120]} is not usable: {exc}")


def _same(a, b):
    keys = ("object", "notes", "size", "outputs", "style", "camera", "reuse", "max_versions", "min_score",
            "budget_usd", "references", "angle", "colours", "mechanism", "cut")
    return all(a.get(k) == b.get(k) for k in keys)


def submit(payload):
    """Queue a job, or return the one it repeats: the same request_key from the same program, or an
    identical request from it still waiting in the queue. Returns the job as the API shows it."""
    client, key, req = clean_request(payload)
    if key:
        old = db.job_by_key(client, key)
        if old:
            return public(old)
    for old in db.queued_jobs():
        if old["client"] == client and old["status"] == "queued" and _same(old["request"], req):
            return public(old)
    req["reference_ids"] = [_reference(r) for r in req["references"]]
    job = db.add_job(f"j_{secrets.token_hex(6)}", client, req["object"], req, key)
    db.add_event("api_job_queued", data={"job": job["id"], "client": client, "object": req["object"]})
    wake()
    return public(job)


def cancel(job_id):
    job = db.get_job(job_id)
    if not job:
        return None
    if job["status"] in db.JOB_LIVE:
        db.update_job(job_id, status="cancelled", finished=db.now(), step="")
        if job["project"] and pipeline.is_running(job["project"]):
            pipeline.stop(job["project"])
        db.add_event("api_job_cancelled", project=job["project"], data={"job": job_id})
    return public(db.get_job(job_id))


# --- what the API shows ----------------------------------------------------------------------

def _link(path):
    rel = Path(path).relative_to(config.projects_dir()).as_posix()
    return f"{BASE_URL or ''}/files/{rel}"


def version_files(project, number, outputs=OUTPUTS, style=None, camera=None):
    """{"files": {relative path: {url, path}}, "main": {...}} of one version, for the outputs asked."""
    folder = pipeline.version_dir(project, number)
    found, main = [], {}
    if "game" in outputs and (folder / "game").is_dir():
        game = sorted(p for p in (folder / "game").iterdir() if p.is_file())
        found += game
        main["game"] = next((p for p in game if p.name.endswith("_game.glb")), None)
    if "full" in outputs:
        exports = sorted(p for p in (folder / "exports").iterdir() if p.is_file()) if (folder / "exports").is_dir() else []
        found += exports + ([folder / "model.blend"] if (folder / "model.blend").is_file() else [])
        main["full"] = next((p for p in exports if p.suffix == ".glb"), None)
    for output, index in [("views", "views.json")] + [(k, f"{k}.json") for k in SEQUENCES]:
        if output not in outputs:
            continue
        sets = ([folder / output / f"{style}-{camera}"] if style and camera else
                sorted(p for p in (folder / output).iterdir() if p.is_dir()) if (folder / output).is_dir() else [])
        for made in (s for s in sets if s.is_dir()):
            found += sorted(p for p in made.iterdir() if p.is_file())
            if style and camera and (made / index).is_file():
                main[output] = made / index
    files = {p.relative_to(folder).as_posix(): {"url": _link(p), "path": str(p.resolve())} for p in found}
    return {"files": files, "main": {k: p.relative_to(folder).as_posix() for k, p in main.items() if p}}


def source_of(project, number):
    if pipeline.remix_car(project, number) is not None:
        return "car_generator"
    if pipeline.library_asset(project, number) is not None:
        return "ready_made"
    if pipeline.parts_sheet(project, number) is not None:
        return "part_tools"
    return "built"


def _mechanism_record(project, number):
    """A version's mechanism.json (the sheet, what was filled in, its key), or {}."""
    path = pipeline.version_dir(project, number) / "mechanism.json"
    try:
        return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
    except ValueError:
        return {}


def public(job):
    out = {k: job.get(k) for k in ("id", "client", "request_key", "object", "status", "project", "version",
                                   "score", "passed", "source", "error", "retry_at", "created", "started",
                                   "finished")}
    out["request"] = {k: v for k, v in job["request"].items() if k not in ("reference_ids", "assumed")}
    out["notes"] = [n for n in (job.get("notes") or "").split("\n") if n]
    out["assumed"] = list(job["request"].get("assumed") or [])
    if job.get("project") and job.get("version") and not out["assumed"]:
        out["assumed"] = list(_mechanism_record(job["project"], job["version"]).get("assumed") or [])
    out["step"] = job.get("step") or ""
    project = job.get("project")
    if job["status"] == "building" and project:
        phase = db.events_of(["phase"], project=project, limit=1)
        if phase:
            out["step"] = phase[-1]["data"].get("text") or out["step"]
    if job["status"] == "queued":
        waiting = [j["id"] for j in db.queued_jobs()]
        out["position"] = waiting.index(job["id"]) + 1 if job["id"] in waiting else None
    out["cost_usd"] = db.spend_where(project=project) if project and job.get("source") != "existing" else 0.0
    out["files"], out["main"] = {}, {}
    if job["status"] == "done" and project and job.get("version") and db.get_project(project):
        req = job["request"]
        made = version_files(project, job["version"], req["outputs"], req["style"], req["camera"])
        out["files"], out["main"] = made["files"], made["main"]
        record = _mechanism_record(project, job["version"])
        if record:
            out["mechanism"] = {"key": record.get("key"), "sizes_mm": record.get("sizes_mm"),
                                "colours": record.get("colours")}
    if job["request"].get("design_match"):
        out["design"] = job["request"]["design_match"]
    return out


def parts_catalogue():
    """GET /v1/parts: every part type the tools build, with its fields, units, ranges and defaults."""
    out = mechanism.catalogue()
    out["outputs"] = {"motion": "a seamless transparent loop of the mechanism working, motion/<style>-<camera>/",
                      "views": "the angle pictures", "game": "game-ready files", "full": "full-detail files"}
    out["styles"], out["angles"] = list(STYLES), list(ANGLES)
    return out


def health():
    exe = config.blender_path()
    paused = next((j for j in db.queued_jobs() if j["status"] == "waiting_for_usage"), None)
    return {"ok": True, "app": "ModelGen", "api": "v1", "version": _app_version(), "blender": bool(exe),
            "claudein": _claudein_up(), "queued": len(db.queued_jobs()),
            "building": next((j["id"] for j in db.list_jobs(status="building", limit=1)), None),
            "paused_for_usage": bool(paused), "retry_at": paused["retry_at"] if paused else None}


def _app_version():
    try:
        from . import __version__
        return __version__
    except ImportError:
        return None


def _claudein_up():
    """Whether ClaudeIn (the tray program the agents ask Claude through) is listening on this PC."""
    import socket
    try:
        from claudein_client import _client
        _, port = _client._local.load(force=True)
        with socket.create_connection(("127.0.0.1", int(port)), timeout=0.5):
            return True
    except Exception:
        return False


# --- finding a model that was made already -------------------------------------------------------

def _tokens(text):
    return {asset_catalog._norm(w) for w in asset_catalog._words(text or "") if len(w) > 2}


def _made_models():
    """Every accepted model, with the words it can be found by."""
    out = []
    for p in db.list_projects():
        number = p.get("accepted_version")
        if not number or not db.get_version(p["name"], number):
            continue
        proj = db.get_project(p["name"])
        spec = proj.get("spec") or {}
        words = _tokens(" ".join(str(x or "") for x in (proj["title"], proj.get("request"), spec.get("object"))))
        out.append({"proj": proj, "version": db.get_version(p["name"], number), "words": words})
    return out


def _model_info(proj, v, outputs=("game", "full")):
    folder = pipeline.version_dir(proj["name"], v["number"])
    game = folder / "game" / "game.json"
    size = None
    try:
        size = json.loads(game.read_text(encoding="utf-8")).get("size") if game.is_file() else None
    except ValueError:
        pass
    styles = sorted(p.name for p in (folder / "views").iterdir() if p.is_dir()) if (folder / "views").is_dir() else []
    return {"name": proj["name"], "title": proj["title"], "request": proj.get("request", ""),
            "client": db.project_client(proj["name"]), "source": source_of(proj["name"], v["number"]),
            "version": v["number"], "score": v.get("score"), "passed": v.get("passed"), "size_m": size,
            "views": styles, **version_files(proj["name"], v["number"], outputs)}


def find_models(q, limit=10, min_score=DEFAULTS["min_score"]):
    """Accepted models that match the words and reach min_score (or passed, when unscored), best
    first, and the ready-made catalogue's candidates (made in about a minute when a job asks for one)."""
    head, mods = asset_catalog.object_words(q)
    wanted = _tokens(q)
    hits = []
    for m in _made_models():
        if head and asset_catalog._norm(head) not in m["words"]:
            continue
        if not _good_enough(m["version"], min_score):
            continue
        share = len(wanted & m["words"]) / max(len(wanted), 1)
        hits.append((share, m["version"].get("score") or 0, m))
    hits.sort(key=lambda h: (h[0], h[1]), reverse=True)
    made = [_model_info(m["proj"], m["version"]) for _, _, m in hits[:limit]]
    catalogue = []
    if asset_catalog.CATALOG.is_file():
        for h in asset_catalog.search(q, limit=min(limit, 6)):
            e = h["entry"]
            catalogue.append({"id": e["id"], "name": e.get("name"), "source": "ready_made",
                              "size_m": e.get("size"), "triangles": e.get("triangles"),
                              "note": "not made yet: a job for this object makes it in about a minute"})
    return {"models": made, "ready_made": catalogue}


def _good_enough(v, min_score):
    """Whether a version meets a job's bar: its score against min_score, or its own pass when unscored."""
    score = v.get("score")
    return score >= min_score if score is not None else bool(v.get("passed"))


async def _reuse(job):
    """An accepted model that IS the object asked for, checked by the caster; (project, version) or None."""
    req = job["request"]
    text = req["object"] + (f". {req['notes']}" if req["notes"] else "")
    head, _ = asset_catalog.object_words(req["object"])
    wanted = _tokens(text)
    found = []
    for m in _made_models():
        if head and asset_catalog._norm(head) not in m["words"]:
            continue
        if not _good_enough(m["version"], req["min_score"]):
            continue  # a model that never passed is not reused: build anew
        found.append((len(wanted & m["words"]), m))
    if not found:
        return None
    found.sort(key=lambda h: h[0], reverse=True)
    shown = []
    for _, m in found[:6]:
        info = _model_info(m["proj"], m["version"])
        spec = m["proj"].get("spec") or {}
        shown.append({"id": m["proj"]["name"], "name": m["proj"]["title"], "description": spec.get("summary") or
                      m["proj"].get("request"), "size_m": info["size_m"], "style": spec.get("style")})
    size = asset_catalog.metres(req.get("size"))
    sink = {}
    prompt = (f"## The request\n{text}" + (f"\nSize asked for (metres): {json.dumps(size)}" if size else "") +
              f"\n\n## Models the studio already made that might be it (best match first)\n"
              f"{json.dumps(shown, indent=1)}\n\nCall `choose` once.")
    res = await runner.run_agent("caster", prompt, at.caster_tools(sink, [s["id"] for s in shown]),
                                 doing="looking for a model made before")
    if res and runner.account_limit(res.error):
        raise UsageLimit(runner.account_limit(res.error))
    picked = sink.get("asset")
    if not picked:
        return None
    m = next(m for _, m in found if m["proj"]["name"] == picked)
    return picked, m["version"]["number"]


# --- the worker ---------------------------------------------------------------------------------

def wake():
    if _wake is not None:
        _wake.set()


def recover():
    """After a restart: a job that was being built goes back in the queue. It keeps its model, so the
    versions it finished are kept and the build goes on from there."""
    for job in db.list_jobs(status="building"):
        db.update_job(job["id"], status="queued", step="", notes=_note(job, "ModelGen restarted while this job "
                                                                           "was building; it was queued again"))


def _note(job, text):
    return "\n".join([n for n in (job.get("notes") or "").split("\n") if n] + [text])


def _busy():
    return any(pipeline.is_running(name) for name in list(pipeline.RUNNING))


def next_job(now=None):
    """The job to build now, or None: the oldest in the queue, unless the queue is paused for the
    usage limit (a job waiting for it holds the queue until its retry time)."""
    now = now or datetime.now()
    for job in db.queued_jobs():
        if job["status"] == "waiting_for_usage":
            retry = datetime.fromisoformat(job["retry_at"]) if job.get("retry_at") else now
            return job if retry <= now else None
        return job
    return None


async def worker(sleep=asyncio.sleep):
    """Build the queued jobs one at a time, for as long as the server runs."""
    global _wake
    _wake = asyncio.Event()
    while True:
        job = None if _busy() else next_job()
        if job is None:
            _wake.clear()
            try:
                await asyncio.wait_for(_wake.wait(), IDLE_S)
            except asyncio.TimeoutError:
                pass
            continue
        await run(job)
        await sleep(0)


async def run(job):
    """Build one job to its end (done, failed, cancelled) or until the usage limit pauses it."""
    job_id = job["id"]
    db.update_job(job_id, status="building", started=job.get("started") or db.now(), step="starting", retry_at=None)
    try:
        await _build(db.get_job(job_id))
    except asyncio.CancelledError:
        raise
    except UsageLimit as exc:
        retry = (datetime.now() + timedelta(seconds=USAGE_RETRY_S)).isoformat(timespec="seconds")
        db.update_job(job_id, status="waiting_for_usage", retry_at=retry,
                      step=f"waiting for the Claude usage limit ({exc}); trying again at {retry[11:16]}")
        db.add_event("api_job_paused", project=job.get("project"), data={"job": job_id, "retry_at": retry})
    except Exception as exc:
        _fail(job_id, f"{type(exc).__name__}: {exc}")


def _cancelled(job_id):
    return (db.get_job(job_id) or {}).get("status") == "cancelled"


def _fail(job_id, error):
    if _cancelled(job_id):
        return
    db.update_job(job_id, status="failed", error=str(error)[:2000], finished=db.now(), step="")
    db.add_event("api_job_failed", project=(db.get_job(job_id) or {}).get("project"),
                 data={"job": job_id, "error": str(error)[:500]})


def _usage_hit(project, since, error=""):
    """Whether the Claude usage limit stopped this build: in its error, or in an agent run since it began."""
    if runner.account_limit(error or ""):
        return runner.account_limit(error)
    for r in db.runs(project, limit=40):
        if r["started"] >= since and runner.account_limit(r.get("error") or ""):
            return runner.account_limit(r["error"])
    return None


def _done_versions(project):
    return [v for v in db.versions(project) if v["status"] == "done"]


def _best(project):
    done = _done_versions(project)
    scored = [v for v in done if v.get("score") is not None]
    if scored:
        return max(scored, key=lambda v: (v["score"], v["number"]))
    return done[-1] if done else None


def _new_project(job):
    req = job["request"]
    text = req["object"] + (f". {req['notes']}" if req["notes"] else "")
    options = {"api": {"client": job["client"], "job": job["id"]}}
    if req.get("size"):
        options["size"] = req["size"]
    if req.get("budget_usd"):
        options["budget_usd"] = req["budget_usd"]
    if req.get("reference_ids"):
        options["references"] = req["reference_ids"]
    if req.get("mechanism"):
        options["mechanism"], options["assumed"] = req["mechanism"], req.get("assumed") or []
    proj = db.create_project(req["object"][:80], text, options=options)
    db.add_event("project_created", project=proj["name"], data={"title": proj["title"], "client": job["client"]})
    db.update_job(job["id"], project=proj["name"])
    return proj["name"]


async def _build(job):
    job_id, req = job["id"], job["request"]
    if not config.blender_path():
        raise RuntimeError(f"Blender is not installed on this PC ({config.INSTALL_HINT})")
    if req["reuse"] and not job.get("project") and req.get("mechanism"):
        hit = exact_reuse(req["mechanism"])  # the same numbers: the same model, no agent needed
        if hit:
            project, number = hit
            db.update_job(job_id, project=project, source="existing")
            await _finish(job_id, project, number, source="existing")
            return
    elif req["reuse"] and not job.get("project"):
        db.update_job(job_id, step="looking for a model made before")
        hit = await _reuse(job)
        if hit:
            project, number = hit
            db.update_job(job_id, project=project, source="existing")
            await _finish(job_id, project, number, source="existing")
            return
    if _cancelled(job_id):
        return
    project = job.get("project") or _new_project(job)
    since = job.get("started") or db.now()
    last_error = None
    for _ in range(req["max_versions"] + 1):  # never more builds than versions asked for (+1 after a restart)
        if len(_done_versions(project)) >= req["max_versions"]:
            break
        done = _done_versions(project)
        best = _best(project)
        if best and (best.get("passed") or source_of(project, best["number"]) != "built"):
            break  # it passed, or a ready-made or generated model: no critic changes to build on
        if done and not pipeline.open_changes(project):
            break
        db.update_job(job_id, step=f"building version {len(done) + 1}")
        try:
            task = pipeline.start(project, next_version=bool(done))
        except pipeline.Busy:
            task = pipeline.RUNNING[project]
        outcome = await task
        if _cancelled(job_id):
            return
        if not outcome.get("ok"):
            last_error = outcome.get("reason") or outcome.get("error") or "the build failed"
            usage = _usage_hit(project, since, last_error)
            if usage:
                raise UsageLimit(usage)
            break  # a failed or stopped build: keep the best version made so far, if any
    best = _best(project)
    if best is None:
        raise RuntimeError(last_error or "no version could be built")
    if last_error:
        db.update_job(job_id, notes=_note(db.get_job(job_id), f"a later version failed: {last_error[:300]}"))
    await _finish(job_id, project, best["number"], source=source_of(project, best["number"]))


async def _finish(job_id, project, number, source):
    job = db.get_job(job_id)
    req = job["request"]
    v = db.get_version(project, number)
    if source != "existing":
        db.update_project(project, accepted_version=number, status="accepted")
        db.add_event("accepted", project=project, data={"version": number, "by": job["client"]})
    score = v.get("score")
    passed = (score >= req["min_score"]) if score is not None else bool(v.get("passed"))
    colours = {**(_mechanism_record(project, number).get("colours") or {}), **(req.get("colours") or {})}
    if "views" in req["outputs"]:
        db.update_job(job_id, step="rendering the angle pictures", project=project, version=number)
        error = await render_views(project, number, req["style"], req["camera"],
                                   **({"colours": colours} if colours else {}))
        if error:
            db.update_job(job_id, notes=_note(db.get_job(job_id), f"the angle pictures could not be made: {error}"))
    for kind in (k for k in SEQUENCES if k in req["outputs"]):
        db.update_job(job_id, step=f"rendering the {kind} pictures", project=project, version=number)
        error = await render_motion(project, number, req["style"], req["camera"], req.get("angle", DEFAULTS["angle"]),
                                    colours, kind, req.get("cut", DEFAULTS["cut"]))
        if error:
            db.update_job(job_id, notes=_note(db.get_job(job_id), f"the {kind} pictures could not be made: {error}"))
    if _cancelled(job_id):
        return
    db.update_job(job_id, status="done", step="", project=project, version=number, score=score, passed=passed,
                  source=source, finished=db.now())
    db.add_event("api_job_done", project=project, data={"job": job_id, "version": number, "passed": passed})


def exact_reuse(sheet):
    """An accepted model built by the part tools from exactly these numbers: (project, version) or None."""
    want = mechanism.key(sheet)
    for p in db.list_projects():
        number = p.get("accepted_version")
        if number and _mechanism_record(p["name"], number).get("key") == want and db.get_version(p["name"], number):
            return p["name"], number
    return None


async def render_views(project, number, style="realistic", camera="persp", colours=None):
    """The transparent angle pictures of one version into v<n>\\views\\<style>-<camera>, with
    views.json. Made once per style and camera. Returns None, or what went wrong."""
    folder = pipeline.version_dir(project, number)
    out = folder / "views" / f"{style}-{camera}"
    if (out / "views.json").is_file():
        return None
    blend = folder / "model.blend"
    if not blend.is_file():
        return "this version has no .blend to render"
    samples = int(config.get("render", "samples", 96))
    runs = [blender.asset_views_step(out, camera, style, ("views", "shadows"), size=VIEWS_SIZE, samples=samples,
                                     colours=colours),
            blender.asset_views_step(out, camera, style, ("turntable",), turntable=TURNTABLE_FRAMES,
                                     size=VIEWS_SIZE, samples=max(16, samples // 2), colours=colours)]
    made = []
    with runner.acting("critic", project, f"rendering angle pictures of v{number}", "render") as act:
        for step in runs:
            result = await blender.run_job_async([step], open_blend=blend, timeout=VIEWS_TIMEOUT_S)
            if not result.ok:
                act.ok = False
                info = result.summary()
                return (info.get("traceback") or info.get("error") or "Blender failed")[-800:]
            made += result.outputs.get("asset_views") or []
    first = made[0] if made else {}
    record = {"style": style, "camera": camera, "size_px": first.get("size_px"), "size_m": first.get("size_m"),
              "views": first.get("views") or {},
              "turntable": {"frames": TURNTABLE_FRAMES, "elevation_deg": 15 if camera == "persp" else 0,
                            "first_azimuth_deg": 0},
              "shadows": camera == "persp",
              "files": sorted(p.name for p in out.iterdir() if p.suffix == ".png")}
    (out / "views.json").write_text(json.dumps(record, indent=1), encoding="utf-8")
    return None


def _loop_report(folder):
    path = folder / "motion_report.json"
    try:
        return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
    except ValueError:
        return {}


async def render_motion(project, number, style="explainer", camera="persp", angle="front34", colours=None,
                        kind="motion", cut="auto"):
    """The model working, as transparent pictures in v<n>\\<kind>\\<style>-<camera>: frame_000.png ...,
    <kind>.json (frames, frame rate, how to repeat them) and parts.json (where each part is in every
    frame, and the air paths). kind "motion" is the seamless loop; "cutaway" the same loop with the
    housings sliced open (cut: the plane); "exploded" the parts sliding apart along the shaft in
    assembly order and back. More than 24 frames are split over two Blender runs at once. Returns
    None, or what went wrong."""
    folder = pipeline.version_dir(project, number)
    out = folder / kind / f"{style}-{camera}"
    index = out / f"{kind}.json"
    if index.is_file():
        try:
            old = json.loads(index.read_text(encoding="utf-8"))
            if old.get("angle") == angle and old.get("cut", cut) == cut and (
                    style != "explainer" or not colours
                    or all(old.get("colours", {}).get(k) == v for k, v in colours.items())):
                return None
        except ValueError:
            pass
        for old in out.glob("*"):
            old.unlink()
    blend = folder / "model.blend"
    if not blend.is_file():
        return "this version has no .blend to render"
    report = _loop_report(folder)
    record = _mechanism_record(project, number)
    if kind in ("motion", "cutaway") and not report and not (folder / "motion.py").is_file():
        return "this model does not move; ask for views instead"
    if kind == "cutaway" and not record.get("housings"):
        return "this model has no housing to cut open"
    if kind == "exploded" and not record.get("exploded"):
        return "an exploded view needs a mechanism built by the part tools"
    frames = 48 if kind == "exploded" else int(report.get("frames") or 0)
    samples = max(16, int(config.get("render", "samples", 96)) // 2)
    if frames > 24:
        half = frames // 2
        runs = [(0, half - 1), (half, frames - 1)]
    else:
        runs = [(None, None)]
    steps = [blender.motion_frames_step(out, camera, style, angle, a, b, size=VIEWS_SIZE, samples=samples,
                                        colours=colours, mode=kind, cut=record.get("housings"), plane=cut,
                                        offsets=record.get("exploded")) for a, b in runs]
    with runner.acting("critic", project, f"rendering the {kind} of v{number}", "render") as act:
        results = await asyncio.gather(*(blender.run_job_async([s], open_blend=blend, timeout=MOTION_TIMEOUT_S)
                                         for s in steps))
        act.ok = all(r.ok for r in results)
    for r in results:
        if not r.ok:
            info = r.summary()
            return (info.get("traceback") or info.get("error") or "Blender failed")[-800:]
    made = [m for r in results for m in r.outputs.get("motion_frames") or []]
    where = {}
    for m in made:
        where.update({int(k): v for k, v in m["parts"].items()})
    first = made[0]
    loop = report.get("mechanism") or (json.loads(first["loop"]) if first.get("loop") else {})
    count = first.get("frames") or len(where)
    fps = first.get("fps") or 24
    names = [f"frame_{i:03d}.png" for i in sorted(where)]
    (out / "parts.json").write_text(json.dumps(
        {"size_px": first.get("size_px"), "flows": first.get("flows") or [],
         "frames": [{"frame": i, "file": f"frame_{i:03d}.png", "parts": where[i]} for i in sorted(where)]},
        indent=1), encoding="utf-8")
    info = {"kind": kind, "style": style, "camera": camera, "angle": angle, "size_px": first.get("size_px"),
            "fps": fps, "frames": count, "loop_seconds": round(count / fps, 4), "view": first.get("view"),
            "repeat": f"play frame_000 to frame_{count - 1:03d} in order, then start again at frame_000: the "
                      "loop is seamless (the frame after the last is the first)",
            "colours": first.get("colours") or {}, "files": names}
    if kind == "exploded":
        info["exploded"] = ("the parts slide apart along the shaft in assembly order (frame_000 assembled, "
                            f"frame_{count // 2:03d} furthest apart) and back together")
        info["offsets_m"] = record.get("exploded")
    else:
        info.update(mode=loop.get("mode", "spin"), driver=loop.get("driver"),
                    driver_rpm=loop.get("driver_rpm") or loop.get("driver_rpm_peak"), parts=loop.get("parts") or {})
        if loop.get("mode") == "spin" and loop.get("driver_turns"):
            info["driver_turns_per_loop"] = loop["driver_turns"]
            info["note"] = ("the loop is the shortest stretch after which every part looks as it started (a plain "
                            "gear repeats every tooth), so it is short: repeat it as often as the scene needs")
        if loop.get("note"):
            info["warning"] = loop["note"]
    if kind == "cutaway":
        info["cut"], info["cut_parts"], info["cut_colour"] = cut, record.get("housings"), "#ff2e63"
    index.write_text(json.dumps(info, indent=1), encoding="utf-8")
    return None
