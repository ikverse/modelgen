"""The /v1 API for other programs (api_v1.py) and its jobs (jobs.py), with the pipeline faked: no
Blender and no Claude. One test at the end runs a real job through Blender (marked blender)."""

import asyncio
import json
from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from modelgen import (__version__, budget, config, db, director, jobs, pipeline, runner, server, settings_store,
                      studio_state, updater)
from modelgen.runner import RunResult

HYDRANT = {"object": "fire hydrant", "client": "village", "notes": "old cast iron, red",
           "size": {"h": 80, "unit": "cm"}, "outputs": ["game", "full"]}


@pytest.fixture
def api(monkeypatch):
    monkeypatch.setattr(server.app.state, "run_jobs", False)  # the tests drive jobs.run themselves
    with TestClient(server.app) as c:
        c.headers["X-ModelGen-Key"] = jobs.api_key()
        yield c


@pytest.fixture
def fake_build(monkeypatch):
    """pipeline.start made fake: each build adds the next version with the next score from
    `scores` (the critic asking for a change while it has not passed), with a game file and exports."""
    state = {"scores": [], "calls": [], "error": None}
    monkeypatch.setattr(config, "blender_path", lambda: "blender.exe")

    async def build(project, next_version):
        if state["error"]:
            return {"ok": False, "error": state["error"]}
        n = db.highest_version_number(project) + 1
        score = state["scores"].pop(0)
        passed = score >= 80
        folder = pipeline.version_dir(project, n)
        (folder / "game").mkdir(parents=True)
        (folder / "exports").mkdir()
        (folder / "game" / f"{project}_game.glb").write_bytes(b"glb")
        (folder / "game" / "game.json").write_text(json.dumps({"size": [0.3, 0.3, 0.8]}), encoding="utf-8")
        (folder / "exports" / f"{project}.glb").write_bytes(b"glb")
        (folder / "model.blend").write_bytes(b"blend")
        db.add_version(project, n)
        db.update_version(project, n, status="done", score=score, passed=int(passed),
                          review={"changes": [] if passed else [{"target": "body", "text": "rounder"}]})
        return {"ok": True, "version": n, "score": score, "passed": passed}

    def start(project, request=None, changes=None, on_done=None, next_version=False):
        state["calls"].append({"project": project, "next_version": next_version})
        return build(project, next_version)

    monkeypatch.setattr(pipeline, "start", start)
    return state


def run(job_id):
    asyncio.run(jobs.run(db.get_job(job_id)))
    return jobs.public(db.get_job(job_id))


# --- access ------------------------------------------------------------------------------

def test_health_needs_no_key_but_everything_else_does(api):
    health = api.get("/v1/health", headers={"X-ModelGen-Key": ""}).json()
    assert health["ok"] and health["api"] == "v1" and health["version"] == __version__ and health["queued"] == 0
    assert api.post("/v1/jobs", json=HYDRANT, headers={"X-ModelGen-Key": ""}).status_code == 401
    assert api.post("/v1/jobs", json=HYDRANT, headers={"X-ModelGen-Key": "wrong"}).status_code == 401
    assert api.get("/v1/jobs").status_code == 200
    assert (config.data_dir() / "api_key.txt").read_text(encoding="utf-8") == jobs.api_key()


def test_a_web_page_and_another_computer_are_refused(api, monkeypatch):
    page = api.post("/v1/jobs", json=HYDRANT, headers={"Origin": "https://evil.example"})
    assert page.status_code == 403 and "web pages" in page.json()["detail"]
    assert api.get("/v1/health", headers={"Origin": "http://localhost:3000"}).status_code == 403
    monkeypatch.setattr(server.app.state, "run_jobs", False)
    with TestClient(server.app, client=("192.168.1.20", 5000)) as far:
        assert far.get("/v1/health").status_code == 403
        assert far.get("/v1/jobs", headers={"X-ModelGen-Key": jobs.api_key()}).status_code == 403


# --- new jobs --------------------------------------------------------------------------------

