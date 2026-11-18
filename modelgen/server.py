"""The local web page: FastAPI with a websocket for chat, live events and the studio snapshot.

The page is the studio in map\\ (served at /map/, and / goes there); /static serves the 3D viewer
(modelgen\\web\\viewer.js). Run with `python -m modelgen.server` (ModelGen.bat does this and adds --open).
"""

import argparse
import asyncio
import io
import json
import re
import shutil
import threading
import webbrowser
import zipfile
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Body, FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles

from . import (api_v1, config, db, director, jobs, pipeline, questions, runner, settings_store, studio_state, talk,
               updater, uploads)
from .tools.files import OutsideProject, ProjectFiles
from .tools.library import Library, clean_name

THREE_DIR = config.WEB_DIR / "vendor" / "node_modules" / "three"
MAP_DIR = config.ROOT / "map"
LIVE_STATES = ("briefing", "planning", "building")
SNAPSHOT_DELAY_S = 0.2
TICK_S = 5

_clients = set()
_tasks = set()
_loop = None
_snap_pending = False


def _send(item):
    for queue in list(_clients):
        queue.put_nowait(item)


def _send_snapshot():
    global _snap_pending
    _snap_pending = False
    if not _clients:
        return
    try:
        snap = studio_state.snapshot()
    except Exception as exc:  # a snapshot bug must never break the event stream
        _send({"type": "error", "text": f"snapshot failed: {exc}"})
        return
    _send({"type": "studio", "snapshot": snap})


def _schedule_snapshot():
    """Send one fresh snapshot shortly after a burst of events, not one per event."""
    global _snap_pending
    if _snap_pending or not _clients or _loop is None:
        return
    _snap_pending = True
    _loop.call_later(SNAPSHOT_DELAY_S, _send_snapshot)


def _broadcast(item):
    """Called by db for every event and message, from any thread."""
    if _loop is None:
        return
    _loop.call_soon_threadsafe(_send, {"type": "event", "item": item})
    _loop.call_soon_threadsafe(_schedule_snapshot)


async def _ticker():
    """Done and failed fade back to free after a while; a regular snapshot shows that."""
    while True:
        await asyncio.sleep(TICK_S)
        _schedule_snapshot()


def _update_changed(state):
    """Called by the updater, from any thread: the page shows the new update state."""
    if _loop is not None:
        _loop.call_soon_threadsafe(_send, {"type": "update", "update": state})


@asynccontextmanager
async def lifespan(app):
    global _loop
    _loop = asyncio.get_running_loop()
    for p in db.list_projects():  # a build cannot survive a restart
        if p["status"] in LIVE_STATES and not pipeline.is_running(p["name"]):
            db.update_project(p["name"], status="interrupted")
    jobs.recover()  # a job from another program that was being built goes back in the queue
    jobs.api_key()  # made now, so a program can read it before its first call
    db.subscribe(_broadcast)
    updater.subscribe(_update_changed)
    ticker = asyncio.create_task(_ticker())
    checker = asyncio.create_task(updater.loop(asyncio.sleep))
    builder = asyncio.create_task(jobs.worker()) if app.state.run_jobs else None
    try:
        yield
    finally:
        ticker.cancel()
        checker.cancel()
        if builder:
            builder.cancel()
        updater.unsubscribe(_update_changed)
        db.unsubscribe(_broadcast)
        _loop = None


class FreshStaticFiles(StaticFiles):
    """Static files the browser may keep but must check before each use (no-cache): an unchanged
    file gets a short 304 by its ETag, a changed one comes down fresh, so an update to the page
    shows on a normal reload without anyone changing the ?v= tags in map\\index.html."""

    def file_response(self, *args, **kwargs):
        response = super().file_response(*args, **kwargs)
        response.headers["Cache-Control"] = "no-cache"
        return response


app = FastAPI(title="ModelGen", lifespan=lifespan)
app.state.run_jobs = True  # the tests turn the job worker off and drive jobs.run themselves
app.include_router(api_v1.router)
app.mount("/static", FreshStaticFiles(directory=config.WEB_DIR), name="static")
app.mount("/map", FreshStaticFiles(directory=MAP_DIR, html=True), name="map")


@app.get("/")
def index(request: Request):
    query = request.url.query
    return RedirectResponse("/map/" + (f"?{query}" if query else ""))


