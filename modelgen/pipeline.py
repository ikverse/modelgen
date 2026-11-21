"""The ModelGen loop: brief, architect, builders in parallel, assembler, materials, render, critic.

A build makes loop.max_rounds versions (one by default) and then stops: the person decides whether
the next version is built, with the critic's change list, their own comments, or both. A next
version always starts from the best version so far (or the one the person picked), and each item
of the change list goes to the agent it names. Every version is a new folder
projects\\<name>\\v1, v2, ... holding its scripts, previews, renders, exports, checks and review;
every step is an event in the database so the page can follow along and history survives.
"""

import ast
import asyncio
import json
import math
import random
import re
import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path

from . import budget, change_router, config, db, families, runner
from .bpy_lib.shells import car_sheet
from .tools import agent_tools as at
from .tools import asset_catalog, blender, car_library, car_request, character, checks, mechanism, render
from .tools import clips as clip_lib
from .tools.files import ProjectFiles
from .tools.library import Library

RUNNING = {}
CARRY = ["parts", "assembly.py", "materials.py", "motion.py", "part_reports.json"]


class Busy(Exception):
    pass


class PipelineError(Exception):
    pass


@dataclass
class Round:
    project: str
    number: int
    dir: Path
    files: ProjectFiles
    spec: dict
    plan: dict
    sink: dict = field(default_factory=dict)
    attempts: dict = field(default_factory=dict)
    part_ok: dict = field(default_factory=dict)
    part_errors: dict = field(default_factory=dict)
    part_reports: dict = field(default_factory=dict)
    notes: dict = field(default_factory=dict)  # this version's notes, by target (a part, assembly, materials)
    prev_dir: Path = None  # the version this one starts from, whose renders the notes were written about


def version_dir(project, number):
    return config.projects_dir() / project / f"v{number}"


def is_running(project):
    task = RUNNING.get(project)
    return bool(task and not task.done())


def start(project, request=None, changes=None, on_done=None, next_version=False, review=None):
    """Start the loop for a project in the background. Raises Busy if it is already running.
    changes: the person's comments for the next version (the critic's open changes go with them).
    next_version: build the next version from the critic's change list alone.
    review: a version number to have the critic review again, nothing rebuilt."""
    if is_running(project):
        raise Busy(f"'{project}' is still being built")
    task = asyncio.create_task(run(project, request, changes, on_done, next_version, review))
    RUNNING[project] = task
    return task


def stop(project):
    """Stop a running build: every agent and Blender process in it ends now. Returns False if
    nothing is running for that project."""
    if not is_running(project):
        return False
    db.add_event("stop_requested", project=project)
    RUNNING[project].cancel()
    return True


def _mark_stopped(project):
    for v in db.versions(project):
        if v["status"] == "building":
            db.update_version(project, v["number"], status="failed", note="stopped")


async def run(project, request=None, changes=None, on_done=None, next_version=False, review=None):
    kind = "review" if review else "changes" if changes else "next" if next_version else "new"
    text = f"review v{review} again" if review else changes or request or ""
    began = db.add_event("build_started", project=project, data={"kind": kind, "text": text})
    clock = time.monotonic()
    try:
        outcome = await (_review_again(project, review) if review else _run(project, request, changes, next_version))
    except asyncio.CancelledError:
        # stop() cancelled the build; finish recording it and report, instead of ending silently
        asyncio.current_task().uncancel()
        outcome = {"ok": False, "error": "cancelled"}
        db.update_project(project, status="stopped")
        _mark_stopped(project)
        db.add_event("warning", project=project, agent="director", data={"text": "Build stopped by you"})
    except budget.BudgetStop as exc:
        outcome = {"ok": False, "error": "stopped", "reason": str(exc)}
        db.update_project(project, status="stopped")
        db.add_event("warning", project=project, agent="director", data={"text": f"Build {exc}"})
    except Exception as exc:
        outcome = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
        db.update_project(project, status="failed")
        db.add_event("error", project=project, data={"text": outcome["error"]})
    keep = outcome.pop("keep", None)  # the version whose good parts the librarian keeps
    db.add_event("finished", project=project, data=outcome)

    async def tell():
        if db.project_client(project):
            pass  # made for another program (the /v1 API): its job reports to it, not to the chat
        elif outcome.get("remix"):
            _report_remix(project, outcome)  # said plainly: no agent writes the report of a car made without agents
        elif outcome.get("library"):
            _report_library(project, outcome)
        elif outcome.get("parts"):
            _report_parts(project, outcome)
        elif outcome.get("character"):
            _report_character(project, outcome)
        elif on_done:
            try:
                await on_done(project, outcome)
            except Exception as exc:
                db.add_event("error", project=project, data={"text": f"reporting failed: {exc}"})

    # the person gets the report while the librarian files the good parts: neither waits for the other
    await asyncio.gather(tell(), *([_keep_parts(project, keep)] if keep else []))
    _write_timings(project, began["id"], kind, outcome, time.monotonic() - clock)
    return outcome


async def _keep_parts(project, number):
    """The librarian's step, after the build's result is known. Never fails the build."""
    try:
        _handoff(project, "critic", "librarian", f"v{number} to keep")
        _phase(project, "The librarian is keeping the good parts", step="library", version=number)
        await _librarian(project, number, Library())
    except Exception as exc:
        db.add_event("warning", project=project, agent="librarian",
                     data={"text": f"the library was not updated ({type(exc).__name__}: {exc})"})


def _seconds(event):
    return time.mktime(time.strptime(event["created"][:19], "%Y-%m-%dT%H:%M:%S"))


def _write_timings(project, after_id, kind, outcome, total):
    """Where a build's time went, as timings.json in its version's folder: seconds per step of the
    loop, per agent and per tool, read from the build's own events. Never fails the build."""
    try:
        number = outcome.get("version")
        if not number or kind == "review":  # a review again builds nothing: the version keeps its build's timings
            return
        events = db.events(project, after_id=after_id, limit=20000)
        steps, agents, tools, opened = {}, {}, {}, {}
        phases = [e for e in events if e["kind"] == "phase" and e["data"].get("step")]
        ends = [_seconds(e) for e in phases[1:]] + ([_seconds(events[-1])] if events else [])
        for e, end in zip(phases, ends):
            step = e["data"]["step"]
            steps[step] = steps.get(step, 0) + max(0, round(end - _seconds(e)))
        for e in events:
            data = e["data"]
            if e["kind"] == "run":
                a = agents.setdefault(data.get("role") or e["agent"], {"runs": 0, "seconds": 0.0, "usd": 0.0})
                a["runs"] += 1
                a["seconds"] = round(a["seconds"] + float(data.get("seconds") or 0), 1)
                a["usd"] = round(a["usd"] + float(data.get("cost_usd") or 0), 4)
            elif e["kind"] == "tool_call":
                opened.setdefault((e["agent"], data.get("tool")), []).append(_seconds(e))
            elif e["kind"] == "tool_done" and opened.get((e["agent"], data.get("tool"))):
                t = tools.setdefault(data.get("tool"), {"calls": 0, "seconds": 0})
                t["calls"] += 1
                t["seconds"] += max(0, round(_seconds(e) - opened[(e["agent"], data.get("tool"))].pop(0)))
        out = {"build": kind, "total_seconds": round(total, 1), "steps": steps, "agents": agents, "tools": tools}
        (version_dir(project, number) / "timings.json").write_text(_json(out), encoding="utf-8")
    except Exception as exc:
        db.add_event("warning", project=project, data={"text": f"the timings were not written ({exc})"})



def _saved_sheets(renders):
    """The contact sheets a finished version's render left in its renders folder, by name, as the
    critic's tools take them."""
    sheets = {}
    for name in ("turntable", "wireframe", "closeups", "car_views", "reference_pairs", "family_views", "motion"):
        f = renders / f"sheet_{name}.jpg"
        if f.is_file():
            sheets[name] = str(f)
    large = [str(f) for f in sorted(renders.glob("critic_turntable_*.jpg"))]
    if large:
        sheets["turntable_large"] = large
    return sheets


def _built_for_changes(project, number):
    """Whether a version was built for the person's own changes: the build that started it, read
    from the build's own events. False when those events are gone."""
    kind = None
    for e in db.events_of(["build_started", "version_started"], project=project, limit=5000):
        if e["kind"] == "build_started":
            kind = e["data"].get("kind")
        elif e["data"].get("version") == number:
            return kind == "changes"
    return False


async def _review_again(project, number):
    """Have the critic review a built version again (one it could not review, or to check it anew):
    nothing is rebuilt; the version gets the new score and review."""
    v = db.get_version(project, number)
    folder = version_dir(project, number)
    renders = folder / "renders"
    checks_file = folder / "checks.json"
    if not v or v["status"] != "done" or not checks_file.is_file() or not (renders / "sheet_turntable.jpg").is_file():
        return {"ok": False, "error": f"v{number} has no finished renders to review"}
    spec = json.loads((folder / "spec.json").read_text(encoding="utf-8"))
    plan = json.loads((folder / "plan.json").read_text(encoding="utf-8"))
    rnd = Round(project=project, number=number, dir=folder, files=ProjectFiles(folder), spec=spec, plan=plan)
    found = json.loads(checks_file.read_text(encoding="utf-8"))
    outcome = {"ok": True, "checks": found, "sheets": _saved_sheets(renders)}
    earlier = [x for x in db.versions(project) if x["number"] < number and x.get("review")]
    last_review = earlier[-1]["review"] if earlier else None
    prev_dir = version_dir(project, earlier[-1]["number"]) if earlier else None
    _phase(project, f"The critic is reviewing v{number} again", step="review", version=number)
    # a version built for the person's own changes was not built from the earlier change list: the
    # critic sees that list for what it is and marks none of it, as when the version was built
    review = await _critic(rnd, outcome, last_review, prev_dir, judge=not _built_for_changes(project, number))
    reviewed = review.get("score") is not None
    pass_score = float(config.get("loop", "pass_score", 80))
    passed = reviewed and found["passed"] and review["score"] >= pass_score
    rnd.files.write("review.json", _json(review))
    db.update_version(project, number, score=review["score"], passed=int(passed),
                      review=review if reviewed else None)
    db.add_event("version_done", project=project,
                 data={"version": number, "score": review["score"], "passed": passed, "summary": review["summary"],
                       "changes": len(review.get("changes") or [])})
    if reviewed:
        db.update_project(project, status="done" if passed else "needs_changes")
    return {"ok": True, "version": number, "score": review["score"], "passed": passed, "summary": review["summary"],
            "changes": (review.get("changes") or [])[:3], "gates": found.get("gates"),
            **({} if reviewed else {"not_reviewed": True})}

STEPS = ["catalog", "research", "spec", "plan", "build", "assemble", "materials", "render", "review", "library"]


def _phase(project, text, step=None, **data):
    """A phase of the loop. step is one of STEPS; the page's run panel follows these."""
    db.add_event("phase", project=project, data={"text": text, "step": step, **data})


def _handoff(project, sender, receiver, label):
    """Work passes from one agent to the next: the map shows the sender carrying it over."""
    db.add_event("handoff", project=project, agent=sender, data={"from": sender, "to": receiver, "label": label})


def _meeting(project, kind, agents):
    db.add_event("meeting", project=project, data={"kind": kind, "agents": list(agents)})


def _json(obj):
    return at.dumps(obj)


# --- the loop -------------------------------------------------------------------------

