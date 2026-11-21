"""One chat turn with the director, and the director's report when a build finishes.

The director is the only agent that talks with the person. Its tools start a build, send a change
request to the current model, or look at how the current model is going.
"""

from . import config, db, pipeline, runner, talk, uploads
from .tools import agent_tools as at
from .tools import blender

HISTORY = 14
FALLBACK = ("Sorry, I could not reach Claude just now ({error}). Check that ClaudeIn is running on this PC "
            "(its icon is in the hidden icons by the clock), then send your message again.")


def current_project(name=None):
    if name:
        proj = db.get_project(name)
        if proj:
            return proj
    # the newest of the person's own models: one another program asked for (the /v1 API) is never
    # picked on its own, so a message in the chat cannot change it by accident
    own = next((p for p in db.list_projects() if not db.project_client(p["name"])), None)
    return db.get_project(own["name"]) if own else None


def project_summary(name):
    proj = db.get_project(name)
    if not proj:
        return {"project": None}
    versions = db.versions(name)
    done = [v for v in versions if v["status"] == "done"]
    latest = done[-1] if done else None
    return {
        "project": name,
        "title": proj["title"],
        "status": proj["status"],
        "building_now": pipeline.is_running(name),
        "versions": len(versions),
        "accepted_version": proj["accepted_version"],
        "latest": None if not latest else {
            "version": latest["number"], "score": latest["score"], "passed": latest["passed"],
            "summary": (latest["review"] or {}).get("summary"),
            "top_changes": (latest["review"] or {}).get("changes", [])[:3],
        },
        "recent": [e["data"].get("text") for e in db.events(name, limit=60) if e["kind"] == "phase"][-3:],
    }


def _history():
    lines = []
    for m in db.messages(HISTORY):
        who = "Person" if m["role"] == "user" else "You"
        n = len((m.get("meta") or {}).get("attachments") or [])
        lines.append(f"{who}: {m['text']}" + (f" [attached {n} image{'s' if n != 1 else ''}]" if n else ""))
    return "\n".join(lines)


def version_card(project, number):
    """What the chat shows as a card for a finished version: title, score, cost, thumbnail."""
    proj = db.get_project(project)
    v = db.get_version(project, number) if proj else None
    if not v:
        return None
    turn = pipeline.version_dir(project, number) / "renders" / "sheet_turntable.jpg"
    score = f"{v['score'] / 10:.1f} / 10" if v.get("score") is not None else "not scored"
    changes = len((v.get("review") or {}).get("changes") or [])
    cost = db.spend_where(project, version=number)
    return {"project": project, "number": number, "title": f"{proj['title']} v{number}",
            "meta": f"{score} · {changes} change{'s' if changes != 1 else ''} · ${cost:.2f}",
            "thumb": f"/files/{project}/v{number}/renders/sheet_turntable.jpg" if turn.is_file() else None}


async def report(project, outcome):
    """Tell the person how a build ended, in the language they have been using."""
    # The report is written while the build task is still finishing, so is_running() still says
    # yes; the build is over by now, and the summary must say so.
    status = {**project_summary(project), "building_now": False}
    if outcome.get("error") == "blender_missing":
        facts = {"result": "could not start", "reason": f"Blender is not installed on this PC. The install command "
                                                        f"is: {config.INSTALL_HINT}"}
    elif outcome.get("error") == "stopped":
        facts = {"result": "the person stopped the build at a spending limit", "detail": outcome.get("reason"),
                 "status": status}
    elif outcome.get("error") == "cancelled":
        facts = {"result": "the person stopped the build themselves; confirm it is stopped in one sentence, "
                           "and that what was built so far is kept", "status": status}
    else:
        facts = {"result": outcome, "status": status}
    prompt = (f"A build has finished. Report it to the person now, in the language of their latest message.\n\n"
              f"## The result\n{at.dumps(facts)}\n\n## The conversation so far\n{_history()}")
    res = await runner.run_agent("director", prompt, [], project=project, doing="reporting the result")
    text = res.text if res.ok and res.text else (
        f"The build of {project} finished: {outcome.get('error') or ('not reviewed' if outcome.get('score') is None else 'score ' + str(outcome.get('score')))}.")
    card = version_card(project, outcome["version"]) if outcome.get("version") else None
    return db.add_message("assistant", text, project, meta={"version": card} if card else None)


