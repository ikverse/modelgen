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


# --- stage 2: the look, styles ------------------------------------------------------------------------

def _bpy_dict_keys(path, name):
    """The keys of a dict literal assigned at the top of a bpy_lib file (read without Blender)."""
    import ast
    from pathlib import Path
    tree = ast.parse(Path(path).read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(getattr(t, "id", None) == name for t in node.targets):
            return [ast.literal_eval(k) for k in node.value.keys] if isinstance(node.value, ast.Dict) else \
                list(ast.literal_eval(node.value))
    raise AssertionError(f"{name} not found")


def test_the_host_lists_match_what_the_blender_tools_make():
    parts = config.PACKAGE / "bpy_lib" / "character_parts.py"
    assert set(_bpy_dict_keys(parts, "GARMENTS")) == set(ch.GARMENT_TYPES)
    assert tuple(_bpy_dict_keys(parts, "HAIR_STYLES")) == ch.HAIR_STYLES
    kinds = {}
    for t in ch.GARMENT_TYPES:
        kinds[t] = ch.GARMENT_KIND[t]
    assert set(kinds) == set(ch.GARMENT_TYPES)


def test_every_named_face_slider_exists_in_the_body_model():
    names = set(ch.face_sliders())
    assert len(names) > 150 and set(ch.FACE_SLIDERS) <= names
    for st in ch.STYLES:
        assert set(ch.style(st)["face"]) <= names, st


def test_a_plain_look_is_filled_in_for_the_age_and_listed():
    baby, assumed = ch.clean({"age": 0.5})
    assert [g["type"] for g in baby["outfit"]] == ["onesie"] and baby["hair"]["style"] == "buzz"
    assert baby["brows"]["thickness"] == 0.05 and any(a.startswith("outfit:") for a in assumed)
    old = ch.clean({"age": 80, "sex": "female"})[0]
    assert old["hair"]["colour"] == "#d8d6d2" and old["hair"]["style"] == "bob"


def test_layers_are_ordered_inner_to_outer():
    sheet = ch.clean({**GUY, "outfit": [{"type": "leather_jacket", "colour": "#111111"},
                                        {"type": "sneakers", "colour": "#ffffff"},
                                        {"type": "tshirt", "colour": "#eeeeee"}, {"type": "jeans", "colour": "#223344"}]})[0]
    assert [g["type"] for g in sheet["outfit"]] == ["tshirt", "jeans", "sneakers", "leather_jacket"]


@pytest.mark.parametrize("outfit, words", [
    ([{"type": "tshirt"}, {"type": "sweater"}], "two top garments"),
    ([{"type": "dress"}, {"type": "jeans"}], "a dress takes no other"),
    ([{"type": "onesie"}, {"type": "jeans"}, {"type": "flats"}], "onesie is worn alone"),
    ([{"type": "cape"}], "outfit[0].type"),
    ([{"type": "skirt", "length": "ankle"}], "length must be one of"),
    ([{"type": "jeans", "colour": "blue"}], "outfit[0].colour")])
def test_an_outfit_that_cannot_be_worn_is_refused_by_name(outfit, words):
    with pytest.raises(ch.CharacterError) as exc:
        ch.clean({**GUY, "outfit": outfit})
    assert words in str(exc.value)


def test_bad_hair_face_and_glasses_are_refused_by_name():
    for raw, words in [({"hair": {"style": "mohawk"}}, "hair.style"), ({"face": {"nose-length": 0.5}}, "no slider"),
                       ({"face": {"chin-width-decr-incr": 3}}, "chin-width-decr-incr"),
                       ({"accessories": [{"type": "glasses", "shape": "heart"}]}, "shape round or square")]:
        with pytest.raises(ch.CharacterError) as exc:
            ch.clean({**GUY, **raw})
        assert words in str(exc.value), raw


def test_a_style_sets_proportions_face_shading_and_colours():
    sheet = ch.clean({**GUY, "style": "shonen_anime", "face": {"chin-width-decr-incr": 0.8}})[0]
    body = ch.body(sheet)
    assert body["style_proportions"]["head"] > 1 and body["style_proportions"]["eyes"] > 1
    assert body["local"]["eye-scale-decr-incr"] == 0.9
    assert body["local"]["chin-width-decr-incr"] == pytest.approx(0.3)  # the style's -0.5 plus the sheet's 0.8
    look = ch.look(sheet)
    assert look["shading"] == "toon" and look["outline"] > 0 and look["glint"] and look["hair"]["volume"] > 1
    real = ch.look(ch.clean(GUY)[0])
    assert real["shading"] == "pbr" and real["outline"] == 0 and not real["glint"]
    assert ch.look(ch.clean({**GUY, "style": "gritty_anime"})[0])["skin"] != real["skin"]  # muted colours


def test_the_tailor_and_hair_tools_check_against_the_sheet():
    sheet = ch.clean(GUY)[0]
    sink = {}
    tailor = at.character_tailor_tools(sink, sheet)[0]
    assert tailor.fn({"outfit": [{"type": "dress", "colour": "#ff0000"}, {"type": "jeans", "colour": "#000000"}]})[
        "ok"] is False and not sink
    assert tailor.fn({"outfit": [{"type": "hoodie", "colour": "#ff0000"}]})["ok"] and sink["outfit"][0]["type"] == "hoodie"
    hair_sink = {}
    hair = at.character_hair_tools(hair_sink, sheet)[0]
    assert hair.fn({"hair": {"style": "mullet", "colour": "#000000"}})["ok"] is False
    assert hair.fn({"hair": {"style": "spiky", "colour": "#000000"}, "brows": {"thickness": 0.9}})["ok"]
    assert hair_sink["brows"]["thickness"] == 0.9


def test_a_person_is_scored_by_the_character_rubric():
    assert at.rubric_file(None, {"family": "person"}).name == "rubric_character.yaml"
    rubric = at.load_rubric(None, {"family": "person"})
    assert {c["id"] for c in rubric["criteria"]} >= {"who", "face", "hair_clothes", "rig", "style"}
    assert {"clothes_cover", "skinned"} <= {g["id"] for g in rubric["gates"]}


def test_skin_through_the_clothes_fails_the_cover_gate():
    sheet = ch.clean(GUY)[0]
    made = {"height": sheet["height_cm"] / 100, "ground": 0.0, "parts": {}}
    good = checks.character_checks(made, _rig(poke={"rest": {"checked": 60, "through": 1, "share": 0.017}}), sheet)
    assert good["gates"]["clothes_cover"]["pass"]
    bad = checks.character_checks(made, _rig(poke={"arms_up": {"checked": 60, "through": 12, "share": 0.2}}), sheet)
    assert not bad["gates"]["clothes_cover"]["pass"] and "arms_up" in bad["gates"]["clothes_cover"]["detail"]


# --- stage 3: clips ---------------------------------------------------------------------------------

from modelgen.tools import clips as clip_lib  # noqa: E402

needs_library = pytest.mark.skipif(not clip_lib.available(), reason="the animation library is not on this PC")


@needs_library
def test_the_library_lists_its_clips_and_the_everyday_set_is_in_it():
    names = clip_lib.available()
    assert len(names) >= 40 and "A_TPose" not in names
    assert set(clip_lib.DEFAULT) <= set(names)


@needs_library
def test_a_sheet_takes_default_all_or_named_clips():
    assert ch.clean(GUY)[0]["clips"] == list(clip_lib.DEFAULT)
    assert len(ch.clean({**GUY, "clips": "all"})[0]["clips"]) == len(clip_lib.available())
    assert ch.clean({**GUY, "clips": ["walk_loop", "PUNCH_JAB", "Walk_Loop"]})[0]["clips"] == ["Walk_Loop", "Punch_Jab"]
    with pytest.raises(ch.CharacterError) as exc:
        ch.clean({**GUY, "clips": ["moonwalk"]})
    assert "no clip 'moonwalk'" in str(exc.value)


@needs_library
def test_the_build_step_moves_library_clips_instead_of_the_test_clip():
    step = ch.build_step(ch.clean({**GUY, "clips": ["Walk_Loop"]})[0])
    assert step["library_clips"] == ["Walk_Loop"] and step["library"].endswith("UAL1_Standard.glb")
    assert step["clips"] == []
    assert clip_lib.nice("Armature|Walk_Loop") == "walk_loop"


@pytest.mark.blender
@needs_library
def test_a_character_job_carries_its_library_clips_in_every_file():
    sheet, assumed = ch.clean({**GUY, "clips": ["Idle_Loop", "Walk_Loop", "Punch_Jab"]})
    proj = db.create_project("clips", "a cool guy", options={"character": sheet, "assumed": assumed})
    out = asyncio.run(pipeline._run_character(proj["name"], sheet, assumed))
    folder = pipeline.version_dir(proj["name"], out["version"])
    assert out["ok"] and out["passed"], json.loads((folder / "checks.json").read_text(encoding="utf-8"))["gates"]
    for path in [folder / "exports" / f"{proj['name']}.glb", folder / "game" / f"{proj['name']}_LOD2.glb"]:
        data = path.read_bytes()
        gltf = json.loads(data[20:20 + int.from_bytes(data[12:16], "little")])
        assert sorted(a["name"] for a in gltf["animations"]) == ["idle_loop", "punch_jab", "walk_loop"], path.name
    record = json.loads((folder / "clips.json").read_text(encoding="utf-8"))
    assert [c["name"] for c in record["clips"]] == ["idle_loop", "punch_jab", "walk_loop"]
    assert (folder / "renders" / "sheet_clips.jpg").is_file()


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
        seen.setdefault("order", []).append(name)
        if name == "character_designer":
            seen["name"], seen["prompt"] = name, prompt
            tools[0].fn({"character": {"name": "nerd", "age": 22, "sex": "male", "build": "slim",
                                       "look": "glasses, a hoodie"}})
        elif name == "character_tailor":
            assert "hoodie" in prompt and "cardigan" in prompt  # the look and the garment list
            tools[0].fn({"outfit": [{"type": "hoodie", "colour": "#445566"}, {"type": "chinos", "colour": "#c2b08a"},
                                    {"type": "sneakers", "colour": "#333333"}],
                         "accessories": [{"type": "glasses", "shape": "square"}]})
        elif name == "character_hair":
            assert "spiky" in prompt and "chin-width-decr-incr" in prompt
            tools[0].fn({"hair": {"style": "short", "colour": "#3b2a1e"}, "face": {"chin-width-decr-incr": -0.3}})
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
    assert seen["order"][0] == "character_designer" and set(seen["order"][1:]) == {"character_tailor", "character_hair"}
    sheet = built["sheet"]
    assert sheet["build"] == "slim" and sheet["look"] == "glasses, a hoodie"
    assert [g["type"] for g in sheet["outfit"]] == ["hoodie", "chinos", "sneakers"]
    assert sheet["accessories"][0]["shape"] == "square" and sheet["face"] == {"chin-width-decr-incr": -0.3}
    assert not any(a.startswith(("outfit:", "hair:")) for a in built["assumed"])  # chosen, not defaulted


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
    sheets = [ch.clean({**raw, "clips": []})[0] for _, raw in AGES]  # clips are tested on their own
    results = []
    for sheet in sheets:
        result = blender.run_job([ch.build_step(sheet), ch.report_step()], timeout=600)
        assert result.ok, result.traceback
        results.append((sheet, result.outputs))
    for sheet, out in results:
        found = checks.character_checks(out["character"], out["rig_report"], sheet)
        assert found["passed"], (ch.who(sheet), {k: g for k, g in found["gates"].items() if not g["pass"]})
        names = out["rig_report"]["bone_names"]
        assert len([n for n in names if not n.startswith("spring_")]) == 53  # the game skeleton, plus any springs
        assert {"body", "eyes", "brows"} <= set(out["character"]["parts"])
    heights = [out["character"]["height"] for _, out in results]
    assert heights[0] < heights[1] < heights[2] and 0.55 < heights[0] < 0.75


@pytest.mark.blender
@pytest.mark.parametrize("style", ch.STYLES)
def test_every_style_builds_a_dressed_character_that_passes_every_check(style):
    raw = {**GUY, "clips": [], "style": style, "hair": {"style": "spiky" if "anime" in style else "short", "colour": "#1a1a22"},
           "outfit": [{"type": "tshirt", "colour": "#eeeeee"}, {"type": "jeans", "colour": "#2f3e5c"},
                      {"type": "sneakers", "colour": "#f0f0f0"}, {"type": "leather_jacket", "colour": "#1c1c1e"}],
           "accessories": [{"type": "glasses"}]}
    sheet = ch.clean(raw)[0]
    result = blender.run_job([ch.build_step(sheet), ch.report_step()], timeout=600)
    assert result.ok, result.traceback
    made, rig = result.outputs["character"], result.outputs["rig_report"]
    found = checks.character_checks(made, rig, sheet)
    assert found["passed"], {k: g for k, g in found["gates"].items() if not g["pass"]}
    assert "clothes_cover" in found["gates"]
    names = {n.split(".")[0] for n in made["parts"]}
    assert {"body", "tshirt", "jeans", "sneakers", "leather_jacket", "hair", "brows", "glasses"} <= names
    assert ("hair_spikes" in names) == ("anime" in style)


@pytest.mark.blender
def test_a_real_character_job_exports_a_skinned_animated_game_file():
    sheet, assumed = ch.clean({**GUY, "clips": []})  # with no library clips: the rig_test loop
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


@pytest.mark.blender
def test_a_skirt_coat_and_ponytail_swing_on_baked_spring_chains():
    raw = {**GUY, "clips": [], "sex": "female", "hair": {"style": "ponytail", "colour": "#5a3d2b"},
           "outfit": [{"type": "blouse", "colour": "#efe6d6"}, {"type": "skirt", "length": "long", "colour": "#7a2e3a"},
                      {"type": "boots", "colour": "#2b2018"}, {"type": "coat", "colour": "#55606e"}]}
    sheet, assumed = ch.clean(raw)
    proj = db.create_project("coat", "a woman in a coat", options={"character": sheet, "assumed": assumed})
    out = asyncio.run(pipeline._run_character(proj["name"], sheet, assumed))
    folder = pipeline.version_dir(proj["name"], out["version"])
    assert out["ok"] and out["passed"], json.loads((folder / "checks.json").read_text(encoding="utf-8"))["gates"]
    data = (folder / "exports" / f"{proj['name']}.glb").read_bytes()
    gltf = json.loads(data[20:20 + int.from_bytes(data[12:16], "little")])
    nodes = {n.get("name"): n for n in gltf["nodes"]}
    roots = [n for n in nodes if n.startswith("spring_") and n.endswith("_0") and "mg_spring" in (nodes[n].get("extras") or {})]
    assert any(n.startswith("spring_ponytail") for n in roots) and any(n.startswith("spring_coat_tails") for n in roots)
    assert not any(n.startswith("spring_skirt") for n in nodes)  # the skirt under the coat hangs from the coat's chains
    assert "mg_collider" in (nodes["thigh_l"].get("extras") or {})
    joints = {gltf["nodes"][j]["name"] for j in gltf["skins"][0]["joints"]}
    anim = gltf["animations"][0]
    animated = {gltf["nodes"][c["target"]["node"]]["name"] for c in anim["channels"]}
    assert {n for n in joints if n.startswith("spring_")} <= animated  # every chain bone carries baked keys


@pytest.mark.blender
def test_a_drawn_character_exports_cel_bands_and_outline_widths_on_every_material():
    sheet, assumed = ch.clean({**GUY, "clips": [], "style": "shonen_anime", "hair": {"style": "spiky", "colour": "#1a1a22"}})
    proj = db.create_project("hero", "an anime hero", options={"character": sheet, "assumed": assumed})
    out = asyncio.run(pipeline._run_character(proj["name"], sheet, assumed))
    assert out["ok"] and out["passed"]
    data = (pipeline.version_dir(proj["name"], out["version"]) / "game" / f"{proj['name']}_game.glb").read_bytes()
    gltf = json.loads(data[20:20 + int.from_bytes(data[12:16], "little")])
    for mat in gltf["materials"]:
        extras = mat.get("extras") or {}
        assert "mg_toon_colors" in extras and extras.get("mg_outline", 0) > 0, mat["name"]
    assert not any("outline" in m["name"] for m in gltf["meshes"])  # the render-only ink shells stay out