async def _run(project, request, changes, next_version=False):
    if not blender.available():
        db.update_project(project, status="failed")
        return {"ok": False, "error": "blender_missing"}
    proj = db.get_project(project)
    existing = db.versions(project)
    sheet, custom = None, []  # a mechanism the team builds (a part has no tool): every agent gets its sheet
    made = await _try_parts(project, request, changes, next_version)
    if made is not None and made.get("team"):
        sheet, custom = made["sheet"], made.get("custom") or []
    elif made is not None:
        return made
    if sheet is None:
        made = await _try_character(project, request, changes, next_version)
        if made is not None:
            return made
    if uses_remix(project, request, changes or next_version):
        return await _run_remix(project, request, changes, next_version)
    made = await _try_library(project, request, changes, next_version)
    if made is not None:
        return made
    # a ready-made model the person wants built by the team instead: a first build, with their words
    fresh = bool(existing) and library_asset(project, base_number(project)) is not None
    library = Library()
    start_from = None  # in next_version mode: the version this build starts from, to beat
    judge = True  # the critic marks the last review's changes landed or not (not after the person's own changes)

    if (changes or next_version) and existing and not fresh:
        base = base_number(project)
        base_dir = version_dir(project, base)
        spec = json.loads((base_dir / "spec.json").read_text(encoding="utf-8"))
        plan = json.loads((base_dir / "plan.json").read_text(encoding="utf-8"))
        if changes:
            db.update_project(project, status="planning")
            _phase(project, f"Reading what your changes need on top of v{base}", step="plan", base=base)
            note = f"The person asked for these changes: {changes}"
            routed = await change_router.route(project, changes, spec, plan)
            if routed:
                # only what the change touches is worked on: every other part and stage keeps the base's files
                mine = note + ("\nDo exactly what in this concerns your own work and nothing else: the rest of it "
                               "goes to the others it names, and everything it does not mention stays as it is.")
                rebuild = set(routed["parts"])
                notes = {name: [mine] for name in rebuild} | {k: [mine] for k in change_router.STAGES if routed[k]}
                _handoff(project, "director", "builder" if rebuild else _role_of(next(iter(notes))), "your changes")
                _phase(project, f"Your changes go straight to {change_router.describe(routed)}", step="plan",
                       base=base, routed=routed)
            else:
                _handoff(project, "director", "architect", "your changes")
                _phase(project, f"Planning the changes on top of v{base}", step="plan", base=base)
                if at.shell_template(plan=plan):
                    plan, rebuild = await _shell_architect_change(project, spec, plan, changes, library, base_dir)
                    notes = {name: [note] for name in rebuild} | {"materials": [note]}  # the assembly follows the sheet
                else:
                    plan, rebuild = await _architect_change(project, spec, plan, changes, library)
                    notes = {name: [note] for name in rebuild} | {"assembly": [note], "materials": [note]}
                if at.has_motion(spec):
                    notes["motion"] = [note]
            # the person's own changes are built alone: the critic's open list waits for "Build next version"
            _, last_review = critic_notes_of(project, base, at.plan_parts(plan))
            judge = False
        else:
            _phase(project, f"Building the next version on top of v{base} with the critic's changes", step="plan",
                   base=base)
            rebuild, notes = set(), {}
            critic_notes, last_review = critic_notes_of(project, base, at.plan_parts(plan))
            for target, items in critic_notes.items():
                notes.setdefault(target, []).extend(items)
            rebuild |= {k for k in critic_notes if k in at.plan_parts(plan)}
            plan, rebuild = await _plan_changes(project, spec, plan, notes, rebuild, library)
        if next_version and last_review:
            start_from = {"version": base, "score": last_review["score"], "dir": base_dir}
        prev_dir = base_dir
    else:
        db.update_project(project, status="briefing")
        _meeting(project, "kickoff", ["director", "brief", "architect"])
        _handoff(project, "director", "brief", "your request")
        _phase(project, "Writing the spec", step="spec")
        request = request or proj["request"]
        if changes:  # changes asked for before any version was built: they shape the first one
            request += f"\n\nThe person also asked for these changes: {changes}"
        spec = await _brief(project, request, sheet)
        if sheet:
            spec["mechanism_custom"] = custom
        db.update_project(project, spec=spec, status="planning")
        _handoff(project, "brief", "architect", f"spec: {spec.get('object', 'the model')}")
        if at.shell_template(spec=spec):
            _phase(project, "Planning the car as one body from a sheet", step="plan", object=spec.get("object"))
            plan = await _shell_architect(project, spec, library)
        else:
            _phase(project, "Planning how each part is built", step="plan", object=spec.get("object"))
            plan = await _architect(project, spec, library)
        if spec.get("mechanism"):
            plan = with_tool_parts(plan, spec["mechanism"])
        rebuild, notes, prev_dir, last_review = set(at.plan_parts(plan)), {}, None, None
    if rebuild or not prev_dir:
        _handoff(project, "architect", "builder", f"plan: {len(at.plan_parts(plan))} parts")

    db.update_project(project, spec=spec, status="building")
    max_rounds = max(1, int(config.get("loop", "max_rounds", 1)))
    pass_score = float(config.get("loop", "pass_score", 80))
    best = None
    anchor = start_from  # the best version so far: a next round starts from it, never from a worse one

    for round_index in range(max_rounds):
        n = db.highest_version_number(project) + 1  # a deleted version's number is never used again
        rnd = _new_round(project, n, spec, plan, prev_dir)
        rnd.notes = notes
        plan.pop("reseed", None)  # parts to start again from the plan's seed code: the first round only
        db.add_version(project, n, note=f"round {round_index + 1}")
        db.add_event("version_started", project=project, data={"version": n, "round": round_index + 1})

        parts = at.plan_parts(plan)
        todo = [p for p in parts if p in rebuild or not rnd.files.exists(f"parts/{p}.py")]
        # Parts rebuilt on top of a finished model: its assembly, materials and motion are kept and
        # checked against how they came out before, and an agent redoes one only when it has notes or
        # the check finds it no longer fits. `before` is read while the builders work.
        carried = bool(prev_dir) and all(rnd.files.exists(f) for f in ("assembly.py", "materials.py"))
        before, sizes = None, {p: (rnd.part_reports.get(p) or {}).get("size_m") for p in todo}
        if todo:
            _phase(project, f"Builders are writing {len(todo)} part(s): {', '.join(todo)}", step="build",
                   version=n, parts=todo)
            _, before = await asyncio.gather(_build_parts(rnd, todo, notes, library, prev_dir),
                                             _stage_report(_round_of(project, prev_dir)) if carried else _none())
            _handoff(project, "builder", "assembler", f"{len(todo)} built part{'s' if len(todo) != 1 else ''}")
        asm_notes, assembled = list(notes.get("assembly", [])), False
        if at.shell_template(plan=plan):
            if todo or asm_notes or not rnd.files.exists("assembly.py"):
                _phase(project, "Assembling the parts", step="assemble", version=n)
                assembled = await _assemble_shell(rnd, asm_notes)
        elif asm_notes or not rnd.files.exists("assembly.py"):
            _phase(project, "Assembling the parts", step="assemble", version=n)
            await _assemble(rnd, asm_notes)
            assembled = True
        elif todo:
            _phase(project, "Checking that the rebuilt parts still fit the model", step="assemble", version=n)
            faults = await _assembly_faults(rnd) + _size_notes(rnd, todo, sizes)
            if faults:
                _phase(project, "Assembling the parts again", step="assemble", version=n)
                await _assemble(rnd, faults)
                assembled = True
        if todo or assembled:
            _handoff(project, "assembler", "materials", "the assembled model")
        if notes.get("materials") or not rnd.files.exists("materials.py"):
            _phase(project, "Choosing and tuning materials", step="materials", version=n)
            await _materials(rnd, notes.get("materials", []), library)
        elif todo:
            _phase(project, "Checking the materials still reach the rebuilt parts", step="materials", version=n)
            fault = await _stage_fault(rnd, before, "materials", todo)
            if fault:
                _phase(project, "Tuning the materials again", step="materials", version=n)
                await _materials(rnd, [fault], library)
        # the motion follows the parts and their placement: redone when the placement changed or it has
        # notes, checked when only parts were rebuilt
        moved = False
        if at.has_motion(spec):
            if asm_notes or assembled or notes.get("motion") or not rnd.files.exists("motion.py"):
                _handoff(project, "materials", "animator", "the model with materials")
                _phase(project, "Adding the motion", step="motion", version=n)
                await _animate(rnd, notes.get("motion", []))
                moved = True
            elif todo:
                _phase(project, "Checking the motion still drives the rebuilt parts", step="motion", version=n)
                fault = await _stage_fault(rnd, before, "motion", todo)
                if fault:
                    _handoff(project, "materials", "animator", "the model with materials")
                    _phase(project, "Adding the motion again", step="motion", version=n)
                    await _animate(rnd, [fault])
                    moved = True
        rnd.files.write("part_reports.json", _json(rnd.part_reports))

        _phase(project, "Building, exporting and rendering the full model", step="render", version=n)
        outcome = await _final_build(rnd)
        if not outcome["ok"]:
            db.update_version(project, n, status="failed", note=outcome["error"][:500])
            db.add_event("version_failed", project=project, data={"version": n, "error": outcome["error"][:2000]})
            if outcome.get("fatal"):
                break
            notes = _route_failure(outcome, parts)
            rebuild = {k for k in notes if k in parts}
            _handoff(project, "assembler", _role_of(next(iter(notes))), "the build failed here")
            prev_dir = rnd.dir
            continue

        _handoff(project, "animator" if moved else "materials", "critic",
                 f"v{n} with {'motion' if moved else 'materials'}")
        _phase(project, "The critic is reviewing the renders", step="review", version=n)
        review = await _critic(rnd, outcome, last_review, prev_dir, judge=judge)
        judge = True  # a further round in this build works on this review's own changes
        reviewed = review.get("score") is not None
        passed = reviewed and outcome["checks"]["passed"] and review["score"] >= pass_score
        rnd.files.write("review.json", _json(review))
        # a version the critic could not review keeps no score and no review: "review again" can give it one
        db.update_version(project, n, status="done", score=review["score"], passed=int(passed),
                          review=review if reviewed else None, checks=outcome["checks"])
        db.add_event("version_done", project=project,
                     data={"version": n, "score": review["score"], "passed": passed, "summary": review["summary"],
                           "changes": len(review.get("changes") or [])})
        rank = review["score"] if reviewed else -1.0
        if best is None or rank > best["rank"]:
            best = {"version": n, "score": review["score"], "rank": rank, "passed": passed,
                    "summary": review["summary"], "changes": review["changes"][:3],
                    "gates": outcome["checks"]["gates"]}
            if outcome["checks"].get("game"):
                best["game_files"] = {k: outcome["checks"]["game"].get(k) for k in ("triangles", "budget")}
        if reviewed and (anchor is None or anchor.get("score") is None or review["score"] > anchor["score"]):
            anchor = {"version": n, "score": review["score"], "dir": rnd.dir}
        if not reviewed:
            db.add_event("warning", project=project, agent="critic",
                         data={"text": f"v{n} could not be reviewed; ask for \"review v{n} again\" to score it"})
            break  # no review, so no changes for another round
        if passed:
            _handoff(project, "critic", "director", f"v{n} passed with {review['score'] / 10:.1f}")
            break
        if round_index + 1 >= max_rounds:
            break
        # another round in this build (only when loop.max_rounds > 1): it starts from the best version so far
        if anchor["version"] == n:
            notes, last_review, prev_dir = _route_changes(review, outcome, parts), review, rnd.dir
        else:
            notes, last_review = critic_notes_of(project, anchor["version"], parts)
            prev_dir = anchor["dir"]
            db.add_event("warning", project=project, agent="critic",
                         data={"text": f"v{n} scored lower than v{anchor['version']}; the next version starts "
                                       f"from v{anchor['version']}"})
        rebuild = {k for k in notes if k in parts}
        plan, rebuild = await _plan_changes(project, spec, plan, notes, rebuild, library)
        _meeting(project, "review", ["director", "critic", "assembler", "materials"])
        for target in sorted({_role_of(k) for k in notes}):
            count = sum(len(v) for k, v in notes.items() if _role_of(k) == target)
            _handoff(project, "critic", target, f"{count} change{'s' if count != 1 else ''} for v{n + 1}")

    if best is None:
        db.update_project(project, status="failed")
        return {"ok": False, "error": "no version could be built; see the events for the tracebacks"}
    # The next version starts from the best one. After the person's own changes that is this build's
    # best (it carries their changes); after the critic's changes alone, a worse result keeps the old one.
    best.pop("rank", None)
    base_after = anchor["version"] if anchor else best["version"]
    db.update_project(project, status="done" if best["passed"] else "needs_changes", base_version=base_after)
    extra = {}
    if base_after != best["version"]:
        extra["next_starts_from"] = (f"v{best['version']} scored lower than v{base_after}, so the next version "
                                     f"starts from v{base_after}")
    if best["score"] is None:
        extra["not_reviewed"] = True
        _handoff(project, "critic", "director", f"v{best['version']} could not be reviewed")
    elif not best["passed"]:
        _handoff(project, "critic", "director", f"best: v{best['version']} with {best['score'] / 10:.1f}")
    return {"ok": True, **best, **extra, "keep": best["version"]}  # run() has the librarian keep its good parts



# --- an object made from a ready-made free model (tools.asset_catalog): no build team --------

FROM_SCRATCH = re.compile(r"\b(from scratch|original|custom|your own|build it yourself|not ready[- ]made|"
                          r"no ready[- ]made|by the team|hand[- ]?built)\b", re.IGNORECASE)


def library_asset(project, number):
    """The ready-made model a version was made from (its plan), or None for any other version."""
    plan_file = version_dir(project, number) / "plan.json" if number else None
    plan = json.loads(plan_file.read_text(encoding="utf-8")) if plan_file and plan_file.is_file() else {}
    return plan if plan.get("kind") == "library" else None


def ready_made(project, number):
    """Whether a version was made without the build team: a generated car or a ready-made model."""
    return remix_car(project, number) is not None or library_asset(project, number) is not None


def _library_on():
    return (str(config.get("objects", "generator", "library")).lower() == "library"
            and asset_catalog.CATALOG.is_file())


async def _cast(project, request, hits):
    """The casting step: a small agent reads the request and the catalogue's best candidates and
    says which one, if any, IS the object asked for. Returns the candidates' ids, the chosen first,
    or [] when none fits (then the team builds it)."""
    sink = {}
    shown = [{"id": h["entry"]["id"], "name": h["entry"]["name"], "description": h["entry"].get("description"),
              "categories": h["entry"].get("categories"), "tags": (h["entry"].get("tags") or [])[:12],
              "size_m": h["entry"].get("size"), "triangles": h["entry"].get("triangles"),
              "style": h["entry"].get("style"), "library": h["entry"].get("library")} for h in hits]
    prompt = (f"## The request\n{request}\n\n## Ready-made models that might be it (best match first)\n"
              f"{_json(shown)}\n\nCall `choose` once.")
    res = await runner.run_agent("caster", prompt, at.caster_tools(sink, [s["id"] for s in shown]),
                                 project=project, doing="looking for a ready-made model", step=True)
    picked = sink.get("asset")
    if not picked:
        if res and res.error:
            db.add_event("warning", project=project, agent="brief",
                         data={"text": f"casting did not decide ({res.error[:200]}); the team builds it"})
        return []
    return [picked] + [s["id"] for s in shown if s["id"] != picked]


def _library_checks(after):
    tol = float(config.get("model", "floor_tolerance_m", 0.002))
    dims = [after.get(k) or 0 for k in ("width", "length", "height")]
    gates = {
        "correct_size": {"pass": all(0.005 < d < 200 for d in dims),
                         "detail": f"{dims[0]:.2f} m wide, {dims[1]:.2f} m deep, {dims[2]:.2f} m high"},
        "on_floor": {"pass": abs(after.get("ground") or 0) <= max(tol, 0.005),
                     "detail": f"lowest point {(after.get('ground') or 0) * 1000:.0f} mm from the floor"},
    }
    return {"passed": all(g["pass"] for g in gates.values()), "gates": gates, "warnings": [],
            "triangles": after.get("triangles")}


async def _try_library(project, request, changes, next_version):
    """Make the model from a ready-made one when the catalogue has it. Returns the build's outcome,
    or None when the team should build it: nothing fits, the person asked for an original, or the
    instant path is off. A follow-up on a ready-made version changes its colour or size (changes),
    or takes the next candidate (next version)."""
    proj = db.get_project(project)
    base = base_number(project) if (changes or next_version) and db.versions(project) else None
    before = library_asset(project, base) if base else None
    if base and before is None:
        return None  # a follow-up on a version the team built
    if changes and FROM_SCRATCH.search(changes):
        return None
    catalogue = asset_catalog.load() if asset_catalog.CATALOG.is_file() else []
    size = asset_catalog.metres(((proj or {}).get("options") or {}).get("size"))
    db.update_project(project, status="planning")
    if before and changes:
        made, candidates = asset_catalog.changed(before["asset"], changes), before.get("candidates") or []
    elif before:
        candidates = before.get("candidates") or []
        used = before["asset"]["asset"]
        rest = candidates[candidates.index(used) + 1:] if used in candidates else []
        entry = next((e for e in (asset_catalog.entry_by_id(i, catalogue) for i in rest) if e), None)
        if entry is None:
            db.add_event("warning", project=project, agent="director",
                         data={"text": "there is no other ready-made model for this request"})
            db.update_project(project, status="done")
            return {"ok": False, "error": "no other ready-made model fits this request; ask for a colour or size "
                                          "change, or say \"build it from scratch\" for an original from the team"}
        made = asset_catalog.plan(before["asset"].get("request") or proj["request"], entry, size)
    else:
        text = request or proj["request"]
        if changes:
            text += f" {changes}"
        if (not _library_on() or FROM_SCRATCH.search(text) or at.looks_like_car(text)
                or families.guess(text) in ("mechanical", "person")):
            return None  # a downloaded model cannot be animated part by part, nor rigged as a character
        _phase(project, "Looking for a ready-made model", step="catalog")
        hits = asset_catalog.search(text, limit=6, entries=catalogue)
        if not hits:
            return None
        candidates = await _cast(project, text, hits)
        entry = asset_catalog.entry_by_id(candidates[0], catalogue) if candidates else None
        if entry is None:
            return None
        made = asset_catalog.plan(text, entry, size)
    what = asset_catalog.describe(made)
    family = families.guess(f"{made.get('request', '')} {made['name']}")
    spec = {"object": made["name"], "category": "library", "family": family, "use": "game",
            "request": made.get("request"), "summary": what}
    n = db.highest_version_number(project) + 1
    folder = version_dir(project, n)
    (folder / "exports").mkdir(parents=True, exist_ok=True)
    files = ProjectFiles(folder)
    files.write("spec.json", _json(spec))
    files.write("plan.json", _json({"kind": "library", "parts": [], "asset": made, "candidates": candidates}))
    db.update_project(project, spec=spec, status="building")
    db.add_version(project, n, note="made from a ready-made model")
    db.add_event("version_started", project=project, data={"version": n, "round": 1})
    _phase(project, f"Making {what}", step="build", version=n)

    def failed(error):
        db.update_version(project, n, status="failed", note=error[:500])
        db.add_event("version_failed", project=project, data={"version": n, "error": error[:2000]})
        db.update_project(project, status="failed")
        return {"ok": False, "error": error[:500]}

    blend = folder / "model.blend"
    try:
        with runner.acting("assembler", project, f"making the model for v{n}", "full_build") as act:
            record = await asyncio.to_thread(asset_catalog.make, made, folder / "exports" / f"{project}.glb", blend,
                                             catalogue)
            act.ok = True
    except RuntimeError as exc:
        return failed(str(exc))
    after = record["after"]
    found = _library_checks(after)
    files.write("measure.json", _json(after))
    files.write("asset.json", _json(record))
    _phase(project, "Rendering the model", step="render", version=n)
    (rendered, _, sheets), game = await asyncio.gather(_render(project, n, blend, folder / "renders"),
                                                       _game_export(folder, project, spec))
    if game:
        found["game"] = game
    files.write("checks.json", _json(found))
    if not rendered.ok or not sheets.get("turntable"):
        info = rendered.summary()
        return failed("rendering failed: " + (info.get("traceback") or info.get("error") or "no images"))
    tris = (game or {}).get("triangles", {}).get("game") or after.get("triangles")
    summary = (f"{what[0].upper()}{what[1:]}: {after['width']:.2f} x {after['length']:.2f} x {after['height']:.2f} m, "
               f"{tris} triangles in the game file." + (f" Note: {made['note']}." if made.get("note") else ""))
    db.update_version(project, n, status="done", passed=int(found["passed"]), checks=found)
    db.add_event("version_done", project=project,
                 data={"version": n, "passed": found["passed"], "summary": summary, "changes": 0})
    db.update_project(project, status="done" if found["passed"] else "needs_changes", base_version=n)
    return {"ok": True, "library": True, "version": n, "score": None, "passed": found["passed"], "summary": summary,
            "changes": [], "gates": found["gates"], "more": len(candidates) > 1}


