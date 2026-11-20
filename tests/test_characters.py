"""Characters (tools\\character.py, bpy_lib\\character_body.py and the character path): the sheet
checks, the family words, the designer's tool, the checks, the /v1 API's character jobs. Tests
marked blender build real bodies and run a real job."""

import asyncio
import json

import pytest
from fastapi.testclient import TestClient

from modelgen import config, db, families, jobs, pipeline, runner, server
from modelgen.tools import agent_tools as at
from modelgen.tools import blender, checks
from modelgen.tools import character as ch

GUY = {"name": "cool guy", "age": 26, "sex": "male", "build": "athletic"}


# --- the sheet --------------------------------------------------------------------------------------

def test_a_sheet_is_filled_in_and_every_default_is_listed():
    sheet, assumed = ch.clean({"age": 8, "sex": "girl"})
    assert sheet["female"] == 1.0 and sheet["sex"] == "female" and sheet["build"] == "average"
    assert sheet["height_cm"] == round(ch.typical_height_cm(8, 1.0), 1) == 127.0
    assert any(a.startswith("height: 127") for a in assumed) and any("ancestry" in a for a in assumed)
    assert abs(sum(sheet["ancestry"].values()) - 1) < 0.002 and sheet["style"] == "realistic"
    assert ch.who(sheet) == "an 8-year-old girl"


@pytest.mark.parametrize("raw, words", [({"age": 0.5}, "a 6-month-old baby"), ({"age": 80, "sex": "male"},
                                         "an 80-year-old man"), ({"age": 15, "sex": "female"},
                                         "a 15-year-old teenage girl"), ({"age": 30}, "a 30-year-old person")])
def test_who_names_the_person(raw, words):
    assert ch.who(ch.clean(raw)[0]) == words


@pytest.mark.parametrize("raw, field", [({"age": 130}, "age"), ({"sex": "robot"}, "sex"), ({"build": "huge"}, "build"),
                                        ({"height_cm": 10}, "height_cm"), ({"skin": "brown"}, "skin"),
                                        ({"ancestry": "martian"}, "ancestry"), ({"style": "anime"}, "style"),
                                        ({"age": 1, "build": "muscular"}, "build")])
def test_a_bad_sheet_is_refused_by_name(raw, field):
    with pytest.raises(ch.CharacterError) as exc:
        ch.clean(raw)
    assert field in str(exc.value)


def test_build_sets_the_sliders_and_explicit_numbers_win():
    sheet = ch.clean({"build": "heavy", "muscle": 0.9, "ancestry": {"asian": 3, "caucasian": 1}})[0]
    assert sheet["weight"] == ch.BUILDS["heavy"][1] and sheet["muscle"] == 0.9
    assert sheet["ancestry"] == {"african": 0.0, "asian": 0.75, "caucasian": 0.25} and sheet["skin"] == ch.SKIN["asian"]
    body = ch.body(sheet)
    assert body["asian"] == 0.75 and body["height_cm"] == sheet["height_cm"] and body["age"] == 30


@pytest.mark.parametrize("raw", [GUY, {"age": 0.5}, {"age": 70, "ancestry": {"african": 1, "asian": 2}}])
def test_cleaning_a_clean_sheet_changes_nothing(raw):
    sheet = ch.clean(raw)[0]
    assert ch.clean(sheet)[0] == sheet


def test_the_same_sheet_gives_the_same_key_and_the_name_does_not_count():
    a = ch.clean(GUY)[0]
    assert ch.key(a) == ch.key(ch.clean({**GUY, "name": "other"})[0]) != ch.key(ch.clean({**GUY, "age": 27})[0])


# --- words: who is a person ------------------------------------------------------------------------

@pytest.mark.parametrize("text", ["a cool guy", "old woman with a cane", "baby girl", "a nerd", "teenage boy",
                                  "an anime character", "dragon ball style character", "grandpa",
                                  "a soldier", "a realistic character for my game"])
def test_a_person_word_picks_the_person_family(text):
    assert families.guess(text) == "person" and families.person_word(text)


@pytest.mark.parametrize("text, family", [("a robot man", "character"), ("a cartoon dog", "character"),
                                          ("a dragon", "character"), ("statue of a woman", "character"),
                                          ("a park with people", "scene"), ("car engine piston", "mechanical")])
def test_robots_animals_statues_scenes_and_parts_are_not_people(text, family):
    assert families.guess(text) == family
    assert family == "scene" or not families.person_word(text)


def test_a_person_skips_the_ready_made_catalogue(monkeypatch):
    proj = db.create_project("guy", "a cool guy")
    monkeypatch.setattr(pipeline, "_library_on", lambda: True)
    monkeypatch.setattr(pipeline.asset_catalog, "search", lambda *a, **k: pytest.fail("no catalogue search"))
    monkeypatch.setattr(pipeline.asset_catalog, "load", lambda: [])
    assert asyncio.run(pipeline._try_library(proj["name"], "a cool guy", None, False)) is None


