"""The web server: chat with a fake director, status, projects, versions and files."""

import pytest
from fastapi.testclient import TestClient

from modelgen import db, pipeline, runner, server
from modelgen.messages import AssistantMessage, ResultMessage, TextBlock

REPLY = "اهلا! عايز المج يبقى حجمه قد ايه؟"


@pytest.fixture
def client(monkeypatch):
    async def fake_query(*, prompt, options):
        yield AssistantMessage(content=[TextBlock(text=REPLY)], model="claude-opus-5-5")
        yield ResultMessage(subtype="success", duration_ms=1, duration_api_ms=1, is_error=False, num_turns=1,
                            session_id="s", total_cost_usd=0.002, result=REPLY)

    monkeypatch.setattr(runner, "query", fake_query)
    with TestClient(server.app) as c:
        yield c


def test_page_and_static_files_are_served(client):
    page = client.get("/")
    assert page.status_code == 200 and str(page.url).endswith("/map/")
    assert "ModelGen Studio" in page.text and 'dir="auto"' in page.text and "live" in page.text
    for name in ("js/app.js", "js/live.js", "js/studio.js", "css/app.css", "mock.json"):
        assert client.get(f"/map/{name}").status_code == 200
    assert client.get("/static/viewer.js").status_code == 200  # the studio's 3D viewer
    assert client.get("/classic").status_code == 404  # the old simple page is gone
    for name in ("index.html", "app.js", "style.css"):
        assert client.get(f"/static/{name}").status_code == 404


def test_a_waiting_question_is_a_card_in_the_chat_and_a_line_in_the_strip():
    """Runs the page's own app.js in Node (tests/page_asks.js)."""
    import json, shutil, subprocess
    from pathlib import Path
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js is not installed")
    out = subprocess.run([node, str(Path(__file__).with_name("page_asks.js"))], capture_output=True, text=True,
                         timeout=60, encoding="utf-8")
    assert out.returncode == 0, out.stderr
    r = json.loads(out.stdout)
    assert r["cards"] == 1 and r["afterReport"]  # once, after Hange's report, before your next message
    assert r["buttons"] == 3 and r["changes"]
    assert r["stripLines"] == 1 and r["stripPoints"] and r["stripText"]  # the strip points to that card
    assert r["firstSentence"] == "v2 is ready with 7.1 / 10" and r["noQuestionAt"] == 3
    assert r["earlyCards"] == 0 and r["earlyStrip"] == ""  # nothing until the chat has loaded
    assert r["acceptIsMain"] and r["othersPlain"]  # Accept is the one main button


def test_chat_round_trip_stores_both_messages(client):
    reply = client.post("/api/chat", json={"text": "عايز مج قهوة سيراميك"}).json()
    assert reply["role"] == "assistant" and reply["text"] == REPLY
    msgs = client.get("/api/messages").json()
    assert [m["role"] for m in msgs] == ["user", "assistant"]
    assert msgs[0]["text"] == "عايز مج قهوة سيراميك"
    assert db.runs()[0]["agent"] == "director" and db.runs()[0]["cost_usd"] == 0.002


def test_empty_chat_is_refused(client):
    assert client.post("/api/chat", json={"text": "  "}).status_code == 400


def test_status_lists_the_team_blender_and_spend(client):
    s = client.get("/api/status").json()
    assert {a["id"] for a in s["agents"]} == {"director", "brief", "architect", "builder-1", "assembler",
                                               "materials", "animator", "critic", "librarian"}
    assert all({"id", "name", "role", "state", "doing", "since", "model"} <= set(a) for a in s["agents"])
    assert set(s["blender"]) == {"found", "path", "install"} and "winget" in s["blender"]["install"]
    assert s["spend"] == {"model": 0.0, "today": 0.0}


def test_websocket_says_hello_and_streams_the_chat(client):
    with client.websocket_connect("/ws") as ws:
        hello = ws.receive_json()
        assert hello["type"] == "hello" and "agents" in hello["status"]
        ws.send_json({"type": "chat", "text": "hello"})
        texts = []
        for _ in range(30):
            msg = ws.receive_json()
            if msg["type"] == "event" and msg["item"]["kind"] == "message":
                texts.append(msg["item"]["data"]["text"])
                if REPLY in texts:
                    break
        assert texts == ["hello", REPLY]


