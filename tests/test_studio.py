"""The studio link: seats, the snapshot, questions, spending limits, direct chat, uploads, settings,
page data and the new endpoints. Every Claude call is a fake; nothing is spent."""

import asyncio
import base64
import io
import json
import zipfile
from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from modelgen import budget, config, db, director, pipeline, questions, runner, server, settings_store, studio_state, talk
from modelgen.messages import AssistantMessage, ResultMessage, TextBlock
from modelgen.runner import AgentTool


@pytest.fixture(autouse=True)
def clean_state():
    runner.STATUS.clear()
    questions.OPEN.clear()
    yield
    runner.STATUS.clear()
    questions.OPEN.clear()


def result(cost=0.01, text="ok"):
    return ResultMessage(subtype="success", duration_ms=1, duration_api_ms=1, is_error=False, num_turns=1,
                         session_id="s", total_cost_usd=cost, result=text)


def fake(text="ok", cost=0.01, captured=None, wait=None):
    async def query(*, prompt, options):
        if captured is not None:
            captured.append({"prompt": prompt, "options": options})
        if wait:
            await wait()
        yield AssistantMessage(content=[TextBlock(text=text)], model="m")
        yield result(cost, text)
    return query


def png_data_url():
    buf = io.BytesIO()
    Image.new("RGB", (8, 6), (30, 120, 200)).save(buf, "PNG")
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()


def scored_project(title="oak stool", score=84.0, passed=True, status="done"):
    proj = db.create_project(title, "a stool")
    name = proj["name"]
    db.add_version(name, 1)
    db.update_version(name, 1, status="done", score=score, passed=int(passed),
                      review={"score": score, "summary": "solid", "scores": {"silhouette": 9, "parts": 8},
                              "changes": [{"target": "seat", "parameter": "thickness", "change": "make it 30 mm"}]},
                      checks={"passed": True, "gates": {"watertight": {"pass": True, "detail": "closed"}}, "warnings": []})
    db.update_project(name, status=status, base_version=1)
    folder = pipeline.version_dir(name, 1)
    (folder / "exports").mkdir(parents=True)
    (folder / "exports" / f"{name}.glb").write_bytes(b"glTF" + b"\0" * 32)
    (folder / "exports" / f"{name}.stl").write_bytes(b"solid x")
    (folder / "renders").mkdir()
    Image.new("RGB", (4, 4)).save(folder / "renders" / "turntable_00.png")
    (folder / "model.blend").write_bytes(b"BLENDER")
    return name


# --- seats and live status -------------------------------------------------------------------

def test_parallel_builders_take_their_own_seats_and_free_them():
    gate = asyncio.Event()

    async def main():
        async def hold():
            await gate.wait()
        tasks = [asyncio.create_task(runner.run_agent("builder", "x", [], project="p", doing=f"part {n}",
                                                      query_fn=fake(wait=hold))) for n in ("leg", "seat")]
        await asyncio.sleep(0.05)
        working = {k: v["state"] for k, v in runner.STATUS.items()}
        gate.set()
        await asyncio.gather(*tasks)
        return working

    working = asyncio.run(main())
    assert working == {"builder-1": "working", "builder-2": "working"}
    assert {r["seat"] for r in db.runs()} == {"builder-1", "builder-2"}
    assert all(runner.STATUS[s]["state"] == "idle" for s in ("builder-1", "builder-2"))


def test_a_step_run_shows_done_then_free_and_a_failed_run_shows_failed(monkeypatch):
    asyncio.run(runner.run_agent("critic", "x", [], project="p", doing="reviewing v1", step=True, query_fn=fake()))
    assert runner.STATUS["critic"]["state"] == "done"
    snap = {a["id"]: a for a in studio_state.snapshot()["agents"]}
    assert snap["critic"]["state"] == "done"
    runner.STATUS["critic"]["since"] = (datetime.now() - timedelta(seconds=120)).isoformat(timespec="seconds")
    assert {a["id"]: a for a in studio_state.snapshot()["agents"]}["critic"]["state"] == "idle"

    async def broken(*, prompt, options):
        raise RuntimeError("boom")
        yield  # pragma: no cover
    asyncio.run(runner.run_agent("assembler", "x", [], project="p", doing="placing", step=True, query_fn=broken))
    a = {a["id"]: a for a in studio_state.snapshot()["agents"]}["assembler"]
    assert a["state"] == "failed" and "boom" in a["doing"]


def test_tool_calls_are_tracked_with_their_result_and_map_spot():
    agent = runner.load_agent("builder")
    seat = runner.take_seat(agent)
    runner._set_status(seat, agent, "p", add="part leg")
    tools = [AgentTool("write_part", "", {"type": "object", "properties": {}}, lambda a: {"ok": False, "error": "bad"}),
             AgentTool("search_library", "", {"type": "object", "properties": {"query": {"type": "string"}}},
                       lambda a: {"results": [1, 2]})]
    write, search = runner.make_tools(agent, tools, "p", seat, "part leg")
    asyncio.run(search.handler({"query": "turned leg"}))
    asyncio.run(write.handler({}))
    calls = runner.STATUS[seat]["calls"]
    assert [c["tool"] for c in calls] == ["search_library", "write_part"]
    assert calls[0]["ok"] is True and "turned leg" in calls[0]["text"] and "2 found" in calls[0]["text"]
    assert calls[1]["ok"] is False and "bad" in calls[1]["text"]
    a = {a["id"]: a for a in studio_state.snapshot()["agents"]}[seat]
    assert a["state"] == "working" and a["tool"] == "write_part" and a["spot"] == "run_blender"
    assert studio_state.spot("critic", "render") == "render_views" and studio_state.spot("director", "x") == "check_queue"
    done = db.events_of(["tool_done"])
    assert done[-1]["data"]["ok"] is False and done[-1]["agent"] == seat


def test_acting_shows_a_pipeline_step_on_an_agent():
    with runner.acting("critic", "p", "rendering v2", "render") as act:
        a = {a["id"]: a for a in studio_state.snapshot()["agents"]}["critic"]
        assert a["state"] == "working" and a["spot"] == "render_views"
        act.ok = False
    assert runner.STATUS["critic"]["state"] == "failed"


# --- questions -------------------------------------------------------------------------------

def test_an_agent_question_waits_for_the_answer_and_shows_as_waiting():
    db.create_project("p", "a test model")

    async def main():
        task = asyncio.create_task(questions.ask("p", "builder-2", "Rails 40 or 45 mm?", ["40 mm", "45 mm"], timeout=5))
        await asyncio.sleep(0.01)
        runner._entry("builder-2", runner.load_agent("builder"))["project"] = "p"
        snap = studio_state.snapshot()
        assert questions.answer("builder-2", "45 mm")["text"] == "Rails 40 or 45 mm?"
        return snap, await task

    snap, answer = asyncio.run(main())
    assert answer == "45 mm"
    a = {a["id"]: a for a in snap["agents"]}["builder-2"]
    assert a["state"] == "waiting" and a["question"]["options"] == ["40 mm", "45 mm"]
    assert not questions.OPEN
    kinds = [e["kind"] for e in db.events_of(["question", "answered"])]
    assert kinds == ["question", "answered"]