def _report_library(project, outcome):
    """The chat message for a model made from a ready-made one: what it is and what can be asked next."""
    from . import director  # director imports this module
    another = ("\"Build next version\" tries another ready-made model for the same request; " if outcome.get("more")
               else "")
    text = (f"v{outcome['version']} is ready. {outcome['summary']} It is a free ready-made model, not one the team "
            f"built. {another}tell me a colour or a size to change it, or say \"build it from scratch\" for an "
            "original from the team.")
    card = director.version_card(project, outcome["version"])
    db.add_message("assistant", text, project, meta={"version": card} if card else None)


# --- a mechanism built by the part tools (tools.mechanism): no build team ----------------------

def parts_sheet(project, number):
    """The mechanism sheet a version was built from by the part tools (its plan), or None."""
    plan_file = version_dir(project, number) / "plan.json" if number else None
    plan = json.loads(plan_file.read_text(encoding="utf-8")) if plan_file and plan_file.is_file() else {}
    return plan.get("mechanism") if plan.get("kind") == "parts" else None


def _engineer_caps():
    """The engineer's turn and dollar caps: config.yaml's caps.engineer line, else these."""
    table = (config.settings().get("caps") or {}).get("engineer") or {}
    return {"max_turns": 6, "max_budget_usd": 0.30, **table}


async def _engineer(project, text, before=None):
    """The engineer turns a request in plain words (or changes to a mechanism) into a mechanism
    sheet the part tools build: every part's numbers, how they connect, which drives and how fast.
    Returns {"sheet", "assumed", "custom", "notes"}; custom lists parts no tool makes."""
    sink = {}
    prompt = [f"Work out the mechanism for this request.\n\n## Request\n{text}\n"]
    if before:
        prompt += ["## The mechanism as it is now (change only what the request asks)",
                   _json({k: before[k] for k in ("parts", "driver", "rpm", "direction")}), ""]
    prompt += ["## What the part tools build (field units: mm, degrees, rpm)", _json(mechanism.catalogue()), "",
               _options_text(project)]
    res = None
    for attempt in range(2):
        res = await runner.run_agent("engineer", "\n".join(prompt), at.engineer_tools(sink), project=project,
                                     doing="working out the mechanism", step=True, caps=_engineer_caps())
        if sink.get("mechanism"):
            return {"sheet": sink["mechanism"], "assumed": sink.get("assumed") or [],
                    "custom": sink.get("custom") or [], "notes": sink.get("notes") or ""}
        if _out_of_usage(res):
            break
        prompt.append("\nYou must call submit_mechanism." + (f" Last error: {res.error}" if res and res.error else ""))
    raise PipelineError("the engineer did not submit a mechanism" + _reason(res))


async def _try_parts(project, request, changes, next_version):
    """A mechanism the part tools build: from the numbers a program sent (the job's mechanism), from
    the engineer's sheet for a request in plain words that names a mechanical part, or a change to a
    mechanism built so. Returns the build's outcome; {"team": True, "sheet": ...} when a part has no
    tool and the team builds it from the sheet; or None when this is not a mechanism."""
    proj = db.get_project(project) or {}
    options = proj.get("options") or {}
    if (changes or next_version) and db.versions(project):
        before = parts_sheet(project, base_number(project))
        if before is None:
            return None
        if not changes:
            return {"ok": False, "error": "this mechanism was built exactly from its numbers, so another version "
                                          "would be the same: say what to change (\"make the big gear 30 teeth\")"}
        direct = change_router.direct_mechanism(changes, before)
        if direct and 0.5 <= direct["rpm"] <= 600 and direct["rpm"] != before.get("rpm"):
            _phase(project, "Setting your change on the mechanism sheet", step="spec")
            return await _run_parts(project, direct, [])
        _phase(project, "The engineer is changing the mechanism", step="spec")
        found = await _engineer(project, changes, before)
        if found["custom"]:
            return {"team": True, **found}
        return await _run_parts(project, found["sheet"], found["assumed"])
    if options.get("mechanism"):
        return await _run_parts(project, options["mechanism"], options.get("assumed") or [])
    text = request or proj.get("request") or ""
    if families.guess(text) != "mechanical" or FROM_SCRATCH.search(text):
        return None
    db.update_project(project, status="briefing")
    _handoff(project, "director", "engineer", "your request")
    _phase(project, "The engineer is working out the mechanism", step="spec")
    found = await _engineer(project, text)
    if found["custom"]:
        db.add_event("warning", project=project, agent="engineer",
                     data={"text": "no part tool makes " + ", ".join(c.get("name", "?") for c in found["custom"])
                                   + ": the team builds those from the mechanism sheet"})
        return {"team": True, **found}
    return await _run_parts(project, found["sheet"], found["assumed"])


def with_tool_parts(plan, sheet):
    """The team's plan with every part a tool makes taken from the tools: its code is the tool call
    with the sheet's numbers and no builder writes it. A sheet part the plan left out is added."""
    lay = mechanism.layout(sheet)
    entries = {p.get("name"): p for p in plan.get("parts", [])}
    for p in sheet["parts"]:
        entry = entries.get(p["name"])
        if entry is None:
            entry = {"name": p["name"], "description": f"{p['type'].replace('_', ' ')} from the mechanism sheet",
                     "attaches_to": p.get("on_shaft") or ", ".join(p.get("meshes_with") or [])}
            plan.setdefault("parts", []).append(entry)
        entry["tool"] = p["type"]
        entry["seed"] = mechanism.part_code(p, lay, {q["name"]: q for q in sheet["parts"]})
    plan["reseed"] = sorted(set(plan.get("reseed") or []) | {p["name"] for p in sheet["parts"]})
    return plan


def tool_parts(plan):
    """The names of the parts the part tools make: no builder writes them."""
    return [p["name"] for p in (plan or {}).get("parts", []) if p.get("tool")]


async def _run_parts(project, sheet, assumed, note=""):
    """Build a mechanism straight from the part tools: the scripts are written from the sheet, one
    Blender run builds, moves, measures and runs the mesh check, then the game files and the
    renders. The measured checks stand in for the critic. No agent runs."""
    proj = db.get_project(project) or {}
    what = mechanism.describe(sheet)
    files_code, lay = mechanism.scripts(sheet)
    spec = {"object": what, "category": "parts", "family": "mechanical", "use": "game", "request": proj.get("request"),
            "summary": what, "mechanism": sheet, "motion": {"mechanism": True}}
    n = db.highest_version_number(project) + 1
    folder = version_dir(project, n)
    (folder / "exports").mkdir(parents=True, exist_ok=True)
    files = ProjectFiles(folder)
    for path, code in files_code.items():
        files.write(path, code)
    key = mechanism.key(sheet)
    record = {"sheet": sheet, "assumed": list(assumed or []), "key": key, "sizes_mm": mechanism.size_mm(sheet),
              "pairs": mechanism.check_pairs(lay), "colours": sheet.get("colours") or mechanism.colours(sheet),
              "housings": mechanism.housings(sheet), "exploded": mechanism.exploded_offsets(sheet, lay)}
    files.write("spec.json", _json(spec))
    files.write("plan.json", _json({"kind": "parts", "parts": mechanism.plan_entries(sheet), "mechanism": sheet,
                                    "key": key}))
    files.write("mechanism.json", _json(record))
    db.update_project(project, spec=spec, status="building")
    db.add_version(project, n, note="built by the part tools")
    db.add_event("version_started", project=project, data={"version": n, "round": 1})
    _phase(project, f"Building {what} from its numbers", step="build", version=n)

    def failed(error):
        db.update_version(project, n, status="failed", note=error[:500])
        db.add_event("version_failed", project=project, data={"version": n, "error": error[:2000]})
        db.update_project(project, status="failed")
        return {"ok": False, "error": error[:500]}

    blend = folder / "model.blend"
    steps = [blender.part_step(p["name"], files.path(f"parts/{p['name']}.py")) for p in sheet["parts"]]
    steps += [blender.assembly_step(files.path("assembly.py")), blender.materials_step(files.path("materials.py"))]
    if files.exists("motion.py"):
        steps.append(blender.motion_step(files.path("motion.py")))
    steps += [{"kind": "save", "path": str(blend), "label": "save the .blend"}, blender.measure_step(),
              blender.mesh_check_step(record["pairs"]),
              {"kind": "export", "dir": str(files.path("exports")), "basename": project, "label": "export"}]
    with runner.acting("assembler", project, f"building {what} for v{n}", "full_build") as act:
        result = await blender.run_job_async(steps)
        act.ok = result.ok
    if not result.ok:
        info = result.summary()
        return failed(info.get("traceback") or info.get("error") or "Blender failed")
    measure = result.outputs.get("measure") or {}
    mesh = result.outputs.get("mesh_check") or {}
    found = checks.parts_checks(measure, mesh)
    files.write("measure.json", _json(measure))
    files.write("mesh_check.json", _json(mesh))
    files.write("motion_report.json", _json(result.outputs.get("motion") or {}))
    _phase(project, "Rendering the mechanism", step="render", version=n)
    (rendered, _, sheets), game = await asyncio.gather(
        _render(project, n, blend, folder / "renders", motion=files.exists("motion.py"),
                views=families.views("mechanical")),
        _game_export(folder, project, spec))
    if game:
        found["game"] = game
    files.write("checks.json", _json(found))
    if not rendered.ok or not sheets.get("turntable"):
        info = rendered.summary()
        return failed("rendering failed: " + (info.get("traceback") or info.get("error") or "no images"))
    loop = (result.outputs.get("motion") or {}).get("mechanism") or {}
    failing = [k for k, g in found["gates"].items() if not g["pass"]]
    summary = (f"{what[0].upper()}{what[1:]}, built from its numbers by the part tools: "
               f"{found.get('triangles')} triangles"
               + (f", a {loop.get('seconds')} s loop" if loop.get("seconds") else "")
               + (". Every check passed." if not failing else f". Checks that failed: {', '.join(failing)}.")
               + (f" {note}" if note else ""))
    db.update_version(project, n, status="done", passed=int(found["passed"]), checks=found)
    db.add_event("version_done", project=project,
                 data={"version": n, "passed": found["passed"], "summary": summary, "changes": 0})
    db.update_project(project, status="done" if found["passed"] else "needs_changes", base_version=n)
    return {"ok": True, "parts": True, "version": n, "score": None, "passed": found["passed"], "summary": summary,
            "changes": [], "gates": found["gates"], "assumed": list(assumed or [])}


def _report_parts(project, outcome):
    """The chat message for a mechanism the part tools built: what it is and what can be asked next."""
    from . import director  # director imports this module
    filled = outcome.get("assumed") or []
    text = (f"v{outcome['version']} is ready. {outcome['summary']}"
            + (f" Numbers I chose: {'; '.join(filled[:8])}." if filled else "")
            + " Tell me what to change (a tooth count, a size, the speed) for the next version.")
    card = director.version_card(project, outcome["version"])
    db.add_message("assistant", text, project, meta={"version": card} if card else None)


# --- a person built by the character path (tools.character): no build team --------------------

def character_sheet(project, number):
    """The character sheet a version was built from by the character path (its plan), or None."""
    plan_file = version_dir(project, number) / "plan.json" if number else None
    plan = json.loads(plan_file.read_text(encoding="utf-8")) if plan_file and plan_file.is_file() else {}
    return plan.get("character") if plan.get("kind") == "character" else None


def _designer_caps():
    """The character designer's turn and dollar caps: config.yaml's caps.character_designer line, else these."""
    table = (config.settings().get("caps") or {}).get("character_designer") or {}
    return {"max_turns": 6, "max_budget_usd": 0.30, **table}


async def _designer(project, text, before=None):
    """The character designer turns a request for a person (or changes to one) into a character
    sheet. Returns {"sheet", "assumed", "notes"}."""
    sink = {}
    prompt = [f"Design the character for this request.\n\n## Request\n{text}\n"]
    if before:
        prompt += ["## The character as it is now (change only what the request asks)", _json(before), ""]
    prompt += ["## What a character sheet takes", _json(character.catalogue()), "", _options_text(project)]
    res = None
    for attempt in range(2):
        res = await runner.run_agent("character_designer", "\n".join(prompt), at.character_designer_tools(sink),
                                     project=project, doing="designing the character", step=True,
                                     caps=_designer_caps())
        if sink.get("character"):
            return {"sheet": sink["character"], "assumed": sink.get("assumed") or [], "notes": sink.get("notes") or ""}
        if _out_of_usage(res):
            break
        prompt.append("\nYou must call submit_character." + (f" Last error: {res.error}" if res and res.error else ""))
    raise PipelineError("the character designer did not submit a character" + _reason(res))


def _look_caps(name):
    table = (config.settings().get("caps") or {}).get(name) or {}
    return {"max_turns": 5, "max_budget_usd": 0.25, **table}


