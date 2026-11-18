"""Tools that work without Blender: project files, the library, the rubric, scoring and routing."""

import asyncio
import json
from pathlib import Path

import pytest
from PIL import Image

from modelgen import config, pipeline
from modelgen.tools import agent_tools as at
from modelgen.tools import checks, render
from modelgen.tools.files import OutsideProject, ProjectFiles
from modelgen.tools.library import Library


def test_project_files_refuse_paths_outside(tmp_path):
    files = ProjectFiles(tmp_path / "proj")
    files.write("parts/leg.py", "x = 1\n")
    assert files.read("parts/leg.py") == "x = 1\n"
    assert files.list() == ["parts/leg.py"]
    for bad in ("../escape.py", "parts/../../escape.py", "C:/Windows/x.py", "/etc/passwd", "", "..\\x.py"):
        with pytest.raises(OutsideProject):
            files.path(bad)


def test_library_add_search_get(tmp_path):
    lib = Library(tmp_path / "library")
    lib.add_part("Tapered Round Leg", "turned wooden leg, 0.43 m", "def build(): pass\n", ["leg", "wood"], "chair v2")
    lib.add_part("c_handle", "C-shaped mug handle", "def build(): pass\n", ["handle"])
    lib.add_material("light oak", "pale oak", "def recipe(): pass\n")
    assert lib.search("wooden leg")[0]["name"] == "tapered_round_leg"
    assert lib.search("handle", kind="part")[0]["name"] == "c_handle"
    assert lib.get("tapered_round_leg") == "def build(): pass\n"
    assert {e["name"] for e in lib.list("material")} == {"light_oak"}
    lib.add_part("c_handle", "better handle", "def build(): return 1\n")
    entry = next(e for e in lib.list("part") if e["name"] == "c_handle")
    assert entry["revisions"] == 2 and entry["description"] == "better handle"
    with pytest.raises(KeyError):
        lib.get("missing")


def test_library_keeps_scores_and_offers_the_best_close_parts_as_examples(tmp_path):
    from modelgen import db
    names = []
    for title, score in (("Old Mug", 40.0), ("Good Mug", 75.0)):
        names.append(db.create_project(title, "a mug")["name"])
        db.add_version(names[-1], 1)
        db.update_version(names[-1], 1, score=score)
    old, good = names
    lib = Library(tmp_path / "library")
    lib.add_part("c_handle_plain", "C-shaped mug handle", "PLAIN = 1\n", ["handle"], f"{old} v1 part handle")
    lib.add_part("c_handle_fine", "C-shaped mug handle, rolled edges", "FINE = 1\n", ["handle"], f"{good} v1 part handle")
    lib.add_part("pot_lid", "lid; the handle sits beside it", "LID = 1\n", ["lid"], f"{good} v1 part lid")
    assert next(e for e in lib.list("part") if e["name"] == "c_handle_fine")["score"] == 75.0
    picks = lib.examples("handle mug handle")
    assert [p["name"] for p in picks] == ["c_handle_fine", "c_handle_plain"]  # best model first; the lid only mentions it
    assert picks[0]["code"] == "FINE = 1\n" and picks[0]["score"] == 75.0
    assert [p["name"] for p in lib.examples("handle", exclude=["c_handle_fine"], limit=1)] == ["c_handle_plain"]
    assert lib.examples("propeller") == []


def test_a_builder_gets_worked_examples_from_the_library(tmp_path, monkeypatch):
    rnd = make_round(tmp_path)
    lib = Library(tmp_path / "lib")
    lib.add_part("c_handle_round_section", "C-shaped mug handle", "EXAMPLE_HANDLE = 1\n", ["handle"])
    prompts = {}

    async def fake_run_agent(agent, prompt, tools, **kw):
        prompts[kw["doing"].split()[-1]] = prompt
        rnd.part_ok[kw["doing"].split()[-1]] = True

    monkeypatch.setattr(pipeline.runner, "run_agent", fake_run_agent)
    asyncio.run(pipeline._build_parts(rnd, ["handle", "body"], {}, lib, None))
    assert "Worked examples from the library" in prompts["handle"] and "EXAMPLE_HANDLE = 1" in prompts["handle"]
    assert "Worked examples" not in prompts["body"]