def test_a_job_is_queued_with_its_settings_cleaned(api):
    job = api.post("/v1/jobs", json=HYDRANT).json()
    assert job["status"] == "queued" and job["position"] == 1 and job["id"].startswith("j_")
    req = job["request"]
    assert req["size"] == {"height": 80.0, "unit": "cm"}  # the page's own size format
    assert req["max_versions"] == 2 and req["min_score"] == 70 and req["reuse"] and req["style"] == "realistic"
    assert api.get(f"/v1/jobs/{job['id']}").json()["object"] == "fire hydrant"
    assert [j["id"] for j in api.get("/v1/jobs?client=village&status=queued").json()] == [job["id"]]
    assert api.get("/v1/jobs?client=other").json() == []


@pytest.mark.parametrize("bad, words", [
    ({"client": "village"}, "object is required"),
    ({"object": "lamp"}, "client is required"),
    ({"object": "lamp", "client": "Village Two"}, "client is required"),
    ({"object": "lamp", "client": "v", "size": {"h": 80, "unit": "ft"}}, "size unit"),
    ({"object": "lamp", "client": "v", "size": "80 cm"}, "size must be"),
    ({"object": "lamp", "client": "v", "outputs": ["png"]}, "outputs"),
    ({"object": "lamp", "client": "v", "style": "anime"}, "style"),
    ({"object": "lamp", "client": "v", "max_versions": 9}, "max_versions"),
    ({"object": "lamp", "client": "v", "references": ["not/a/full/path.png"]}, "no image file"),
])
def test_a_bad_request_says_what_is_wrong(api, bad, words):
    r = api.post("/v1/jobs", json=bad)
    assert r.status_code == 400 and words in r.json()["detail"]


def test_a_reference_file_is_stored_as_an_upload(api, tmp_path):
    from PIL import Image
    photo = tmp_path / "hydrant.png"
    Image.new("RGB", (40, 60), (200, 20, 20)).save(photo)
    job = api.post("/v1/jobs", json={**HYDRANT, "references": [str(photo)]}).json()
    stored = db.get_job(job["id"])["request"]["reference_ids"]
    from modelgen import uploads
    assert len(stored) == 1 and uploads.path(stored[0]) is not None
    assert "reference_ids" not in job["request"]  # the upload ids stay inside ModelGen


def test_a_repeated_request_joins_the_job_already_there(api):
    first = api.post("/v1/jobs", json={**HYDRANT, "request_key": "news-0412-hydrant"}).json()
    again = api.post("/v1/jobs", json={**HYDRANT, "request_key": "news-0412-hydrant", "notes": "other"}).json()
    assert again["id"] == first["id"]  # the same key from the same program: the same job
    assert api.post("/v1/jobs", json=HYDRANT).json()["id"] == first["id"]  # identical and still queued
    green = {**HYDRANT, "notes": "green"}
    same = api.post("/v1/jobs", json=green).json()
    assert same["id"] != first["id"] and api.post("/v1/jobs", json=green).json()["id"] == same["id"]
    other = api.post("/v1/jobs", json={**HYDRANT, "client": "racewall"}).json()
    assert other["id"] not in (first["id"], same["id"])
    assert [j["id"] for j in db.queued_jobs()] == [first["id"], same["id"], other["id"]]
    assert api.get(f"/v1/jobs/{other['id']}").json()["position"] == 3


# --- the queue -------------------------------------------------------------------------------

def test_the_queue_is_oldest_first_and_waits_for_the_usage_limit(api):
    a = api.post("/v1/jobs", json=HYDRANT).json()["id"]
    b = api.post("/v1/jobs", json={**HYDRANT, "object": "park bench"}).json()["id"]
    assert jobs.next_job()["id"] == a
    later = (datetime.now() + timedelta(minutes=10)).isoformat(timespec="seconds")
    db.update_job(a, status="waiting_for_usage", retry_at=later)
    assert jobs.next_job() is None  # the whole queue pauses, b too
    assert api.get("/v1/health").json()["paused_for_usage"]
    assert jobs.next_job(datetime.now() + timedelta(minutes=11))["id"] == a
    db.update_job(a, status="done")
    assert jobs.next_job()["id"] == b