async def _dress(project, text, sheet):
    """The tailor and the hair artist, side by side, turn the sheet's look in words into what the
    tools make: the outfit and accessories, the hair, brows and face sliders. Returns the sheet
    with them, cleaned; a part neither agent submitted keeps the sheet's defaults."""
    shown = {k: sheet[k] for k in ("name", "age", "sex", "build", "style", "look", "ancestry")}
    base = f"## Request\n{text}\n\n## The character sheet\n{_json(shown)}\n\n"
    tailor, hairs = {}, {}
    tailor_prompt = (base + "## What the clothing tools make\n" + _json({
        "garments": {t: character.GARMENT_KIND[t] for t in character.GARMENT_TYPES},
        "outer_tops": list(character.OUTER), "skirt_and_dress_lengths": list(character.SKIRT_LENGTHS),
        "accessories": {"glasses": {"shape": ["round", "square"], "frame": ["thin", "thick"]}}})
        + "\n\nCall `submit_outfit` once.")
    hair_prompt = (base + "## What the hair and face tools make\n" + _json({
        "hair_styles": list(character.HAIR_STYLES), "face_sliders": list(character.FACE_SLIDERS)})
        + "\n\nCall `submit_hair` once.")
    _phase(project, "The tailor and the hair artist are dressing the character", step="plan")
    await asyncio.gather(
        runner.run_agent("character_tailor", tailor_prompt, at.character_tailor_tools(tailor, sheet), project=project,
                         doing="choosing the clothes", step=True, caps=_look_caps("character_tailor")),
        runner.run_agent("character_hair", hair_prompt, at.character_hair_tools(hairs, sheet), project=project,
                         doing="choosing the hair and face", step=True, caps=_look_caps("character_hair")))
    raw = dict(sheet)
    if tailor.get("outfit"):
        raw.update(outfit=tailor["outfit"], accessories=tailor.get("accessories") or [])
    if hairs.get("hair"):
        raw.update(hair=hairs["hair"], brows=hairs.get("brows"), face=hairs.get("face") or {})
    for who, got in (("tailor", tailor), ("hair artist", hairs)):
        if not got:
            db.add_event("warning", project=project, agent="director",
                         data={"text": f"the {who} submitted nothing: the plain default is used"})
    notes = " ".join(n for n in (tailor.get("notes"), hairs.get("notes")) if n)
    cleaned, _ = character.clean(raw)
    return cleaned, notes


async def _try_character(project, request, changes, next_version):
    """A person the character path builds: from the sheet a program sent (the job's character), from
    the designer's sheet for a request in plain words that asks for a person, or a change to a
    character built so. Returns the build's outcome, or None when this is not a person (or the body
    data is missing: the team then builds a stylised figure)."""
    proj = db.get_project(project) or {}
    options = proj.get("options") or {}
    if (changes or next_version) and db.versions(project):
        before = character_sheet(project, base_number(project))
        if before is None:
            return None
        if not changes:
            return {"ok": False, "error": "this character was built exactly from its sheet, so another version "
                                          "would be the same: say what to change (\"make her ten years older\")"}
        direct = change_router.direct_character(changes, before, list(character.BUILDS), character.HEIGHT_RANGE)
        if direct:
            try:  # only on a sheet that reads back as it was written, so nothing else in it can shift
                sheet = character.clean({**before, **direct})[0] if character.clean(before)[0] == before else None
            except character.CharacterError:
                sheet = None
            if sheet and sheet != before:
                _phase(project, "Setting your change on the character sheet", step="spec")
                return await _run_character(project, sheet, [], "")
        _phase(project, "The character designer is changing the character", step="spec")
        found = await _designer(project, changes, before)
        return await _run_character(project, found["sheet"], found["assumed"], found["notes"])
    if options.get("character"):
        sheet, assumed = character.clean(options["character"])
        return await _run_character(project, sheet, (options.get("assumed") or []) + assumed)
    text = request or proj.get("request") or ""
    if families.guess(text) != "person":
        return None
    if not character.available():
        db.add_event("warning", project=project, agent="director",
                     data={"text": "the body data (assets_cc0\\anny_body) is missing: the team builds a stylised "
                                   "figure instead"})
        return None
    db.update_project(project, status="briefing")
    _handoff(project, "director", "character_designer", "your request")
    _phase(project, "The character designer is working out the character", step="spec")
    found = await _designer(project, text)
    sheet, look_notes = await _dress(project, text, found["sheet"])
    assumed = [a for a in found["assumed"] if not a.startswith(("outfit:", "hair:"))]
    notes = " ".join(n for n in (found["notes"], look_notes) if n)
    return await _run_character(project, sheet, assumed, notes)


async def _run_character(project, sheet, assumed, notes=""):
    """Build a character straight from its sheet: one Blender run builds the body and its skeleton,
    skins, colours and animates it, checks the skinning in test poses and exports it; then the game
    files and the renders. The measured checks stand in for the critic. No agent runs."""
    proj = db.get_project(project) or {}
    what = character.describe(sheet)
    clips = [clip_lib.nice(c) for c in sheet.get("clips") or []] or ["rig_test"]
    spec = {"object": character.who(sheet), "category": "character", "family": "person", "use": "game",
            "request": proj.get("request"), "summary": what, "character": sheet, "motion": {"clips": clips}}
    n = db.highest_version_number(project) + 1
    folder = version_dir(project, n)
    (folder / "exports").mkdir(parents=True, exist_ok=True)
    files = ProjectFiles(folder)
    key = character.key(sheet)
    files.write("spec.json", _json(spec))
    files.write("plan.json", _json({"kind": "character", "parts": [{"name": "body"}, {"name": "eyes"}],
                                    "character": sheet, "key": key}))
    files.write("character.json", _json({"sheet": sheet, "assumed": list(assumed or []), "notes": notes, "key": key}))
    db.update_project(project, spec=spec, status="building")
    db.add_version(project, n, note="built by the character path")
    db.add_event("version_started", project=project, data={"version": n, "round": 1})
    _phase(project, f"Building {what}", step="build", version=n)

    def failed(error):
        db.update_version(project, n, status="failed", note=error[:500])
        db.add_event("version_failed", project=project, data={"version": n, "error": error[:2000]})
        db.update_project(project, status="failed")
        return {"ok": False, "error": error[:500]}

    blend = folder / "model.blend"
    steps = [character.build_step(sheet), character.report_step(),
             {"kind": "save", "path": str(blend), "label": "save the .blend"}, blender.measure_step(),
             {"kind": "export", "dir": str(files.path("exports")), "basename": project, "label": "export"}]
    with runner.acting("assembler", project, f"building {character.who(sheet)} for v{n}", "full_build") as act:
        result = await blender.run_job_async(steps)
        act.ok = result.ok
    if not result.ok:
        info = result.summary()
        return failed(info.get("traceback") or info.get("error") or "Blender failed")
    made = result.outputs.get("character") or {}
    rig_report = result.outputs.get("rig_report") or {}
    found = checks.character_checks(made, rig_report, sheet)
    files.write("measure.json", _json(result.outputs.get("measure") or {}))
    files.write("rig_report.json", _json(rig_report))
    files.write("clips.json", _json({"clips": made.get("clips") or [], "springs": made.get("springs") or {},
                                     "skeleton": "53-bone game skeleton" + (" plus spring chains" if (made.get("springs")
                                                                                                    or {}).get("chains")
                                                                           else "")}))
    _phase(project, "Rendering the character", step="render", version=n)

    async def game_then_clips():
        made_game = await _game_export(folder, project, spec)
        await _clip_sheet(folder, project, n)
        return made_game

    # the renders, the game files and the clip strips all read the saved model: made side by side
    (rendered, _, sheets), game = await asyncio.gather(
        _render(project, n, blend, folder / "renders", views=["front", "right"]), game_then_clips())
    if game:
        found["game"] = game
        found["triangles"] = (game.get("triangles") or {}).get("game") or found.get("triangles")
    files.write("checks.json", _json(found))
    if not rendered.ok or not sheets.get("turntable"):
        info = rendered.summary()
        return failed("rendering failed: " + (info.get("traceback") or info.get("error") or "no images"))
    failing = [k for k, g in found["gates"].items() if not g["pass"]]
    summary = (f"{what[0].upper()}{what[1:]}, built from the character sheet: a {made.get('bones')}-bone game "
               f"skeleton, {found.get('triangles')} triangles in the game file, clips: "
               f"{', '.join(c['name'] for c in made.get('clips') or []) or 'none'}"
               + (". Every check passed." if not failing else f". Checks that failed: {', '.join(failing)}.")
               + (f" {notes}" if notes else ""))
    db.update_version(project, n, status="done", passed=int(found["passed"]), checks=found)
    db.add_event("version_done", project=project,
                 data={"version": n, "passed": found["passed"], "summary": summary, "changes": 0})
    db.update_project(project, status="done" if found["passed"] else "needs_changes", base_version=n)
    return {"ok": True, "character": True, "version": n, "score": None, "passed": found["passed"],
            "summary": summary, "changes": [], "gates": found["gates"], "assumed": list(assumed or [])}


async def _clip_sheet(folder, project, n, count=4):
    """A strip of pictures across every clip of a character, one row per clip, as renders/
    sheet_clips.jpg. Never fails the build."""
    out = folder / "renders" / "clips"
    step = {"kind": "render", "set": "clip_strips", "dir": str(out), "count": count,
            "size": int(config.get("render", "preview_size", 384)),
            "samples": int(config.get("render", "preview_samples", 24)), "label": "render the clip strips"}
    with runner.acting("animator", project, f"rendering the clips of v{n}", "render") as act:
        result = await blender.run_job_async([step], open_blend=folder / "model.blend")
        act.ok = result.ok
    strips = (result.outputs or {}).get("clip_strips") or {}
    if not result.ok or not strips:
        db.add_event("warning", project=project, agent="animator", data={"text": "the clip strips could not be rendered"})
        return None
    paths, labels = [], []
    for clip, shots in strips.items():
        paths += shots
        labels += [clip.replace("_", " ")] + [""] * (len(shots) - 1)
    return render.contact_sheet(paths, folder / "renders" / "sheet_clips.jpg", columns=count, tile=256, labels=labels)


def _report_character(project, outcome):
    """The chat message for a character the character path built: what it is and what can be asked next."""
    from . import director  # director imports this module
    filled = outcome.get("assumed") or []
    text = (f"v{outcome['version']} is ready. {outcome['summary']}"
            + (f" Values I chose: {'; '.join(filled[:8])}." if filled else "")
            + " Tell me what to change (age, height, build, skin tone) for the next version. Hair, clothes and "
              "real animation clips come in the next stages of the character path.")
    card = director.version_card(project, outcome["version"])
    db.add_message("assistant", text, project, meta={"version": card} if card else None)


async def _render(project, number, blend, out_dir, **options):
    """The final renders of a saved version, shown on the map as the critic rendering. Returns what
    render.render_version returns."""
    with runner.acting("critic", project, f"rendering v{number}", "render") as act:
        rendered, renders, sheets = await render.render_version(blend, out_dir, **options)
        act.ok = bool(rendered.ok and sheets.get("turntable"))
    return rendered, renders, sheets


async def _game_export(folder, project, spec):
    """The game-ready files of a finished version (bpy_lib.game), into its game folder: the asset
    within its family's triangle limit, lower-detail versions and a collision box. Never fails the
    build: a version without them says so in the events. Returns what was made, or None."""
    if not config.get("game", "enabled", True):
        return None
    folder = Path(folder)
    blend = folder / "model.blend"
    if not blend.is_file():
        return None
    limit = families.budget(families.of(spec))
    lods = config.get("game", "lods", [0.5, 0.2]) or []
    with runner.acting("assembler", project, "making the game-ready files", "full_build") as act:
        result = await blender.run_job_async([blender.game_step(folder / "game", project, limit, lods)],
                                             open_blend=blend)
        act.ok = result.ok
    if not result.ok:
        info = result.summary()
        db.add_event("warning", project=project, agent="assembler",
                     data={"text": "the game-ready files could not be made",
                           "traceback": (info.get("traceback") or info.get("error") or "")[-1500:]})
        return None
    made = result.outputs.get("game") or {}
    out = {"triangles": made.get("triangles"), "budget": made.get("budget"), "reduced": made.get("reduced"),
           "size": made.get("size"), "files": sorted(Path(p).name for p in (made.get("files") or {}).values())}
    (folder / "game" / "game.json").write_text(_json(out), encoding="utf-8")
    return out


# --- research before the spec (agents\\researcher.md) -----------------------------------

async def _research(project, request):
    """What the real object is like, looked up on the web before the spec is written: its real
    size, proportions and telling features, and reference photos kept with the model. None when
    research is off or found nothing: the spec is then written from the request alone."""
    if not config.get("research", "enabled", True):
        return None
    sink = {}
    _phase(project, "Looking up the real object", step="research")
    prompt = f"Research this object before it is modelled.\n\n## Request\n{request}\n\n{_options_text(project)}"
    tools = at.researcher_tools(sink, project, side_view=families.guess(request) == "vehicle")
    res = await runner.run_agent("researcher", prompt, tools, project=project,
                                 doing="looking up the real object", step=True)
    found = sink.get("research")
    if not found:
        db.add_event("warning", project=project, agent="brief",
                     data={"text": "the research found nothing" + _reason(res) + "; the spec is written from the "
                                   "request alone"})
        return None
    if sink.get("images"):
        found["reference_photos"] = len(sink["images"])
    return found


# --- a car made from a ready-made one (tools.car_request): no agents ---------------------

def remix_car(project, number):
    """The car a version was made as by the car generator (its plan), or None for a version the
    agents built."""
    plan_file = version_dir(project, number) / "plan.json" if number else None
    plan = json.loads(plan_file.read_text(encoding="utf-8")) if plan_file and plan_file.is_file() else {}
    return plan.get("car") if plan.get("kind") == "remix" else None


def uses_remix(project, request=None, follow_up=False):
    """Whether this build is the car generator's: a change or next version of a car it made, or a
    new request that names a car (config cars.generator: remix, the default; agents gives a car to
    the agent team as before)."""
    if follow_up and db.versions(project):
        return remix_car(project, base_number(project)) is not None
    if str(config.get("cars", "generator", "remix")).lower() != "remix" or not car_library.CATALOG.is_file():
        return False
    return at.looks_like_car(request or (db.get_project(project) or {}).get("request"))


def _remix_checks(after):
    """The checks a remixed car is held to: car-sized and standing on the floor. It is not asked to
    be one closed solid without loose pieces: the base cars are made of separate shells."""
    tol = float(config.get("model", "floor_tolerance_m", 0.002))
    gates = {
        "correct_size": {"pass": 2.0 < after["length"] < 8.0 and 1.2 < after["width"] < 2.5,
                         "detail": f"{after['length']:.2f} m long, {after['width']:.2f} m wide, "
                                   f"{after['height']:.2f} m high"},
        "on_floor": {"pass": abs(after["ground"]) <= max(tol, 0.005),
                     "detail": f"lowest point {after['ground'] * 1000:.0f} mm from the floor"},
    }
    warnings = [f"{after['triangles']} triangles"] if after["triangles"] > 30000 else []
    return {"passed": all(g["pass"] for g in gates.values()), "gates": gates, "warnings": warnings}


