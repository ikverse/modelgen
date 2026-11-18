"""Objects that are not cars: families and their rules, the triangle report, the casting and
research tools, the web tools only the researcher gets, the instant path from a ready-made model
(with Blender, the catalogue and the caster faked), ready-made parts in a plan, and the game-ready
export (needs Blender)."""

import asyncio
import json
from pathlib import Path

import pytest

from modelgen import config, db, families, pipeline, runner
from modelgen.tools import agent_tools as at
from modelgen.tools import asset_catalog, blender, checks

FIXTURES = Path(__file__).resolve().parent / "fixtures"


# --- families ---------------------------------------------------------------------------

@pytest.mark.parametrize("text, family", [
    ("yellow school bus", "vehicle"), ("bus station", "building"), ("ceramic coffee mug", "container"),
    ("Amsterdam canal house", "building"), ("small kids playground park", "scene"),
    ("retro handheld console", "device"), ("oak dining table", "furniture"), ("stylised hero figure", "character"),
    ("a sword", "general"),
])
def test_an_objects_name_points_to_its_family(text, family):
    assert families.guess(text) == family


def test_a_spec_keeps_the_family_it_names_and_falls_back_to_a_guess():
    assert families.of({"object": "coffee mug", "family": "device"}) == "device"
    assert families.of({"object": "coffee mug", "family": "nonsense"}) == "container"
    assert families.of({"object": "coffee mug"}) == "container"
    assert families.of({}) == "general"


def test_every_family_has_a_budget_and_its_rules_name_only_kit_functions_that_exist():
    kits = (config.BPY_LIB / "kits.py").read_text(encoding="utf-8")
    for name in families.NAMES:
        assert families.budget(name) > 1000
        for fn in families.kit(name):
            assert f"\ndef {fn}(" in kits, f"{name}: k.{fn} is not in kits.py"
    assert "k.vessel" in families.rules("container", "plan")
    assert families.rules("general", "plan") == "" and families.rules("container", "nothing") == ""
    assert families.views("building") and families.views("vehicle")
    assert not families.studio("vehicle") and not families.studio("scene")  # a long vehicle washes out in the car studio


def test_the_kit_reference_shows_only_the_familys_functions():
    text = at.kits_reference("container")
    assert "k.vessel(" in text and "k.handle(" in text and "k.walls(" not in text
    assert at.kits_reference("furniture") == "" and at.kits_reference("general") == ""


def test_family_rules_and_the_triangle_limit_reach_the_architect_and_the_builder():
    spec = {"object": "mug", "family": "container", "use": "game", "dimensions": {}}
    prompt = pipeline._architect_prompt(spec)
    assert "This is a container" in prompt and "k.vessel(" in prompt and "at most about 5000 triangles" in prompt
    render = pipeline._architect_prompt({**spec, "use": "render"})
    assert "This is a container" in render and "triangles for the whole model" not in render


# --- checks -----------------------------------------------------------------------------

def _measure(parts, floaters=()):
    return {"bbox": {"size": [1, 1, 1]}, "min_z": 0.0, "parts": parts, "floaters": list(floaters), "overlaps": []}


def test_a_game_asset_gets_its_triangles_against_its_familys_limit():
    parts = [{"object": "a", "part": "a", "triangles": 4000, "non_manifold_edges": 0},
             {"object": "b", "part": "b", "triangles": 3000, "non_manifold_edges": 0}]
    spec = {"object": "mug", "use": "game", "dimensions": {"width": 1, "depth": 1, "height": 1}}
    found = checks.evaluate(_measure(parts), spec)
    assert found["triangles"] == 7000 and found["triangle_budget"] == 5000 and found["over_budget"] is True
    assert found["passed"] and not found["warnings"]  # over the limit is told to the critic, not a failed gate
    render = checks.evaluate(_measure(parts), {**spec, "use": "render"})
    assert render["triangles"] == 7000 and "triangle_budget" not in render