def test_rubric_and_score():
    rubric = at.load_rubric()
    assert [c["id"] for c in rubric["criteria"]] == ["silhouette", "parts", "proportions", "materials", "surface"]
    assert [g["id"] for g in rubric["gates"]] == ["watertight", "no_floaters", "correct_size", "on_floor"]
    assert at.score({c["id"]: 10 for c in rubric["criteria"]}) == 100.0
    assert at.score({"silhouette": 10}) == 30.0  # weight 3 of 10


def measure(**over):
    base = {
        "bbox": {"min": [-0.06, -0.042, 0.0], "max": [0.06, 0.042, 0.095], "size": [0.12, 0.084, 0.095]},
        "min_z": 0.0,
        "parts": [{"object": "body", "part": "body", "non_manifold_edges": 0, "islands": 1, "faces": 900,
                   "size": [0.084, 0.084, 0.095]}],
        "floaters": [], "overlaps": [],
    }
    return {**base, **over}


SPEC = {"dimensions": {"width": 0.12, "depth": 0.084, "height": 0.095}}


def test_checks_pass_and_each_gate_fails():
    assert checks.evaluate(measure(), SPEC)["passed"]
    turned = measure(bbox={"min": [0, 0, 0], "max": [0, 0, 0], "size": [0.084, 0.12, 0.095]})
    assert checks.evaluate(turned, SPEC)["gates"]["correct_size"]["pass"]
    tall = measure(bbox={"min": [0, 0, 0], "max": [0, 0, 0], "size": [0.12, 0.084, 0.2]})
    assert not checks.evaluate(tall, SPEC)["gates"]["correct_size"]["pass"]
    open_part = measure(parts=[{"object": "body", "part": "body", "non_manifold_edges": 12}])
    assert "body (12 edges)" in checks.evaluate(open_part, SPEC)["gates"]["watertight"]["detail"]
    floating = measure(floaters=[{"object": "handle", "island": 0, "center": [0.07, 0, 0.05]}])
    assert not checks.evaluate(floating, SPEC)["gates"]["no_floaters"]["pass"]
    assert not checks.evaluate(measure(min_z=0.01), SPEC)["gates"]["on_floor"]["pass"]
    overlap = measure(overlaps=[{"a": "body", "b": "handle", "attached": False, "pairs": 40}])
    result = checks.evaluate(overlap, SPEC)
    assert result["passed"] and "body and handle" in result["warnings"][0]
    assert not checks.evaluate({"empty": True, "parts": []}, SPEC)["passed"]


def test_part_report_compares_with_the_plan():
    report = checks.part_report(measure(), {"width": 0.084, "depth": 0.084, "height": 0.095})
    assert report["size_vs_plan"].startswith("matches") and report["separate_pieces"] == 1
    report = checks.part_report(measure(), {"width": 0.2, "depth": 0.084, "height": 0.095})
    assert report["size_vs_plan"].startswith("DOES NOT MATCH")


def test_library_references_list_the_helpers_and_recipes():
    ref = at.helpers_reference()
    for fn in ("h.revolve(", "h.sweep(", "h.loft(", "h.boolean(", "h.mirror(", "h.array(", "h.subdivide(",
               "h.displace(", "h.bevel(", "h.place(", "h.attach("):
        assert fn in ref
    assert "h.finalize_part(" not in ref
    mats = at.materials_reference()
    for fn in ("wood", "brushed_metal", "polished_metal", "glass", "ceramic", "leather", "fabric", "painted"):
        assert f"m.{fn}(" in mats