def test_an_unanswered_question_times_out_and_the_agent_decides():
    assert asyncio.run(questions.ask("p", "brief", "Which style?", ["modern"], timeout=0.05)) is None
    assert db.events_of(["question_timeout"])
    tool = runner.ask_tool("p", "brief")
    reply = asyncio.run(asyncio.wait_for(_answer_soon(tool), 3))
    assert reply == {"answered": True, "answer": "modern"}


async def _answer_soon(tool):
    task = asyncio.create_task(tool.fn({"question": "Which style?", "options": ["modern", "classic"]}))
    await asyncio.sleep(0.01)
    questions.answer("brief", "modern")
    return await task


def test_only_step_runs_of_asking_agents_get_ask_human():
    seen = []
    asyncio.run(runner.run_agent("builder", "x", [], project="p", step=True, query_fn=fake(captured=seen)))
    asyncio.run(runner.run_agent("critic", "x", [], project="p", step=True, query_fn=fake(captured=seen)))
    asyncio.run(runner.run_agent("builder", "x", [], project="p", query_fn=fake(captured=seen)))
    tools = [[t.name for t in c["options"].tools] for c in seen]
    assert "ask_human" in tools[0]
    assert "ask_human" not in tools[1] and "ask_human" not in tools[2]


# --- spending limits ---------------------------------------------------------------------------

def test_a_run_that_could_pass_the_limit_waits_for_the_person(monkeypatch):
    settings_store.save({"daily_usd": 0.1, "per_model_usd": 100})

    async def main(choice):
        task = asyncio.create_task(runner.run_agent("brief", "x", [], project="p", step=True, query_fn=fake()))
        for _ in range(100):
            await asyncio.sleep(0.01)
            if questions.OPEN:
                break
        q = next(iter(questions.OPEN.values()))
        assert q["agent"] == "director" and q["kind"] == "budget" and q["options"] == [budget.RAISE, budget.STOP]
        questions.answer("director", choice)
        return await task

    res = asyncio.run(main(budget.RAISE))
    assert res.ok and settings_store.load()["daily_usd"] > 0.1
    assert db.events_of(["limit_raised"])
    settings_store.save({"daily_usd": 0.1})
    with pytest.raises(budget.BudgetStop):
        asyncio.run(main(budget.STOP))


def test_a_model_budget_replaces_the_per_model_limit():
    proj = db.create_project("cup", "a cup", options={"budget_usd": 0.1})
    assert budget.limits(proj["name"])["per_model_usd"] == 0.1
    assert budget.check(proj["name"], "brief")[0] == "model"


def test_a_stopped_build_is_recorded_as_stopped(monkeypatch):
    proj = db.create_project("cup", "a cup")

    async def stop(*a, **k):
        raise budget.BudgetStop("stopped at today's limit")
    monkeypatch.setattr(pipeline, "_run", stop)
    out = asyncio.run(pipeline.run(proj["name"], request="a cup"))
    assert out["error"] == "stopped" and db.get_project(proj["name"])["status"] == "stopped"


def test_stopping_a_running_build_ends_it_and_records_it(monkeypatch):
    name = db.create_project("cup", "a cup")["name"]
    db.add_version(name, 1)
    reported = []

    async def slow(*a, **k):
        db.update_version(name, 1, status="building")
        await asyncio.sleep(60)
    monkeypatch.setattr(pipeline, "_run", slow)

    async def on_done(project, outcome):
        reported.append(outcome)

    async def main():
        task = pipeline.start(name, request="a cup", on_done=on_done)
        await asyncio.sleep(0.05)
        assert pipeline.stop(name)
        return await asyncio.wait_for(task, 5)

    out = asyncio.run(main())
    assert out == {"ok": False, "error": "cancelled"} and reported == [out]
    assert db.get_project(name)["status"] == "stopped"
    v = db.get_version(name, 1)
    assert v["status"] == "failed" and v["note"] == "stopped"
    assert db.events_of(["stop_requested"], project=name) and db.events_of(["finished"], project=name)
    assert not pipeline.is_running(name) and pipeline.stop(name) is False


def test_a_stopped_agent_run_is_recorded_as_stopped():
    async def main():
        started = asyncio.Event()

        async def wait():
            started.set()
            await asyncio.sleep(60)
        task = asyncio.create_task(runner.run_agent("builder", "build it", query_fn=fake(wait=wait)))
        await started.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(main())
    run = db.runs()[0]
    assert run["ok"] == 0 and run["error"] == "stopped" and run["ended"]


def test_the_director_can_stop_the_build(monkeypatch):
    name = db.create_project("cup", "a cup")["name"]
    stopped = []
    monkeypatch.setattr(pipeline, "stop", lambda project: stopped.append(project) or True)
    given = {}
    real = director.at.director_tools

    def keep(*a, **k):
        given.update(k)
        return real(*a, **k)
    monkeypatch.setattr(director.at, "director_tools", keep)
    captured = []
    asyncio.run(director.chat_turn("stop", name, query_fn=fake("Stopped.", captured=captured)))
    assert "stop_build" in [t.name for t in captured[0]["options"].tools]
    assert given["stop_build"]({})["ok"] and stopped == [name]
    monkeypatch.setattr(pipeline, "stop", lambda project: False)
    assert given["stop_build"]({})["ok"] is False


def test_the_stop_endpoint(client, monkeypatch):
    name = db.create_project("cup", "a cup")["name"]
    assert client.post("/api/projects/nope/stop").status_code == 404
    assert client.post(f"/api/projects/{name}/stop").status_code == 409
    monkeypatch.setattr(pipeline, "stop", lambda project: True)
    assert client.post(f"/api/projects/{name}/stop").json() == {"ok": True, "project": name}


# --- the run panel, hand-offs and meetings -------------------------------------------------------

def test_run_steps_hand_offs_and_meetings_follow_the_pipeline_events():
    proj = db.create_project("lamp", "a lamp")
    name = proj["name"]
    db.add_event("build_started", project=name, data={"kind": "new", "text": "a lamp"})
    pipeline._meeting(name, "kickoff", ["director", "brief", "architect"])
    pipeline._handoff(name, "director", "brief", "your request")
    pipeline._phase(name, "Writing the spec", step="spec")
    pipeline._phase(name, "Planning", step="plan", object="desk lamp")
    db.add_event("version_started", project=name, data={"version": 1, "round": 1})
    pipeline._phase(name, "Builders", step="build", version=1, parts=["arm", "base"])
    snap = studio_state.snapshot()
    assert snap["project"] == name
    assert snap["handoffs"][-1] == {"id": snap["handoffs"][-1]["id"], "from": "director", "to": "brief", "label": "your request"}
    assert snap["meetings"][0]["kind"] == "kickoff"
    run = snap["runs"][0]
    states = {s["label"]: s.get("state") for s in run["steps"]}
    assert states == {"Spec": "done", "Plan": "done", "Build": "active", "Assemble": None, "Materials": None,
                      "Render": None, "Review": None, "Library": None}
    assert run["title"] == "lamp v1" and run["round"].startswith("Round 1 of")
    build = next(s for s in run["steps"] if s["label"] == "Build")
    assert build["note"] == "2 parts" and build["agent"] == ["builder-1"]
    assert next(s for s in run["steps"] if s["label"] == "Plan")["note"] == "desk lamp"