def test_a_job_building_at_a_restart_goes_back_in_the_queue(api):
    job = api.post("/v1/jobs", json=HYDRANT).json()["id"]
    db.update_job(job, status="building", project="fire-hydrant")
    jobs.recover()
    after = db.get_job(job)
    assert after["status"] == "queued" and after["project"] == "fire-hydrant" and "restarted" in after["notes"]


def test_an_update_asks_first_while_jobs_wait(api, monkeypatch, tmp_path):
    monkeypatch.setattr(config, "ROOT", tmp_path)
    (tmp_path / updater.MARKER).write_text("{}", encoding="utf-8")
    major, minor, patch = updater.parse_version(__version__)
    tag = f"v{major}.{minor}.{patch + 1}"
    updater.check(fetch=lambda url: {"tag_name": tag, "html_url": "https://example.invalid", "assets": [
        {"name": n, "browser_download_url": f"https://example.invalid/{n}"} for n in (updater.ASSET, updater.SUMS)]})
    api.post("/v1/jobs", json=HYDRANT)
    out = updater.install(launch=lambda p: pytest.fail("must not start yet"))
    assert out == {"action": "confirm", "running": ["village: fire hydrant"]}


# --- building a job, unattended --------------------------------------------------------------

def test_a_job_builds_versions_until_one_passes_then_accepts_the_best(api, fake_build):
    fake_build["scores"] = [62, 86]
    job = api.post("/v1/jobs", json={**HYDRANT, "max_versions": 3}).json()
    out = run(job["id"])
    assert [c["next_version"] for c in fake_build["calls"]] == [False, True]  # v2 passed, so no v3
    project = out["project"]
    assert out["status"] == "done" and out["version"] == 2 and out["score"] == 86 and out["passed"]
    assert out["source"] == "built" and db.get_project(project)["accepted_version"] == 2
    assert db.project_client(project) == "village"
    assert out["main"]["game"] == f"game/{project}_game.glb" and out["main"]["full"] == f"exports/{project}.glb"
    assert f"exports/{project}.glb" in out["files"] and "model.blend" in out["files"]
    game = out["files"][out["main"]["game"]]
    assert game["url"].endswith(f"/files/{project}/v2/game/{project}_game.glb") and game["path"].endswith("_game.glb")
    assert api.get(game["url"].replace(jobs.BASE_URL or "", "")).content == b"glb"


def test_the_version_count_is_kept_and_passed_follows_min_score(api, fake_build):
    fake_build["scores"] = [62]
    job = api.post("/v1/jobs", json={**HYDRANT, "max_versions": 1}).json()
    out = run(job["id"])
    assert len(fake_build["calls"]) == 1 and out["status"] == "done" and out["score"] == 62
    assert out["passed"] is False  # 62 is under the default min_score of 70
    fake_build["scores"] = [62]
    low = api.post("/v1/jobs", json={**HYDRANT, "object": "street lamp", "max_versions": 1, "min_score": 60}).json()
    assert run(low["id"])["passed"] is True


def test_the_usage_limit_pauses_the_job_instead_of_failing_it(api, fake_build):
    fake_build["error"] = "the brief agent did not submit a spec (You've hit your session limit · resets 5pm)"
    job = api.post("/v1/jobs", json=HYDRANT).json()
    out = run(job["id"])
    assert out["status"] == "waiting_for_usage" and out["retry_at"] and "usage limit" in out["step"]
    assert jobs.next_job() is None
    fake_build["error"], fake_build["scores"] = None, [90]  # the limit reset: the same job goes on
    db.update_job(job["id"], retry_at=db.now())
    assert run(job["id"])["status"] == "done"


def test_a_failed_build_fails_the_job_with_the_reason(api, fake_build):
    fake_build["error"] = "the architect did not submit a plan"
    out = run(api.post("/v1/jobs", json=HYDRANT).json()["id"])
    assert out["status"] == "failed" and "architect" in out["error"]