async def _run_remix(project, request, changes, next_version):
    proj = db.get_project(project)
    catalogue = car_library.load()
    base = base_number(project) if (changes or next_version) and db.versions(project) else None
    before = remix_car(project, base)
    db.update_project(project, status="planning")
    if before and changes:
        made = car_request.changed(before, changes, catalogue)
    elif before:
        made = car_request.another(before, catalogue)
    else:
        text = request or proj["request"]
        if changes:  # changes asked for before any version was built: they shape the first one
            text += f" {changes}"
        made = car_request.plan(text, random.randrange(1, 1000000), catalogue)
    what = car_request.describe(made)
    spec = {"object": f"{made['type']} car", "category": "remix:car", "request": made["request"], "summary": what}
    n = db.highest_version_number(project) + 1
    folder = version_dir(project, n)
    (folder / "exports").mkdir(parents=True, exist_ok=True)
    files = ProjectFiles(folder)
    files.write("spec.json", _json(spec))
    files.write("plan.json", _json({"kind": "remix", "parts": [], "car": made}))
    db.update_project(project, spec=spec, status="building")
    db.add_version(project, n, note="made from a base car")
    db.add_event("version_started", project=project, data={"version": n, "round": 1})
    _phase(project, f"Making {what}", step="build", version=n)

    def failed(error):
        db.update_version(project, n, status="failed", note=error[:500])
        db.add_event("version_failed", project=project, data={"version": n, "error": error[:2000]})
        db.update_project(project, status="failed")
        return {"ok": False, "error": error[:500]}

    blend = folder / "model.blend"
    try:
        with runner.acting("assembler", project, f"making the car for v{n}", "full_build") as act:
            record = await asyncio.to_thread(car_request.make, made, folder / "exports" / f"{project}.glb", None,
                                             blend, catalogue)
            act.ok = True
    except RuntimeError as exc:
        return failed(str(exc))
    found = _remix_checks(record["after"])
    files.write("measure.json", _json(record["after"]))
    files.write("checks.json", _json(found))
    files.write("car.json", _json(record))
    _phase(project, "Rendering the car", step="render", version=n)
    with runner.acting("critic", project, f"rendering v{n}", "render") as act:
        rendered, _, sheets = await render.render_version(blend, folder / "renders")
        act.ok = bool(rendered.ok and sheets.get("turntable"))
    if not rendered.ok or not sheets.get("turntable"):
        info = rendered.summary()
        return failed("rendering failed: " + (info.get("traceback") or info.get("error") or "no images"))
    after = record["after"]
    summary = (f"{what[0].upper()}{what[1:]}: {after['length']:.2f} m long, {after['triangles']} triangles."
               + (f" Note: {made['note']}." if made.get("note") else ""))
    db.update_version(project, n, status="done", passed=int(found["passed"]), checks=found)
    db.add_event("version_done", project=project,
                 data={"version": n, "passed": found["passed"], "summary": summary, "changes": 0})
    db.update_project(project, status="done" if found["passed"] else "needs_changes", base_version=n)
    return {"ok": True, "remix": True, "version": n, "score": None, "passed": found["passed"], "summary": summary,
            "changes": [], "gates": found["gates"]}


def _report_remix(project, outcome):
    """The chat message for a car the generator made: what it is and what can be asked next."""
    from . import director  # director imports this module
    text = (f"v{outcome['version']} is ready. {outcome['summary']} \"Build next version\" makes another car for "
            f"the same request; or tell me a colour, type or era to change.")
    card = director.version_card(project, outcome["version"])
    db.add_message("assistant", text, project, meta={"version": card} if card else None)


def base_number(project):
    """The version the next one starts from: the one picked with "Build changes from" or kept as the
    best at the end of the last build, else the best-scored version, else the newest."""
    proj = db.get_project(project) or {}
    versions = db.versions(project)
    if proj.get("base_version") and any(v["number"] == proj["base_version"] for v in versions):
        return proj["base_version"]
    scored = [v for v in versions if v.get("score") is not None]
    if scored:
        return max(scored, key=lambda v: v["score"])["number"]
    return versions[-1]["number"] if versions else None


def critic_notes_of(project, number, parts):
    """The critic's open change list for a reviewed version, routed to the agents it names, with its
    failed gates; and that version's review. ({}, None) when the version has no review."""
    v = db.get_version(project, number) if number else None
    review, found = (v or {}).get("review"), (v or {}).get("checks")
    if not review or not found or not found.get("gates"):
        return {}, None
    measure_file = version_dir(project, number) / "measure.json"
    measure = json.loads(measure_file.read_text(encoding="utf-8")) if measure_file.is_file() else {}
    notes = _route_changes(review, {"checks": found, "measure": measure}, parts)
    keep = set(parts) | {"assembly", "materials", "motion", at.PLAN_TARGET}
    return {k: v for k, v in notes.items() if k in keep}, review


def open_changes(project):
    """How many critic changes the next version would act on (0: nothing for "Build next version")."""
    number = base_number(project)
    if ready_made(project, number):
        return 1  # the next version of a generated car or a ready-made model is another one for the same request
    v = db.get_version(project, number) if number else None
    return len(((v or {}).get("review") or {}).get("changes") or [])


AUTO_PREFIXES = ("Hard gate", "Check warning")  # notes the checks add, not the critic


def next_changes(project):
    """What the next version acts on, for the page: the critic's open changes on the version it
    starts from, routed exactly as a build routes them (critic_notes_of), each with the agent it
    goes to, and `auto` on the fixes the checks add. While a next version is building, its number
    and the person's comments (they come first). None when there is nothing to show: no version
    yet, or a first version building."""
    base = base_number(project)
    if not base:
        return None
    building, comments = None, ""
    if is_running(project):
        data = (db.last_event("build_started", project) or {}).get("data") or {}
        if data.get("kind") not in ("changes", "next"):
            return None
        rows = [v for v in db.versions(project) if v["status"] == "building"]
        building = rows[-1]["number"] if rows else None
        if data.get("kind") == "changes":
            comments = data.get("text") or ""
    if remix_car(project, base):
        return {"from": base, "building": building, "comments": comments,
                "items": [{"target": "car", "role": "builder", "text": "Another car for the same request",
                           "auto": False, "short": "Another car, same request"}]}
    if library_asset(project, base):
        return {"from": base, "building": building, "comments": comments,
                "items": [{"target": "model", "role": "builder", "auto": False,
                           "text": "Another ready-made model for the same request",
                           "short": "Another ready-made model"}]}
    plan_file = version_dir(project, base) / "plan.json"
    parts = at.plan_parts(json.loads(plan_file.read_text(encoding="utf-8"))) if plan_file.is_file() else []
    if not parts:  # no saved plan: every change target that is not a stage is a part
        v = db.get_version(project, base) or {}
        parts = [c.get("target") for c in (v.get("review") or {}).get("changes") or []
                 if c.get("target") not in ("assembly", "materials", "motion")]
    notes, review = critic_notes_of(project, base, parts)
    shorts = {(c.get("target"), _change_text(c)): _short_of(c) for c in (review or {}).get("changes") or []}
    items = [{"target": target, "role": _role_of(target), "text": text, "auto": text.startswith(AUTO_PREFIXES),
              "short": shorts.get((target, text)) or _check_short(target, text)}
             for target, texts in notes.items() for text in texts]
    items.sort(key=lambda i: i["auto"])  # the critic's changes first, then what the checks found
    return {"from": base, "building": building, "comments": comments, "items": items}


def _cut(text, n=60):
    text = " ".join(text.split())
    return text if len(text) <= n else text[:n].rsplit(" ", 1)[0].rstrip(",;:") + "…"


def _change_text(change):
    """A critic change as _route_changes words it for the team."""
    return f"{change.get('parameter', '')}: {change.get('change', '')}".strip(": ")


def _short_of(change):
    """The critic's plain-words summary of a change; for a review written before it gave one, the
    change's label without the bracketed asides, cut short."""
    if change.get("short"):
        return change["short"].strip()
    label = re.sub(r"\s*\([^)]*\)", "", change.get("parameter") or "").strip(" ,;:")
    return _cut((label or change.get("change") or "").replace("_", " "))


GATE_SHORTS = {"on_floor": "Put it on the floor", "no_floaters": "Remove floating pieces",
               "correct_size": "Fix the overall size"}


def _check_short(target, text):
    """Plain words for a fix the checks added (a failed gate, open edges, a warning)."""
    for gate, words in GATE_SHORTS.items():
        if text.startswith(f"Hard gate {gate} "):
            return words
    if text.startswith("Hard gate: the mesh has"):
        return f"Close open edges on {target.replace('_', ' ')}"
    return _cut(text.removeprefix("Check warning: "))


def _role_of(target):
    """The agent a change-list target belongs to: a part name means a builder."""
    return {"assembly": "assembler", "materials": "materials", "motion": "animator",
            at.PLAN_TARGET: "architect"}.get(target, "builder")


def _new_round(project, n, spec, plan, prev_dir):
    folder = version_dir(project, n)
    folder.mkdir(parents=True, exist_ok=True)
    if prev_dir:
        for name in CARRY:
            src = Path(prev_dir) / name
            if src.is_dir():
                shutil.copytree(src, folder / name, dirs_exist_ok=True)
            elif src.is_file():
                shutil.copy2(src, folder / name)
    files = ProjectFiles(folder)
    files.write("spec.json", _json(spec))
    files.write("plan.json", _json(plan))
    rnd = Round(project=project, number=n, dir=folder, files=files, spec=spec, plan=plan,
                prev_dir=Path(prev_dir) if prev_dir else None)
    if files.exists("part_reports.json"):
        rnd.part_reports = json.loads(files.read("part_reports.json"))
    _seed(rnd)
    return rnd


def _seed(rnd):
    """Start files from the plan: a part with seed code and no file yet (or listed in the plan's
    `reseed`) gets its seed; a car also gets its generated assembly and materials files."""
    reseed = set(rnd.plan.get("reseed") or [])
    _seed_assets(rnd)
    for entry in rnd.plan.get("parts", []):
        path = f"parts/{entry['name']}.py"
        if entry.get("seed") and (entry["name"] in reseed or not rnd.files.exists(path)):
            rnd.files.write(path, entry["seed"])
    template = at.shell_template(plan=rnd.plan)
    if template:
        sheet = at.SHELL_TEMPLATES[template]
        if not rnd.files.exists("assembly.py"):
            rnd.files.write("assembly.py", sheet.ASSEMBLY_MODULE)
        if not rnd.files.exists("materials.py"):
            finish = rnd.plan.get("finish") or {}
            rnd.files.write("materials.py", sheet.materials_module(finish.get("paint") or (0.12, 0.36, 0.78),
                                                                    finish.get("style") or "stylised"))


ASSET_PART = '''from bpy_lib import assets


def build():
    """A ready-made model ({asset}) placed as this part: it keeps its own textures."""
    obj = assets.load(r"{file}", height={height!r}, name="{name}")
    assets.lighten(obj, {triangles})
    return obj
'''


def asset_parts(plan):
    """The names of the parts that are ready-made models: no builder writes them."""
    return [p["name"] for p in (plan or {}).get("parts", []) if p.get("asset")]