def _past_runs(projects, step_s=60):
    """Write finished runs whose steps each took step_s seconds, a day apart."""
    def ev(kind, p, data, stamp):
        item = db.add_event(kind, project=p, data=data)
        db._exec("UPDATE events SET created = ? WHERE id = ?", (stamp, item["id"]))
    for i, p in enumerate(projects):
        db.create_project(p, p)
        t0 = datetime(2026, 1, 1 + i, 10, 0, 0)
        stamp = lambda k: (t0 + timedelta(seconds=k * step_s)).isoformat(timespec="seconds")
        ev("build_started", p, {"kind": "new"}, stamp(0))
        keys = ["spec", "plan", "build", "assemble", "materials", "render", "review", "library"]
        for j, key in enumerate(keys):
            ev("phase", p, {"step": key}, stamp(j))
        ev("finished", p, {"ok": True}, stamp(len(keys)))


def test_step_medians_need_two_timed_runs():
    studio_state._STEP_TIMES["at"] = 0
    _past_runs(["one"])
    assert studio_state.step_medians() == {}
    studio_state._STEP_TIMES["at"] = 0
    _past_runs(["two"])
    medians = studio_state.step_medians()
    assert medians["spec"] == 60 and medians["library"] == 60 and "motion" not in medians


def test_a_run_on_plan_says_its_step_percent_and_time_left():
    studio_state._STEP_TIMES["at"] = 0
    _past_runs(["one", "two"])
    name = db.create_project("yellow school bus", "a school bus")["name"]
    db.add_event("build_started", project=name, data={"kind": "new"})
    pipeline._phase(name, "Writing the spec", step="spec")
    pipeline._phase(name, "Planning", step="plan")
    run = studio_state.snapshot()["runs"][0]
    assert run["current"] == "Plan" and run["model"] == "yellow school bus" and run["project"] == name
    assert run["percent"] == 12 and 400 <= run["eta_s"] <= 420


def test_a_finished_run_has_no_time_left():
    studio_state._STEP_TIMES["at"] = 0
    name = db.create_project("lamp", "a lamp")["name"]
    db.add_event("build_started", project=name, data={"kind": "new"})
    pipeline._phase(name, "Writing the spec", step="spec")
    db.add_event("finished", project=name, data={"ok": False, "error": "cancelled"})
    run = studio_state.snapshot()["runs"][0]
    assert run["eta_s"] is None and run["busy"] is False
    other = db.create_project("mug", "a mug")["name"]
    db.add_event("build_started", project=other, data={"kind": "new"})
    pipeline._phase(other, "Reviewing", step="review")
    db.add_event("finished", project=other, data={"ok": True})
    run = studio_state._run(other, db.last_event("build_started", other), [])
    assert run["percent"] == 100 and run["current"] is None and run["eta_s"] == 0


def test_a_version_without_a_score_shows_not_scored():
    name = db.create_project("red car", "a red car")["name"]
    db.add_event("build_started", project=name, data={"kind": "new"})
    pipeline._phase(name, "Rendering", step="render", version=1)
    db.add_event("version_done", project=name, data={"version": 1, "passed": True, "summary": "a car"})
    db.add_event("finished", project=name, data={"ok": True, "score": None})
    run = studio_state._run(name, db.last_event("build_started", name), [])
    notes = [s.get("note") for s in run["steps"]]
    assert "v1: not scored · passed" in notes, notes


def test_the_step_a_run_failed_on_says_why():
    name = db.create_project("airpods", "AirPods")["name"]
    db.add_event("build_started", project=name, data={"kind": "new", "text": "AirPods"})
    pipeline._phase(name, "Writing the spec", step="spec")
    pipeline._phase(name, "Planning", step="plan", object="AirPods in an open case")
    db.add_event("finished", project=name, data={"ok": False, "error": "PipelineError: the architect did not submit "
                                                 "a plan (Claude session limit reached, resets 1:40am)"})
    steps = {s["label"]: s for s in studio_state.snapshot()["runs"][0]["steps"]}
    assert steps["Plan"]["state"] == "failed"
    assert steps["Plan"]["note"] == "the architect did not submit a plan (Claude session limit reached, resets 1:40am)"
    assert steps["Spec"]["state"] == "done" and "note" not in steps["Spec"]
    assert studio_state._failure_reason("cancelled") == "stopped by you"
    assert studio_state._failure_reason("x" * 300).endswith("…") and len(studio_state._failure_reason("x" * 300)) == 160


# --- the director's accept question and the answer endpoint --------------------------------------

@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(runner, "query", fake("Annie gave it 8.4 because the seat is thin."))
    with TestClient(server.app) as c:
        yield c


def test_the_director_waits_for_accept_and_the_answer_accepts(client):
    name = scored_project(status="done")
    d = {a["id"]: a for a in studio_state.snapshot()["agents"]}["director"]
    assert d["state"] == "waiting"
    assert d["question"]["options"] == ["Accept v1", "Build next version", "Add my comments"]
    r = client.post("/api/answer", json={"agent": "director", "choice": "Add my comments"}).json()
    assert r["action"] == "compose" and r["prefill"].startswith("Comments")
    r = client.post("/api/answer", json={"agent": "director", "choice": "Accept v1"}).json()
    assert r["action"] == "accepted" and db.get_project(name)["accepted_version"] == 1
    assert {a["id"]: a for a in studio_state.snapshot()["agents"]}["director"]["state"] == "idle"
    assert client.post("/api/answer", json={"agent": "director", "choice": "Accept v1"}).status_code == 404


def test_build_next_version_starts_only_when_the_person_chooses_it(client, monkeypatch):
    name = scored_project(status="needs_changes", score=55.0, passed=False)
    started = []
    monkeypatch.setattr(pipeline, "start", lambda project, **kw: started.append((project, kw)))
    d = {a["id"]: a for a in studio_state.snapshot()["agents"]}["director"]
    assert "Build next version" in d["question"]["options"] and not started  # it waits; nothing starts on its own
    r = client.post("/api/answer", json={"agent": "director", "choice": "Build next version"}).json()
    assert r["action"] == "building" and started[0][0] == name and started[0][1]["next_version"] is True
    assert db.events_of(["next_version_requested"], project=name)


def test_without_open_changes_the_question_offers_comments_only(client, monkeypatch):
    name = scored_project(status="needs_changes", score=55.0, passed=False)
    v = db.get_version(name, 1)
    db.update_version(name, 1, review={**v["review"], "changes": []})
    started = []
    monkeypatch.setattr(pipeline, "start", lambda project, **kw: started.append(project))
    d = {a["id"]: a for a in studio_state.snapshot()["agents"]}["director"]
    assert d["question"]["options"] == ["Accept v1", "Add my comments"]
    r = client.post("/api/answer", json={"agent": "director", "choice": "Build next version"}).json()
    assert r["action"] == "compose" and not started


def test_the_next_version_starts_from_the_best_one_and_carries_the_critic_changes(monkeypatch):
    name = scored_project(status="needs_changes", score=55.0, passed=False)
    db.add_version(name, 2)
    db.update_version(name, 2, status="done", score=48.0, passed=0,
                      review={"score": 48.0, "summary": "worse", "scores": {}, "changes": []},
                      checks={"passed": True, "gates": {"watertight": {"pass": True, "detail": ""}}, "warnings": []})
    db.update_project(name, base_version=None)
    assert pipeline.base_number(name) == 1  # the best, not the newest
    db.update_project(name, base_version=2)
    assert pipeline.base_number(name) == 2  # the one the person picked with "Build changes from"
    notes, review = pipeline.critic_notes_of(name, 1, ["seat", "leg"])
    assert notes == {"seat": ["thickness: make it 30 mm"]} and review["score"] == 55.0
    assert pipeline.critic_notes_of(name, 1, ["leg"])[0] == {}  # a part no longer in the plan gets nothing
    assert pipeline.open_changes(name) == 0
    db.update_project(name, base_version=1)
    assert pipeline.open_changes(name) == 1