def test_contact_sheet(tmp_path):
    paths = []
    for i in range(3):
        p = tmp_path / f"{i}.png"
        Image.new("RGB", (64, 64), (i * 80, 100, 100)).save(p)
        paths.append(p)
    sheet = render.contact_sheet(paths, tmp_path / "s.jpg", columns=2, tile=64, labels=["a", "b", "c"])
    with Image.open(sheet) as im:
        assert im.size == (128, 128)
    assert render.contact_sheet([tmp_path / "none.png"], tmp_path / "x.jpg") is None


def make_round(tmp_path):
    files = ProjectFiles(tmp_path / "v1")
    plan = {"parts": [{"name": "body", "method": "revolve", "dimensions": {}},
                      {"name": "handle", "method": "sweep", "dimensions": {}}]}
    return pipeline.Round(project="mug", number=1, dir=files.root, files=files, spec=SPEC, plan=plan)


def test_critic_review_is_validated_and_scored(tmp_path):
    rnd = make_round(tmp_path)
    view, _measure, _inspect, submit = at.critic_tools(rnd, {"turntable": "t.jpg"})
    assert view.fn({})["_images"] == ["t.jpg"]
    item = submit.schema["properties"]["changes"]["items"]
    assert "short" in item["properties"] and "short" in item["required"]  # plain words for the person
    assert "missing" in submit.fn({"scores": {"silhouette": 5}})["error"]
    scores = {"silhouette": 8, "parts": 7, "proportions": 8, "materials": 6, "surface": 7}
    bad = submit.fn({"scores": scores, "changes": [{"target": "lid", "change": "x"}]})
    assert "unknown targets" in bad["error"]
    ok = submit.fn({"scores": scores, "summary": "s", "changes": [{"target": "handle", "parameter": "radius",
                                                                    "change": "thicker", "criterion": "proportions"},
                                                                   {"target": "materials", "change": "matte glaze",
                                                                    "criterion": "materials"}]})
    assert ok["ok"] and rnd.sink["review"]["score"] == at.score(scores)


def test_critic_lists_at_most_seven_changes_and_marks_the_previous_ones(tmp_path):
    rnd = make_round(tmp_path)
    previous = [{"target": "handle", "parameter": "radius", "change": "thicker"},
                {"target": "materials", "change": "matte glaze"}]
    prev_sheet = tmp_path / "prev.jpg"
    Image.new("RGB", (8, 8)).save(prev_sheet)
    view, _measure, _inspect, submit = at.critic_tools(rnd, {"turntable": "t.jpg"}, prev_sheet, previous)
    assert view.fn({})["_images"] == ["t.jpg", str(prev_sheet)]
    scores = {"silhouette": 6, "parts": 6, "proportions": 6, "materials": 6, "surface": 6}
    eight = [{"target": "body", "change": str(i)} for i in range(8)]
    assert "at most 7" in submit.fn({"scores": scores, "summary": "s", "changes": eight, "landed": []})["error"]
    one = [{"target": "body", "change": "taller", "criterion": "silhouette"}]
    assert "missing numbers [2]" in submit.fn({"scores": scores, "summary": "s", "changes": one,
                                               "landed": [{"change": 1, "landed": True}]})["error"]
    landed = [{"change": 1, "landed": True}, {"change": 2, "landed": False}]
    out = submit.fn({"scores": scores, "summary": "s", "changes": one, "landed": landed})
    assert "did not land" in out["error"] and 2 in out["not_landed"]
    again = [{"target": "materials", "change": "matte glaze (not landed)"}] + one
    assert submit.fn({"scores": scores, "summary": "s", "changes": again, "landed": landed})["ok"]
    assert rnd.sink["review"]["landed"] == landed


