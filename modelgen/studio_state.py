"""Everything the page shows, built from the live status, the database and the version folders.

snapshot() is what the studio map's applyState() takes: every character's state, where its tool
sends it, its recent calls, its open question and direct messages, plus recent hand-offs and
meetings and the run panel's steps. page_data() fills the chat, the model panel, the Models, Parts
library and Activity screens, the status line and Settings. Nothing here changes any state.
"""

import json
from datetime import datetime
from pathlib import Path

from . import budget, config, db, pipeline, questions, runner, settings_store, talk
from .tools import agent_tools as at
from .tools.library import Library

# Where each engine tool sends its character on the map (the map's own tool names, see
# map\js\stations.js). Tools not listed keep the character at their desk.
TOOL_SPOTS = {
    ("librarian", "search_library"): "search_library", ("librarian", "list_library"): "read_part",
    ("librarian", "get_library_part"): "read_part", ("librarian", "add_part"): "save_part",
    ("librarian", "add_material"): "archive_version",
    ("materials", "list_recipes"): "pick_material", ("materials", "get_library_material"): "pick_material",
    ("materials", "search_library"): "pick_material", ("materials", "write_materials"): "make_material",
    ("materials", "edit_materials"): "make_material",
    ("critic", "view_renders"): "view_renders", ("critic", "submit_review"): "view_renders",
    ("critic", "view_reference"): "view_renders", ("critic", "render"): "render_views",
    ("architect", "submit_plan"): "plan_parts", ("architect", "view_reference"): "plan_parts",
    ("architect", "search_library"): "fetch_plans", ("architect", "get_library_part"): "fetch_plans",
    ("brief", "submit_spec"): "pin_note", ("brief", "view_reference"): "pin_note",
    ("assembler", "write_assembly"): "check_fit", ("assembler", "edit_assembly"): "check_fit",
    ("assembler", "full_build"): "check_fit",
    ("builder", "write_part"): "run_blender", ("builder", "edit_part"): "run_blender",
    ("builder", "search_library"): "read_parts",
    ("builder", "get_library_part"): "read_parts",
    ("animator", "write_motion"): "preview_frames", ("animator", "edit_motion"): "preview_frames",
    ("animator", "list_motion_helpers"): "motion_desk",
}
STEPS = [("catalog", "Ready-made", "brief"), ("research", "Research", "brief"),
         ("spec", "Spec", "brief"), ("plan", "Plan", "architect"), ("build", "Build", "builder"),
         ("assemble", "Assemble", "assembler"), ("materials", "Materials", "materials"),
         ("motion", "Motion", "animator"),
         ("render", "Render", "critic"), ("review", "Review", "critic"), ("library", "Library", "librarian")]
OPTIONAL_STEPS = ("catalog", "research")   # shown only in a run that did them
LIVE = ("briefing", "planning", "building")
HANDOFF_S = 90   # hand-offs this recent are sent; the map plays each once
MEETING_S = 30


def _age(stamp):
    try:
        return (datetime.now() - datetime.fromisoformat(stamp)).total_seconds()
    except (TypeError, ValueError):
        return 1e9


def _ago_stamp(seconds):
    return datetime.fromtimestamp(datetime.now().timestamp() - seconds).isoformat(timespec="seconds")


def spot(role, tool):
    if not tool:
        return None
    if role == "director":
        return "check_queue"
    return TOOL_SPOTS.get((role, tool))


def cast_id(seat):
    role = talk.role_of(seat or "")
    return talk.CAST[role].lower() if role else None


def current_project():
    """The person's model being built now, else their newest one. Models other programs asked for
    (the /v1 API) are left out: the page opens one only when the person picks it."""
    projects = [p for p in db.list_projects() if not db.project_client(p["name"])]
    for p in projects:
        if pipeline.is_running(p["name"]):
            return p["name"]
    return projects[0]["name"] if projects else None