def test_the_page_shows_what_the_next_version_will_change(client, monkeypatch):
    name = scored_project(status="needs_changes", score=55.0, passed=False)
    v = db.get_version(name, 1)
    gates = {**v["checks"]["gates"], "on_floor": {"pass": False, "detail": "4 mm above the floor"}}
    db.update_version(name, 1, checks={**v["checks"], "gates": gates})
    nxt = pipeline.next_changes(name)
    assert nxt["from"] == 1 and nxt["building"] is None and nxt["comments"] == ""
    assert nxt["items"] == [
        {"target": "seat", "role": "builder", "text": "thickness: make it 30 mm", "auto": False,
         "short": "thickness"},  # a review from before `short`: its label stands in
        {"target": "assembly", "role": "assembler", "text": "Hard gate on_floor failed: 4 mm above the floor", "auto": True,
         "short": "Put it on the floor"}]
    # exactly what a build would hand the team
    notes, _ = pipeline.critic_notes_of(name, 1, ["seat"])
    assert sorted((i["target"], i["text"]) for i in nxt["items"]) == sorted((t, x) for t, xs in notes.items() for x in xs)
    assert studio_state.model(name)["next"] == nxt
    d = {a["id"]: a for a in studio_state.snapshot()["agents"]}["director"]
    assert "below" in d["question"]["text"] and d["question"]["items"] == nxt["items"]
    # while the next version builds with the person's comments, the list says so
    db.add_version(name, 2)
    db.add_event("build_started", project=name, data={"kind": "changes", "text": "a darker oak"})
    monkeypatch.setattr(pipeline, "is_running", lambda project: True)
    building = pipeline.next_changes(name)
    assert building["building"] == 2 and building["comments"] == "a darker oak" and building["items"] == nxt["items"]
    db.add_event("build_started", project=name, data={"kind": "new", "text": "a stool"})
    assert pipeline.next_changes(name) is None  # a first build has no change list


def test_every_change_has_a_plain_words_summary():
    assert pipeline._short_of({"short": " Thicker mug handle ", "parameter": "handle radius"}) == "Thicker mug handle"
    assert pipeline._short_of({"parameter": "trim, glass, rim slots (repeat, not landed three times)",
                               "change": "x"}) == "trim, glass, rim slots"
    assert pipeline._short_of({"parameter": "lamp_rear and paint slots", "change": "x"}) == "lamp rear and paint slots"
    long = pipeline._short_of({"change": "lamp_rear and paint slots: lamp_rear slot base colour and a slight emission"})
    assert long.endswith("…") and len(long) <= 61
    assert pipeline._check_short("seat_board", "Hard gate: the mesh has 12 open (non-manifold) edges")         == "Close open edges on seat board"
    assert pipeline._check_short("assembly", "Hard gate no_floaters failed: 2 pieces") == "Remove floating pieces"
    assert pipeline._check_short("assembly", "Hard gate correct_size failed: 20% too tall") == "Fix the overall size"
    assert pipeline._check_short("assembly", "Check warning: legs overlap by 3 mm") == "legs overlap by 3 mm"


def test_a_new_review_carries_the_critics_short_into_the_list():
    name = scored_project(status="needs_changes", score=55.0, passed=False)
    v = db.get_version(name, 1)
    change = {**v["review"]["changes"][0], "short": "Thicker seat"}
    db.update_version(name, 1, review={**v["review"], "changes": [change]})
    assert pipeline.next_changes(name)["items"][0]["short"] == "Thicker seat"


def _fake_build(monkeypatch, name, scores):
    """Run the pipeline with every agent and Blender step faked; each version gets the next score."""
    from modelgen.tools import blender
    from modelgen.tools.files import ProjectFiles
    scores, seen = list(scores), {"notes": [], "prev": [], "motion": [], "judge": [], "route": None, "ran": [],
                                  "faults": {}}
    monkeypatch.setattr(blender, "available", lambda: True)

    async def route(project, changes, spec, plan):
        return seen["route"]  # None: the architect plans the change, as before the router

    async def stage_report(rnd, motion=True):
        return {"ok": True, "materials": {}, "motion": {}}

    async def assembly_faults(rnd, doing=""):
        seen["ran"].append("check assembly")
        return list(seen["faults"].get("assembly", []))

    async def stage_fault(rnd, before, stage, rebuilt):
        seen["ran"].append(f"check {stage}")
        return seen["faults"].get(stage)

    def agent(label):
        async def run(rnd, notes, *a, **k):
            seen["ran"].append((label, list(notes)))
        return run

    async def nothing(*a, **k):
        return None

    async def build_parts(rnd, todo, notes, library, prev_dir):
        seen["notes"].append(dict(notes))
        seen["prev"].append(prev_dir)
        for p in todo:
            rnd.files.write(f"parts/{p}.py", "def build(): pass\n")

    async def final_build(rnd):
        found = {"passed": True, "gates": {g: {"pass": True, "detail": ""}
                                           for g in ("watertight", "no_floaters", "correct_size", "on_floor")},
                 "warnings": []}
        return {"ok": True, "checks": found, "measure": {}, "sheets": {"turntable": "t.jpg"}}

    async def critic(rnd, outcome, last_review, prev_dir=None, judge=True):
        seen["judge"].append(judge)
        s = scores.pop(0)
        return {"score": s, "scores": {}, "summary": f"scored {s}",
                "changes": [{"target": "seat", "parameter": "edge", "change": f"round it (v{rnd.number})"}]}

    async def animate(rnd, notes):
        seen["motion"].append(list(notes))
        rnd.files.write("motion.py", "def animate(parts): pass\n")

    for fn, fake_fn in (("_build_parts", build_parts), ("_final_build", final_build), ("_critic", critic),
                        ("_assemble", agent("assembler")), ("_materials", agent("materials")),
                        ("_animate", animate), ("_librarian", nothing), ("_stage_report", stage_report),
                        ("_assembly_faults", assembly_faults), ("_stage_fault", stage_fault)):
        monkeypatch.setattr(pipeline, fn, fake_fn)
    monkeypatch.setattr(pipeline.change_router, "route", route)
    folder = pipeline.version_dir(name, 1)
    files = ProjectFiles(folder)
    files.write("spec.json", json.dumps({"object": "stool"}))
    files.write("plan.json", json.dumps({"parts": [{"name": "seat", "method": "extrude", "dimensions": {}}]}))
    return seen