def test_a_ready_made_part_is_not_held_to_watertight_or_floaters():
    parts = [{"object": "rock", "part": "rock", "triangles": 10, "non_manifold_edges": 50, "asset": True},
             {"object": "leg", "part": "leg", "triangles": 10, "non_manifold_edges": 0}]
    floaters = [{"object": "rock", "part": "rock", "island": 3, "center": [0, 0, 1]}]
    found = checks.evaluate(_measure(parts, floaters), {"dimensions": {"width": 1, "depth": 1, "height": 1}})
    assert found["gates"]["watertight"]["pass"] and found["gates"]["no_floaters"]["pass"]
    parts[1]["non_manifold_edges"] = 2
    floaters.append({"object": "leg", "part": "leg", "island": 0, "center": [0, 0, 1]})
    found = checks.evaluate(_measure(parts, floaters), {"dimensions": {"width": 1, "depth": 1, "height": 1}})
    assert not found["gates"]["watertight"]["pass"] and not found["gates"]["no_floaters"]["pass"]


# --- the caster, the researcher and the web ---------------------------------------------

def test_the_caster_chooses_a_listed_model_or_none():
    sink = {}
    (choose,) = at.caster_tools(sink, ["polyhaven/a", "polyhaven/b"])
    assert "unknown id" in choose.fn({"asset": "polyhaven/zzz"})["error"]
    assert choose.fn({"asset": "none"})["ok"] and sink["asset"] is None
    assert choose.fn({"asset": "polyhaven/b"})["ok"] and sink["asset"] == "polyhaven/b"


def test_research_needs_a_real_size_and_features():
    sink = {}
    _save, submit = at.researcher_tools(sink)
    assert "dimensions" in submit.fn({"research": {"object": "bus", "features": ["door"]}})["error"]
    dims = {"width": 2.5, "depth": 12.0, "height": 3.1}
    assert "features" in submit.fn({"research": {"object": "bus", "dimensions": dims}})["error"]
    full = {"research": {"object": "bus", "dimensions": dims, "features": ["two doors"]}}
    assert "reference photo" in submit.fn(full)["error"]  # asked once to try for a picture
    assert submit.fn(full)["ok"]
    assert sink["research"]["dimensions"] == dims


@pytest.mark.parametrize("url", ["file:///c:/windows/win.ini", "http://localhost/a.png", "http://127.0.0.1/a.png",
                                 "ftp://example.com/a.png", "http://printer.local/a.png", "not a url"])
def test_a_reference_image_only_comes_from_a_named_public_web_address(url):
    sink = {}
    save, _submit = at.researcher_tools(sink)
    out = asyncio.run(save.fn({"url": url}))
    assert out["ok"] is False and not sink.get("images")


def test_only_the_researcher_gets_the_web_tools():
    echo = runner.AgentTool("echo", "", {"type": "object", "properties": {}}, lambda a: {})
    web = runner.build_options(runner.load_agent("researcher"), [echo])
    assert web.builtin_tools == ["WebSearch", "WebFetch"] and [t.name for t in web.tools] == ["echo"]
    for name in ("brief", "architect", "builder", "critic", "caster", "director"):
        plain = runner.build_options(runner.load_agent(name), [echo])
        assert plain.builtin_tools == [] and [t.name for t in plain.tools] == ["echo"]