def _clock(stamp):
    try:
        return datetime.fromisoformat(stamp).strftime("%H:%M")
    except (TypeError, ValueError):
        return ""


# --- the map ------------------------------------------------------------------------------

def _accept_question(project):
    """The director waits on the person when a finished version has not been accepted yet."""
    if not project or pipeline.is_running(project):
        return None
    proj = db.get_project(project)
    if not proj or proj["status"] not in ("done", "needs_changes") or proj.get("accepted_version"):
        return None
    scored = [v for v in db.versions(project) if v.get("score") is not None]
    if not scored:
        return None
    best = proj.get("base_version") or max(scored, key=lambda v: v["score"])["number"]
    v = next((x for x in scored if x["number"] == best), scored[-1])
    finished = db.last_event("finished", project)
    # Nothing more is built until the person answers: no timeout, no automatic next version.
    changes = len((v.get("review") or {}).get("changes") or [])
    text = f"v{v['number']} is ready with {v['score'] / 10:.1f} / 10. Accept it"
    text += (f", build the next version with Annie's {changes} change{'s' if changes != 1 else ''} below, or add "
             "your comments first?") if changes else ", or add your comments for a next version?"
    options = [f"Accept v{v['number']}"] + (["Build next version"] if changes else []) + ["Add my comments"]
    nxt = pipeline.next_changes(project) if changes else None
    return {"id": f"accept-{project}-{v['number']}", "kind": "accept", "version": v["number"],
            "text": text, "options": options, "items": (nxt or {}).get("items") or [],
            "asked": finished["created"] if finished else db.now()}


def _agent(entry, project, start):
    done_s = float(config.get("questions", "done_s", 25))
    failed_s = float(config.get("questions", "failed_s", 90))
    seat, role = entry["id"], entry["role"]
    state, since, doing = entry["state"], entry["since"], entry["doing"]
    q = questions.for_agent(seat)
    if role == "director" and not q and state != "working":
        q = _accept_question(project)
    if state == "done" and _age(since) > done_s or state == "failed" and _age(since) > failed_s:
        state, doing = "idle", ""
    if state == "failed" and entry.get("error"):
        doing = f"{doing}: {entry['error']}" if doing else entry["error"]
    if q and state != "working":
        state, doing, since = "waiting", q["text"], q.get("asked") or since
    calls = [{k: c.get(k) for k in ("tool", "text", "at", "ok")} for c in entry.get("calls", [])]
    out = {"id": seat, "role": role, "state": state, "doing": (doing or "")[:160], "since": since,
           "tool": entry.get("tool") if state == "working" else None,
           "spot": spot(role, entry.get("tool")) if state == "working" else None,
           "calls": calls, "model": entry.get("model"), "chat": role != "director"}
    if project and start:
        out["cost"] = db.spend_where(project, since=start, seat=seat)
    if q and state == "waiting":
        out["question"] = {"id": q["id"], "text": q["text"], "options": q["options"], "kind": q.get("kind"),
                           "version": q.get("version"), "items": q.get("items") or []}
    if role != "director":
        out["messages"] = [{"from": m["role"], "text": m["text"], "at": m["created"]}
                           for m in db.agent_messages(seat, 20)]
    return out


FAILURE_WORDS = {"cancelled": "stopped by you", "stopped": "stopped at a spending limit",
                 "blender_missing": "Blender is not installed"}


def _failure_reason(error):
    """A failed run's error as a short line for the step it failed on."""
    text = FAILURE_WORDS.get(error) or str(error or "failed").removeprefix("PipelineError: ")
    return text if len(text) <= 160 else text[:159].rstrip() + "…"