def test_the_animator_runs_only_when_the_spec_asks_for_motion(monkeypatch):
    name = scored_project(status="needs_changes", score=55.0, passed=False)
    seen = _fake_build(monkeypatch, name, [50.0, 60.0])
    asyncio.run(pipeline._run(name, None, None, next_version=True))
    assert seen["motion"] == []  # a stool stands still
    from modelgen.tools.files import ProjectFiles
    files = ProjectFiles(pipeline.version_dir(name, 2))
    files.write("spec.json", json.dumps({"object": "cart", "motion": {"speed_kmh": 10, "wheels": ["seat"]}}))
    db.update_version(name, 2, review={"score": 50.0, "summary": "", "scores": {},
                                        "changes": [{"target": "motion", "change": "spin the wheels the other way"}]},
                      checks={"passed": True, "gates": {"watertight": {"pass": True, "detail": ""}}, "warnings": []})
    db.update_project(name, base_version=2)
    asyncio.run(pipeline._run(name, None, None, next_version=True))
    assert seen["motion"] == [["spin the wheels the other way"]]  # the critic's motion change reached the animator
    assert (pipeline.version_dir(name, 3) / "motion.py").is_file()


def test_a_build_makes_one_version_and_a_worse_one_does_not_become_the_base(monkeypatch):
    name = scored_project(status="needs_changes", score=55.0, passed=False)
    seen = _fake_build(monkeypatch, name, [50.0])
    out = asyncio.run(pipeline._run(name, None, None, next_version=True))
    assert [v["number"] for v in db.versions(name)] == [1, 2]  # one version, then it stops
    assert seen["notes"][0] == {"seat": ["thickness: make it 30 mm"]} and seen["prev"][0] == pipeline.version_dir(name, 1)
    assert db.get_project(name)["base_version"] == 1 and "starts from v1" in out["next_starts_from"]


def test_with_the_persons_comments_the_new_version_becomes_the_base(monkeypatch):
    name = scored_project(status="needs_changes", score=55.0, passed=False)
    _fake_build(monkeypatch, name, [50.0])

    async def architect_change(project, spec, plan, changes, library):
        return plan, {"seat"}
    monkeypatch.setattr(pipeline, "_architect_change", architect_change)
    asyncio.run(pipeline._run(name, None, "make the seat round"))
    assert db.get_project(name)["base_version"] == 2  # it carries the person's changes


def test_answer_endpoint_reaches_an_open_agent_question(client):
    loop_result = {}

    async def main():
        task = asyncio.create_task(questions.ask("p", "brief", "Style?", ["modern"], timeout=5))
        await asyncio.sleep(0.01)
        q = questions.answer("brief", "modern")
        loop_result["q"] = q
        return await task
    assert asyncio.run(main()) == "modern"
    assert client.post("/api/answer", json={"agent": "brief", "choice": ""}).status_code == 400


# --- direct chat and the director's team tool ----------------------------------------------------

def test_messaging_an_agent_directly_stores_the_conversation(client):
    name = scored_project()
    reply = client.post("/api/agents/critic/chat", json={"text": "why 8.4?"}).json()
    assert reply["role"] == "agent" and "8.4" in reply["text"]
    critic = {a["id"]: a for a in studio_state.snapshot()["agents"]}["critic"]
    assert [m["from"] for m in critic["messages"]] == ["you", "agent"] and critic["chat"] is True
    assert db.runs()[0]["doing"] == "talking with you" and db.runs()[0]["project"] == name
    assert client.post("/api/agents/director/chat", json={"text": "hi"}).status_code == 404
    assert client.post("/api/agents/critic/chat", json={"text": " "}).status_code == 400


def test_work_summary_finds_a_member_by_name_or_seat():
    name = scored_project()
    s = talk.work_summary("Annie", name)
    assert s["role"] == "critic" and s["latest_work"]["score"] == 84.0
    assert talk.work_summary("@eren", name)["role"] == "builder"
    assert talk.role_of("builder-3") == "builder" and talk.role_of("Historia") == "librarian"
    assert "error" in talk.work_summary("Levi", name)


def test_the_director_gets_the_team_tool_and_reference_images(monkeypatch):
    seen = []
    monkeypatch.setattr(runner, "query", fake("Got it", captured=seen))
    up = __import__("modelgen.uploads", fromlist=["save"]).save(png_data_url())
    asyncio.run(director.chat_turn("a mug like this", attachments=[up["id"]], options={"size": {"height": 10, "unit": "cm"}}))
    allowed = [t.name for t in seen[-1]["options"].tools]
    assert "team_member" in allowed and "view_reference" in allowed
    assert "Reference images" in seen[-1]["prompt"] and '"height": 10' in seen[-1]["prompt"]
    msg = db.messages()[0]
    assert msg["meta"]["attachments"] == [up["id"]] and msg["meta"]["options"]["size"]["height"] == 10


# --- uploads, settings, page data, export, activity, library -------------------------------------

def test_uploads_are_stored_and_served(client):
    up = client.post("/api/uploads", json={"data": png_data_url()}).json()
    assert up["url"].startswith("/uploads/") and client.get(up["url"]).status_code == 200
    assert client.post("/api/uploads", json={"data": "data:text/plain;base64,aGk="}).status_code == 400
    assert client.get("/uploads/../data/modelgen.db").status_code == 404


def test_settings_are_validated_saved_and_used(client):
    s = client.get("/api/settings").json()
    assert s["daily_usd"] == config.get("budget", "daily_usd") and "blender_found" in s
    saved = client.post("/api/settings", json={"daily_usd": 12.5, "per_model_usd": 3}).json()
    assert saved["daily_usd"] == 12.5 and budget.limits()["per_model_usd"] == 3
    assert client.post("/api/settings", json={"daily_usd": -1}).status_code == 400
    assert client.post("/api/settings", json={"per_model_usd": "lots"}).status_code == 400
    warn = client.post("/api/settings", json={"blender_path": "C:/nowhere/blender.exe"}).json()
    assert warn["blender_found"] is False and "warning" in warn
    client.post("/api/settings", json={"blender_path": ""})


def test_page_data_has_every_panel(client):
    name = scored_project()
    d = client.get("/api/page").json()
    assert d["project"] == name and d["models"][0]["project"] == name and d["models"][0]["thumb"]
    v = d["model"]["versions"][0]
    assert v["id"] == "v1" and v["score"] == 8.4 and v["state"] == "scored" and v["thumb"].endswith("turntable_00.png")
    assert set(v["downloads"]) == {"glb", "stl", "blend"} and v["export"].endswith("/versions/1/export.zip")
    r = v["review"]
    assert r["score"] == 8.4 and r["criteria"][0] == {"name": "Silhouette match", "score": 9.0}
    assert r["changes"] == ["seat: thickness make it 30 mm"] and r["gates"][0]["pass"] is True
    assert r["renders"][0]["url"].endswith("turntable_00.png")
    assert d["model"]["selected"] == "v1" and d["spend"].endswith("this model") and d["budget"].startswith("Today $")
    assert "chat" in d and "settings" in d and "limits" in d


def test_a_version_lists_its_game_ready_files_and_how_it_was_made(client):
    name = scored_project()
    folder = pipeline.version_dir(name, 1)
    assert client.get("/api/page").json()["model"]["versions"][0]["game"] is None   # no game folder yet
    (folder / "game").mkdir()
    for tail in ("_game.glb", "_LOD1.glb", "_LOD2.glb", "_collision.glb"):
        (folder / "game" / f"{name}{tail}").write_bytes(b"glTF" + b"\0" * 8)
    (folder / "game" / "game.json").write_text(json.dumps({"triangles": {"game": 900, "LOD1": 450, "LOD2": 180},
                                                           "budget": 5000, "reduced": False, "size": [0.4, 0.4, 0.5]}))
    (folder / "plan.json").write_text(json.dumps({"kind": "library", "parts": [], "candidates": ["a", "b"],
                                                  "asset": {"name": "wooden stool", "asset": "a"}}))
    v = client.get("/api/page").json()["model"]["versions"][0]
    assert set(v["game"]["files"]) == {"game", "lod1", "lod2", "collision"} and v["game"]["budget"] == 5000
    assert v["game"]["triangles"]["LOD2"] == 180 and v["game"]["files"]["game"].startswith(f"/files/{name}/v1/game/")
    assert client.get(v["game"]["files"]["collision"]).status_code == 200
    assert v["path"] == "library"