def test_research_reaches_the_spec_and_a_failed_research_does_not_stop_the_brief(monkeypatch):
    db.create_project("bus", "a school bus")
    seen = []

    async def fake_run_agent(agent, prompt, tools, **kw):
        seen.append((agent, prompt))
        by = {t.name: t for t in tools}
        if agent == "researcher":
            for _ in range(3):  # sent back once to try for a picture, once more for a vehicle's side view
                by["submit_research"].fn({"research": {"object": "bus", "features": ["stop arm 0.45 m"],
                                                       "dimensions": {"width": 2.4, "depth": 11.0, "height": 3.2}}})
        else:
            by["submit_spec"].fn({"spec": {"object": "school bus", "category": "general", "parts": [{"name": "body"}],
                                           "dimensions": {"width": 2.4, "depth": 11.0, "height": 3.2}}})
        return runner.RunResult(agent=agent)

    monkeypatch.setattr(pipeline.runner, "run_agent", fake_run_agent)
    spec = asyncio.run(pipeline._brief("bus", "a school bus"))
    assert [a for a, _ in seen] == ["researcher", "brief"]
    assert "stop arm 0.45 m" in seen[1][1] and "Object families" in seen[1][1]
    assert spec["family"] == "vehicle" and spec["research"]["features"] == ["stop arm 0.45 m"]

    async def silent_researcher(agent, prompt, tools, **kw):
        if agent == "researcher":
            return runner.RunResult(agent=agent, ok=False, error="limit hit: error_max_turns")
        return await fake_run_agent(agent, prompt, tools, **kw)

    monkeypatch.setattr(pipeline.runner, "run_agent", silent_researcher)
    spec = asyncio.run(pipeline._brief("bus", "a school bus"))
    assert spec["family"] == "vehicle" and "research" not in spec


# --- the instant path -------------------------------------------------------------------

ENTRY = {"id": "polyhaven/Barrel_01", "library": "polyhaven", "name": "Barrel 01", "categories": ["props"],
         "tags": ["barrel"], "style": "realistic", "size": {"width": 0.56, "depth": 0.56, "height": 0.88}}
OTHER = {**ENTRY, "id": "polyhaven/barrel_03", "name": "Barrel 03"}


@pytest.fixture
def instant(monkeypatch, tmp_path):
    """The instant path with the catalogue, the caster, Blender and the renderer faked."""
    catalog = tmp_path / "catalog.json"
    catalog.write_text(json.dumps([ENTRY, OTHER]), encoding="utf-8")
    monkeypatch.setattr(asset_catalog, "CATALOG", catalog)
    monkeypatch.setattr(asset_catalog, "search",
                        lambda text, limit=5, entries=None: [{"entry": ENTRY, "score": 5, "why": ""},
                                                             {"entry": OTHER, "score": 4, "why": ""}])
    made = []

    def fake_make(plan, out_glb, blend_path=None, entries=None):
        Path(out_glb).write_bytes(b"glb")
        Path(blend_path).write_bytes(b"blend")
        made.append(plan)
        return {**plan, "after": {"width": 0.56, "length": 0.56, "height": 0.88, "ground": 0.0, "triangles": 2682}}

    async def fake_cast(project, request, hits):
        return [h["entry"]["id"] for h in hits]

    async def fake_render(blend, out_dir, **kw):
        return blender.BlenderResult(ok=True), {}, {"turntable": "t.jpg"}

    async def fake_game(folder, project, spec):
        return {"triangles": {"built": 2682, "game": 2682}, "budget": families.budget(families.of(spec))}

    monkeypatch.setattr(asset_catalog, "make", fake_make)
    monkeypatch.setattr(pipeline, "_cast", fake_cast)
    monkeypatch.setattr(pipeline.render, "render_version", fake_render)
    monkeypatch.setattr(pipeline, "_game_export", fake_game)
    monkeypatch.setattr(pipeline.blender, "available", lambda: True)
    return made


def test_a_request_the_catalogue_has_is_made_from_a_ready_made_model(instant):
    name = db.create_project("barrel", "a rusty barrel")["name"]
    out = asyncio.run(pipeline.run(name, request="a rusty barrel"))
    assert out["ok"] and out["library"] and out["version"] == 1 and out["passed"]
    plan = pipeline.library_asset(name, 1)
    assert plan["asset"]["asset"] == "polyhaven/Barrel_01" and plan["candidates"][1] == "polyhaven/barrel_03"
    assert pipeline.ready_made(name, 1) and pipeline.open_changes(name) == 1
    assert pipeline.next_changes(name)["items"][0]["short"] == "Another ready-made model"
    assert "ready-made" in db.messages()[-1]["text"]  # the studio reports it itself, with no director run
    assert not [r for r in db.runs() if r["agent"] in ("brief", "architect", "builder", "critic")]

    out = asyncio.run(pipeline.run(name, changes="make it green"))  # a colour: the same model, recoloured
    assert out["library"] and instant[-1]["asset"] == "polyhaven/Barrel_01" and instant[-1]["colour"]

    out = asyncio.run(pipeline.run(name, next_version=True))        # next version: the next candidate
    assert out["library"] and instant[-1]["asset"] == "polyhaven/barrel_03"