def _pending(project_created=None):
    """Attachments and page options the person sent since the last model was started."""
    since = project_created or "0000"
    refs, options = [], {}
    for m in db.messages_since(since):
        if m["role"] != "user":
            continue
        refs += [a for a in (m["meta"].get("attachments") or []) if uploads.path(a)]
        options.update({k: v for k, v in (m["meta"].get("options") or {}).items() if v})
    return list(dict.fromkeys(refs)), options


async def chat_turn(text, project=None, query_fn=None, attachments=None, options=None):
    """Store the person's message, run the director, store and return its reply.

    attachments: upload ids of reference images sent with the message. options: what the person set
    on the New model screen (size, budget_usd); both go to the next model the director starts."""
    proj = current_project(project)
    state = {"project": proj["name"] if proj else None}
    meta = {}
    if attachments:
        meta["attachments"] = [a for a in attachments if uploads.path(a)]
    if options:
        meta["options"] = {k: v for k, v in options.items() if k in ("size", "budget_usd") and v}
    latest = db.list_projects()
    turn_started = db.now()
    db.add_message("user", text, state["project"], meta=meta or None)
    refs, pending_options = _pending(latest[0]["created"] if latest else None)

    def start_build(args):
        if not blender.available():
            return {"ok": False, "error": f"Blender is not installed on this PC, so nothing can be built yet. "
                                          f"Tell the person to install it with: {config.INSTALL_HINT}"}
        created = db.create_project(args["title"], args["request"],
                                    options={"references": refs, **pending_options})
        # The message that started this model, and what this chat turn costs, belong to it, not
        # to the model that was open before.
        db.move_chat_turn(state["project"], created["name"], turn_started)
        state["project"] = created["name"]
        db.add_event("project_created", project=created["name"], data={"title": created["title"]})
        pipeline.start(created["name"], request=args["request"], on_done=report)
        return {"ok": True, "project": created["name"]}

    def request_changes(args):
        name = state["project"]
        if not name:
            return {"ok": False, "error": "there is no model yet; start one with start_build"}
        if not blender.available():
            return {"ok": False, "error": f"Blender is not installed ({config.INSTALL_HINT})"}
        try:
            pipeline.start(name, changes=args["changes"], on_done=report)
        except pipeline.Busy:
            return {"ok": False, "busy": True, "error": "the team is still working on the current round; ask the "
                                                        "person to send the change again when it finishes"}
        db.add_event("change_requested", project=name, data={"text": args["changes"]})
        return {"ok": True, "project": name}

    def stop_build(args):
        name = state["project"]
        if not name or not pipeline.stop(name):
            return {"ok": False, "error": "nothing is being built right now"}
        return {"ok": True, "project": name, "note": "stopped; a short report follows on its own, so just confirm"}

    def status(args):
        return project_summary(state["project"]) if state["project"] else {"project": None}

    def review_again(args):
        name = state["project"]
        if not name:
            return {"ok": False, "error": "there is no model yet"}
        try:
            number = int(args.get("version") or 0)
        except (TypeError, ValueError):
            number = 0
        v = db.get_version(name, number) if number else None
        if not v or v["status"] != "done":
            return {"ok": False, "error": f"there is no finished v{number} to review"}
        try:
            pipeline.start(name, on_done=report, review=number)
        except pipeline.Busy:
            return {"ok": False, "busy": True, "error": "the team is still working on this model; ask again when "
                                                        "it finishes"}
        return {"ok": True, "project": name, "note": f"the critic is reviewing v{number} again; a report follows"}

    def team_member(args):
        return talk.work_summary(args.get("name", ""), state["project"])

    current = at.dumps(project_summary(state["project"])) if state["project"] else "none yet"
    extra = ""
    if refs:
        extra += (f"\n\n## Reference images\nThe person attached {len(refs)} reference image(s) for the next model. "
                  "Look at them with view_reference.")
    if pending_options:
        extra += f"\n\n## Set on the page for the next model\n{at.dumps(pending_options)}"
    prompt = (f"## The current model\n{current}\n\n## The conversation so far (the last line is new)\n{_history()}"
              f"{extra}\n\nReply to the person's latest message.")
    tools = at.director_tools(start_build, request_changes, status, team_member,
                              [uploads.path(r) for r in refs], stop_build=stop_build,
                              review_again=review_again)
    res = await runner.run_agent("director", prompt, tools, project=state["project"], doing="chatting",
                                 query_fn=query_fn)
    reply = res.text if res.ok and res.text.strip() else FALLBACK.format(error=res.error or "no reply")
    return db.add_message("assistant", reply, state["project"])