def _run(project, start, builders):
    proj = db.get_project(project)
    if not proj or not start:
        return None
    events = db.events_since(start["created"], ["phase", "version_started", "version_done", "version_failed",
                                                 "finished", "question"], project=project)
    phases = [e for e in events if e["kind"] == "phase" and e["data"].get("step")]
    started = [e for e in events if e["kind"] == "version_started"]
    finished = next((e for e in reversed(events) if e["kind"] == "finished"), None)
    order = [s[0] for s in STEPS]
    last = order.index(phases[-1]["data"]["step"]) if phases else -1
    seen = {}
    for e in phases:
        seen[e["data"]["step"]] = e
    done_v = [e for e in events if e["kind"] == "version_done"]
    waiting_roles = {talk.role_of(q["agent"]) for q in questions.open_questions(project)}
    budget_wait = any(q["kind"] == "budget" for q in questions.open_questions(project))
    steps = []
    for i, (key, label, role) in enumerate(STEPS):
        if key == "motion" and key not in seen and not at.has_motion(proj.get("spec")):
            continue  # a still model has no motion step
        if key in OPTIONAL_STEPS and key not in seen:
            continue  # this run did not look for a ready-made model, or did no research
        agent = builders if key == "build" else role
        step = {"agent": agent, "label": label}
        if finished and finished["data"].get("ok"):
            state = "done" if i <= max(last, order.index("review")) or key in seen else None
        elif finished:
            state = "done" if i < last else "failed" if i == last else None
        else:
            state = "done" if i < last else "active" if i == last else None
            if state == "active" and (role in waiting_roles or budget_wait):
                state = "waiting"
        if key == "spec" and start["data"].get("kind") in ("changes", "next"):
            state, step["note"] = "done", "kept from the earlier version"
        if state:
            step["state"] = state
        if state == "done" and key in seen:
            step["at"] = seen[key]["created"]
        data = seen.get(key, {}).get("data", {})
        if key == "catalog" and state == "done" and ("research" in seen or "spec" in seen):
            step["note"] = "none fits, the team builds it"
        if key == "plan" and data.get("object"):
            step["note"] = data["object"]
        if key == "build" and data.get("parts"):
            step["note"] = f"{len(data['parts'])} part{'s' if len(data['parts']) != 1 else ''}"
        if key == "review" and done_v:
            d = done_v[-1]["data"]
            score = f"{d['score'] / 10:.1f} / 10" if d.get("score") is not None else "not scored"
            step["note"] = f"v{d['version']}: {score}" + (" · passed" if d.get("passed") else "")
        if state == "failed":  # the step the run failed on says why
            step["note"] = _failure_reason(finished["data"].get("error"))
        steps.append(step)
    number = started[-1]["data"]["version"] if started else None
    rnd = started[-1]["data"].get("round") if started else None
    out = {"id": f"run-{start['id']}", "title": proj["title"] + (f" v{number}" if number else ""),
           "round": f"Round {rnd} of {int(config.get('loop', 'max_rounds', 4))}" if rnd else "",
           "started": start["created"], "cost": db.spend_where(project, since=start["created"]),
           "finished": bool(finished), "steps": steps,
           "project": project, "model": proj["title"], "busy": pipeline.is_running(project)}
    if finished and finished["data"].get("ok"):
        out.update(current=None, percent=100, eta_s=0)
    else:
        out.update(_progress(steps, seen))
        if finished:
            out["eta_s"] = None   # failed or stopped: nothing is left to wait for
    return out


# --- how far a run is, and how long it has left ----------------------------------------------

_STEP_TIMES = {"at": 0.0, "medians": {}}
STEP_TIMES_S = 60      # the step medians are worked out again after this long
STEP_MAX_S = 3600      # a step that took longer than this (a crash, a night's pause) is left out