def test_the_team_builds_it_when_nothing_fits_or_the_person_wants_an_original(instant, monkeypatch):
    name = db.create_project("barrel", "a barrel")["name"]
    assert asyncio.run(pipeline._try_library(name, "a barrel, built from scratch by the team", None, False)) is None
    assert asyncio.run(pipeline._try_library(name, "a red sports car", None, False)) is None

    async def nobody(project, request, hits):
        return []

    monkeypatch.setattr(pipeline, "_cast", nobody)
    assert asyncio.run(pipeline._try_library(name, "a barrel", None, False)) is None
    monkeypatch.setattr(asset_catalog, "search", lambda *a, **k: [])
    assert asyncio.run(pipeline._try_library(name, "a barrel", None, False)) is None
    assert not db.versions(name)


def test_an_architect_may_only_name_a_ready_made_part_the_catalogue_has(instant):
    sink = {}
    tools = {t.name: t for t in at.architect_tools(sink, assets=True)}
    assert "search_assets" in tools and "search_assets" not in {t.name for t in at.architect_tools({})}
    part = {"name": "barrel", "method": "asset", "dimensions": {"height": 0.9}}
    out = tools["submit_plan"].fn({"plan": {"parts": [{**part, "asset": "polyhaven/nope"}]}})
    assert "not in the catalogue" in out["error"]
    assert tools["submit_plan"].fn({"plan": {"parts": [{**part, "asset": "polyhaven/Barrel_01"}]}})["ok"]
    assert pipeline.asset_parts(sink["plan"]) == ["barrel"]


# --- the game-ready export (Blender) -----------------------------------------------------

@pytest.mark.blender
def test_the_game_export_keeps_to_the_limit_and_writes_lower_detail_versions(tmp_path):
    file = FIXTURES / "kit_container.py"
    blend = tmp_path / "model.blend"
    steps = [blender.part_step(p, file, func=f"build_{p}") for p in ("mug", "bowl", "vase")]
    steps += [blender.assembly_step(file), blender.materials_step(file), {"kind": "save", "path": str(blend)}]
    built = blender.run_job(steps, timeout=300)
    assert built.ok, built.traceback or built.stdout[-2000:]
    result = blender.run_job([blender.game_step(tmp_path / "game", "cups", 3000, (0.5, 0.2))], open_blend=blend)
    assert result.ok, result.traceback or result.stdout[-2000:]
    game = result.outputs["game"]
    tris = game["triangles"]
    assert tris["built"] > 3000 and tris["game"] <= 3100 and game["reduced"]
    assert tris["LOD1"] <= tris["game"] * 0.55 and tris["LOD2"] <= tris["game"] * 0.25
    for key in ("game", "LOD1", "LOD2", "collision"):
        assert Path(game["files"][key]).stat().st_size > 500
    assert abs(game["size"][2] - 0.212) < 0.01  # real-world scale kept


# --- what holds a model to its quality: the critic, the builder, the architect ------------

def _round(tmp_path, spec=None, parts=("body", "handle")):
    from modelgen.tools.files import ProjectFiles
    files = ProjectFiles(tmp_path / "v1")
    plan = {"parts": [{"name": n, "method": "revolve", "dimensions": {"width": 0.1 * (i + 1), "depth": 0.1,
                                                                      "height": 0.1}} for i, n in enumerate(parts)]}
    return pipeline.Round(project="mug", number=1, dir=files.root, files=files,
                          spec=spec or {"object": "mug", "use": "game"}, plan=plan)


