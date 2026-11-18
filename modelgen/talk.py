"""The team as the person meets it: names, what each member has been doing, and direct messages.

work_summary() is what one member has done lately, read from the runs, the live status and the
latest version's files. The director's team_member tool uses it for "Ask Hange about Annie", and a
member answering a direct message reads it through its my_work tool.
"""

import json

from . import config, db, pipeline, runner
from .tools.library import Library

CAST = {"director": "Hange", "brief": "Armin", "architect": "Pieck", "builder": "Eren", "assembler": "Mikasa",
        "materials": "Reiner", "animator": "Jean", "critic": "Annie", "librarian": "Historia"}
JOBS = {"director": "talks with the person and runs the studio", "brief": "writes the spec",
        "architect": "plans how each part is built", "builder": "writes each part's Blender code",
        "assembler": "places the parts and checks the fit", "materials": "picks and tunes the materials",
        "animator": "gives the model its motion: turning wheels, a bounce, smoke and steam",
        "critic": "renders and reviews each version", "librarian": "keeps reusable parts in the library"}
FALLBACK = "Sorry, I couldn't answer just now ({error}). Try again in a moment."


def role_of(name):
    """The role for a name, role or seat id ('Annie', 'critic', 'builder-2'), or None."""
    text = (name or "").strip().lstrip("@").lower()
    base = text.split("-")[0]
    if base in CAST:
        return base
    for role, person in CAST.items():
        if person.lower() == text or text.startswith(person.lower()):
            return role
    return None


def _latest_project():
    projects = db.list_projects()
    return projects[0]["name"] if projects else None


def _clip(text, n=3000):
    text = text or ""
    return text if len(text) <= n else text[:n] + "\n... (cut)"


def _latest_version(project):
    versions = db.versions(project) if project else []
    return versions[-1] if versions else None


def _role_work(role, seats, project):
    """The latest piece of work this role made, from the newest version's files."""
    version = _latest_version(project)
    if not version:
        return None
    folder = pipeline.version_dir(project, version["number"])
    read = lambda name: (folder / name).read_text(encoding="utf-8") if (folder / name).is_file() else None
    if role == "brief":
        return {"spec": (db.get_project(project) or {}).get("spec")}
    if role == "architect":
        plan = json.loads(read("plan.json") or "{}")
        return {"plan_parts": [{k: p.get(k) for k in ("name", "method", "dimensions")} for p in plan.get("parts", [])],
                "assembly_notes": plan.get("assembly_notes")}
    if role == "builder":
        mine = [r["doing"].replace("part ", "") for r in db.runs(project, 40)
                if r.get("seat") in seats and str(r.get("doing", "")).startswith("part ")]
        parts = list(dict.fromkeys(mine))[:6]
        warnings = [e["data"].get("text") for e in db.events(project, limit=200)
                    if e["kind"] == "warning" and e["agent"] == "builder"][-4:]
        code = {}
        for part in parts[:2]:
            text = read(f"parts/{part}.py")
            if text:
                code[part] = _clip(text, 1800)
        return {"version": version["number"], "parts_i_built": parts, "build_warnings": warnings, "code": code}
    if role == "assembler":
        return {"version": version["number"], "checks": json.loads(read("checks.json") or "null"),
                "assembly": _clip(read("assembly.py"), 2000)}
    if role == "materials":
        return {"version": version["number"], "materials": _clip(read("materials.py"), 2500)}
    if role == "animator":
        return {"version": version["number"], "motion": _clip(read("motion.py"), 2500)}
    if role == "critic":
        scored = [v for v in db.versions(project) if v.get("review")]
        if not scored:
            return None
        v = scored[-1]
        return {"version": v["number"], "score": v["score"], "passed": v["passed"], "review": v["review"]}
    if role == "librarian":
        return {"saved_from_this_model": [e["name"] for e in Library().list() if
                                          str(e.get("source", "")).startswith(f"{project} ")]}
    return None


def work_summary(name, project=None):
    role = role_of(name)
    if not role:
        return {"error": f"there is no team member called '{name}'; the team is "
                         + ", ".join(f"{p} ({r})" for r, p in CAST.items())}
    project = project or _latest_project()
    if role == "builder":
        seats = [name] if str(name).startswith("builder-") else [s for s in runner.STATUS if s.startswith("builder-")]
        seats = seats or ["builder-1"]
    else:
        seats = [role]
    runs = [r for r in db.runs(None, 200) if r.get("seat") in seats or (r.get("seat") is None and r["agent"] == role)]
    calls = []
    for seat in seats:
        entry = runner.STATUS.get(seat) or {}
        calls += [{k: c.get(k) for k in ("tool", "text", "at", "ok")} for c in entry.get("calls", [])]
    from . import questions
    return {
        "name": CAST[role], "role": role, "job": JOBS[role], "model": runner.load_agent(role).model,
        "project": project,
        "now": [{k: (runner.STATUS.get(s) or {}).get(k) for k in ("id", "state", "doing", "since", "error")}
                for s in seats],
        "recent_runs": [{k: r.get(k) for k in ("project", "version", "doing", "ok", "error", "cost_usd", "started")}
                        for r in runs[:6]],
        "recent_tool_calls": sorted(calls, key=lambda c: c["at"] or "")[-8:],
        "open_question": next((questions.for_agent(s) for s in seats if questions.for_agent(s)), None),
        "spent_on_this_model": sum(db.spend_where(project, seat=s) for s in seats) if project else 0,
        "latest_work": _role_work(role, seats, project),
    }


async def chat(agent_id, text, query_fn=None):
    """The person messages one team member directly. Stores both messages and returns the reply."""
    role = role_of(agent_id)
    if not role or role == "director":
        raise ValueError("message the director in the main chat")
    project = _latest_project()
    db.add_agent_message(agent_id, "you", text, project)
    history = "\n".join(f"{'Person' if m['role'] == 'you' else 'You'}: {m['text']}"
                        for m in db.agent_messages(agent_id, 12))
    tool = runner.AgentTool("my_work", "Your recent runs, tool calls, errors, cost and your latest work.",
                            {"type": "object", "properties": {}}, lambda args: work_summary(agent_id, project))
    prompt = (f"You are {CAST[role]}, the {role} of the ModelGen studio: you {JOBS[role]}. This is not a work task. "
              "The person is messaging you directly about your work. Do not build, write or submit anything. "
              "Look at your work with my_work when the question needs facts, then answer as yourself in one to "
              "four short sentences, plainly, in the language of the person's latest message. If they ask for a "
              "change to the model, tell them to ask Hange in the main chat, who starts the team on it.\n\n"
              f"## The conversation (the last line is new)\n{history}")
    res = await runner.run_agent(role, prompt, [tool], project=project, doing="talking with you",
                                 caps=config.caps("talk"), seat=agent_id if role == "builder" else None,
                                 query_fn=query_fn)
    reply = res.text.strip() if res.ok and res.text.strip() else FALLBACK.format(error=res.error or "no reply")
    return db.add_agent_message(agent_id, "agent", reply, project)