def test_the_brief_carries_the_request_the_spec_the_research_and_the_path(client):
    name = scored_project()
    db.update_project(name, options={"size": {"width": 40, "unit": "cm"}, "references": ["mine.png"],
                                     "found_references": ["found.jpg"]},
                      spec={"object": "stool", "family": "furniture", "summary": "a plain oak stool",
                            "dimensions": {"width": 0.4, "depth": 0.4, "height": 0.5}, "parts": [{"name": "seat"}],
                            "research": {"proportions": ["seat 45 cm high"], "unverified": ["leg splay"]}})
    b = client.get("/api/page").json()["model"]["brief"]
    assert b["request"] == "a stool" and b["size"] == {"width": 40, "unit": "cm"} and b["path"] == "team"
    assert b["references"] == ["/uploads/mine.png"] and b["found"] == ["/uploads/found.jpg"]
    assert b["spec"]["family"] == "furniture" and b["spec"]["parts"] == 1 and b["spec"]["motion"] is False
    assert b["research"]["proportions"] == ["seat 45 cm high"] and "asset" not in b


def test_research_and_the_ready_made_search_are_their_own_run_steps():
    name = db.create_project("barrel", "a barrel")["name"]
    db.add_event("build_started", project=name, data={"kind": "new", "text": "a barrel"})
    pipeline._phase(name, "Looking for a ready-made model", step="catalog")
    run = studio_state.snapshot()["runs"][0]
    assert [s["label"] for s in run["steps"]][:2] == ["Ready-made", "Spec"] and run["current"] == "Ready-made"
    pipeline._phase(name, "Looking up the real object", step="research")
    pipeline._phase(name, "Writing the spec", step="spec")
    steps = {s["label"]: s for s in studio_state.snapshot()["runs"][0]["steps"]}
    assert steps["Ready-made"]["state"] == "done" and steps["Ready-made"]["note"] == "none fits, the team builds it"
    assert steps["Research"]["state"] == "done" and steps["Spec"]["state"] == "active"


def test_export_zip_packs_the_version(client):
    name = scored_project()
    r = client.get(f"/api/projects/{name}/versions/1/export.zip")
    assert r.status_code == 200 and r.headers["content-type"] == "application/zip"
    names = set(zipfile.ZipFile(io.BytesIO(r.content)).namelist())
    assert {f"exports/{name}.glb", f"exports/{name}.stl", "renders/turntable_00.png", "model.blend"} <= names
    assert client.get(f"/api/projects/{name}/versions/9/export.zip").status_code == 404
    blend = client.get(f"/files/{name}/v1/model.blend")
    assert blend.status_code == 200 and f"{name}.blend" in blend.headers["content-disposition"]


def test_activity_lists_tool_results_hand_offs_and_answers(client):
    name = scored_project()
    agent = runner.load_agent("critic")
    runner._set_status("critic", agent, name, add="reviewing")
    call = runner.record_call("critic", name, "view_renders", "reviewing")
    runner.record_result("critic", name, call, True, "looked at turntable")
    pipeline._handoff(name, "critic", "director", "v1 passed")
    rows = client.get("/api/activity").json()
    assert rows[0]["kind"] == "handoff" and rows[0]["text"] == "Annie → Hange: v1 passed" and rows[0]["cast"] == "annie"
    assert rows[1]["tool"] == "view_renders" and rows[1]["ok"] is True
    assert all(r["cast"] == "annie" for r in client.get("/api/activity?agent=annie").json())


def test_library_counts_reuse_and_serves_part_pictures(client, tmp_path):
    from modelgen.tools.library import Library
    lib = Library()
    picture = tmp_path / "sheet.jpg"
    Image.new("RGB", (4, 4)).save(picture)
    lib.add_part("round_leg", "a leg", "def build(): pass", ["leg"], "stool v1 part leg", preview=picture)
    lib.get("round_leg", count=True)
    lib.get("round_leg")
    part = next(p for p in client.get("/api/page").json()["parts"] if p["name"] == "round_leg")
    assert part["uses"] == 1 and part["from"] == "stool v1" and part["thumb"] == "/library/previews/round_leg.jpg"
    assert client.get(part["thumb"]).status_code == 200
    assert client.get("/library/previews/nothing.jpg").status_code == 404


def test_the_page_files_are_checked_for_changes_on_each_load(client):
    r = client.get("/map/js/app.js?v=21")
    assert r.status_code == 200 and r.headers["cache-control"] == "no-cache" and r.headers.get("etag")
    again = client.get("/map/js/app.js?v=21", headers={"If-None-Match": r.headers["etag"]})
    assert again.status_code == 304  # unchanged: a short answer, nothing downloaded again
    assert client.get("/map/").headers["cache-control"] == "no-cache"
    assert client.get("/static/viewer.js").headers["cache-control"] == "no-cache"


def test_the_websocket_sends_the_snapshot(client):
    scored_project()
    with client.websocket_connect("/ws") as ws:
        assert ws.receive_json()["type"] == "hello"
        snap = ws.receive_json()
        assert snap["type"] == "studio" and {a["id"] for a in snap["snapshot"]["agents"]} >= {"director", "builder-1"}
        ws.send_json({"type": "snapshot"})
        for _ in range(20):
            msg = ws.receive_json()
            if msg["type"] == "studio":
                break
        assert msg["type"] == "studio"


def test_the_end_of_build_report_says_the_build_is_over_and_carries_a_card(monkeypatch):
    name = scored_project(status="needs_changes")
    monkeypatch.setattr(pipeline, "is_running", lambda project: True)  # the task is still finishing
    seen = []
    monkeypatch.setattr(runner, "query", fake("v1 scored 8.4.", captured=seen))
    msg = asyncio.run(director.report(name, {"ok": True, "version": 1, "score": 84.0, "passed": True}))
    assert '"building_now": false' in seen[-1]["prompt"]
    assert msg["meta"]["version"]["number"] == 1 and msg["meta"]["version"]["title"] == "oak stool v1"


def test_a_building_version_lists_its_live_views(monkeypatch):
    import os
    name = db.create_project("cup", "a cup")["name"]
    db.add_version(name, 1)
    db.update_version(name, 1, status="building")
    assert studio_state.live(name, 1) is None
    folder = pipeline.version_dir(name, 1) / "previews"
    for i, rel in enumerate(["body/01", "handle/01", "body/02", "assembly/01"]):
        glb = folder / rel / "live.glb"
        glb.parent.mkdir(parents=True, exist_ok=True)
        glb.write_bytes(b"glTF")
        os.utime(glb, (1000 + i, 1000 + i))
    live = studio_state.live(name, 1)
    assert live["latest"]["key"] == "assembly" and live["latest"]["kind"] == "assembly"
    assert [(s["key"], s["attempt"]) for s in live["steps"]] == [("handle", 1), ("body", 2), ("assembly", 1)]
    body = next(s for s in live["steps"] if s["key"] == "body")
    assert body["url"] == f"/files/{name}/v1/previews/body/02/live.glb" and body["label"] == "body · attempt 2"

    monkeypatch.setattr(pipeline, "is_running", lambda project: True)
    (v,) = studio_state.versions(name)
    assert v["state"] == "building" and v["live"]["latest"]["key"] == "assembly"
    monkeypatch.setattr(pipeline, "is_running", lambda project: False)
    db.update_version(name, 1, status="done", score=80.0)
    assert studio_state.versions(name)[0]["live"] is None