def test_a_game_asset_is_scored_by_the_game_rubric_and_a_render_by_the_old_one():
    game = at.load_rubric(None, {"use": "game"})
    plain = at.load_rubric(None, {"use": "render"})
    assert {c["id"] for c in game["criteria"]} == {c["id"] for c in plain["criteria"]}  # scores stay comparable
    assert "product photo" in json.dumps(plain)
    assert "product photo" not in " ".join(c["guide"] for c in game["criteria"])
    assert at.rubric_file({"kind": "shell", "template": "car"}, {"use": "game"}) == config.SHELL_RUBRICS["car"]
    weights = {c["id"]: c["weight"] for c in game["criteria"]}
    assert weights["proportions"] > weights["materials"]


def test_the_critic_can_send_a_change_to_the_plan_and_must_check_the_researchs_proportions(tmp_path):
    spec = {"object": "mug", "use": "game", "research": {"proportions": ["handle 14 x 10 mm", "wall 4 mm"]}}
    rnd = _round(tmp_path, spec)
    tools = {t.name: t for t in at.critic_tools(rnd, {"turntable": "t.jpg", "turntable_large": ["a.jpg", "b.jpg"]})}
    assert tools["view_renders"].fn({})["_images"] == ["a.jpg", "b.jpg"]  # the large views replace the small sheet
    scores = {"silhouette": 7, "parts": 5, "proportions": 7, "materials": 7, "surface": 7}
    change = [{"target": "plan", "change": "add a saucer part 150 mm across under the mug", "short": "Add a saucer",
               "criterion": "parts"}]
    out = tools["submit_review"].fn({"scores": scores, "summary": "s", "changes": change})
    assert "accuracy" in out["error"] and out["proportions"] == {1: "handle 14 x 10 mm", 2: "wall 4 mm"}
    accuracy = [{"item": 1, "met": True, "measured": "14.1 x 10 mm"}, {"item": 2, "met": False, "measured": "6 mm"}]
    out = tools["submit_review"].fn({"scores": scores, "summary": "s", "changes": change, "accuracy": accuracy})
    assert out["ok"] and rnd.sink["review"]["accuracy"] == accuracy
    notes = pipeline._route_changes(rnd.sink["review"], {"checks": {"gates": {}}, "measure": {}}, ["body", "handle"])
    assert notes["plan"] == ["add a saucer part 150 mm across under the mug"]
    assert pipeline._role_of("plan") == "architect"


def test_a_plan_change_goes_to_the_architect_before_the_builders(monkeypatch):
    plan = {"parts": [{"name": "body"}]}
    new = {"parts": [{"name": "body"}, {"name": "saucer"}]}
    asked = {}

    async def fake_change(project, spec, old, text, library):
        asked["text"] = text
        return new, {"saucer"}

    monkeypatch.setattr(pipeline, "_architect_change", fake_change)
    notes = {"plan": ["add a saucer"], "body": ["taller"]}
    got, rebuild = asyncio.run(pipeline._plan_changes("mug", {}, plan, notes, {"body"}, None))
    assert got is new and rebuild == {"body", "saucer"} and "add a saucer" in asked["text"]
    assert "plan" not in notes and notes["assembly"] and notes["materials"]
    same, kept = asyncio.run(pipeline._plan_changes("mug", {}, plan, {"body": ["x"]}, {"body"}, None))
    assert same is plan and kept == {"body"}