@app.get("/vendor/three/{path:path}")
def three(path):
    full = (THREE_DIR / path).resolve()
    if not full.is_relative_to(THREE_DIR.resolve()) or not full.is_file():
        raise HTTPException(404, "the viewer library is not installed; run Setup-ModelGen.bat")
    return FileResponse(full, media_type="text/javascript" if full.suffix == ".js" else None)


# --- status, page data, chat -----------------------------------------------------------------

def status(project=None):
    exe = config.blender_path()
    return {
        "agents": runner.status_snapshot(),
        "blender": {"found": bool(exe), "path": exe, "install": config.INSTALL_HINT},
        "viewer": THREE_DIR.joinpath("build", "three.module.js").is_file(),
        "running": [p["name"] for p in db.list_projects() if pipeline.is_running(p["name"])],
        "spend": db.spend(project),
    }


@app.get("/api/status")
def api_status(project: str | None = None):
    return status(project)


@app.get("/api/snapshot")
def api_snapshot():
    return studio_state.snapshot()


@app.get("/api/page")
def api_page(project: str | None = None, version: str | None = None):
    data = studio_state.page_data(project, version)
    data["viewer"] = THREE_DIR.joinpath("build", "three.module.js").is_file()
    return data


@app.get("/api/activity")
def api_activity(agent: str | None = None, limit: int = 150):
    return studio_state.activity(agent or None, min(max(limit, 1), 500))


@app.get("/api/messages")
def api_messages(limit: int = 60):
    return db.messages(limit)


def _chat_args(payload):
    text = (payload.get("text") or "").strip()
    attachments = [a for a in (payload.get("attachments") or []) if isinstance(a, str)]
    if not text and not attachments:
        raise HTTPException(400, "empty message")
    options = payload.get("options") or {}
    clean = {}
    size = options.get("size") or {}
    if isinstance(size, dict):
        dims = {}
        for k in ("width", "depth", "height"):
            try:
                if size.get(k) not in (None, ""):
                    dims[k] = round(float(size[k]), 2)
            except (TypeError, ValueError):
                raise HTTPException(400, f"size {k} must be a number")
        if dims:
            clean["size"] = {**dims, "unit": size.get("unit") if size.get("unit") in ("mm", "cm", "m") else "cm"}
    if options.get("budget_usd") not in (None, ""):
        try:
            clean["budget_usd"] = round(float(options["budget_usd"]), 2)
        except (TypeError, ValueError):
            raise HTTPException(400, "budget must be a number")
    return text or "(see the attached image)", attachments, clean


@app.post("/api/chat")
async def api_chat(payload: dict = Body(...)):
    text, attachments, options = _chat_args(payload)
    return await director.chat_turn(text, payload.get("project"), attachments=attachments, options=options)


@app.post("/api/agents/{agent}/chat")
async def api_agent_chat(agent: str, payload: dict = Body(...)):
    text = (payload.get("text") or "").strip()
    if not text:
        raise HTTPException(400, "empty message")
    role = talk.role_of(agent)
    if not role or role == "director":
        raise HTTPException(404, "message the director in the main chat")
    return await talk.chat(agent, text)


@app.post("/api/answer")
async def api_answer(payload: dict = Body(...)):
    """The person answers a question shown on the page: an agent's, the spending limit's, or the
    director's 'accept this version?'."""
    agent, choice = payload.get("agent") or "", str(payload.get("choice") or "").strip()
    if not choice:
        raise HTTPException(400, "give an answer")
    q = questions.answer(agent, choice, payload.get("id"))
    if q:
        return {"ok": True, "question": q}
    if talk.role_of(agent) == "director":
        project = studio_state.current_project()
        waiting = studio_state._accept_question(project)
        if waiting:
            if choice.lower().startswith("accept"):
                _accept(project, waiting["version"])
                return {"ok": True, "action": "accepted", "project": project, "version": waiting["version"]}
            if choice.lower().startswith("build next") and pipeline.open_changes(project):
                try:
                    pipeline.start(project, next_version=True, on_done=director.report)
                except pipeline.Busy:
                    raise HTTPException(409, "the team is still working on this model")
                db.add_message("user", choice, project)
                db.add_event("next_version_requested", project=project, data={"from": waiting["version"]})
                return {"ok": True, "action": "building", "project": project}
            return {"ok": True, "action": "compose", "prefill": "Comments for the next version: "}
    raise HTTPException(404, "nothing is waiting for that answer")


@app.post("/api/uploads")
def api_upload(payload: dict = Body(...)):
    try:
        return uploads.save(payload.get("data") or "")
    except ValueError as exc:
        raise HTTPException(400, str(exc))