def test_builder_finish_answers_every_note_and_refuses_open_edges_once(tmp_path):
    rnd = make_round(tmp_path)
    rnd.notes = {"body": ["wall 4 mm", "rim rounder"]}
    finish = {t.name: t for t in at.builder_tools(rnd, "body", Library(tmp_path / "lib"))}["finish"]
    rnd.attempts["body"] = 1  # built since the notes came
    assert "missing: [2]" in finish.fn({"notes": "x", "notes_done": [{"note": 1, "done": True}]})["error"]
    half = [{"note": 1, "done": True, "how": "the wall measures 4 mm in the part views"},
            {"note": 2, "done": False, "how": "the profile has no rim"}]
    assert "not done" in finish.fn({"notes": "x", "notes_done": half})["error"]
    rnd.part_reports["body"] = {"non_manifold_edges": 4, "size_vs_plan": "DOES NOT MATCH: model 0.2 x 0.2 x 0.2 m"}
    out = finish.fn({"notes": "x", "notes_done": half})  # the note is answered; the open edges refuse once
    assert "4 open" in out["error"] and "size model 0.2" in out["error"]
    assert not finish.fn({"notes": "x", "notes_done": half})["ok"]  # a second try without a reason
    assert finish.fn({"notes": "x", "notes_done": half, "reason": "a thin sheet in the plan"})["ok"]
    assert rnd.sink["notes_done"]["body"] == half


def test_builder_cannot_finish_notes_without_building_or_without_evidence(tmp_path):
    rnd = make_round(tmp_path)
    rnd.notes = {"body": ["wall 4 mm"]}
    finish = {t.name: t for t in at.builder_tools(rnd, "body", Library(tmp_path / "lib"))}["finish"]
    done = [{"note": 1, "done": True, "how": "the wall measures 4 mm in the part views"}]
    for _ in range(2):  # no reason gets past it
        out = finish.fn({"notes": "x", "notes_done": done, "reason": "it is fine"})
        assert "not built your part since" in out["error"]
    rnd.attempts["body"] = 1
    bare = finish.fn({"notes": "x", "notes_done": [{"note": 1, "done": True, "how": "done"}]})
    assert "without evidence" in bare["error"]
    assert finish.fn({"notes": "x", "notes_done": done})["ok"]


def test_builder_without_notes_finishes_without_building_again(tmp_path):
    rnd = make_round(tmp_path)
    finish = {t.name: t for t in at.builder_tools(rnd, "body", Library(tmp_path / "lib"))}["finish"]
    assert finish.fn({"notes": "kept"})["ok"]


def test_critic_must_measure_before_naming_sizes_and_target_its_lowest_score(tmp_path):
    rnd = make_round(tmp_path)
    rnd.files.write("model.blend", "")  # a saved model exists, so measuring is possible
    _view, measure, _inspect, submit = at.critic_tools(rnd, {"turntable": "t.jpg"})
    assert "unknown part" in asyncio.run(measure.fn({"part": "lid"}))["error"]
    scores = {"silhouette": 8, "parts": 7, "proportions": 8, "materials": 4, "surface": 7}
    sized = [{"target": "handle", "parameter": "thickness", "change": "make it 14 mm", "criterion": "proportions"}]
    out = submit.fn({"scores": scores, "summary": "s", "changes": sized})
    assert "not measured" in out["error"] and "handle" in out["error"]
    shape_only = [{"target": "handle", "parameter": "shape", "change": "rounder", "criterion": "proportions"}]
    out = submit.fn({"scores": scores, "summary": "s", "changes": shape_only})
    assert "lowest score" in out["error"] and "materials" in out["error"]
    fixed = shape_only + [{"target": "materials", "change": "matte glaze", "criterion": "materials"}]
    # every model, not only a car: a material change needs the real materials read first; refused once
    out = submit.fn({"scores": scores, "summary": "s", "changes": fixed})
    assert "inspect_materials" in out["error"]
    assert submit.fn({"scores": scores, "summary": "s", "changes": fixed})["ok"]