def _seed_assets(rnd):
    """A part the plan names a catalogue model for gets its code written here: the model is fetched
    (downloaded the first time) and loaded at the part's planned height. When it cannot be fetched
    the entry loses its `asset` and a builder makes the part by hand."""
    share = max(600, families.budget(families.of(rnd.spec)) // 12)
    for entry in rnd.plan.get("parts", []):
        if not entry.get("asset") or rnd.files.exists(f"parts/{entry['name']}.py"):
            continue
        found = asset_catalog.entry_by_id(entry["asset"]) if asset_catalog.CATALOG.is_file() else None
        try:
            if found is None:
                raise RuntimeError(f"{entry['asset']} is not in the catalogue")
            file = asset_catalog.fetch(found)
        except RuntimeError as exc:
            db.add_event("warning", project=rnd.project, agent="architect",
                         data={"text": f"part {entry['name']}: the ready-made model could not be used ({exc}); a "
                                       "builder makes it instead"})
            entry.pop("asset", None)
            continue
        height = float((entry.get("dimensions") or {}).get("height") or 0) or None
        rnd.files.write(f"parts/{entry['name']}.py", ASSET_PART.format(
            asset=entry["asset"], file=str(file), height=height, name=entry["name"], triangles=share))
    rnd.files.write("plan.json", _json(rnd.plan))


async def _measure_asset_parts(rnd, names):
    """Ready-made parts are built once here, with no builder, so the assembler knows their size."""
    for name in names:
        if rnd.part_reports.get(name) or not rnd.files.exists(f"parts/{name}.py"):
            continue
        entry = next((p for p in rnd.plan["parts"] if p["name"] == name), {})
        result = await blender.run_job_async([blender.part_step(name, rnd.files.path(f"parts/{name}.py")),
                                              blender.measure_step()])
        rnd.part_ok[name] = result.ok
        if result.ok:
            rnd.part_reports[name] = checks.part_report(result.outputs.get("measure"), entry.get("dimensions"))
        else:
            rnd.part_errors[name] = (result.summary().get("traceback") or "")[-1500:]
            db.add_event("warning", project=rnd.project, agent="builder",
                         data={"text": f"the ready-made part {name} did not load", "traceback": rnd.part_errors[name]})


def _family_lines(spec, role):
    """The family's rules for one role and, for a game asset, the triangle limit, as prompt lines."""
    family = families.of(spec)
    lines = []
    text = families.rules(family, role)
    if text:
        lines += [text]
    if str((spec or {}).get("use") or "").lower() == "game" and role in ("plan", "build", "review"):
        limit = families.budget(family)
        lines += [f"## This is a game asset: at most about {limit} triangles for the whole model",
                  {"plan": "Share them out: give each part its share in `triangles` (all its copies together; the "
                           "main body the most, small pieces 100 to 400; the shares must add up to the limit or "
                           "less), and segment counts that keep to it (16 to 32 around a round form, more only on "
                           "a large curve seen close up). A builder cannot finish a part far over its share.",
                   "build": "`write_part` reports your part's triangles: keep to the `triangles` share in your "
                            "plan entry (all copies together); `finish` sends you back when you are more than half "
                            "over it. Spend triangles on the silhouette, not on flat faces or hidden insides.",
                   "review": "The checks give the model's triangle count against this limit. The game file is "
                             "reduced to the limit by itself, so a model far over it loses detail there: when "
                             "`over_budget` is true, one change should name the densest part to rebuild lighter."}[role],
                  ""]
    return lines


def _without_seeds(plan):
    """The plan as the agents read it: the seed code is shown as each part's current code instead."""
    return {**plan, "parts": [{k: v for k, v in p.items() if k != "seed"} for p in plan.get("parts", [])]}


def body_sheet(code):
    """The SHEET dict in a car body module, or None when it cannot be read as plain data."""
    try:
        tree = ast.parse(code or "")
    except SyntaxError:
        return None
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(getattr(t, "id", None) == "SHEET" for t in node.targets):
            try:
                return ast.literal_eval(node.value)
            except ValueError:
                return None
    return None


# --- each agent's step ------------------------------------------------------------------

def _options_text(project):
    """What the person set on the page for this model: a size and reference images."""
    options = (db.get_project(project) or {}).get("options") or {}
    lines = []
    if (options.get("api") or {}).get("client"):
        lines += ["## Made for another program (no person is watching)",
                  f"{options['api']['client']} asked for this model for its videos and games. Make a generic "
                  "design: no brand names, logos, trademarks or a brand's signature shapes, even when the request "
                  "or the research names a real product. Nobody can answer questions: decide yourself.", ""]
    size = options.get("size") or {}
    if any(size.get(k) for k in ("width", "depth", "height")):
        unit = size.get("unit", "cm")
        dims = " x ".join(f"{k} {size[k]} {unit}" for k in ("width", "depth", "height") if size.get(k))
        lines += ["## Size the person gave (use it exactly)", dims, ""]
    if options.get("references"):
        lines += [f"## Reference images\nThe person attached {len(options['references'])} reference image(s). "
                  "Look at them with view_reference before you decide.", ""]
    if options.get("found_references"):
        lines += [f"## Reference photos\nThe research kept {len(options['found_references'])} photo(s) of the real "
                  "object. view_reference shows them" + (" after the person's own" if options.get("references")
                                                          else "") + ": use them for shape and proportions.", ""]
    return "\n".join(lines)


def _reason(res):
    """Why an agent submitted nothing, for the end of the failure message: the Claude session limit
    in plain words when that was it, else the agent's own error; empty when there was none."""
    if not res or not res.error:
        return ""
    return f" ({runner.account_limit(res.error) or res.error[:300]})"


def _out_of_usage(res):
    """A second attempt fails the same way until the Claude session limit resets."""
    return bool(res and runner.account_limit(res.error))


async def _brief(project, request, sheet=None):
    sink = {}
    research = await _research(project, request)
    prompt = (f"Write the spec for this request.\n\n## Request\n{request}\n\n{_options_text(project)}\n"
              f"## Object families (choose `family`)\n{families.brief_list()}\n")
    if sheet:
        prompt += ("\n## The mechanism sheet (the engineer's; family `mechanical`)\nEvery part's numbers, how they "
                   "connect and how each moves are settled here: keep them exactly; the parts a tool makes are built "
                   f"from it.\n{_json(sheet)}\n")
    if research:
        prompt += ("\n## Research: the real object (looked up)\nIts `dimensions`, `proportions` (each says whether "
                   "it was published or measured on a kept photo) and `features` are to be used as they are, unless "
                   "the person said otherwise. Its `unverified` list holds guesses: never copy one into the spec as "
                   f"a size or a proportion.\n{_json(research)}\n")
    res = None
    for attempt in range(2):
        res = await runner.run_agent("brief", prompt, at.brief_tools(sink) + at.reference_tools(project),
                                     project=project, doing="writing the spec", step=True)
        if sink.get("spec"):
            spec = sink["spec"]
            spec["family"] = families.of(spec)
            if sheet:
                spec["family"], spec["mechanism"] = "mechanical", sheet
            if research:
                spec["research"] = {k: research.get(k) for k in ("dimensions", "proportions", "unverified",
                                                                 "features", "colours", "notes", "reference_photos")
                                    if research.get(k)}
            return spec
        if _out_of_usage(res):
            break
        prompt += "\n\nYou must call submit_spec with the whole spec." + (f" Last error: {res.error}" if res.error else "")
    raise PipelineError("the brief agent did not submit a spec" + _reason(res))


def _architect_prompt(spec):
    family = families.of(spec)
    features = list((spec.get("research") or {}).get("features") or [])
    listed = (["## The real object's features (from the research): every one needs a part",
               *[f"{i}. {f}" for i, f in enumerate(features, 1)],
               "List each in `research_features` with the part that carries it.", ""] if features else [])
    tooled = []
    if spec.get("mechanism"):
        names = [p["name"] for p in spec["mechanism"]["parts"]]
        custom = [c.get("name") for c in spec.get("mechanism_custom") or []]
        tooled = ["## Parts the part tools make (already in the plan: do not plan them again)",
                  f"{', '.join(names)} are built by the part tools from the mechanism sheet, under exactly these "
                  "names. Never add another entry for any of them under another name (no second shaft, wheel, "
                  "gear, bearing or housing). Plan only the parts no tool makes"
                  + (f": {', '.join(custom)}" if custom else "") + ", and use the names above in attaches_to.", ""]
    extra = "\n".join([*listed, *tooled, *_family_lines(spec, "plan"), at.kits_reference(family)])
    return (f"Make the build plan for this spec.\n\n## Spec\n{_json(spec)}\n\n{extra}\n\n"
            f"## The helper library the builders use (as h)\n{at.helpers_reference()}")


async def _architect(project, spec, library):
    sink = {}
    prompt = _architect_prompt(spec) + "\n\n" + _options_text(project)
    res = None
    for attempt in range(2):
        game = str(spec.get("use") or "").lower() == "game"
        tools = at.architect_tools(sink, library, assets=families.of(spec) == "scene",
                                   features=(spec.get("research") or {}).get("features"),
                                   budget=families.budget(families.of(spec)) if game else None)
        res = await runner.run_agent("architect", prompt, tools + at.reference_tools(project),
                                     project=project, doing="planning the parts", step=True)
        if sink.get("plan"):
            return sink["plan"]
        if _out_of_usage(res):
            break
        prompt += "\n\nYou must call submit_plan with the whole plan." + (f" Last error: {res.error}" if res.error else "")
    raise PipelineError("the architect did not submit a plan" + _reason(res))


async def _architect_change(project, spec, plan, changes, library):
    sink = {}
    prompt = (f"The person wants changes to a finished model. Update the plan.\n\n## Change request\n{changes}\n\n"
              f"## Spec\n{_json(spec)}\n\n## Current plan\n{_json(plan)}\n\n"
              "Return the whole updated plan with submit_plan, and list in `rebuild` every part whose code must "
              "change. If the spec's overall dimensions change, say so in assembly_notes.")
    res = await runner.run_agent("architect", prompt, at.architect_tools(sink, library) + at.reference_tools(project),
                                 project=project, doing="planning the changes", step=True)
    new = sink.get("plan")
    if not new:
        raise PipelineError("the architect did not submit an updated plan" + (_reason(res) or " (no reason given)"))
    names = at.plan_parts(new)
    rebuild = {n for n in new.get("rebuild", []) if n in names} | (set(names) - set(at.plan_parts(plan)))
    return new, rebuild


async def _shell_architect(project, spec, library):
    sink = {}
    prompt = (f"Plan this car.\n\n## Spec\n{_json(spec)}\n\n## Car sheets\n{at.car_reference()}\n\n"
              + _options_text(project))
    res = None
    for attempt in range(2):
        tools = at.shell_architect_tools(sink, library, at.reference_paths(project)) + at.reference_tools(project)
        res = await runner.run_agent("shell_architect", prompt, tools, project=project, doing="planning the car",
                                     step=True)
        if sink.get("plan"):
            return sink["plan"]
        if _out_of_usage(res):
            break
        prompt += "\n\nYou must call submit_plan with the whole submission." + (f" Last error: {res.error}" if res.error else "")
    raise PipelineError("the car architect did not submit a plan" + _reason(res))


def _submission(plan, sheet):
    """A car plan back in the form the car architect submits, with the body's current sheet."""
    return {"sheet": sheet if sheet is not None else plan.get("sheet", {}), "accessories": plan.get("accessories", {}),
            "reference_views": plan.get("reference_views", {}), "paint": (plan.get("finish") or {}).get("paint"),
            "style": (plan.get("finish") or {}).get("style"), "notes": plan.get("notes", "")}


async def _shell_architect_change(project, spec, plan, changes, library, base_dir):
    """The car architect updates the submission; parts whose seed changed start again from it."""
    body = Path(base_dir) / "parts" / "body.py"
    current = body_sheet(body.read_text(encoding="utf-8")) if body.is_file() else None
    sink = {}
    prompt = (f"The person wants changes to a finished car. Update it.\n\n## Change request\n{changes}\n\n"
              f"## Spec\n{_json(spec)}\n\n## Current submission (the sheet is the body's current one)\n"
              f"{_json(_submission(plan, current))}\n\n## Car sheets\n{at.car_reference(current)}\n\n"
              "Return the whole updated submission with submit_plan.")
    tools = at.shell_architect_tools(sink, library, at.reference_paths(project)) + at.reference_tools(project)
    res = await runner.run_agent("shell_architect", prompt, tools, project=project, doing="planning the changes",
                                 step=True)
    new = sink.get("plan")
    if not new:
        raise PipelineError("the car architect did not submit an updated plan" + (_reason(res) or " (no reason given)"))
    old_seeds = {p["name"]: p.get("seed") for p in plan.get("parts", [])}
    reseed = {p["name"] for p in new["parts"] if p["name"] != "body" and old_seeds.get(p["name"]) != p.get("seed")}
    if new.get("sheet") != current:  # the body module holds the sheet: a new sheet starts it again
        reseed.add("body")
    new["reseed"] = sorted(reseed)
    rebuild = reseed | {"body"}  # the body builder checks every change against the renders
    return new, rebuild


async def _plan_changes(project, spec, plan, notes, rebuild, library):
    """The critic's changes to the plan itself (a part to add, merge or split) go to the architect
    before anyone builds: the plan is updated, the parts it adds or changes join the ones to build,
    and the assembler and the materials agent are told. `notes` loses its plan entry. Returns the
    plan and the parts to build; both unchanged when there is nothing for the architect, or it
    could not do it (then the version is built from the plan as it was)."""
    asked = notes.pop(at.PLAN_TARGET, None)
    if not asked or at.shell_template(plan=plan):
        return plan, rebuild
    _handoff(project, "critic", "architect", f"{len(asked)} change{'s' if len(asked) != 1 else ''} to the plan")
    _phase(project, "Changing the plan as the review asks", step="plan")
    text = ("The critic's review of the last version asks for these changes to the plan itself (a part to add, "
            "merge, split or re-plan). Make exactly these and leave every other part as it is:\n"
            + "\n".join(f"- {item}" for item in asked))
    try:
        new, changed = await _architect_change(project, spec, plan, text, library)
    except PipelineError as exc:
        db.add_event("warning", project=project, agent="architect",
                     data={"text": f"the plan was not changed ({exc}); this version is built from the plan as it was"})
        return plan, rebuild
    note = "The plan changed since the last version (" + "; ".join(asked)[:600] + "): place and fit what is new."
    notes.setdefault("assembly", []).append(note)
    notes.setdefault("materials", []).append(note.replace("place and fit", "give materials to"))
    return new, set(rebuild) | set(changed)


def _parent(plan, part):
    """The part this one attaches to: the first part name in its plan entry's attaches_to, if any."""
    entry = next((p for p in plan.get("parts", []) if p.get("name") == part), {})
    names = set(at.plan_parts(plan)) - {part}
    for word in str(entry.get("attaches_to") or "").replace(",", " ").replace("(", " ").split():
        if word in names:
            return word
    return None


def _waits_for(plan, todo):
    """For each part in todo, the part in todo it must wait for (its parent), skipping any that would loop."""
    waits = {}
    for part in todo:
        parent = _parent(plan, part)
        if parent not in todo:
            continue
        seen, step = {part}, parent
        while step in waits and step not in seen:
            seen.add(step)
            step = waits[step]
        if step not in seen:
            waits[part] = parent
    return waits


def _notes_lines(rnd, notes):
    """The notes an agent gets for this version, numbered so its finish can answer each one."""
    lines = ["## Notes for this round (fix exactly these)", *[f"{i}. {n}" for i, n in enumerate(notes, 1)]]
    if at.previous_renders(rnd):
        lines.append("Call view_previous_version first: it shows the renders of the version these notes were "
                     "written about, so you can see each problem before you fix it.")
    lines += ["When you call finish, answer every note by its number in notes_done: whether you did it, and how "
              "(or why you could not).", ""]
    return lines


def _shell_part_prompt(rnd, part, notes):
    """The body builder's task: the car, its notes, the current body module and the sheet reference."""
    plan = _without_seeds(rnd.plan)
    code = rnd.files.read(f"parts/{part}.py") if rnd.files.exists(f"parts/{part}.py") else ""
    lines = [f"Shape the car body (`{part}`) for version {rnd.number}.", "", "## The car (plan)", _json(plan), "",
             "## Spec", _json(rnd.spec), ""]
    if rnd.plan.get("reference_views"):
        lines += ["Reference images are paired with views (reference number: view); write_part shows each beside "
                  "the same view of your body.", ""]
    if notes:
        lines += _notes_lines(rnd, notes)
    lines += ["## Current body module (keep what works)", "```python", code, "```", "",
              "## Car sheets", at.car_reference(body_sheet(code))]
    return "\n".join(lines)


def _examples_lines(entry, library):
    """Up to two well-scored library parts like this one, as worked examples to borrow from."""
    if library is None:
        return []
    query = " ".join([entry.get("name", "").replace("_", " "), entry.get("description", "")])
    examples = library.examples(query, exclude=[entry.get("library_part")])
    if not examples:
        return []
    lines = ["## Worked examples from the library (parts like yours from well-scored models)",
             "Working code from earlier models. Do not copy it: borrow how it builds the shape and its "
             "small details (profiles, bevels, insets, how pieces meet) where they fit your plan entry.", ""]
    for ex in examples:
        score = f", model scored {ex['score']:g}/100" if ex.get("score") is not None else ""
        lines += [f"### {ex['name']}{score}: {ex['description']}", "```python", ex["code"], "```", ""]
    return lines


def _part_prompt(rnd, part, notes, library=None):
    entry = next(p for p in _without_seeds(rnd.plan)["parts"] if p["name"] == part)
    if entry.get("builder") == "shell":
        return _shell_part_prompt(rnd, part, notes)
    others = [f"- {p['name']}: {p.get('dimensions')} attaches to {p.get('attaches_to', '?')}"
              for p in rnd.plan["parts"] if p["name"] != part]
    lines = [f"Build the part `{part}` for version {rnd.number}.", "", "## Your plan entry", _json(entry), "",
             "## Spec", _json(rnd.spec), "", "## The other parts (build only yours)", *others, ""]
    parent = _parent(rnd.plan, part)
    if parent and rnd.files.exists(f"parts/{parent}.py"):
        lines += [f"## The part yours attaches to: {parent} (fit yours to this code, not to the plan's numbers)",
                  f"measured: {_json(rnd.part_reports.get(parent))}", "```python",
                  rnd.files.read(f"parts/{parent}.py"), "```", ""]
    if notes:
        lines += _notes_lines(rnd, notes)
    if rnd.files.exists(f"parts/{part}.py"):
        lines += ["## Current code of your part (keep what works)", "```python", rnd.files.read(f"parts/{part}.py"),
                  "```", ""]
    lines += _examples_lines(entry, library)
    lines += _family_lines(rnd.spec, "build")
    kit = at.kits_reference(families.of(rnd.spec))
    if kit:
        lines += [kit, ""]
    lines += ["## Helper library reference (from bpy_lib import helpers as h)", at.helpers_reference()]
    if at.shell_template(plan=rnd.plan):
        lines += ["", "## This piece goes on a car body built from a sheet",
                  "The assembly places it from the sheet, so build it exactly about the anchor in your plan entry. "
                  "Your current code starts from a shell helper; keep its material slots (shell.slot names) so the "
                  "car's materials reach it.", "",
                  "## Shell helper reference (from bpy_lib import shell)",
                  at.api_reference(config.BPY_LIB / "shell.py", "shell", skip=())]
    return "\n".join(lines)


def _mechanism_lines(rnd, role):
    """The mechanism sheet for the assembler, the animator or the critic, with what it means for them."""
    sheet = (rnd.spec or {}).get("mechanism")
    if not sheet:
        return []
    lay = mechanism.layout(sheet)
    places = {n: {"centre_m": [round(v, 5) for v in pl["centre"]],
                  "axis": [round(pl["rotation"][r][2], 4) for r in range(3)],
                  "start_angle_deg": round(math.degrees(pl["phase"]), 3)} for n, pl in lay["place"].items()}
    head = ["## The mechanism sheet (the engineer's numbers; every agent works from these)", _json(sheet), "",
            "## Where the sheet puts each part (centre in metres, its own axis, the angle it starts at about it)",
            _json(places), ""]
    tools = tool_parts(rnd.plan)
    if role == "assembly":
        return head + [f"The parts {', '.join(tools) or '(none)'} are made by the part tools with their axis on local Z "
                       "and tooth 0 on local +X: place each exactly as above (matrix = translation @ the axis rotation "
                       "@ a turn of start_angle about local Z) so its teeth sit in its mate's gaps, then put the whole "
                       "mechanism on the floor together (h.drop_to_floor on every part at once). The mesh check measures "
                       "meshing teeth through the loop and fails a pair set too close or too far apart.", ""]
    if role == "motion":
        return head + ["Take every speed from the sheet, do not choose them: spin the driver with "
                       f"m.spin(parts['{sheet['driver']}'], rpm={sheet['rpm']}"
                       f"{', reverse=True' if sheet['direction'] == 'clockwise' else ''}) (a positive turn is "
                       "counterclockwise seen from the front), then m.mesh_drive(driver, driven) along every mesh and "
                       "m.mesh_drive(gear, shaft, ratio=1, same_direction=True) for a part fixed to a shaft. The loop "
                       "length is chosen by itself so every part ends where it began.", ""]
    return head + ["## This is a mechanism: judge tooth shape and meshing first",
                   "The geometric checks include the mesh check (teeth_clear, teeth_engaged, centre_distance, "
                   "speed_ratio, no_clashes), measured at 12 moments across the loop. view_renders ends with the "
                   "motion strip. Compare the teeth with the reference photos: involute flanks, the right tooth "
                   "count and depth, rims and hubs in proportion. A pair that cuts into or floats off its mate goes "
                   "to `assembly`; a wrong speed or direction to `motion`.", ""]


def _lead_part(plan, spec):
    """The model's main part: the largest by its planned size among the parts a builder writes. It
    decides how the whole model looks, so it is written on the stronger model. None for a scene (its
    largest part is the ground) and for a plan with no sizes."""
    if families.of(spec) == "scene":
        return None
    best, biggest = None, 0.0
    for p in (plan or {}).get("parts", []):
        if p.get("asset") or p.get("builder") == "shell":
            continue
        d = p.get("dimensions") or {}
        try:
            volume = float(d.get("width") or 0) * float(d.get("depth") or 0) * float(d.get("height") or 0)
        except (TypeError, ValueError):
            continue
        if volume > biggest:
            best, biggest = p.get("name"), volume
    return best


async def _build_parts(rnd, todo, notes, library, prev_dir):
    ready = set(asset_parts(rnd.plan)) | set(tool_parts(rnd.plan))  # no builder: catalogue models, part tools
    await _measure_asset_parts(rnd, [p for p in todo if p in ready])
    todo = [p for p in todo if p not in ready]
    lead_model = str(config.get("loop", "lead_builder_model", "") or "")
    lead_part = _lead_part(rnd.plan, rnd.spec) if lead_model else None
    gate = asyncio.Semaphore(int(config.get("loop", "parallel_builders", 4)))
    waits = _waits_for(rnd.plan, todo)
    done = {p: asyncio.Event() for p in todo}

    async def one(part):
        try:
            await built(part)
        finally:
            done[part].set()

    async def built(part):
        if part in waits:
            await done[waits[part]].wait()
        entry = next((p for p in rnd.plan["parts"] if p["name"] == part), {})
        shell_body = entry.get("builder") == "shell"
        # reference images: a car's body builder, and every builder of a model that is not a sheet-built car
        refs = (at.reference_tools(rnd.project, on_view=lambda: rnd.sink.setdefault("refs_seen", set()).add(part))
                if shell_body or not at.shell_template(plan=rnd.plan) else [])
        if refs:
            rnd.sink.setdefault("refs_offered", set()).add(part)
        tools = at.builder_tools(rnd, part, library) + refs
        lead = {}
        if part == lead_part and not shell_body:
            lead = {"model": lead_model, "caps": config.caps("lead_builder")}
        async with gate:
            prompt = _part_prompt(rnd, part, notes.get(part, []), library)
            res = await runner.run_agent("shell_builder" if shell_body else "builder", prompt, tools,
                                         project=rnd.project, version=rnd.number, doing=f"part {part}", step=True,
                                         **lead)
        if rnd.part_ok.get(part):
            return
        previous = Path(prev_dir) / "parts" / f"{part}.py" if prev_dir else None
        if previous and previous.is_file():
            rnd.files.write(f"parts/{part}.py", previous.read_text(encoding="utf-8"))
            what = "kept the previous version's code"
        elif entry.get("seed"):
            rnd.files.write(f"parts/{part}.py", entry["seed"])
            what = "went back to the plan's starting code"
        elif rnd.files.exists(f"parts/{part}.py") and rnd.part_ok.get(part) is False:
            rnd.files.path(f"parts/{part}.py").unlink()
            what = "left it out of this version"
        else:
            what = "it has no code"
        db.add_event("warning", project=rnd.project, agent="builder",
                     data={"text": f"part {part} did not build ({res.error or 'last attempt failed'}); {what}",
                           "traceback": rnd.part_errors.get(part, "")[-1500:]})

    await asyncio.gather(*(one(p) for p in todo))


async def _assemble(rnd, notes):
    lines = [f"Write the assembly for version {rnd.number}.", "", "## Plan", _json(_without_seeds(rnd.plan)), "",
             "## Spec dimensions (the finished bounding box)", _json(rnd.spec.get("dimensions")), "",
             "## Measured size of each part in its own frame", _json(rnd.part_reports), ""]
    lines += _mechanism_lines(rnd, "assembly")
    if notes:
        lines += _notes_lines(rnd, notes)
    if rnd.files.exists("assembly.py"):
        lines += ["## Current assembly (keep what works)", "```python", rnd.files.read("assembly.py"), "```", ""]
    lines += ["## Helper library reference (from bpy_lib import helpers as h)", at.helpers_reference()]
    await runner.run_agent("assembler", "\n".join(lines), at.assembler_tools(rnd), project=rnd.project,
                           version=rnd.number, doing="placing the parts", step=True)


async def _none():
    return None


def _round_of(project, folder):
    """A finished version's folder as a round, to rebuild its model from its own files."""
    folder = Path(folder)
    files = ProjectFiles(folder)
    return Round(project=project, number=int(folder.name[1:]) if folder.name[1:].isdigit() else 0, dir=folder,
                 files=files, spec=json.loads(files.read("spec.json")), plan=json.loads(files.read("plan.json")))


async def _assembly_faults(rnd, doing="checking the parts still fit"):
    """The assembly this round already has, built and checked with no agent: what is wrong with it
    as notes for the assembler, [] when every check passes."""
    steps, _ = at.model_steps(rnd, materials=False, motion=False)
    n = at._attempt(rnd, "assembly")
    live = rnd.files.path(f"previews/assembly/{n:02d}/live.glb")
    with runner.acting("assembler", rnd.project, f"{doing} for v{rnd.number}", "full_build") as act:
        result = await blender.run_job_async(steps + [blender.measure_step(), blender.live_step(live)])
        act.ok = result.ok
    at._live(rnd, "assembly", n, result, "assembly")
    if not result.ok:
        return ["The assembly did not build:\n" + (result.summary().get("traceback") or "")[-1500:]]
    found = checks.evaluate(result.outputs.get("measure"), at.check_spec(rnd))
    rnd.sink["assembly_checks"] = found
    return [f"Check {k} failed: {g['detail']}" for k, g in found["gates"].items() if not g["pass"]]


def _size_notes(rnd, parts, before):
    """Notes for the assembler about rebuilt parts whose measured size changed by more than the
    part size tolerance: their neighbours were placed around the old size."""
    tol = float(config.get("model", "part_size_tolerance", 0.03))
    notes = []
    for part in parts:
        old, new = before.get(part) or {}, (rnd.part_reports.get(part) or {}).get("size_m") or {}
        if not old or not new:
            continue
        if any(abs(float(new.get(a) or 0) - float(old.get(a) or 0)) > tol * max(abs(float(old.get(a) or 0)), 1e-6)
               for a in ("x", "y", "z")):
            was, now = (" x ".join(f"{float(s.get(a) or 0):.3f}" for a in ("x", "y", "z")) for s in (old, new))
            notes.append(f"The part {part} was rebuilt and its size changed from {was} m to {now} m (x, y, z): "
                         "place it again so it still meets what it attaches to, and move its neighbours only "
                         "where they no longer fit it. Leave every other part where it is.")
    return notes


def _materials_shape(report):
    """A material report in a form two builds can be compared by: every slot with what it is and
    which parts carry it."""
    return {str(k): {**v, "parts": sorted(v.get("parts") or [])} for k, v in (report or {}).items()
            if isinstance(v, dict)}


def _motion_shape(report):
    """A motion report in a form two builds can be compared by: what moves and how many markers."""
    report = report or {}
    return {"animated": sorted(report.get("animated") or {}), "markers": len(report.get("markers") or []),
            "frames": report.get("frames"), "mechanism": report.get("mechanism")}


async def _stage_report(rnd, motion=True):
    """The model of a round built from its files with no agent, for comparing two versions: how its
    materials and its motion came out. {"ok": False, "failed_step", "error"} when it does not build;
    None when the round has no code."""
    steps, _ = at.model_steps(rnd, motion=motion)
    if not steps:
        return None
    result = await blender.run_job_async(steps + [blender.material_report_step()])
    if not result.ok:
        info = result.summary()
        return {"ok": False, "failed_step": result.failed_step or "",
                "error": (info.get("traceback") or info.get("error") or "")[-1500:]}
    return {"ok": True, "materials": _materials_shape(result.outputs.get("material_report")),
            "motion": _motion_shape(result.outputs.get("motion"))}


async def _stage_fault(rnd, before, stage, rebuilt):
    """Whether the materials (or the motion) kept from the version this one starts from still work
    now that parts were rebuilt: None when they come out as they did before, else the note for the
    agent that redoes them. Without a `before` to compare with, the agent redoes them as it always
    did."""
    names = ", ".join(rebuilt)
    what = {"materials": "materials", "motion": "motion"}[stage]
    again = (f"The parts {names} were rebuilt for this version. Check the {what} still fit them and fix only "
             "what no longer does; keep everything else as it is.")
    if not before or not before.get("ok"):
        return again
    with runner.acting("materials" if stage == "materials" else "animator", rnd.project,
                       f"checking the {what} of v{rnd.number}", "full_build") as act:
        now = await _stage_report(rnd, motion=stage == "motion")
        act.ok = bool(now and now.get("ok"))
    if not now:
        return again
    if not now["ok"]:
        if stage in now["failed_step"]:
            return (f"The parts {names} were rebuilt and the {what} file no longer runs on them. It fails at "
                    f"'{now['failed_step']}' with:\n{now['error']}\nFix it; keep everything else as it is.")
        return None  # a part or the assembly fails: the full build reports it and sends it to its own agent
    if now[stage] == before[stage]:
        return None
    if stage == "materials":
        old, new = before["materials"], now["materials"]
        diffs = [f"slot {k} is gone" for k in old if k not in new]
        diffs += [f"slot {k} is new and has no material from the file" for k in new if k not in old]
        diffs += [f"slot {k} changed (parts {', '.join(new[k]['parts'])})" for k in new if k in old and new[k] != old[k]]
    else:
        old, new = before["motion"], now["motion"]
        diffs = [f"{k}: was {old.get(k)}, now {new.get(k)}" for k in new if new.get(k) != old.get(k)]
    return (f"The parts {names} were rebuilt and the {what} no longer come out as before: " + "; ".join(diffs[:12])
            + ". Fix exactly that; keep everything else as it is.")


async def _assemble_shell(rnd, notes):
    """A car's assembly is generated from its sheet. It is built and checked without an agent; the
    assembler is called only when the checks fail or there are notes for it. Returns whether the
    assembler ran."""
    if rnd.files.exists("assembly.py") and not notes:
        notes = await _assembly_faults(rnd, "placing the car's pieces")
        if not notes:
            return False
    notes = list(notes) + ["This is a car: assembly.py calls bpy_lib.shells.car.assemble(parts), which places the "
                           "wheels on the axles and seats the mirrors, wing and exhaust on the body from the sheet "
                           "stored on the body. Keep that call and correct after it only what the checks show. "
                           "Move the wing through wing_y in the sheet, never with a hand offset after "
                           "car.assemble: it already seats the wing on the deck, and an offset stays put while "
                           "the sheet changes the deck under it."]
    await _assemble(rnd, notes)
    return True


async def _materials(rnd, notes, library):
    parts = [{"name": p["name"], "material": p.get("material"), "description": p.get("description")}
             for p in rnd.plan["parts"]]
    lines = [f"Give version {rnd.number} its materials.", "", "## Spec materials and style",
             _json({k: rnd.spec.get(k) for k in ("object", "style", "materials", "use")}), "",
             "## Parts and their planned material", _json(parts), ""]
    if notes:
        lines += _notes_lines(rnd, notes)
    lines += _family_lines(rnd.spec, "materials")
    if str(rnd.spec.get("use") or "").lower() == "game" and not at.shell_template(plan=rnd.plan):
        colours = (rnd.spec.get("research") or {}).get("colours") or []
        lines += ["## This is a game asset",
                  "Its game file carries, for each material, one plain colour, a roughness and metal-or-not; "
                  "patterns, wear, flake and glaze effects are dropped. So what counts is: the exact colour of "
                  "every part (the person's, then the research's hex codes), the right roughness (glazed ceramic "
                  "0.1 to 0.2, satin paint 0.3 to 0.4, plastic 0.4 to 0.6, rubber 0.8, bare wood 0.6) and metallic "
                  "only on bare metal, and a separate material wherever the real thing changes material or colour, "
                  "so parts read apart. Do not spend attempts tuning a pattern.",
                  *([f"The real colours, from the research: {'; '.join(colours)}"] if colours else []), ""]
    if not at.shell_template(plan=rnd.plan):
        lines += ["Call `list_slots` before your first `write_materials`: parts built with a kit carry named "
                  "material slots, filled with `m.assign_slot(obj, slot, material)`; `m.assign` on such a part "
                  "would wipe its slots and paint it one colour.", ""]
    ready = asset_parts(rnd.plan)
    if ready:
        lines += [f"These parts are ready-made models with their own textures; give them no material: "
                  f"{', '.join(ready)}.", ""]
    if rnd.files.exists("materials.py"):
        lines += ["## Current materials file (keep what works)", "```python", rnd.files.read("materials.py"), "```", ""]
    if at.shell_template(plan=rnd.plan):
        lines += ["## This is a car built with material slots",
                  "Its body and pieces carry one placeholder material per slot (paint, glass, trim, lamp_front, "
                  "lamp_rear, lamp_amber, tyre, rim, chrome, accent). Keep m.car_finish(parts, ...) as the base and "
                  "tune its arguments (paint colour, style, accent, rim, glass_tint, trim), or replace one slot after "
                  "it with m.assign_slot(obj, slot, material). Never m.assign the body: it would paint over the "
                  f"glass, lamps and trim. The person's finish: {_json(rnd.plan.get('finish'))}", ""]
    lines += ["## Recipe reference (from bpy_lib import materials as m)", at.materials_reference()]
    await runner.run_agent("materials", "\n".join(lines), at.materials_tools(rnd, library), project=rnd.project,
                           version=rnd.number, doing="materials", step=True)


async def _animate(rnd, notes):
    parts = [{k: p.get(k) for k in ("name", "description", "count", "location", "rotation_deg", "instances")
              if p.get(k) is not None} for p in rnd.plan["parts"]]
    lines = [f"Give version {rnd.number} its motion.", "",
             "## What the spec asks for",
             _json({k: rnd.spec.get(k) for k in ("object", "summary", "dimensions", "use", "motion")}), "",
             "## Parts (the keys of `parts` in animate(parts)) and where the assembler put them", _json(parts), ""]
    lines += _mechanism_lines(rnd, "motion")
    if notes:
        lines += _notes_lines(rnd, notes)
    if rnd.files.exists("motion.py"):
        lines += ["## Current motion file (keep what works)", "```python", rnd.files.read("motion.py"), "```", ""]
    lines += ["## Helper reference (from bpy_lib import motion as m)", at.motion_reference()]
    await runner.run_agent("animator", "\n".join(lines), at.animator_tools(rnd), project=rnd.project,
                           version=rnd.number, doing="adding the motion", step=True)


async def _final_build(rnd):
    steps, missing = at.model_steps(rnd)
    if not steps:
        return {"ok": False, "error": "no part has working code", "failed_step": ""}
    blend = rnd.files.path("model.blend")
    sheet = (rnd.spec or {}).get("mechanism")
    pairs = mechanism.check_pairs(mechanism.layout(sheet)) if sheet else []
    steps += [{"kind": "save", "path": str(blend), "label": "save the .blend"},
              blender.measure_step(), *([blender.mesh_check_step(pairs)] if pairs else []),
              {"kind": "export", "dir": str(rnd.files.path("exports")), "basename": rnd.project, "label": "export"}]
    with runner.acting("assembler", rnd.project, f"building and checking v{rnd.number}", "full_build") as act:
        result = await blender.run_job_async(steps)
        act.ok = result.ok
    if not result.ok:
        info = result.summary()
        return {"ok": False, "error": info.get("traceback") or info.get("error", "Blender failed"),
                "failed_step": result.failed_step, "fatal": result.missing or result.timed_out}
    measure = result.outputs.get("measure") or {}
    rnd.files.write("measure.json", _json(measure))
    found = checks.evaluate(measure, at.check_spec(rnd))
    if pairs:
        mesh = result.outputs.get("mesh_check") or {}
        rnd.files.write("mesh_check.json", _json(mesh))
        found["gates"].pop("no_floaters", None)  # gears on shafts are shown running, not resting on anything
        for name, g in checks.mesh_gates(mesh).items():
            found["gates"][name] = {"pass": g["pass"], "detail": g["detail"]}
        found["passed"] = all(g["pass"] for g in found["gates"].values())
    if missing:
        found["warnings"].append(f"parts missing from this version: {', '.join(missing)}")
    rnd.files.write("checks.json", _json(found))
    _handoff(rnd.project, "assembler", "critic", f"v{rnd.number} to render")
    family = families.of(rnd.spec)
    # the renders and the game files both read the saved model: they are made side by side
    (rendered, renders, sheets), game = await asyncio.gather(
        _render(rnd.project, rnd.number, blend, rnd.files.path("renders"), car=bool(at.shell_template(plan=rnd.plan)),
                reference_views=rnd.plan.get("reference_views"), references=at.reference_paths(rnd.project),
                motion=rnd.files.exists("motion.py"), views=families.views(family), studio=families.studio(family)),
        _game_export(rnd.dir, rnd.project, rnd.spec))
    if not rendered.ok or not sheets.get("turntable"):
        info = rendered.summary()
        return {"ok": False, "fatal": True, "failed_step": rendered.failed_step,
                "error": "rendering failed: " + (info.get("traceback") or info.get("error") or "no images")}
    if game:
        found["game"] = game
        rnd.files.write("checks.json", _json(found))
    return {"ok": True, "measure": measure, "checks": found, "renders": renders, "sheets": sheets,
            "exports": result.outputs.get("exports", {}), "device": rendered.outputs.get("device")}


async def _critic(rnd, outcome, last_review, prev_dir=None, judge=True):
    """judge=False: this version carries the person's own changes, not the last review's change
    list, so that list is shown for what it is and none of it is marked landed or missed."""
    rubric_text = at.rubric_file(rnd.plan, rnd.spec).read_text(encoding="utf-8")
    plan_summary = [{k: p.get(k) for k in ("name", "description", "dimensions", "material")} for p in rnd.plan["parts"]]
    lines = [f"Review version {rnd.number}.", "", "## Spec", _json(rnd.spec), "", "## Plan (parts)",
             _json(plan_summary), "", "## Rubric", rubric_text, "", "## Geometric checks", _json(outcome["checks"]), "",
             "Give any colour as a hex code (e.g. #2F5D3A) or as 0-1 RGB the way a colour picker shows it, never as "
             "Blender's internal linear values or a glTF baseColorFactor: the material recipes take picker colours "
             "and would convert a linear value a second time, which turns it near-black.", ""]
    lines += _family_lines(rnd.spec, "review")
    proportions = list((rnd.spec.get("research") or {}).get("proportions") or [])
    if proportions:
        lines += ["## The real object's proportions (from the research): check every one",
                  *[f"{i}. {p}" for i, p in enumerate(proportions, 1)],
                  "In `accuracy`, answer each by its number: met or not, and what you measured (measure_part) or "
                  "saw. One that is not met should have a change, the worst first.", ""]
    unverified = list((rnd.spec.get("research") or {}).get("unverified") or [])
    if unverified:
        lines += ["## Guessed, not known (the research could neither find nor measure these)",
                  *[f"- {u}" for u in unverified],
                  "Never score the model against these numbers and never ask for one of them. Judge what they are "
                  "about by eye against the reference photos (view_reference) and the flat views; with no photo "
                  "of it, say in the summary that it is unverified.", ""]
    if not at.shell_template(plan=rnd.plan):
        lines += [f"A change can also target `{at.PLAN_TARGET}`: use it when the fault is in the plan itself, not in "
                  "one part's shape: a feature of the spec or the research that no part carries (a part must be "
                  "added), parts that should be one piece because the real thing is one surface, or one part that "
                  "should be several. It goes to the architect, who changes the plan before the builders start. Say "
                  "what to add, merge or split, with sizes and where it sits.", ""]
    if families.views(families.of(rnd.spec)) and not at.shell_template(plan=rnd.plan):
        lines += ["view_renders also shows a flat views sheet (no perspective): use it to judge proportions and "
                  "to see every side.", ""]
    ready = asset_parts(rnd.plan)
    if ready:
        lines += [f"These parts are ready-made models placed whole ({', '.join(ready)}): they cannot be reshaped "
                  "or given materials. A change to one can only move, turn or resize it (target `assembly`).", ""]
    if not at.shell_template(plan=rnd.plan):
        lines += ["Before any material change, call inspect_materials: it reads what every material really is. "
                  "Drop a change it shows is already set.", ""]
    lines += _mechanism_lines(rnd, "review")
    if at.has_motion(rnd.spec) and not rnd.spec.get("mechanism"):
        lines += ["## This model moves",
                  f"The spec's motion entry: {_json(rnd.spec.get('motion'))}. view_renders ends with the motion "
                  "strip: frames across one loop, from the side close up on the biggest spinning wheel, then a "
                  "close-up of each smoke or steam marker as a ball (grey smoke, white steam). Smoke and steam are drawn by the "
                  "page at those balls and never appear in a render, so never ask for them to show: judge only "
                  "where the balls sit. The criteria score the still model as always; judge the motion only "
                  "through changes with target `motion` (a wheel whose spokes do not move or that turns the wrong "
                  "way, a ball in the wrong place, a part left behind by the bounce), which go to the animator.", ""]
    if at.shell_template(plan=rnd.plan):
        code = rnd.files.read("parts/body.py") if rnd.files.exists("parts/body.py") else ""
        sheet = body_sheet(code) or rnd.plan.get("sheet") or {}
        names = [f["name"] for f in car_sheet.features(car_sheet.resolve(sheet))]
        notes = car_sheet.proportion_notes(car_sheet.resolve(sheet))
        lines += ["## This is a car: its body is one skin built from this sheet", _json(sheet), "",
                  *(["Its proportions against usual cars of its kind (advice, measured from the sheet): "
                     + " ".join(notes), ""] if notes else []),
                  f"Its details (features): {', '.join(names)}.",
                  "For a change to the body, target `body` and name in `parameter` the sheet number (e.g. "
                  "roof_rear, wheel_diameter, belt_z) or the detail (e.g. feature headlights) to change, with the new "
                  "value where you can measure it. The car views sheet shows flat side, front, rear and top views "
                  "and four low three-quarter views; the reference pairs sheet sets each reference image beside the "
                  "same view of the model.",
                  "The car is shot in a car studio: bright streaks and highlight lines on glossy paint and glass are "
                  "reflections of its light strips, not the paint's colour and not holes in the glass. Judge the paint "
                  "colour on the side panels away from a highlight, and do not count a reflection as a material or "
                  "surface fault.",
                  "Score each area of the car in `areas` (nose, lamps, glasshouse, doors_panels, arches_wheels, tail) "
                  "on its own, against the real car or the references, and give every change the `area` it improves. "
                  "At least one change must aim at one of your two weakest areas: the next version works on what "
                  "looks worst, and areas that already look right are left alone. Before any material change, call "
                  "inspect_materials and drop what is already set.", ""]
    listed = (last_review or {}).get("changes") or []
    previous_changes = listed if judge else []
    previous_sheet = Path(prev_dir) / "renders" / "sheet_turntable.jpg" if prev_dir else None
    previous_sheet = previous_sheet if previous_sheet and previous_sheet.is_file() else None
    if last_review:
        lines += ["## The version this one was built from, and its review",
                  f"It scored {last_review['score']}: {last_review.get('summary', '')}",
                  "Its change list, by number:" if judge else
                  "This version was built for changes the person asked for themselves, so nobody worked on that "
                  "review's change list. Here it is for what it is worth: put on your new list whichever of these "
                  "still apply to what you see, and do not treat any as tried.",
                  *[f"{i}. {c.get('target')} | {c.get('parameter', '')}: {c.get('change', '')}"
                    + (f" (already missed {c['misses']} time{'s' if c['misses'] != 1 else ''}: if it missed again it "
                       "is stuck and leaves the list by itself)" if c.get("misses") and judge else "")
                    for i, c in enumerate(listed, 1)], ""]
        stuck = last_review.get("stuck") or []
        if stuck:
            lines += ["These changes are stuck: they missed twice, so the builders cannot make them with the tools "
                      "they have. Do not ask for them again; ask for something else that improves the same area.",
                      *[f"- {s.get('target')} | {s.get('parameter', '')}: {s.get('change', '')}" for s in stuck], ""]
        if previous_sheet:
            lines.append("view_renders also shows that version's turntable after this one's: compare the two, and "
                         "score this version against it (worse than it means lower scores than it got).")
        if previous_changes:
            lines.append("In `landed`, mark every one of those changes by its number: landed true or false. Each "
                         "change that did not land goes back at the top of your new change list.")
    refs = at.reference_tools(rnd.project)
    if refs:
        lines.append("\nThis model has reference images (the person's, or photos the research kept): compare "
                     "with them too (view_reference).")
    waits = list(CRITIC_RETRY_S)
    reminded = False
    error = None
    while True:
        tools = at.critic_tools(rnd, outcome["sheets"], previous_sheet, previous_changes,
                                (last_review or {}).get("stuck") or []) + refs
        res = await runner.run_agent("critic", "\n".join(lines), tools,
                                     project=rnd.project, version=rnd.number, doing=f"reviewing v{rnd.number}",
                                     step=True)
        if rnd.sink.get("review"):
            return rnd.sink["review"]
        error = getattr(res, "error", None)
        if error and runner.account_limit(error):
            break  # out of usage: trying again before the reset fails the same way
        if error and not getattr(res, "turns", None):
            # the run stopped before the critic said anything (Claude Code crashed): wait, then ask again
            if not waits:
                break
            await asyncio.sleep(waits.pop(0))
            continue
        if reminded:
            break
        reminded = True  # it answered but did not submit: once more, with a reminder
        lines.append("\nYou must look with view_renders and then call submit_review.")
    return not_reviewed(error)


CRITIC_RETRY_S = (30, 120)  # waits before asking the critic again after a run that stopped without an answer


def not_reviewed(error=None):
    """The review of a version the critic could not review: no score (never a 0), no changes."""
    why = runner.account_limit(error) if error else None
    return {"scores": {}, "changes": [], "score": None, "reviewed": False,
            "summary": "The critic could not review this version" + (f" ({why})" if why else "") + "."}


async def _librarian(project, number, library):
    folder = version_dir(project, number)
    files = ProjectFiles(folder)
    plan = json.loads(files.read("plan.json"))
    rnd = Round(project=project, number=number, dir=folder, files=files, spec={}, plan=plan)
    review = json.loads(files.read("review.json")) if files.exists("review.json") else {}
    reports = json.loads(files.read("part_reports.json")) if files.exists("part_reports.json") else {}
    kind = " It is a car (plan kind shell): its body module holds the car sheet." if at.shell_template(plan=plan) else ""
    lines = [f"The finished model is {project} v{number}. Decide what to keep in the library.{kind}", "",
             "## Critic's review", _json({k: review.get(k) for k in ("score", "scores", "summary", "changes")}), ""]
    for p in plan["parts"]:
        if files.exists(f"parts/{p['name']}.py"):
            lines += [f"## Part {p['name']}", _json({k: p.get(k) for k in ("description", "method", "dimensions")}),
                      f"measured: {_json(reports.get(p['name']))}", "```python", files.read(f"parts/{p['name']}.py"),
                      "```", ""]
    if files.exists("materials.py"):
        lines += ["## Materials file", "```python", files.read("materials.py"), "```"]
    await runner.run_agent("librarian", "\n".join(lines), at.librarian_tools(rnd, library), project=project,
                           version=number, doing="updating the library", step=True)


# --- routing feedback to the agents ------------------------------------------------------

def _route_failure(outcome, parts):
    step = outcome.get("failed_step", "")
    text = f"The full build failed at '{step}' with:\n{outcome['error'][-1500:]}"
    for p in parts:
        if f"'{p}'" in step:
            return {p: [text]}
    if "assembly" in step:
        return {"assembly": [text]}
    if "materials" in step:
        return {"materials": [text]}
    if "motion" in step:
        return {"motion": [text]}
    return {"assembly": [text]}


def _route_changes(review, outcome, parts):
    notes = {}
    for c in review.get("changes", []):
        target = c.get("target")
        text = f"{c.get('parameter', '')}: {c.get('change', '')}".strip(": ")
        notes.setdefault(target, []).append(text)
    areas = review.get("areas") or {}
    good = sorted(a for a, v in areas.items() if float(v) >= 7)
    if good and "body" in notes:
        notes["body"].append(f"Keep as they are: the areas {', '.join(good)} already look right (critic scored them "
                             "7 or more). Change only what the notes above ask, and nothing in those areas.")
    gates = outcome["checks"]["gates"]
    for part in (outcome.get("measure") or {}).get("parts", []):
        if part.get("non_manifold_edges") and part.get("part") in parts:
            notes.setdefault(part["part"], []).insert(
                0, f"Hard gate: the mesh has {part['non_manifold_edges']} open (non-manifold) edges; make it a closed solid")
    for gate in ("no_floaters", "correct_size", "on_floor"):
        if not gates.get(gate, {"pass": True})["pass"]:
            notes.setdefault("assembly", []).insert(0, f"Hard gate {gate} failed: {gates[gate]['detail']}")
    for warning in outcome["checks"].get("warnings", []):
        notes.setdefault("assembly", []).append(f"Check warning: {warning}")
    return notes