def test_a_person_has_its_own_triangle_limit():
    assert families.budget("person") == 20000


# --- the designer's tool ------------------------------------------------------------------------------

def test_the_designer_tool_checks_the_sheet_and_names_the_field():
    sink = {}
    submit = at.character_designer_tools(sink)[0]
    bad = submit.fn({"character": {"age": 200}})
    assert bad["ok"] is False and "age" in bad["error"] and not sink
    good = submit.fn({"character": GUY, "notes": "an original character"})
    assert good["ok"] and sink["character"]["build"] == "athletic" and good["character"].startswith("a 26-year-old man")


def test_the_designer_writes_the_sheet_for_a_request_in_words(monkeypatch):
    proj = db.create_project("nerd", "a nerd")
    seen = {}

    async def agent(name, prompt, tools, **kw):
        seen["name"], seen["prompt"] = name, prompt
        tools[0].fn({"character": {"name": "nerd", "age": 22, "sex": "male", "build": "slim",
                                   "look": "glasses, a hoodie"}})
        return None

    built = {}

    async def run_character(project, sheet, assumed, notes=""):
        built.update(sheet=sheet, assumed=assumed)
        return {"ok": True, "character": True, "version": 1}

    monkeypatch.setattr(runner, "run_agent", agent)
    monkeypatch.setattr(pipeline, "_run_character", run_character)
    monkeypatch.setattr(ch, "available", lambda: True)
    out = asyncio.run(pipeline._try_character(proj["name"], "a nerd", None, False))
    assert out["character"] and seen["name"] == "character_designer" and '"build"' in seen["prompt"]
    assert built["sheet"]["build"] == "slim" and built["sheet"]["look"] == "glasses, a hoodie"


def test_a_request_that_is_not_a_person_is_left_to_the_other_paths(monkeypatch):
    proj = db.create_project("hydrant", "fire hydrant")
    monkeypatch.setattr(runner, "run_agent", lambda *a, **k: pytest.fail("no designer for a hydrant"))
    assert asyncio.run(pipeline._try_character(proj["name"], "fire hydrant", None, False)) is None


def test_a_next_version_with_no_changes_is_refused(monkeypatch):
    proj = db.create_project("guy", "a cool guy")
    folder = pipeline.version_dir(proj["name"], 1)
    folder.mkdir(parents=True)
    (folder / "plan.json").write_text(json.dumps({"kind": "character", "character": ch.clean(GUY)[0]}), encoding="utf-8")
    db.add_version(proj["name"], 1)
    db.update_version(proj["name"], 1, status="done", passed=1)
    out = asyncio.run(pipeline._try_character(proj["name"], None, None, True))
    assert out["ok"] is False and "say what to change" in out["error"]


# --- the checks ---------------------------------------------------------------------------------------

def _rig(**over):
    return {"rig": True, "bones": 53, "skinned": ["body", "eyes"], "unweighted": {"body": 0, "eyes": 0},
            "max_influences": 4, "stretch": {"arms_up": 1.7, "knee_up": 2.6}, **over}


def test_the_character_checks_pass_a_good_build_and_name_what_fails():
    sheet = ch.clean(GUY)[0]
    made = {"height": sheet["height_cm"] / 100, "ground": 0.0, "parts": {"body": {"faces": 13346}}}
    ok = checks.character_checks(made, _rig(), sheet)
    assert ok["passed"], ok["gates"]
    short = checks.character_checks({**made, "height": 1.5}, _rig(), sheet)
    assert not short["gates"]["correct_size"]["pass"]
    loose = checks.character_checks(made, _rig(unweighted={"body": 12}), sheet)
    assert not loose["gates"]["skinned"]["pass"] and "body (12)" in loose["gates"]["skinned"]["detail"]
    torn = checks.character_checks(made, _rig(stretch={"knee_up": 9.0}), sheet)
    assert not torn["gates"]["skin_follows"]["pass"] and "knee_up" in torn["gates"]["skin_follows"]["detail"]
    bare = checks.character_checks(made, {"rig": False}, sheet)
    assert not bare["gates"]["skeleton"]["pass"]


# --- the /v1 API --------------------------------------------------------------------------------------

@pytest.fixture
def api(monkeypatch):
    monkeypatch.setattr(server.app.state, "run_jobs", False)
    with TestClient(server.app) as c:
        c.headers["X-ModelGen-Key"] = jobs.api_key()
        yield c