def step_medians():
    """How long each run step usually takes, in seconds: the median over past runs, from the
    phase events. A step needs two timed runs before it gets a median."""
    import statistics
    import time
    if _STEP_TIMES["at"] and time.time() - _STEP_TIMES["at"] < STEP_TIMES_S:
        return _STEP_TIMES["medians"]
    spans, open_step = {}, {}
    for e in db.events_of(["build_started", "phase", "finished"], limit=5000):
        p = e["project"]
        step = e["data"].get("step") if e["kind"] == "phase" else None
        prev = open_step.get(p)
        if prev and step == prev[0]:
            continue  # the same step said again: it is still running
        if prev:
            try:
                secs = (datetime.fromisoformat(e["created"]) - datetime.fromisoformat(prev[1])).total_seconds()
            except (TypeError, ValueError):
                secs = None
            if secs is not None and 0 <= secs <= STEP_MAX_S:
                spans.setdefault(prev[0], []).append(secs)
        if step:
            open_step[p] = (step, e["created"])
        else:
            open_step.pop(p, None)
    medians = {k: statistics.median(v) for k, v in spans.items() if len(v) >= 2}
    _STEP_TIMES.update(at=time.time(), medians=medians)
    return medians


def _progress(steps, seen):
    """The step the run is on, how far along it is (0 to 100) and the seconds it likely has
    left (None when past runs do not say). Uses the step medians when there are some, else
    counts each step the same."""
    keys = {label: key for key, label, _ in STEPS}
    current = next((s for s in steps if s.get("state") in ("active", "waiting", "failed")), None)
    done = [s for s in steps if s.get("state") == "done"]
    if not current and len(done) == len(steps):
        return {"current": None, "percent": 100, "eta_s": 0 if steps else None}
    medians = step_medians()
    key = keys.get(current["label"]) if current else None
    elapsed = _age(seen[key]["created"]) if key in seen else 0
    timed = all(keys[s["label"]] in medians for s in steps)
    if timed:
        total = sum(medians[keys[s["label"]]] for s in steps) or 1
        part = sum(medians[keys[s["label"]]] for s in done)
        if current:
            part += min(elapsed, 0.95 * medians[key])
        later = [s for s in steps if not s.get("state")]
        left = (max(medians[key] - elapsed, 0.1 * medians[key]) if current else 0) + \
            sum(medians[keys[s["label"]]] for s in later)
        percent, eta = 100 * part / total, round(left)
    else:
        percent, eta = 100 * (len(done) + (0.5 if current else 0)) / max(len(steps), 1), None
    return {"current": current["label"] if current else None, "percent": int(min(max(percent, 0), 99)),
            "eta_s": eta}


def snapshot():
    project = current_project()
    start_event = db.last_event("build_started", project) if project else None
    start = start_event["created"] if start_event else None
    seats = runner.status_snapshot()
    agents = []
    for s in seats:
        entry = runner.STATUS.get(s["id"])
        if not entry:
            continue
        if s["id"].startswith("builder-") and s["id"] != "builder-1" and entry.get("project") != project:
            continue
        agents.append(_agent(entry, project, start))
    builders = [a["id"] for a in agents if a["role"] == "builder"]
    recent = db.events_since(_ago_stamp(HANDOFF_S), ["handoff", "meeting"])
    handoffs = [{"id": f"h{e['id']}", "from": e["data"]["from"], "to": e["data"]["to"], "label": e["data"]["label"]}
                for e in recent if e["kind"] == "handoff"]
    meetings = [{"id": f"m{e['id']}", "kind": e["data"]["kind"], "agents": e["data"]["agents"]}
                for e in recent if e["kind"] == "meeting" and _age(e["created"]) <= MEETING_S]
    run = _run(project, start_event, builders) if project else None
    return {"project": project, "agents": agents, "handoffs": handoffs, "meetings": meetings,
            "runs": [run] if run else []}


# --- the panels and screens ---------------------------------------------------------------

def _rel(project, path):
    return f"/files/{project}/" + Path(path).relative_to(config.projects_dir() / project).as_posix()


def _stamped(project, path):
    """A download's address with the file's modification time: a file written again (a re-export)
    gets a new address, so no browser keeps showing the copy it saved before."""
    try:
        return f"{_rel(project, path)}?t={int(Path(path).stat().st_mtime)}"
    except OSError:
        return _rel(project, path)