def test_a_builder_is_held_to_three_percent_a_second_attempt_and_the_references(tmp_path):
    measure = {"parts": [{"size": [0.1, 0.1, 0.0965], "faces": 10, "islands": 1, "non_manifold_edges": 0}]}
    report = checks.part_report(measure, {"width": 0.1, "depth": 0.1, "height": 0.1})
    assert report["size_vs_plan"].startswith("DOES NOT MATCH")  # 3.5 percent short used to pass
    near = checks.part_report(measure, {"width": 0.1, "depth": 0.1, "height": 0.098})
    assert near["size_vs_plan"].startswith("matches")

    spec = {"object": "mug", "use": "game", "research": {"features": ["foot ring 3 mm"]}}
    rnd = _round(tmp_path, spec)
    rnd.sink["refs_offered"] = {"body"}
    finish = {t.name: t for t in at.builder_tools(rnd, "body")}["finish"]
    rnd.attempts["body"] = 1
    assert "view_reference" in finish.fn({"notes": "done"})["error"]          # every time, until it has looked
    rnd.sink["refs_seen"] = {"body"}
    assert "research_check" in finish.fn({"notes": "done"})["error"]          # asked for once
    check = "foot ring 3 mm: built, measures 3 mm high and 7 mm wide"
    assert "built this part once" in finish.fn({"notes": "done", "research_check": check})["error"]
    rnd.attempts["body"] = 2
    assert finish.fn({"notes": "done", "research_check": check})["ok"]


def test_the_architect_must_give_every_research_feature_a_part():
    sink = {}
    submit = {t.name: t for t in at.architect_tools(sink, features=["rim", "foot ring"])}["submit_plan"]
    plan = {"parts": [{"name": "body", "method": "k.vessel", "dimensions": {}}],
            "research_features": [{"feature": 1, "part": "body"}, {"feature": 2, "part": "nowhere"}]}
    out = submit.fn({"plan": plan})
    assert out["features"] == {2: "foot ring"} and "plan" not in sink
    plan["research_features"][1]["part"] = "body"
    assert submit.fn({"plan": plan})["ok"] and sink["plan"] is plan


def test_the_main_part_goes_to_the_lead_builder_and_a_scene_has_none(tmp_path, monkeypatch):
    rnd = _round(tmp_path, parts=("handle", "body"))  # body is the larger
    assert pipeline._lead_part(rnd.plan, rnd.spec) == "body"
    assert pipeline._lead_part(rnd.plan, {"object": "park", "family": "scene"}) is None
    calls = {}

    async def fake_run_agent(agent, prompt, tools, **kw):
        calls[kw["doing"].split()[-1]] = kw
        rnd.part_ok[kw["doing"].split()[-1]] = True

    monkeypatch.setattr(pipeline.runner, "run_agent", fake_run_agent)
    asyncio.run(pipeline._build_parts(rnd, ["body", "handle"], {}, None, None))
    assert calls["body"]["model"] == config.get("loop", "lead_builder_model")
    assert calls["body"]["caps"] == config.caps("lead_builder") and "model" not in calls["handle"]


def test_a_game_assets_parts_have_triangle_shares_that_fit_and_a_builder_keeps_to_its_share(tmp_path):
    sink = {}
    submit = {t.name: t for t in at.architect_tools(sink, budget=5000)}["submit_plan"]
    part = {"name": "body", "method": "k.vessel", "dimensions": {}}
    assert "no `triangles` share" in submit.fn({"plan": {"parts": [dict(part)]}})["error"]
    sink.clear()
    assert "over the model's limit" in submit.fn({"plan": {"parts": [{**part, "triangles": 9000}]}})["error"]
    assert submit.fn({"plan": {"parts": [{**part, "triangles": 9000}]}})["ok"]  # asked once, then accepted
    sink.clear()
    assert submit.fn({"plan": {"parts": [{**part, "triangles": 4000}]}})["ok"]

    rnd = _round(tmp_path)
    rnd.plan["parts"][0].update({"triangles": 600, "count": 4})
    rnd.files.write("parts/body.py", "def build():\n    pass\n")  # a part being changed: no second-attempt rule
    finish = {t.name: t for t in at.builder_tools(rnd, "body")}["finish"]
    rnd.part_reports["body"] = {"triangles": 400}                   # four copies: 1600 against a share of 600
    out = finish.fn({"notes": "done"})
    assert "1600 triangles over its 4 copies" in out["error"] and "share of the model's limit is 600" in out["error"]
    rnd.part_reports["body"] = {"triangles": 200}                   # 800: within half over the share
    assert finish.fn({"notes": "done"})["ok"]