@app.get("/uploads/{name}")
def get_upload(name: str):
    p = uploads.path(name)
    if not p:
        raise HTTPException(404, "no such upload")
    return FileResponse(p)


@app.get("/library/previews/{name}.jpg")
def library_preview(name: str):
    try:
        p = Library().preview(clean_name(name))
    except ValueError:
        p = None
    if not p:
        raise HTTPException(404, "no picture for that part")
    return FileResponse(p, media_type="image/jpeg")


@app.get("/api/settings")
def api_settings():
    return studio_state.settings()


@app.post("/api/settings")
def api_save_settings(payload: dict = Body(...)):
    try:
        settings_store.save(payload)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    out = studio_state.settings()
    if payload.get("blender_path") and not out["blender_found"]:
        out["warning"] = "No Blender found at that path; ModelGen will look for it automatically instead."
    return out


# --- updates ------------------------------------------------------------------------------

@app.get("/api/update")
def api_update():
    return updater.public_state()


@app.post("/api/update/check")
async def api_update_check():
    return await asyncio.to_thread(updater.check)


@app.post("/api/update/install")
async def api_update_install(payload: dict = Body(default={})):
    """Install the newer version. With a build running, the answer asks the page to confirm first
    ({"action": "confirm"}); force=true then stops the build and goes ahead."""
    force = bool((payload or {}).get("force"))
    try:
        if not force:
            return await asyncio.to_thread(updater.install, False)
        if updater.installed():
            for name in updater.running_builds():
                pipeline.stop(name)  # what the team built so far is kept
        return await asyncio.to_thread(updater.install, True)
    except updater.UpdateError as exc:
        raise HTTPException(409, str(exc))


# --- projects and versions -----------------------------------------------------------------

def _url(project, folder, path):
    rel = Path(path).relative_to(folder.parent).as_posix()
    return f"/files/{project}/{rel}"


def version_info(project, v):
    folder = pipeline.version_dir(project, v["number"])
    renders = folder / "renders"
    exports = folder / "exports"
    info = dict(v)
    info["renders"] = [_url(project, folder, p) for p in sorted(renders.glob("*.png"))] if renders.is_dir() else []
    info["sheets"] = {p.stem.replace("sheet_", ""): _url(project, folder, p)
                      for p in sorted(renders.glob("sheet_*.jpg"))} if renders.is_dir() else {}
    info["downloads"] = {p.suffix[1:]: _url(project, folder, p) for p in sorted(exports.iterdir())
                         if p.suffix in (".glb", ".fbx", ".obj", ".stl")} if exports.is_dir() else {}
    if (folder / "model.blend").is_file():
        info["downloads"]["blend"] = _url(project, folder, folder / "model.blend")
    info["glb"] = info["downloads"].get("glb")
    return info


@app.get("/api/projects")
def api_projects():
    return db.list_projects()


@app.get("/api/projects/{name}")
def api_project(name: str):
    proj = db.get_project(name)
    if not proj:
        raise HTTPException(404, "no such project")
    proj["running"] = pipeline.is_running(name)
    proj["versions"] = [version_info(name, v) for v in db.versions(name)]
    proj["spend"] = db.spend(name)
    return proj


@app.get("/api/projects/{name}/events")
def api_events(name: str, after: int = 0):
    return db.events(name, after_id=after)


def _version_arg(name, payload):
    try:
        number = int(str(payload.get("version")).lstrip("v"))
    except (TypeError, ValueError):
        raise HTTPException(400, "give a version number")
    if not db.get_version(name, number):
        raise HTTPException(404, "no such version")
    return number


def _accept(name, number):
    db.update_project(name, accepted_version=number, status="accepted")
    db.add_event("accepted", project=name, data={"version": number})


@app.post("/api/projects/{name}/accept")
def api_accept(name: str, payload: dict = Body(...)):
    number = _version_arg(name, payload)
    _accept(name, number)
    return {"ok": True, "accepted_version": number}


@app.post("/api/projects/{name}/stop")
def api_stop(name: str):
    """Stop the running build of this model now. What it built so far is kept."""
    if not db.get_project(name):
        raise HTTPException(404, "no such project")
    if not pipeline.stop(name):
        raise HTTPException(409, "nothing is being built for this model")
    return {"ok": True, "project": name}


RETRYABLE = ("failed", "stopped", "interrupted")