def _pretty(stem):
    return stem.replace("closeup_", "close-up ").replace("_", " ").strip()


def _review(project, v, rubric):
    review = v.get("review")
    if not review:
        return None
    folder = pipeline.version_dir(project, v["number"]) / "renders"
    renders = [{"name": _pretty(p.stem), "url": _rel(project, p)} for p in sorted(folder.glob("*.png"))] \
        if folder.is_dir() else []
    names = {c["id"]: c["name"] for c in rubric["criteria"]}
    gates = ((v.get("checks") or {}).get("gates") or {})
    gate_names = {g["id"]: g["name"] for g in rubric.get("gates", [])}
    return {
        "version": f"v{v['number']}", "score": round((review.get("score") or 0) / 10, 1),
        "summary": review.get("summary", ""), "passed": v.get("passed"),
        "criteria": [{"name": names.get(k, k), "score": round(float(s), 1)} for k, s in (review.get("scores") or {}).items()],
        "changes": [f"{c.get('target')}: " + " ".join(x for x in (c.get("parameter"), c.get("change")) if x)
                    for c in review.get("changes") or []],
        "gates": [{"name": gate_names.get(k, k), "pass": bool(g.get("pass")), "detail": g.get("detail", "")}
                  for k, g in gates.items()],
        "renders": renders,
    }


def _thumb(project, number):
    folder = pipeline.version_dir(project, number) / "renders"
    for name in ("turntable_00.png", "sheet_turntable.jpg"):
        if (folder / name).is_file():
            return _rel(project, folder / name)
    pngs = sorted(folder.glob("turntable_*.png")) if folder.is_dir() else []
    return _rel(project, pngs[0]) if pngs else None


def _live_label(key, attempt):
    what = {"assembly": "assembled, no materials yet", "materials": "with materials"}.get(key, key.replace("_", " "))
    return f"{what} · attempt {attempt}"


def live(project, number):
    """The live 3D views of a version being built: the newest GLB a build step exported, and the
    newest one of each part, the assembly and the materials. None when there is none yet."""
    folder = pipeline.version_dir(project, number) / "previews"
    newest = {}
    for glb in folder.glob("*/*/live.glb") if folder.is_dir() else []:
        key, attempt = glb.parent.parent.name, glb.parent.name
        if not attempt.isdigit():
            continue
        stamp = glb.stat().st_mtime
        if key not in newest or int(attempt) > newest[key]["attempt"]:
            newest[key] = {"key": key, "kind": key if key in ("assembly", "materials") else "part",
                           "attempt": int(attempt), "url": _rel(project, glb), "label": _live_label(key, int(attempt)),
                           "at": stamp}
    if not newest:
        return None
    steps = sorted(newest.values(), key=lambda e: e["at"])
    return {"latest": steps[-1], "steps": steps}


GAME_FILES = (("game", "_game.glb"), ("lod1", "_LOD1.glb"), ("lod2", "_LOD2.glb"), ("collision", "_collision.glb"))