def test_critic_sends_material_changes_to_the_materials_agent_not_a_builder(tmp_path):
    rnd = make_round(tmp_path)
    _view, _measure, _inspect, submit = at.critic_tools(rnd, {"turntable": "t.jpg"})
    scores = {"silhouette": 8, "parts": 7, "proportions": 8, "materials": 5, "surface": 7}
    aimed_at_part = [{"target": "handle", "parameter": "glaze", "change": "base colour #1F2E5A, roughness 0.3",
                      "criterion": "materials"},
                     {"target": "body", "parameter": "wall", "change": "thinner", "criterion": "proportions"}]
    out = submit.fn({"scores": scores, "summary": "s", "changes": aimed_at_part})
    assert "builders never touch materials" in out["error"] and list(out["misrouted"]) == [1]
    retargeted = [{"target": "materials", "parameter": "handle glaze", "change": "handle: base colour #1F2E5A",
                   "criterion": "materials"}, aimed_at_part[1]]
    assert submit.fn({"scores": scores, "summary": "s", "changes": retargeted})["ok"]


def test_finish_without_notes_or_problems_is_accepted(tmp_path):
    rnd = make_round(tmp_path)
    finish = {t.name: t for t in at.assembler_tools(rnd)}["finish"]
    assert finish.fn({"notes": "placed"})["ok"]


def test_a_next_round_knows_the_version_it_starts_from(tmp_path):
    prev = tmp_path / "v1"
    prev.mkdir()
    rnd = pipeline._new_round("prev-dir-test", 2, SPEC, {"parts": []}, prev)
    assert rnd.prev_dir == prev
    assert pipeline._new_round("prev-dir-test", 3, SPEC, {"parts": []}, None).prev_dir is None


def test_agents_with_notes_can_see_the_previous_versions_renders(tmp_path):
    rnd = make_round(tmp_path)
    prev = tmp_path / "v0"
    (prev / "renders").mkdir(parents=True)
    for name in ("sheet_turntable.jpg", "closeup_handle.png"):
        Image.new("RGB", (8, 8)).save(prev / "renders" / name)
    rnd.prev_dir = prev
    assert "view_previous_version" not in {t.name for t in at.builder_tools(rnd, "handle", Library(tmp_path / "l"))}
    rnd.notes = {"handle": ["thicker"]}
    tools = {t.name: t for t in at.builder_tools(rnd, "handle", Library(tmp_path / "l"))}
    shown = tools["view_previous_version"].fn({})["_images"]
    assert [Path(p).name for p in shown] == ["sheet_turntable.jpg", "closeup_handle.png"]
    assert "view_previous_version" not in {t.name for t in at.assembler_tools(rnd)}  # no notes for the assembler
    assert "view_previous_version" in pipeline._notes_lines(rnd, ["thicker"])[2]


def test_builder_refuses_code_without_build_before_blender(tmp_path):
    rnd = make_round(tmp_path)
    tools = {t.name: t for t in at.builder_tools(rnd, "body", Library(tmp_path / "lib"))}
    out = asyncio.run(tools["write_part"].fn({"code": "def make():\n    pass\n"}))
    assert "build()" in out["error"]
    out = asyncio.run(tools["write_part"].fn({"code": "def build(:\n"}))
    assert "syntax error" in out["error"]
    assert not rnd.files.exists("parts/body.py")


def test_brief_and_architect_submissions(tmp_path):
    sink = {}
    (submit_spec,) = at.brief_tools(sink)
    assert not submit_spec.fn({"spec": {"object": "mug", "dimensions": {"width": 0}, "parts": [{}]}})["ok"]
    assert submit_spec.fn({"spec": {"object": "mug", "dimensions": SPEC["dimensions"], "parts": [{"name": "body"}]}})["ok"]
    _, submit_plan = at.architect_tools(sink, Library(tmp_path / "lib"))
    assert "unique" in submit_plan.fn({"plan": {"parts": [{"name": "a"}, {"name": "a"}]}})["error"]
    assert "snake_case" in submit_plan.fn({"plan": {"parts": [{"name": "the leg"}]}})["error"]
    assert submit_plan.fn({"plan": {"parts": [{"name": "leg"}]}})["ok"] and sink["plan"]["parts"][0]["name"] == "leg"