@app.post("/api/projects/{name}/retry")
async def api_retry(name: str):
    """Run the model's last build again, the same way: a model with no versions starts again from its
    request, a change round again with the same change text. Only after a build that failed, was
    stopped, or was cut off by a restart."""
    proj = db.get_project(name)
    if not proj:
        raise HTTPException(404, "no such project")
    if pipeline.is_running(name):
        raise HTTPException(409, "the team is still working on this model")
    if proj["status"] not in RETRYABLE:
        raise HTTPException(409, "the last build of this model did not fail")
    last = db.last_event("build_started", project=name) or {"data": {}}
    kind, text = last["data"].get("kind"), last["data"].get("text") or ""
    if kind == "changes" and text:
        pipeline.start(name, changes=text, on_done=director.report)
    elif kind == "next" and db.versions(name):
        pipeline.start(name, next_version=True, on_done=director.report)
    else:
        pipeline.start(name, on_done=director.report)
    db.add_event("retry_requested", project=name, data={"kind": kind or "new"})
    return {"ok": True, "project": name}


@app.post("/api/projects/{name}/delete")
def api_delete_project(name: str):
    """Delete a model for good: its row, its versions and its folder. What it cost and the activity
    log stay, so the day's spend stays right. Refused while it is being built."""
    if not db.get_project(name):
        raise HTTPException(404, "no such project")
    if pipeline.is_running(name):
        raise HTTPException(409, "the team is still working on this model; stop the build first")
    folder = config.projects_dir() / name
    db.delete_project(name)
    if folder.is_dir():
        shutil.rmtree(folder, ignore_errors=True)
    db.add_event("project_deleted", project=name)
    return {"ok": True, "deleted": name}


@app.post("/api/projects/{name}/revert")
def api_revert(name: str, payload: dict = Body(...)):
    """Make an earlier version the one the next change starts from."""
    number = _version_arg(name, payload)
    if pipeline.is_running(name):
        raise HTTPException(409, "the team is still working on this model")
    db.update_project(name, base_version=number)
    db.add_event("reverted", project=name, data={"version": number})
    return {"ok": True, "base_version": number}


@app.post("/api/projects/{name}/versions/{number}/delete")
def api_delete_version(name: str, number: int):
    """Delete one version for good: its row and its folder. What it cost and the activity log stay.
    If it was the accepted version or the one the next version starts from, that falls back to the
    best version left. Refused while the model is being built."""
    proj = db.get_project(name)
    if not proj or not db.get_version(name, number):
        raise HTTPException(404, "no such version")
    if pipeline.is_running(name):
        raise HTTPException(409, "the team is still working on this model; stop the build first")
    folder = pipeline.version_dir(name, number)
    db.delete_version(name, number)
    if folder.is_dir():
        shutil.rmtree(folder, ignore_errors=True)
    fields = {}
    if proj.get("base_version") == number:
        fields["base_version"] = None
    if proj.get("accepted_version") == number:
        fields["accepted_version"] = None
        if proj["status"] == "accepted":
            fields["status"] = "needs_changes"
    if fields:
        db.update_project(name, **fields)
    if "base_version" in fields:  # the best version left is where the next one starts
        left = pipeline.base_number(name)
        if left:
            db.update_project(name, base_version=left)
    db.add_event("version_deleted", project=name, data={"version": number})
    return {"ok": True, "deleted": number, "base_version": db.get_project(name).get("base_version")}


@app.get("/api/projects/{name}/versions/{number}/export.zip")
def api_export(name: str, number: int):
    """One version packed for download: every export, the .blend and the renders."""
    if not db.get_version(name, number):
        raise HTTPException(404, "no such version")
    folder = pipeline.version_dir(name, number)
    files = []
    for sub in ("exports", "game", "renders"):
        if (folder / sub).is_dir():
            files += [p for p in sorted((folder / sub).iterdir()) if p.is_file()]
    if (folder / "model.blend").is_file():
        files.append(folder / "model.blend")
    if not files:
        raise HTTPException(404, "this version has nothing to export yet")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for p in files:
            zf.write(p, p.relative_to(folder).as_posix())
    return Response(buf.getvalue(), media_type="application/zip",
                    headers={"Content-Disposition": f'attachment; filename="{name}-v{number}.zip"'})


HEX_COLOUR = re.compile(r"^#[0-9a-fA-F]{6}$")