def test_cancelling_a_queued_or_building_job(api, fake_build, monkeypatch):
    queued = api.post("/v1/jobs", json=HYDRANT).json()["id"]
    assert api.post(f"/v1/jobs/{queued}/cancel").json()["status"] == "cancelled"
    assert jobs.next_job() is None
    stopped = []
    building = api.post("/v1/jobs", json={**HYDRANT, "object": "mailbox"}).json()["id"]
    db.update_job(building, status="building", project="mailbox")
    monkeypatch.setattr(pipeline, "is_running", lambda name: True)
    monkeypatch.setattr(pipeline, "stop", lambda name: stopped.append(name) or True)
    assert api.post(f"/v1/jobs/{building}/cancel").json()["status"] == "cancelled" and stopped == ["mailbox"]
    assert api.post("/v1/jobs/j_nope/cancel").status_code == 404


def test_nobody_is_asked_anything_for_another_programs_model(api, monkeypatch):
    proj = db.create_project("fire hydrant", "fire hydrant", options={"api": {"client": "village", "job": "j_1"},
                                                                       "budget_usd": 0.5})
    ask = runner.ask_tool(proj["name"], "builder-1")
    answer = asyncio.run(ask.fn({"question": "Which red?", "options": ["dark", "bright"]}))
    assert answer["answered"] is False and "No person is available" in answer["note"]
    db.start_run("builder", "m", proj["name"])
    db.finish_run(db.runs(proj["name"])[0]["id"], 0.45)
    with pytest.raises(budget.BudgetStop, match="this model's limit"):  # stops at once, no question
        asyncio.run(asyncio.wait_for(budget.ensure(proj["name"], "builder"), 2))
    text = pipeline._options_text(proj["name"])
    assert "village asked for this model" in text and "no brand names" in text
    assert "brand" not in pipeline._options_text(db.create_project("my lamp", "a lamp")["name"])


def test_the_page_and_the_chat_stay_on_the_persons_own_model(api, fake_build):
    mine = db.create_project("oak stool", "an oak stool")["name"]
    fake_build["scores"] = [90]
    out = run(api.post("/v1/jobs", json=HYDRANT).json()["id"])
    assert studio_state.current_project() == mine and director.current_project()["name"] == mine
    listed = {m["project"]: m for m in studio_state.models()}
    assert listed[out["project"]]["client"] == "village" and listed[out["project"]]["meta"].startswith("from village")
    assert listed[mine]["client"] is None
    assert director.current_project(out["project"])["name"] == out["project"]  # opened on purpose, it is used


def test_no_chat_report_is_written_for_another_programs_model(monkeypatch):
    proj = db.create_project("fire hydrant", "x", options={"api": {"client": "village"}})["name"]

    async def fake_run(*a, **kw):
        return {"ok": True, "library": True, "version": 1}

    monkeypatch.setattr(pipeline, "_run", fake_run)
    monkeypatch.setattr(pipeline, "_report_library", lambda *a: pytest.fail("no chat report"))
    asyncio.run(pipeline.run(proj, on_done=lambda *a: pytest.fail("no chat report")))
    assert db.messages() == []


# --- reuse -----------------------------------------------------------------------------------

def made_before(name="fire hydrant", score=88):
    proj = db.create_project(name, f"a {name}")
    db.add_version(proj["name"], 1)
    db.update_version(proj["name"], 1, status="done", score=score, passed=1)
    folder = pipeline.version_dir(proj["name"], 1)
    (folder / "game").mkdir(parents=True)
    (folder / "game" / f"{proj['name']}_game.glb").write_bytes(b"glb")
    db.update_project(proj["name"], accepted_version=1, status="accepted")
    return proj["name"]


def test_a_model_made_before_is_found_and_reused(api, fake_build, monkeypatch):
    old = made_before()
    made_before("garden bench")
    found = api.get("/v1/models?q=red fire hydrant").json()
    assert [m["name"] for m in found["models"]] == [old] and found["models"][0]["source"] == "built"
    assert api.get(f"/v1/models/{old}").json()["main"]["game"] == f"game/{old}_game.glb"
    assert api.get("/v1/models/nothing").status_code == 404
    seen = {}

    async def caster(name, prompt, tools, **kw):
        seen["prompt"] = prompt
        assert name == "caster"
        tools[0].fn({"asset": old})
        return RunResult(agent=name)

    monkeypatch.setattr(runner, "run_agent", caster)
    out = run(api.post("/v1/jobs", json=HYDRANT).json()["id"])
    assert out["status"] == "done" and out["source"] == "existing" and out["project"] == old and out["version"] == 1
    assert fake_build["calls"] == [] and out["cost_usd"] == 0.0 and "garden" not in seen["prompt"]