def test_changes_and_gate_failures_are_routed_to_the_right_agent():
    review = {"changes": [{"target": "handle", "parameter": "thickness", "change": "make it 7 mm"},
                          {"target": "materials", "change": "less glossy"}]}
    outcome = {"checks": checks.evaluate(measure(min_z=0.02, parts=[{"object": "body", "part": "body",
                                                                      "non_manifold_edges": 3}]), SPEC),
               "measure": {"parts": [{"part": "body", "non_manifold_edges": 3}]}}
    notes = pipeline._route_changes(review, outcome, ["body", "handle"])
    assert notes["handle"] == ["thickness: make it 7 mm"]
    assert notes["materials"] == ["less glossy"]
    assert "3 open" in notes["body"][0]
    assert any("on_floor" in n for n in notes["assembly"])
    failure = pipeline._route_failure({"failed_step": "build part 'handle'", "error": "Traceback"}, ["body", "handle"])
    assert list(failure) == ["handle"]
    assert list(pipeline._route_failure({"failed_step": "motion script", "error": "Traceback"}, ["body"])) == ["motion"]


def test_motion_changes_go_to_the_animator_only_for_a_moving_model():
    moving = {"motion": {"speed_kmh": 30, "wheels": ["wheel"], "effects": [{"kind": "smoke", "from": "chimney"}]}}
    assert at.has_motion(moving) and not at.has_motion({"object": "mug"}) and not at.has_motion({"motion": {}})
    review = {"changes": [{"target": "motion", "parameter": "wheel direction", "change": "roll forward"}]}
    outcome = {"checks": checks.evaluate(measure(), SPEC), "measure": {}}
    assert pipeline._route_changes(review, outcome, ["wheel"]) == {"motion": ["wheel direction: roll forward"]}
    assert pipeline._role_of("motion") == "animator"
    assert "spin(" in at.motion_reference() and "marker(" in at.motion_reference()
    assert "report(" not in at.motion_reference()
    labels = render.frame_labels(["d/frame_00.png", "d/frame_01.png", "d/markers_1_smoke.png", "d/markers_2_steam.png"])
    assert labels == ["frame 1 of 2", "frame 2 of 2", "smoke marker: grey ball", "steam marker: white ball"]


def test_a_builder_sees_the_new_code_of_the_part_it_attaches_to(tmp_path, monkeypatch):
    rnd = make_round(tmp_path)
    rnd.plan["parts"][1]["attaches_to"] = "body (embedded into the side)"
    rnd.files.write("parts/body.py", "OLD_BODY = 1\n")
    prompts = {}

    async def fake_run_agent(agent, prompt, tools, **kw):
        part = kw["doing"].split()[-1]
        prompts[part] = prompt
        if part == "body":
            await asyncio.sleep(0.05)
            rnd.files.write("parts/body.py", "NEW_BODY = 1\n")
        rnd.part_ok[part] = True

    monkeypatch.setattr(pipeline.runner, "run_agent", fake_run_agent)
    asyncio.run(pipeline._build_parts(rnd, ["handle", "body"], {}, Library(tmp_path / "lib"), None))
    assert "The part yours attaches to: body" in prompts["handle"]
    assert "NEW_BODY = 1" in prompts["handle"] and "OLD_BODY" not in prompts["handle"]
    assert "attaches to:" not in prompts["body"]


def test_blender_missing_is_reported_cleanly(monkeypatch):
    from modelgen.tools import blender
    monkeypatch.setattr(config, "blender_path", lambda: "")
    result = blender.run_job([])
    assert result.missing and "winget" in result.summary()["error"]
    assert not blender.available()