def _colours_file(name, number):
    if not db.get_version(name, number):
        raise HTTPException(404, "no such version")
    return pipeline.version_dir(name, number) / "colors.json"


@app.get("/api/projects/{name}/versions/{number}/colors")
def api_colours(name: str, number: int):
    """The colours the person gave this version's parts in the viewer (by material group, and by
    single piece); empty when none were changed."""
    path = _colours_file(name, number)
    try:
        data = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
    except (OSError, ValueError):
        data = {}
    return {"groups": data.get("groups") or {}, "pieces": data.get("pieces") or {}}


@app.post("/api/projects/{name}/versions/{number}/colors")
def api_save_colours(name: str, number: int, payload: dict = Body(...)):
    path = _colours_file(name, number)
    clean = {}
    for kind in ("groups", "pieces"):
        given = payload.get(kind) or {}
        if not isinstance(given, dict):
            raise HTTPException(400, f"{kind} must be an object")
        clean[kind] = {str(k)[:200]: v.lower() for k, v in list(given.items())[:2000]
                       if isinstance(v, str) and HEX_COLOUR.match(v)}
    if not path.parent.is_dir():
        raise HTTPException(404, "this version has no folder")
    if clean["groups"] or clean["pieces"]:
        path.write_text(json.dumps(clean, indent=1), encoding="utf-8")
    elif path.is_file():
        path.unlink()
    return {"ok": True, **clean}


@app.get("/files/{name}/{path:path}")
def files(name: str, path: str, request: Request):
    if not db.get_project(name):
        raise HTTPException(404, "no such project")
    try:
        full = ProjectFiles(config.projects_dir() / name).path(path)
    except OutsideProject:
        raise HTTPException(403, "outside the project")
    if not full.is_file():
        raise HTTPException(404, "no such file")
    media = {".glb": "model/gltf-binary"}.get(full.suffix)
    download = full.suffix in (".fbx", ".obj", ".stl", ".blend")
    response = FileResponse(full, media_type=media, filename=f"{name}{full.suffix}" if download else None,
                            stat_result=full.stat())
    response.headers["Cache-Control"] = "no-cache"   # kept, but checked by its ETag before each use
    # the browser's saved copy is still this file: say so instead of sending it again
    etag = response.headers.get("etag")
    asked = [t.strip().removeprefix("W/") for t in request.headers.get("if-none-match", "").split(",")]
    if etag and etag in asked:
        return Response(status_code=304, headers={"ETag": etag, "Cache-Control": "no-cache"})
    return response


# --- websocket ----------------------------------------------------------------------------

@app.websocket("/ws")
async def ws(socket: WebSocket):
    await socket.accept()
    queue = asyncio.Queue()
    _clients.add(queue)

    async def sender():
        while True:
            await socket.send_json(await queue.get())

    send_task = asyncio.create_task(sender())
    try:
        await socket.send_json({"type": "hello", "status": status()})
        await socket.send_json({"type": "studio", "snapshot": studio_state.snapshot()})
        while True:
            msg = await socket.receive_json()
            if msg.get("type") == "chat":
                try:
                    text, attachments, options = _chat_args(msg)
                except HTTPException as exc:
                    await queue.put({"type": "error", "text": exc.detail})
                    continue
                task = asyncio.create_task(director.chat_turn(text, msg.get("project"), attachments=attachments,
                                                              options=options))
                _tasks.add(task)  # keep a reference until it finishes
                task.add_done_callback(_tasks.discard)
            elif msg.get("type") == "status":
                await queue.put({"type": "status", "status": status(msg.get("project"))})
            elif msg.get("type") == "snapshot":
                await queue.put({"type": "studio", "snapshot": studio_state.snapshot()})
    except WebSocketDisconnect:
        pass
    finally:
        _clients.discard(queue)
        send_task.cancel()


def main():
    parser = argparse.ArgumentParser(description="Start the ModelGen page")
    parser.add_argument("--open", action="store_true", help="open the page in the browser")
    parser.add_argument("--port", type=int, default=int(config.get("server", "port", 8770)))
    args = parser.parse_args()
    host = config.get("server", "host", "127.0.0.1")
    url = f"http://{host}:{args.port}/"
    jobs.BASE_URL = url.rstrip("/")  # the /v1 API's file links are absolute
    print(f"ModelGen is running at {url}  (close this window to stop it)")
    if args.open:
        threading.Timer(1.5, webbrowser.open, [url]).start()
    import uvicorn
    uvicorn.run(app, host=host, port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