def test_a_proportion_names_its_source_and_a_guess_is_kept_apart(monkeypatch):
    from modelgen import uploads
    monkeypatch.setattr(at, "_fetch_image", lambda url: "data:image/png;base64,AAAA")
    monkeypatch.setattr(uploads, "save", lambda data: {"id": "0123456789abcdef.jpg", "url": "/uploads/x"})
    monkeypatch.setattr(uploads, "path", lambda upload_id: None)
    dims = {"width": 2.4, "depth": 11.5, "height": 3.2}
    sink = {}
    save, submit = at.researcher_tools(sink, side_view=True)
    assert asyncio.run(save.fn({"url": "https://example.org/bus_side.jpg"}))["ok"]

    def research(proportions, features=("stop arm on the left side",), unverified=()):
        return {"research": {"object": "bus", "dimensions": dims, "features": list(features),
                             "proportions": proportions, "unverified": list(unverified)}}

    out = submit.fn(research(["hood about 20% of overall length"]))            # a bare sentence has no source
    assert "where their number comes from" in out["error"]
    measured = [{"text": "hood ends at 14% of the length (1.6 m)", "basis": "photo", "source": "photo 1"},
                {"text": "front axle at 9% of the length", "basis": "photo", "source": "photo 1"},
                {"text": "hood top at 45% of the height", "basis": "photo", "source": "photo 1"},
                {"text": "width 2.44 m", "basis": "published", "source": "maker's sheet"}]
    out = submit.fn(research(measured, features=["hood about 2.3 m long (typical)"]))
    assert out["guessed"] == ["hood about 2.3 m long (typical)"]              # a guessed size in a feature: sent back once
    out = submit.fn(research(measured, features=["protruding hood"], unverified=["hood height, guess: 1.4 m"]))
    assert out["ok"]
    got = sink["research"]
    assert got["proportions"][0] == "hood ends at 14% of the length (1.6 m) [measured on a kept photo: photo 1]"
    assert got["proportions"][3] == "width 2.44 m [published: maker's sheet]"
    assert got["unverified"] == ["hood height, guess: 1.4 m"]

    sink = {}
    _save, submit = at.researcher_tools(sink, side_view=True)                 # no photo kept this time
    published = [{"text": "width 2.44 m", "basis": "published"}]
    assert "reference photo" in submit.fn(research(published))["error"]        # asked once for any picture
    assert "side view" in submit.fn(research(published))["error"]              # a vehicle: asked once for a side view
    photo = [{"text": "hood ends at 14%", "basis": "photo"}]
    assert "no photo is kept" in submit.fn(research(photo))["error"]           # cannot claim a photo it has not kept
    assert submit.fn(research(published, unverified=["hood length, guess: 1.6 m"]))["ok"]


def test_guesses_never_reach_the_spec_as_facts_and_the_critic_is_told_they_are_guesses(tmp_path):
    import inspect
    source = inspect.getsource(pipeline._brief)
    assert '"unverified"' in source and "never copy one into the spec" in source
    spec = {"object": "bus", "use": "game", "family": "vehicle",
            "research": {"proportions": ["hood ends at 14% [measured on a kept photo]"],
                         "unverified": ["hood height, guess: 1.4 m"]}}
    rnd = _round(tmp_path, spec)
    seen = {}

    async def fake_run_agent(agent, prompt, tools, **kw):
        seen["prompt"] = prompt
        rnd.sink["review"] = {"scores": {}, "summary": "", "changes": [], "score": 0}

    import modelgen.pipeline as pl
    old = pl.runner.run_agent
    pl.runner.run_agent = fake_run_agent
    try:
        asyncio.run(pipeline._critic(rnd, {"checks": {"gates": {}}, "sheets": {}}, None))
    finally:
        pl.runner.run_agent = old
    assert "Guessed, not known" in seen["prompt"] and "hood height, guess: 1.4 m" in seen["prompt"]
    assert "1. hood ends at 14% [measured on a kept photo]" in seen["prompt"]