# --- deleting a version ------------------------------------------------------------------------

def _second_version(name, score=70.0):
    db.add_version(name, 2)
    db.update_version(name, 2, status="done", score=score, passed=0,
                      review={"score": score, "summary": "better", "scores": {}, "changes": []},
                      checks={"passed": True, "gates": {}, "warnings": []})
    (pipeline.version_dir(name, 2) / "renders").mkdir(parents=True)
    run = db.start_run("critic", "m", project=name, version=2)
    db.finish_run(run, 0.25, turns=1)


def test_deleting_a_version_removes_its_row_and_folder_and_moves_base_and_accepted(client):
    name = scored_project(status="needs_changes", score=55.0, passed=False)
    _second_version(name)
    db.update_project(name, base_version=2, accepted_version=2, status="accepted")
    spent = db.spend(name)
    r = client.post(f"/api/projects/{name}/versions/2/delete").json()
    assert r["ok"] and r["base_version"] == 1
    assert db.get_version(name, 2) is None and not pipeline.version_dir(name, 2).exists()
    proj = db.get_project(name)
    assert proj["accepted_version"] is None and proj["status"] == "needs_changes" and proj["base_version"] == 1
    assert db.spend(name) == spent and db.events_of(["version_deleted"], project=name)  # the history stays
    d = {a["id"]: a for a in studio_state.snapshot()["agents"]}["director"]
    assert d["question"]["options"][0] == "Accept v1"  # the question comes back for the version left
    assert client.post(f"/api/projects/{name}/versions/2/delete").status_code == 404


def test_a_version_cannot_be_deleted_while_its_model_is_building(client, monkeypatch):
    name = scored_project()
    monkeypatch.setattr(pipeline, "is_running", lambda project: True)
    assert client.post(f"/api/projects/{name}/versions/1/delete").status_code == 409
    assert db.get_version(name, 1) and pipeline.version_dir(name, 1).is_dir()


def test_a_deleted_versions_number_is_not_used_again():
    name = scored_project()
    _second_version(name)
    db.delete_version(name, 2)
    assert db.highest_version_number(name) == 2  # its run still holds the number
    assert db.highest_version_number("no-such-model") == 0


# --- a change runs only the work it needs ------------------------------------------------------

def _no_architect(monkeypatch):
    async def architect_change(*a, **k):
        raise AssertionError("a routed change does not go to the architect")
    monkeypatch.setattr(pipeline, "_architect_change", architect_change)


def _finished_model(name):
    """Give v1 the files of a finished model, so a change starts from them."""
    from modelgen.tools.files import ProjectFiles
    files = ProjectFiles(pipeline.version_dir(name, 1))
    files.write("parts/seat.py", "def build(): pass\n")
    files.write("assembly.py", "def assemble(parts): pass\n")
    files.write("materials.py", "def apply(parts): pass\n")
    return files


def test_a_routed_change_reaches_only_the_part_it_names_without_the_critics_list(monkeypatch):
    name = scored_project(status="needs_changes", score=55.0, passed=False)
    seen = _fake_build(monkeypatch, name, [70.0])
    _no_architect(monkeypatch)
    seen["route"] = {"parts": ["seat"], "assembly": False, "materials": False, "motion": False, "why": ""}
    asyncio.run(pipeline._run(name, None, "make the seat round"))
    notes = seen["notes"][0]
    assert list(notes) == ["seat"] and len(notes["seat"]) == 1 and "make the seat round" in notes["seat"][0]
    assert "30 mm" not in str(notes)       # the critic's open change waits for "Build next version"
    assert seen["judge"] == [False]         # so the critic does not mark it landed or missed
    assert db.get_project(name)["base_version"] == 2


def test_a_change_the_router_cannot_place_is_planned_by_the_architect_as_before(monkeypatch):
    name = scored_project(status="needs_changes", score=55.0, passed=False)
    seen = _fake_build(monkeypatch, name, [70.0])

    async def architect_change(project, spec, plan, changes, library):
        return plan, {"seat"}
    monkeypatch.setattr(pipeline, "_architect_change", architect_change)
    asyncio.run(pipeline._run(name, None, "add a backrest"))
    assert set(seen["notes"][0]) == {"seat", "assembly", "materials"} and "30 mm" not in str(seen["notes"][0])


def test_a_colour_change_runs_the_materials_agent_alone(monkeypatch):
    name = scored_project(status="needs_changes", score=55.0, passed=False)
    seen = _fake_build(monkeypatch, name, [70.0])
    _no_architect(monkeypatch)
    _finished_model(name)
    seen["route"] = {"parts": [], "assembly": False, "materials": True, "motion": False, "why": ""}
    asyncio.run(pipeline._run(name, None, "paint it red"))
    assert seen["notes"] == []                                   # no builder ran
    assert [r[0] for r in seen["ran"]] == ["materials"] and "paint it red" in seen["ran"][0][1][0]


def test_rebuilt_parts_keep_the_assembly_and_materials_that_still_fit(monkeypatch):
    name = scored_project(status="needs_changes", score=55.0, passed=False)
    seen = _fake_build(monkeypatch, name, [70.0, 70.0])
    _no_architect(monkeypatch)
    _finished_model(name)
    seen["route"] = {"parts": ["seat"], "assembly": False, "materials": False, "motion": False, "why": ""}
    asyncio.run(pipeline._run(name, None, "make the seat round"))
    assert seen["ran"] == ["check assembly", "check materials"]  # checked, and no agent redid them
    files = _finished_model(name)
    db.update_project(name, base_version=1)
    seen["ran"].clear()
    seen["faults"] = {"assembly": ["Check no_floaters failed: 1 piece"], "materials": "slot wood is gone"}
    asyncio.run(pipeline._run(name, None, "make the seat round"))
    assert seen["ran"] == ["check assembly", ("assembler", ["Check no_floaters failed: 1 piece"]),
                           "check materials", ("materials", ["slot wood is gone"])]
    assert files.exists("assembly.py")


def test_a_rebuilt_part_that_changed_size_goes_back_to_the_assembler():
    rnd = pipeline.Round(project="p", number=2, dir=None, files=None, spec={}, plan={})
    rnd.part_reports = {"seat": {"size_m": {"x": 0.40, "y": 0.40, "z": 0.03}},
                        "leg": {"size_m": {"x": 0.04, "y": 0.04, "z": 0.451}}}
    before = {"seat": {"x": 0.30, "y": 0.40, "z": 0.03}, "leg": {"x": 0.04, "y": 0.04, "z": 0.45}}
    notes = pipeline._size_notes(rnd, ["seat", "leg"], before)
    assert len(notes) == 1 and "seat" in notes[0] and "0.300 x 0.400 x 0.030" in notes[0]