def test_the_caster_saying_none_means_a_new_build(api, fake_build, monkeypatch):
    made_before()

    async def caster(name, prompt, tools, **kw):
        tools[0].fn({"asset": "none"})
        return RunResult(agent=name)

    monkeypatch.setattr(runner, "run_agent", caster)
    fake_build["scores"] = [90]
    out = run(api.post("/v1/jobs", json=HYDRANT).json()["id"])
    assert out["source"] == "built" and len(fake_build["calls"]) == 1


def test_views_are_made_for_the_accepted_version(api, fake_build, monkeypatch):
    made = []

    async def fake_views(project, number, style, camera):
        out = pipeline.version_dir(project, number) / "views" / f"{style}-{camera}"
        out.mkdir(parents=True)
        for name in ("front.png", "front_shadow.png", "views.json"):
            (out / name).write_bytes(b"x")
        made.append((number, style, camera))

    monkeypatch.setattr(jobs, "render_views", fake_views)
    fake_build["scores"] = [90]
    out = run(api.post("/v1/jobs", json={**HYDRANT, "outputs": ["game", "views"], "style": "flat",
                                         "camera": "ortho"}).json()["id"])
    assert made == [(1, "flat", "ortho")]
    assert out["main"]["views"] == "views/flat-ortho/views.json" and "views/flat-ortho/front.png" in out["files"]
    assert not any(k.startswith("exports/") for k in out["files"])  # "full" was not asked for


# --- the copy-in client ----------------------------------------------------------------------

def test_the_client_reads_the_key_and_reports_a_missing_modelgen(tmp_path):
    import modelgen_client
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "api_key.txt").write_text("k123\n", encoding="utf-8")
    mg = modelgen_client.ModelGen("village", url="http://127.0.0.1:9", modelgen_dir=tmp_path, timeout=2)
    assert mg.key() == "k123"
    assert mg.health()["ok"] is False
    with pytest.raises(modelgen_client.ModelGenError, match="not running"):
        mg.request("fire hydrant")


# --- one real job through Blender ------------------------------------------------------------

@pytest.mark.blender
def test_real_angle_pictures_are_transparent_with_a_views_file(tmp_path):
    """The angle pictures of a real model: a box built in Blender, rendered small and quickly."""
    from PIL import Image
    from modelgen.tools import blender
    blend = tmp_path / "model.blend"
    script = tmp_path / "make.py"
    script.write_text(
        "import bpy\nfrom bpy_lib import helpers as h\n"
        "def build():\n    bpy.ops.mesh.primitive_cube_add(size=0.4, location=(0, 0, 0.2))\n"
        "    return bpy.context.active_object\n", encoding="utf-8")
    made = blender.run_job([blender.part_step("box", script), {"kind": "save", "path": str(blend)}])
    assert made.ok, made.traceback
    out = tmp_path / "views"
    result = blender.run_job([blender.asset_views_step(out, "persp", "flat", ("views", "shadows", "turntable"),
                                                       turntable=2, size=128, samples=8)], open_blend=blend)
    assert result.ok, result.traceback
    info = result.outputs["asset_views"][0]
    assert set(info["views"]) == {"front", "front34", "side", "back34", "back", "top"}
    assert abs(info["size_m"]["height"] - 0.4) < 0.02 and info["views"]["front"]["box_px"][3] <= 128
    with Image.open(out / "front.png") as im:
        assert im.mode == "RGBA" and im.getpixel((1, 1))[3] == 0 and im.getpixel((64, 64))[3] == 255
    assert (out / "front_shadow.png").is_file() and not (out / "top_shadow.png").exists()
    assert (out / "turntable_01.png").is_file()