def test_get_characters_lists_the_sheet_fields(api):
    out = api.get("/v1/characters").json()
    assert out["fields"]["age"]["unit"] == "years" and "athletic" in out["fields"]["build"]["choices"]
    assert "53-bone" in out["skeleton"]
    assert api.get("/v1/characters", headers={"X-ModelGen-Key": "wrong"}).status_code == 401


def test_a_character_job_is_checked_and_refused_by_name(api):
    bad = api.post("/v1/jobs", json={"client": "village", "character": {"age": -3}})
    assert bad.status_code == 400 and "character.age" in bad.json()["detail"]
    ok = api.post("/v1/jobs", json={"client": "village", "character": GUY, "outputs": ["game"]}).json()
    assert ok["status"] == "queued" and ok["object"] == "a 26-year-old man"
    assert ok["request"]["character"]["build"] == "athletic" and any("height" in a for a in ok["assumed"])


def test_a_character_job_builds_with_no_agent(api, monkeypatch):
    calls = []

    async def build(project, sheet, assumed, notes=""):
        calls.append(project)
        n = db.highest_version_number(project) + 1
        folder = pipeline.version_dir(project, n)
        (folder / "game").mkdir(parents=True)
        (folder / "game" / f"{project}_game.glb").write_bytes(b"glb")
        (folder / "plan.json").write_text(json.dumps({"kind": "character", "character": sheet}), encoding="utf-8")
        (folder / "character.json").write_text(json.dumps({"sheet": sheet, "assumed": assumed, "key": ch.key(sheet)}),
                                               encoding="utf-8")
        db.add_version(project, n)
        db.update_version(project, n, status="done", passed=1)
        return {"ok": True, "character": True, "version": n, "score": None, "passed": True}

    monkeypatch.setattr(pipeline, "_run_character", build)
    monkeypatch.setattr(config, "blender_path", lambda: "blender.exe")

    async def no_agent(*a, **k):
        pytest.fail("no agent may run for a character sent as a sheet")

    monkeypatch.setattr(runner, "run_agent", no_agent)
    first = api.post("/v1/jobs", json={"client": "village", "character": GUY, "outputs": ["game"],
                                       "reuse": False}).json()
    asyncio.run(jobs.run(db.get_job(first["id"])))
    out = jobs.public(db.get_job(first["id"]))
    assert out["status"] == "done" and out["source"] == "character" and out["passed"] and len(calls) == 1
    assert out["cost_usd"] == 0.0 and out["character"]["key"] == ch.key(ch.clean(GUY)[0])
    assert out["main"]["game"].endswith("_game.glb")


# --- Blender: real bodies ---------------------------------------------------------------------------------

AGES = [("baby", {"age": 0.5}), ("girl", {"age": 8, "sex": "female"}), ("woman", {"age": 30, "sex": "female"}),
        ("old man", {"age": 80, "sex": "male"}), ("heavy", {"age": 45, "sex": "male", "build": "heavy"})]


@pytest.mark.blender
def test_every_age_builds_a_skinned_body_at_its_height_on_the_floor():
    sheets = [ch.clean(raw)[0] for _, raw in AGES]
    results = []
    for sheet in sheets:
        result = blender.run_job([ch.build_step(sheet), ch.report_step()], timeout=600)
        assert result.ok, result.traceback
        results.append((sheet, result.outputs))
    for sheet, out in results:
        found = checks.character_checks(out["character"], out["rig_report"], sheet)
        assert found["passed"], (ch.who(sheet), {k: g for k, g in found["gates"].items() if not g["pass"]})
        assert out["rig_report"]["bones"] == 53 and set(out["character"]["parts"]) == {"body", "eyes"}
    heights = [out["character"]["height"] for _, out in results]
    assert heights[0] < heights[1] < heights[2] and 0.55 < heights[0] < 0.75


@pytest.mark.blender
def test_a_real_character_job_exports_a_skinned_animated_game_file():
    sheet, assumed = ch.clean(GUY)
    proj = db.create_project("cool guy", "a cool guy", options={"character": sheet, "assumed": assumed})
    out = asyncio.run(pipeline._run_character(proj["name"], sheet, assumed))
    folder = pipeline.version_dir(proj["name"], out["version"])
    found = json.loads((folder / "checks.json").read_text(encoding="utf-8"))
    assert out["ok"] and out["passed"], found["gates"]
    assert found["game"]["triangles"]["game"] <= 20000
    for path in [folder / "exports" / f"{proj['name']}.glb", folder / "game" / f"{proj['name']}_game.glb"]:
        data = path.read_bytes()
        size = int.from_bytes(data[12:16], "little")
        gltf = json.loads(data[20:20 + size])
        assert [len(s["joints"]) for s in gltf["skins"]] == [53], path.name
        assert [a["name"] for a in gltf["animations"]] == ["rig_test"], path.name
    assert jobs.source_of(proj["name"], out["version"]) == "character"