def _game(project, folder):
    """The game-ready files of a version (its game folder): a link to each, and the triangle
    counts, the limit and the size from game.json. None for a version without them."""
    game = folder / "game"
    if not game.is_dir():
        return None
    files = {key: _stamped(project, game / f"{project}{tail}") for key, tail in GAME_FILES
             if (game / f"{project}{tail}").is_file()}
    if not files:
        return None
    try:
        info = json.loads((game / "game.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        info = {}
    return {"files": files, "triangles": info.get("triangles") or {}, "budget": info.get("budget"),
            "reduced": bool(info.get("reduced")), "size": info.get("size")}


def _plan(project, number):
    try:
        return json.loads((pipeline.version_dir(project, number) / "plan.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _path(project, number, plan=None):
    """How a version was made: from a ready-made model (library), by the car generator (remix),
    as a car body from a sheet (shell), as a rigged person from a character sheet (character), or
    part by part by the team (team)."""
    plan = _plan(project, number) if plan is None else plan
    kind = plan.get("kind")
    return kind if kind in ("library", "remix", "shell", "character") else "team"


def brief(project, number=None):
    """What the Brief tab shows: the request and what the person set, the reference images, what
    the research found, the spec in short, and how the model is being made."""
    proj = db.get_project(project) or {}
    options, spec = proj.get("options") or {}, proj.get("spec") or {}
    plan = _plan(project, number) if number else {}
    out = {"request": proj.get("request", ""), "size": options.get("size"), "budget": options.get("budget_usd"),
           "references": [f"/uploads/{r}" for r in options.get("references") or []],
           "found": [f"/uploads/{r}" for r in options.get("found_references") or []],
           "path": _path(project, number, plan) if number else None}
    if spec:
        out["spec"] = {k: spec.get(k) for k in ("object", "summary", "family", "category", "style", "use",
                                                "dimensions", "materials", "notes") if spec.get(k)}
        out["spec"]["parts"] = len(spec.get("parts") or [])
        out["spec"]["motion"] = bool(at.has_motion(spec))
    if spec.get("research"):
        out["research"] = spec["research"]
    asset = plan.get("asset") if plan.get("kind") == "library" else plan.get("car") if plan.get("kind") == "remix" else None
    if asset:
        out["asset"] = {k: asset.get(k) for k in ("name", "asset", "source", "licence", "license", "base", "type", "era",
                                                  "paint_name", "note", "triangles") if asset.get(k)}
        out["candidates"] = len(plan.get("candidates") or [])
    return out


def versions(project, rubric=None):
    """The model panel's versions, newest first, in the page's shape."""
    rubric = rubric or at.load_rubric()
    running = pipeline.is_running(project)
    proj = db.get_project(project)
    rows = db.versions(project)
    phases = db.events_of(["phase"], project=project, limit=30)
    out = []
    for v in reversed(rows):
        n = v["number"]
        folder = pipeline.version_dir(project, n)
        exports = folder / "exports"
        downloads = {p.suffix[1:]: _stamped(project, p) for p in sorted(exports.iterdir())
                     if p.suffix in (".glb", ".fbx", ".obj", ".stl")} if exports.is_dir() else {}
        if (folder / "model.blend").is_file():
            downloads["blend"] = _stamped(project, folder / "model.blend")
        cost = db.spend_where(project, version=n)
        game = _game(project, folder)
        if v["status"] == "building" and running:
            state = "building"
            mine = [e for e in phases if e["data"].get("version") == n and e["data"].get("step")]
            step = mine[-1]["data"]["step"] if mine else "build"
            order = ["build", "assemble", "materials", "render", "review"]
            progress = int(100 * (order.index(step) if step in order else 0) / len(order)) + 10
            meta = f"Building · started {_clock(v['created'])}"
        elif v["status"] == "failed":
            state, progress = "failed", None
            meta = f"{_clock(v['created'])} · failed · ${cost:.2f}"
        elif v["status"] == "building":
            state, progress, meta = "failed", None, f"{_clock(v['created'])} · interrupted"
        else:
            state, progress = "scored", None
            meta = f"{_clock(v['created'])} · ${cost:.2f}" + (" · passed" if v.get("passed") else "")
        out.append({
            "id": f"v{n}", "number": n, "state": state, "progress": progress, "meta": meta,
            "score": round(v["score"] / 10, 1) if v.get("score") is not None else None, "passed": v.get("passed"),
            "accepted": proj.get("accepted_version") == n, "base": proj.get("base_version") == n,
            "thumb": _thumb(project, n), "glb": downloads.get("glb"), "downloads": downloads,
            "export": f"/api/projects/{project}/versions/{n}/export.zip" if folder.is_dir() else None,
            "review": _review(project, v, rubric), "note": v.get("note", ""),
            "live": live(project, n) if state == "building" else None,
            "game": game, "path": _path(project, n),
        })
    return out


def _status_word(proj):
    status = proj["status"]
    if pipeline.is_running(proj["name"]) or status in LIVE:
        return "working" if pipeline.is_running(proj["name"]) else "failed"
    return {"done": "done", "accepted": "done", "needs_changes": "idle", "new": "idle"}.get(status, "failed")


def model(project, selected=None):
    proj = db.get_project(project)
    if not proj:
        return None
    vs = versions(project)
    scored = [v for v in vs if v["score"] is not None]
    pick = (selected if any(v["id"] == selected for v in vs) else None) \
        or (f"v{proj['accepted_version']}" if proj.get("accepted_version") else None) \
        or (vs[0]["id"] if vs and vs[0]["state"] == "building" and len(vs) == 1 else None) \
        or (f"v{proj['base_version']}" if proj.get("base_version") else None) \
        or (scored[0]["id"] if scored else (vs[0]["id"] if vs else None))
    # Why the last build stopped: the newest failed agent run's error (e.g. a usage limit), for the panel.
    error = None
    if proj["status"] in ("failed", "stopped", "interrupted") and not pipeline.is_running(project):
        error = next((r["error"] for r in db.runs(project, limit=20) if not r["ok"] and r["error"]), None)
    return {"name": proj["title"], "project": project, "status": proj["status"], "error": error,
            "running": pipeline.is_running(project), "selected": pick,
            "accepted": f"v{proj['accepted_version']}" if proj.get("accepted_version") else None,
            "request": proj.get("request", ""), "options": proj.get("options") or {}, "versions": vs,
            "next": pipeline.next_changes(project),
            "brief": brief(project, int(pick[1:]) if pick else None)}


def models():
    out = []
    for p in db.list_projects():
        name = p["name"]
        vs = db.versions(name)
        scored = [v for v in vs if v.get("score") is not None]
        best = max(scored, key=lambda v: v["score"]) if scored else None
        shown = p.get("accepted_version") or (best["number"] if best else None)
        score = next((v["score"] for v in vs if v["number"] == shown and v.get("score") is not None), None)
        if pipeline.is_running(name):
            meta = f"v{vs[-1]['number']} building" if vs else "starting"
        elif p.get("accepted_version"):
            meta = f"v{shown} accepted · {score / 10:.1f}" if score is not None else f"v{shown} accepted"
        elif best:
            meta = f"v{best['number']} · {best['score'] / 10:.1f}" + (" · needs changes" if p["status"] == "needs_changes" else "")
        else:
            meta = {"stopped": "stopped","interrupted": "interrupted"}.get(p["status"], p["status"])
        client = db.project_client(name)
        if client:  # asked for by another program through the /v1 API
            meta = f"from {client} · {meta}"
        out.append({"name": p["title"], "project": name, "status": _status_word(p), "meta": meta,
                    "thumb": _thumb(name, shown) if shown else None, "created": p["created"], "client": client})
    return out


def parts():
    lib = Library()
    out = []
    for e in lib.list():
        source = e.get("source", "")
        frm = source.split(" part ")[0] if source else "unknown"
        has = e["kind"] == "part" and lib.preview(e["name"]) is not None
        out.append({"name": e["name"], "kind": e["kind"], "from": frm.replace("-", " "),
                    "uses": e.get("uses", 0), "saved": _saved(e.get("updated")), "saved_at": e.get("updated", ""),
                    "description": e.get("description", ""),
                    "thumb": f"/library/previews/{e['name']}.jpg" if has else None})
    return sorted(out, key=lambda x: x["saved_at"], reverse=True)


def _saved(stamp):
    try:
        d = datetime.fromisoformat(stamp)
    except (TypeError, ValueError):
        return ""
    today = datetime.now().date()
    if d.date() == today:
        return "today " + d.strftime("%H:%M")
    if (today - d.date()).days == 1:
        return "yesterday"
    return d.strftime("%b %d").replace(" 0", " ")


def activity(agent=None, limit=150):
    """Everything the team did, newest first, for the Activity screen (agent: a cast id or role)."""
    role = talk.role_of(agent) if agent else None
    kinds = ["tool_done", "tool_call", "run", "handoff", "question", "answered", "version_done", "version_failed",
             "accepted", "warning", "error", "limit_raised"]
    rows = []
    for e in reversed(db.events_of(kinds, agent=role, limit=limit * 2)):
        d, kind = e["data"], e["kind"]
        if kind == "tool_call" and "args" not in d:
            continue  # a call of today's format; its tool_done row carries it with the result
        if kind == "run" and d.get("ok") is not False:
            continue  # only failed runs are worth a row
        cast = cast_id(e["agent"]) or ("hange" if kind in ("accepted", "version_done", "version_failed") else None)
        row = {"at": e["created"], "cast": cast, "kind": kind, "project": e["project"]}
        if kind == "tool_done":
            row.update(tool=d.get("tool"), text=d.get("text") or d.get("result"), ok=d.get("ok"))
        elif kind == "tool_call":  # logged before tool results were recorded
            args = d.get("args") or {}
            row.update(tool=d.get("tool"), text=runner._call_text(args, ""))
        elif kind == "run":
            row.update(text=f"{d.get('doing', '')} stopped: {d.get('error') or 'failed'}", ok=False)
        elif kind == "handoff":
            names = [talk.CAST.get(talk.role_of(x) or "", x) for x in (d.get("from"), d.get("to"))]
            row.update(kind="handoff", text=f"{names[0]} → {names[1]}: {d.get('label', '')}")
        elif kind == "question":
            row.update(tool="ask_human", text=d.get("text"), need=True)
        elif kind == "answered":
            row.update(text=f"you answered: {d.get('choice')}")
        elif kind == "version_done":
            row.update(text=f"v{d.get('version')} scored {d.get('score', 0) / 10:.1f}" + (" and passed" if d.get("passed") else ""))
        elif kind == "version_failed":
            row.update(text=f"v{d.get('version')} failed to build", ok=False)
        elif kind == "accepted":
            row.update(text=f"you accepted v{d.get('version')}")
        elif kind == "limit_raised":
            row.update(text=f"{'daily' if d.get('which') == 'daily' else 'model'} limit raised to ${d.get('limit'):.2f}")
        else:
            row.update(text=d.get("text"), ok=False if kind == "error" else None)
        rows.append(row)
        if len(rows) >= limit:
            break
    return rows


def chat(limit=300):   # enough for each model's own talk: the page shows one model's messages at a time
    out = []
    for m in db.messages(limit):
        meta = m.get("meta") or {}
        item = {"id": m["id"], "from": "you" if m["role"] == "user" else "hange", "text": m["text"],
                "time": _clock(m["created"]), "at": m["created"], "project": m.get("project")}
        if meta.get("attachments"):
            item["attachments"] = [f"/uploads/{a}" for a in meta["attachments"]]
        if meta.get("version"):
            item["version"] = meta["version"]
        out.append(item)
    return out


def settings():
    s = settings_store.load()
    exe = config.blender_path()
    return {**s, "blender_found": bool(exe), "blender_in_use": exe, "blender_version": config.blender_version(exe),
            "install": config.INSTALL_HINT}


def page_data(project=None, version=None):
    project = project if project and db.get_project(project) else current_project()
    spent = db.spend(project)
    lim = budget.limits(project)
    exe = config.blender_path()
    ver = config.blender_version(exe)
    return {
        "project": project,
        "model": model(project, version) if project else None,
        "models": models(),
        "parts": parts(),
        "chat": chat(),
        "spend": f"${spent['model']:.2f} this model" if project else "",
        "budget": f"Today ${spent['today']:.2f} of ${lim['daily_usd']:.2f}",
        "blender": (f"Blender {ver} · ready" if ver else "Blender ready") if exe else "Blender not installed",
        "settings": settings(),
        "limits": lim,
    }