def _project_with_version(tmp_path_factory=None):
    proj = db.create_project("ceramic mug", "a mug")
    db.add_version(proj["name"], 1)
    db.update_version(proj["name"], 1, status="done", score=84.0, passed=1,
                      review={"score": 84.0, "summary": "good", "scores": {}, "changes": []},
                      checks={"passed": True, "gates": {}, "warnings": []})
    folder = pipeline.version_dir(proj["name"], 1)
    (folder / "exports").mkdir(parents=True)
    (folder / "exports" / f"{proj['name']}.glb").write_bytes(b"glTF" + b"\0" * 64)
    (folder / "renders").mkdir()
    (folder / "renders" / "sheet_turntable.jpg").write_bytes(b"\xff\xd8\xff")
    return proj["name"]


def test_projects_versions_downloads_accept_and_revert(client):
    name = _project_with_version()
    assert client.get("/api/projects").json()[0]["name"] == name
    detail = client.get(f"/api/projects/{name}").json()
    v = detail["versions"][0]
    assert v["score"] == 84.0 and v["passed"] is True
    assert v["glb"] == f"/files/{name}/v1/exports/{name}.glb"
    assert v["sheets"]["turntable"].endswith("sheet_turntable.jpg")
    glb = client.get(v["glb"])
    assert glb.status_code == 200 and glb.headers["content-type"] == "model/gltf-binary"
    assert client.post(f"/api/projects/{name}/accept", json={"version": 1}).json()["accepted_version"] == 1
    assert client.post(f"/api/projects/{name}/revert", json={"version": 1}).json()["base_version"] == 1
    assert client.post(f"/api/projects/{name}/accept", json={"version": 7}).status_code == 404


def test_files_stay_inside_the_project(client):
    name = _project_with_version()
    assert client.get(f"/files/{name}/..%2F..%2Fdata%2Fmodelgen.db").status_code in (403, 404)
    assert client.get(f"/files/{name}/v1/nothing.png").status_code == 404
    assert client.get("/files/nobody/v1/x.glb").status_code == 404


def test_try_again_runs_the_last_build_the_same_way(client, monkeypatch):
    calls = []
    monkeypatch.setattr(pipeline, "start", lambda name, **kw: calls.append((name, kw)))
    name = db.create_project("airpods pro case", "AirPods Pro in their case")["name"]
    assert client.post("/api/projects/nobody/retry").status_code == 404
    assert client.post(f"/api/projects/{name}/retry").status_code == 409  # nothing failed yet
    db.update_project(name, status="failed")
    db.add_event("build_started", project=name, data={"kind": "new", "text": "AirPods Pro in their case"})
    assert client.post(f"/api/projects/{name}/retry").json()["ok"] is True
    assert calls[-1][0] == name and "changes" not in calls[-1][1] and not calls[-1][1].get("next_version")
    db.add_event("build_started", project=name, data={"kind": "changes", "text": "make the case matte"})
    client.post(f"/api/projects/{name}/retry")
    assert calls[-1][1]["changes"] == "make the case matte"
    monkeypatch.setattr(pipeline, "is_running", lambda n: True)
    assert client.post(f"/api/projects/{name}/retry").status_code == 409


def test_delete_model_removes_it_and_its_folder_but_keeps_the_spend(client, monkeypatch):
    name = _project_with_version()
    run = db.start_run("architect", "m", name, None, "planning the parts")
    db.finish_run(run, 0.25, ok=False, error="failed")
    monkeypatch.setattr(pipeline, "is_running", lambda n: True)
    assert client.post(f"/api/projects/{name}/delete").status_code == 409  # not while it builds
    monkeypatch.setattr(pipeline, "is_running", lambda n: False)
    assert client.post(f"/api/projects/{name}/delete").json() == {"ok": True, "deleted": name}
    assert db.get_project(name) is None and db.versions(name) == []
    assert not pipeline.version_dir(name, 1).parent.exists()
    assert db.runs(name)[0]["cost_usd"] == 0.25  # what it cost stays in the day's spend
    assert client.post(f"/api/projects/{name}/delete").status_code == 404