def test_a_stopped_blender_job_kills_its_process(monkeypatch):
    import subprocess
    import sys
    import time
    from modelgen.tools import blender
    procs = []

    def fake_job(steps, open_blend=None, timeout=None, render=None, on_start=None):
        proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
        procs.append(proc)
        on_start(proc)
        proc.wait()
        return blender.BlenderResult(ok=False)
    monkeypatch.setattr(blender, "run_job", fake_job)

    async def main():
        task = asyncio.create_task(blender.run_job_async([]))
        while not procs:
            await asyncio.sleep(0.02)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(main())
    deadline = time.monotonic() + 5
    while procs[0].poll() is None and time.monotonic() < deadline:
        time.sleep(0.05)
    assert procs[0].poll() is not None


def _fake_blender(monkeypatch, seen):
    """Blender stand-in: records each job's steps and writes the live GLB its live step asks for."""
    from modelgen.tools import blender

    async def fake_job(steps, open_blend=None, timeout=None, render=None):
        seen.append(steps)
        outputs = {"measure": measure(), "renders": {}}
        for step in steps:
            if step["kind"] == "live":
                Path(step["path"]).parent.mkdir(parents=True, exist_ok=True)
                Path(step["path"]).write_bytes(b"glTF")
                outputs["live"] = step["path"]
        return blender.BlenderResult(ok=True, outputs=outputs)

    monkeypatch.setattr(blender, "run_job_async", fake_job)


def test_a_builder_attempt_exports_a_live_view(tmp_path, monkeypatch):
    from modelgen import db
    seen = []
    _fake_blender(monkeypatch, seen)
    rnd = make_round(tmp_path)
    tools = {t.name: t for t in at.builder_tools(rnd, "body", Library(tmp_path / "lib"))}
    out = asyncio.run(tools["write_part"].fn({"code": "def build():\n    pass\n"}))
    assert out["ok"]
    live = seen[-1][-1]
    assert live["kind"] == "live" and Path(live["path"]) == rnd.files.path("previews/body/01/live.glb")
    (event,) = db.events_of(["live_model"], project="mug")
    assert event["data"] == {"version": 1, "key": "body", "attempt": 1, "kind": "part",
                             "file": "v1/previews/body/01/live.glb"}


def test_assembly_and_materials_attempts_export_a_live_view(tmp_path, monkeypatch):
    from modelgen import db
    seen = []
    _fake_blender(monkeypatch, seen)
    rnd = make_round(tmp_path)
    rnd.files.write("parts/body.py", "def build():\n    pass\n")
    (write_assembly, _) = at.assembler_tools(rnd)
    asyncio.run(write_assembly.fn({"code": "def assemble(parts):\n    pass\n"}))
    write_materials = next(t for t in at.materials_tools(rnd, Library(tmp_path / "lib")) if t.name == "write_materials")
    asyncio.run(write_materials.fn({"code": "def apply(parts):\n    pass\n"}))
    assert [s[-1]["kind"] for s in seen] == ["live", "live"]
    events = db.events_of(["live_model"], project="mug")
    assert [(e["data"]["key"], e["data"]["kind"]) for e in events] == [("assembly", "assembly"), ("materials", "materials")]
    assert rnd.files.exists("previews/assembly/01/live.glb") and rnd.files.exists("previews/materials/01/live.glb")


def test_no_live_event_when_the_attempt_fails(tmp_path, monkeypatch):
    from modelgen import db
    from modelgen.tools import blender

    async def failing(steps, open_blend=None, timeout=None, render=None):
        return blender.BlenderResult(ok=False, traceback="boom", failed_step="build part 'body'")
    monkeypatch.setattr(blender, "run_job_async", failing)
    rnd = make_round(tmp_path)
    tools = {t.name: t for t in at.builder_tools(rnd, "body", Library(tmp_path / "lib"))}
    assert not asyncio.run(tools["write_part"].fn({"code": "def build():\n    pass\n"}))["ok"]
    assert db.events_of(["live_model"], project="mug") == []