def test_the_router_answers_what_a_change_needs_or_leaves_it_to_the_architect(monkeypatch):
    from modelgen import change_router
    plan = {"parts": [{"name": "seat", "description": "round seat"}, {"name": "leg", "description": "leg"}]}
    answers = []

    async def run_agent(name, prompt, tools, **kw):
        assert name == "router" and "make the seat round" in prompt and "seat" in prompt
        reply = tools[0].fn(answers.pop(0))
        if reply.get("ok") is False and answers:
            tools[0].fn(answers.pop(0))
        return runner.RunResult(agent="router")
    monkeypatch.setattr(change_router.runner, "run_agent", run_agent)

    def ask(*args):
        answers[:] = args
        return asyncio.run(change_router.route("p", "make the seat round", {"object": "stool"}, plan))

    base = {"parts": [], "assembly": False, "materials": False, "motion": False, "plan": False}
    assert ask({**base, "parts": ["seat"], "motion": True}) == {
        "parts": ["seat"], "plan": False, "assembly": False, "materials": False, "motion": False, "why": ""}
    assert ask({**base, "parts": ["seat"], "plan": True}) is None       # a part to add: the architect plans it
    assert ask(base) is None                                             # nothing named: the architect plans it
    assert ask({**base, "parts": ["backrest"]}) is None                  # an unknown part is refused, nothing routed
    assert ask({**base, "parts": ["backrest"]}, {**base, "materials": True})["materials"] is True
    assert change_router.describe({**base, "parts": ["seat"], "materials": True}) == \
        "the builder of seat, the materials agent"


def test_an_edit_changes_a_few_lines_and_builds_like_a_write():
    from modelgen.tools import agent_tools as at
    code = "def build():\n    width = 0.30\n    depth = 0.30\n"
    assert at.patched(code, [{"old": "width = 0.30", "new": "width = 0.45"}]) == (
        "def build():\n    width = 0.45\n    depth = 0.30\n", None)
    assert "appears 2 times" in at.patched(code, [{"old": "0.30", "new": "0.45"}])[1]
    assert "appears 0 times" in at.patched(code, [{"old": "height", "new": "h"}])[1]
    assert "no code" in at.patched("", [{"old": "a", "new": "b"}])[1]
    assert "at least one edit" in at.patched(code, [])[1]

    class Files:
        def exists(self, path):
            return True

        def read(self, path):
            return code

    built = []

    async def write(args):
        built.append(args["code"])
        return {"ok": True}
    rnd = type("R", (), {"files": Files()})()
    tool = at._edit_tool(rnd, "edit_part", "parts/seat.py", write, "write_part")
    assert asyncio.run(tool.fn({"edits": [{"old": "depth = 0.30", "new": "depth = 0.40"}]})) == {"ok": True}
    assert built == ["def build():\n    width = 0.30\n    depth = 0.40\n"]
    assert asyncio.run(tool.fn({"edits": [{"old": "nope", "new": "x"}]}))["ok"] is False and len(built) == 1


def test_every_agent_that_writes_code_can_also_edit_it():
    from modelgen.tools import agent_tools as at
    from modelgen.tools.files import ProjectFiles
    name = scored_project()
    rnd = pipeline.Round(project=name, number=1, dir=pipeline.version_dir(name, 1),
                         files=ProjectFiles(pipeline.version_dir(name, 1)), spec={"object": "stool"},
                         plan={"parts": [{"name": "seat"}]})
    for tools, wanted in ((at.builder_tools(rnd, "seat"), "edit_part"), (at.assembler_tools(rnd), "edit_assembly"),
                          (at.materials_tools(rnd), "edit_materials"), (at.animator_tools(rnd), "edit_motion")):
        assert wanted in [t.name for t in tools]


def test_a_build_records_where_its_time_went(monkeypatch):
    name = scored_project()

    async def run(*a, **k):
        db.add_event("phase", project=name, data={"text": "Building", "step": "build"})
        db.add_event("tool_call", project=name, agent="builder-1", data={"tool": "write_part"})
        db.add_event("tool_done", project=name, agent="builder-1", data={"tool": "write_part"})
        db.add_event("run", project=name, agent="builder-1", data={"role": "builder", "seconds": 12.5, "cost_usd": 0.2})
        db.add_event("phase", project=name, data={"text": "Rendering", "step": "render"})
        return {"ok": True, "version": 1}
    monkeypatch.setattr(pipeline, "_run", run)
    asyncio.run(pipeline.run(name, changes="make it red"))
    found = json.loads((pipeline.version_dir(name, 1) / "timings.json").read_text(encoding="utf-8"))
    assert found["build"] == "changes" and set(found["steps"]) == {"build", "render"}
    assert found["agents"] == {"builder": {"runs": 1, "seconds": 12.5, "usd": 0.2}}
    assert found["tools"]["write_part"]["calls"] == 1 and found["total_seconds"] >= 0


def test_the_librarian_files_the_parts_while_the_report_is_written(monkeypatch):
    name = scored_project()
    order = []

    async def run(*a, **k):
        return {"ok": True, "version": 1, "score": 84.0, "keep": 1}

    async def librarian(project, number, library):
        order.append("librarian starts")
        await asyncio.sleep(0.02)
        order.append("librarian ends")

    async def on_done(project, outcome):
        order.append("report")
        assert "keep" not in outcome
    monkeypatch.setattr(pipeline, "_run", run)
    monkeypatch.setattr(pipeline, "_librarian", librarian)
    asyncio.run(pipeline.run(name, request="a stool", on_done=on_done))
    assert order == ["report", "librarian starts", "librarian ends"] or order == ["librarian starts", "report",
                                                                                 "librarian ends"]
    assert "keep" not in db.last_event("finished", name)["data"]


@pytest.mark.parametrize("built_by, judge", [("changes", False), ("next", True)])
def test_review_again_marks_the_old_change_list_only_for_a_version_built_from_it(monkeypatch, built_by, judge):
    name = scored_project(status="needs_changes", score=55.0, passed=False)
    folder = pipeline.version_dir(name, 2)
    (folder / "renders").mkdir(parents=True)
    Image.new("RGB", (8, 8)).save(folder / "renders" / "sheet_turntable.jpg")
    (folder / "spec.json").write_text('{"object": "stool"}', encoding="utf-8")
    (folder / "plan.json").write_text('{"parts": [{"name": "seat"}]}', encoding="utf-8")
    (folder / "checks.json").write_text('{"passed": true, "gates": {}}', encoding="utf-8")
    db.add_event("build_started", project=name, data={"kind": built_by, "text": "make the seat round"})
    db.add_event("version_started", project=name, data={"version": 2, "round": 1})
    db.add_version(name, 2)
    db.update_version(name, 2, status="done", score=None, passed=0, review=None)
    seen = {}

    async def critic(rnd, outcome, last_review, prev_dir=None, judge=True):
        seen.update(judge=judge, last=last_review)
        return {"score": 70.0, "summary": "fine", "changes": [], "scores": {}}
    monkeypatch.setattr(pipeline, "_critic", critic)
    out = asyncio.run(pipeline.run(name, review=2))
    assert out["ok"] and out["score"] == 70.0
    assert seen["judge"] is judge and seen["last"]["changes"][0]["change"] == "make it 30 mm"
