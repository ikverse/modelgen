"""The car path without Blender: the car sheet, the plan made from it, the files a car round starts
with, which agent shapes the body, the car rubric, and the reference pairs."""

import ast
import asyncio
from pathlib import Path

import pytest
from PIL import Image

from modelgen import pipeline
from modelgen.bpy_lib.shells import car_archetypes, car_sheet
from modelgen.tools import agent_tools as at
from modelgen.tools import checks, render
from modelgen.tools.files import ProjectFiles
from modelgen.tools.library import Library

SPEC = {"object": "sports coupe", "category": "shell:car",
        "dimensions": {"width": 1.78, "depth": 4.6, "height": 1.36}, "parts": [{"name": "body"}]}
SUBMISSION = {"sheet": {"style": "coupe", "length": 4.6, "wheelbase": 2.7},
              "accessories": {"wheel": {"spokes": 5}, "wing": {"span": 1.6, "height": 0.3}},
              "reference_views": {"1": "side"}, "paint": [0.1, 0.3, 0.8], "style": "stylised"}


def car_round(tmp_path, submission=SUBMISSION, number=1):
    plan = car_sheet.make_plan(submission)
    files = ProjectFiles(tmp_path / "car" / f"v{number}")
    return pipeline.Round(project="car", number=number, dir=files.root, files=files, spec=SPEC, plan=plan)


def test_the_brief_category_picks_the_car_path():
    assert at.shell_template(spec=SPEC) == "car"
    assert at.shell_template(spec={"category": "general"}) is None
    assert at.shell_template(spec={"category": "shell:plane"}) is None  # no such template yet
    assert at.shell_template(plan={"kind": "shell", "template": "car"}) == "car"
    assert at.shell_template(plan={"parts": []}) is None


def test_a_spec_without_a_category_is_a_car_when_its_object_names_one():
    for obj in ("Simple boxy modern SUV (made-up model, no brand logos)", "red sports car", "Coupé",
                "anime hatchback", "family minivan", "vintage station wagon"):
        assert at.shell_template(spec={"object": obj}) == "car", obj
    for obj in ("car seat", "car key fob", "pickup truck", "cargo bike", "carpet", "ceramic mug"):
        assert at.shell_template(spec={"object": obj}) is None, obj
    assert at.shell_template(spec={"object": "red sports car", "category": "general"}) is None
    assert "category" in at.SPEC_SCHEMA["properties"]["spec"]["required"]


def test_a_detail_nudged_moves_its_outline_or_path_only():
    rect = {"name": "a", "outline": {"rect": [0.1, 0.2, 0.3, 0.1, 0.01]}, "depth": 0.01}
    assert car_sheet.nudged(rect, 0.002, -0.001)["outline"]["rect"] == pytest.approx([0.102, 0.199, 0.3, 0.1, 0.01])
    assert car_sheet.nudged(rect, 0, 0) is rect
    circle = car_sheet.nudged({"outline": {"circle": [0.5, 0.5, 0.1]}}, 0.01, 0)
    assert circle["outline"] == {"circle": [0.51, 0.5, 0.1]}
    poly = car_sheet.nudged({"outline": {"poly": [[0, 0], [1, 0], [1, 1]]}}, 0.5, 0.5)
    assert poly["outline"] == [(0.5, 0.5), (1.5, 0.5), (1.5, 1.5)]
    groove = car_sheet.nudged({"kind": "groove", "path": [[0, 0], [1, 1]]}, 0.1, 0)
    assert groove["path"] == [(0.1, 0), (1.1, 1)]


def test_every_example_sheet_is_buildable_and_offered_to_the_car_agents():
    reference = at.car_reference()
    for name, a in car_archetypes.ARCHETYPES.items():
        assert a["description"] and car_sheet.check(a["sheet"]) == [], name
        plan = car_sheet.make_plan({"sheet": a["sheet"], "accessories": a["accessories"]})
        assert at.plan_parts(plan)[:2] == ["body", "wheel"], name
        assert f"**{name}**" in reference


def test_side_glass_stops_before_the_rear_corner():
    for style in car_sheet.STYLES:
        s = car_sheet.resolve({"style": style})
        glass = [f for f in car_sheet.default_features(s) if f["name"].startswith("side_glass")]
        assert glass, style
        end = max(u for f in glass for u, _ in car_sheet.outline_points(f["outline"]))
        assert end <= s["y_rear"] - s["tail_corner"] - s["c_pillar"], style


def test_every_style_resolves_to_a_buildable_sheet_with_matching_rings():
    for style in car_sheet.STYLES:
        sheet = {"style": style}
        assert car_sheet.check(sheet) == [], style
        s = car_sheet.resolve(sheet)
        rings = car_sheet.rings(s)
        assert len({len(r) for r in rings}) == 1 and len(rings) >= 20
        ys = [r[len(r) // 2][1] for r in rings]  # the top centre point of each ring
        # the hood's and deck's edges sit back from the ends by the face's rollover (and rake)
        front = s["y_front"] + s["nose_rollover"] + s["nose_rake"]
        rear = s["y_rear"] - s["tail_rollover"] - s["tail_rake"]
        assert ys == sorted(ys) and abs(ys[0] - front) < 0.06 and abs(ys[-1] - rear) < 0.06
        lows = [r[0][1] for r in rings]  # the bottom centre: the bumpers tuck under by nose_tuck / tail_tuck
        assert lows == sorted(lows) and abs(lows[0] - s["y_front"] - s["nose_tuck"]) < 0.01
        names = [f["name"] for f in car_sheet.features(s)]
        assert len(names) == len(set(names)) and "windshield" in names and "side_glass_front" in names


def test_a_sheet_scales_its_preset_and_reports_impossible_numbers():
    s = car_sheet.resolve({"style": "coupe", "length": 2.3, "height": 0.68, "width": 0.89})  # a half-size toy
    assert abs(s["wheelbase"] - car_sheet.PRESETS["coupe"]["wheelbase"] / 2) < 1e-9
    assert abs(s["belt_z"] - car_sheet.PRESETS["coupe"]["belt_z"] / 2) < 1e-9
    assert any("in front of roof_front" in p for p in car_sheet.check({"style": "coupe", "roof_front": -1.1}))
    assert any("tyres stick out" in p for p in car_sheet.check({"style": "coupe", "track": 1.75}))
    assert any("unknown car style" in p for p in car_sheet.check({"style": "tank"}))


def test_features_replace_remove_and_add_by_name():
    s = car_sheet.resolve({"style": "sedan", "features": [
        {"name": "grille", "depth": 0.04},
        {"name": "fender_vent", "remove": True},
        {"name": "door_handle", "remove": True},
        {"name": "hood_vent", "kind": "recess", "view": "top", "outline": {"rect": [0, -1.2, 0.3, 0.12, 0.02]},
         "until": 0.6, "material": "trim", "depth": 0.015}]})
    feats = {f["name"]: f for f in car_sheet.features(s)}
    assert feats["grille"]["depth"] == 0.04 and feats["grille"]["view"] == "front"  # the rest kept
    assert "door_handle" not in feats and "hood_vent" in feats
    assert len(car_sheet.outline_points({"circle": [0, 0, 0.1]})) == 28
    assert car_sheet.outline_points({"rect": [0, 0, 1, 1, 0]})[0] == (0.5, 0.5)


def test_the_plan_puts_the_wheels_on_the_axles_and_seeds_every_part():
    plan = car_sheet.make_plan(SUBMISSION)
    s = car_sheet.resolve(SUBMISSION["sheet"])
    assert at.plan_parts(plan) == ["body", "wheel", "side_mirror", "wing", "exhaust"]
    body = plan["parts"][0]
    assert body["builder"] == "shell"
    assert pipeline.body_sheet(body["seed"]) == SUBMISSION["sheet"]
    wheel = plan["parts"][1]
    ys = sorted({i["location"][1] for i in wheel["instances"]})
    assert ys == [round(s["axle_front"], 4), round(s["axle_rear"], 4)]
    assert all(i["location"][2] == round(s["wheel_radius"], 4) for i in wheel["instances"])
    for part in plan["parts"]:
        tree = ast.parse(part["seed"])
        assert any(isinstance(n, ast.FunctionDef) and n.name == "build" for n in tree.body)
    assert "spokes=5" in wheel["seed"] and f"hub_reach={s['hub_reach']}" in wheel["seed"]
    assert plan["dimensions"]["width"] > s["width"]  # the mirrors stick out
    assert plan["dimensions"]["depth"] > s["length"]  # so does the exhaust
    no_extras = car_sheet.make_plan({"sheet": {"style": "suv"},
                                     "accessories": {"side_mirror": None, "exhaust": False}})
    assert at.plan_parts(no_extras) == ["body", "wheel"]
    assert no_extras["dimensions"]["width"] == car_sheet.resolve({"style": "suv"})["width"]


def test_the_car_architect_tool_validates_the_submission(tmp_path):
    sink = {}
    tools = {t.name: t for t in at.shell_architect_tools(sink, Library(tmp_path / "lib"))}
    bad = tools["submit_plan"].fn({"submission": {"sheet": {"style": "coupe", "deck_front": 3.0}}})
    assert not bad["ok"] and "cannot be built" in bad["error"] and "plan" not in sink
    views = tools["submit_plan"].fn({"submission": {"sheet": {}, "reference_views": {"1": "diagonal"}}})
    assert "unknown views" in views["error"]
    ok = tools["submit_plan"].fn({"submission": SUBMISSION})
    assert ok["ok"] and sink["plan"]["kind"] == "shell" and ok["overall_size"] == sink["plan"]["dimensions"]
    reference = at.car_reference(SUBMISSION["sheet"])
    assert "wheelbase=2.70" in reference and '"windshield"' in reference and "mirror_x" in reference


def test_a_car_round_starts_from_the_seeds(tmp_path):
    rnd = car_round(tmp_path)
    pipeline._seed(rnd)
    for part in at.plan_parts(rnd.plan):
        assert rnd.files.exists(f"parts/{part}.py")
    assert "car.assemble(parts)" in rnd.files.read("assembly.py")
    assert "m.car_finish(parts, paint=(0.1, 0.3, 0.8), style='stylised')" in rnd.files.read("materials.py")
    rnd.files.write("parts/body.py", "EDITED = 1\n")
    pipeline._seed(rnd)
    assert rnd.files.read("parts/body.py") == "EDITED = 1\n"  # a builder's work is kept
    rnd.plan["reseed"] = ["body"]
    pipeline._seed(rnd)
    assert pipeline.body_sheet(rnd.files.read("parts/body.py")) == SUBMISSION["sheet"]
    assert "seed" not in str(pipeline._without_seeds(rnd.plan)["parts"])


def test_the_body_goes_to_the_shell_builder_with_the_references(tmp_path, monkeypatch):
    rnd = car_round(tmp_path)
    pipeline._seed(rnd)
    calls = {}

    async def fake_run_agent(agent, prompt, tools, **kw):
        part = kw["doing"].split()[-1]
        calls[part] = (agent, prompt, [t.name for t in tools])
        rnd.part_ok[part] = True

    monkeypatch.setattr(pipeline.runner, "run_agent", fake_run_agent)
    monkeypatch.setattr(at, "reference_paths", lambda project: [tmp_path / "ref.png"])
    asyncio.run(pipeline._build_parts(rnd, ["body", "wheel"], {"body": ["raise roof_rear"]}, Library(tmp_path / "lib"), None))
    agent, prompt, tools = calls["body"]
    assert agent == "shell_builder" and "view_reference" in tools and "write_part" in tools
    assert "raise roof_rear" in prompt and "## Car sheets" in prompt and "def build():" in prompt
    agent, prompt, tools = calls["wheel"]
    assert agent == "builder" and "view_reference" not in tools
    assert "shell.wheel(" in prompt and "Shell helper reference" in prompt


def test_a_failed_body_goes_back_to_its_seed(tmp_path, monkeypatch):
    rnd = car_round(tmp_path)
    pipeline._seed(rnd)

    async def broken(agent, prompt, tools, **kw):
        rnd.files.write("parts/body.py", "def build(:\n")
        rnd.part_ok["body"] = False
        return type("R", (), {"error": "out of turns"})()

    monkeypatch.setattr(pipeline.runner, "run_agent", broken)
    asyncio.run(pipeline._build_parts(rnd, ["body"], {}, Library(tmp_path / "lib"), None))
    assert pipeline.body_sheet(rnd.files.read("parts/body.py")) == SUBMISSION["sheet"]


def test_a_change_to_the_car_restarts_only_what_changed(tmp_path, monkeypatch):
    base = car_round(tmp_path)
    pipeline._seed(base)
    base.files.write("plan.json", at.dumps(base.plan))

    async def architect(agent, prompt, tools, **kw):
        assert agent == "shell_architect" and '"wheelbase": 2.7' in prompt
        submit = next(t for t in tools if t.name == "submit_plan")
        submit.fn({"submission": {**SUBMISSION, "sheet": {**SUBMISSION["sheet"], "roof_rear": 1.0}}})
        return type("R", (), {"error": None})()

    monkeypatch.setattr(pipeline.runner, "run_agent", architect)
    new, rebuild = asyncio.run(pipeline._shell_architect_change("car", SPEC, base.plan, "longer roof",
                                                                Library(tmp_path / "lib"), base.dir))
    assert new["reseed"] == ["body"] and rebuild == {"body"}
    assert new["sheet"]["roof_rear"] == 1.0


def test_a_car_is_judged_by_the_car_rubric_and_its_overall_size(tmp_path):
    rnd = car_round(tmp_path)
    assert at.rubric_file(rnd.plan).name == "rubric_car.yaml"
    assert at.rubric_file({"parts": []}).name == "rubric.yaml"
    ids = [c["id"] for c in at.load_rubric(rnd.plan)["criteria"]]
    assert "continuity" in ids and "reference" in ids
    view, _measure, _inspect, submit = at.critic_tools(rnd, {"turntable": "t.jpg", "car_views": "c.jpg",
                                                             "reference_pairs": "p.jpg"})
    assert view.fn({})["_images"] == ["t.jpg", "c.jpg", "p.jpg"]
    assert _inspect.name == "inspect_materials"
    scores = {i: 7 for i in ids}
    areas = {"nose": 3, "lamps": 4, "glasshouse": 6, "doors_panels": 6, "arches_wheels": 8, "tail": 7}
    ok = submit.fn({"scores": scores, "summary": "s", "areas": areas,
                    "changes": [{"target": "body", "parameter": "roof_rear", "change": "0.95", "criterion": ids[0],
                                 "area": "nose"}]})
    assert ok["ok"] and rnd.sink["review"]["score"] == 70.0 and rnd.sink["review"]["areas"] == areas
    dims = rnd.plan["dimensions"]
    measured = {"bbox": {"size": [dims["width"], dims["depth"], dims["height"]]}, "min_z": 0.0, "parts": [],
                "floaters": [], "overlaps": []}
    assert checks.evaluate(measured, at.check_spec(rnd))["gates"]["correct_size"]["pass"]
    assert not checks.evaluate(measured, rnd.spec)["gates"]["correct_size"]["pass"]  # the body size alone


def test_the_nose_and_tail_shape_from_above_and_their_rounded_edges():
    side_flat = {"nose_rollover": 0.0, "nose_tuck": 0.0, "nose_rake": 0.0}  # plan view alone: no side-view shape
    flat = car_sheet.resolve({"style": "sedan", "nose_point": 0.0, "nose_edge_radius": 0.0, **side_flat})
    pointed = car_sheet.resolve({"style": "sedan", "nose_point": 0.3, "nose_round": 1.5, **side_flat})
    assert car_sheet.resolve({"style": "sedan"})["nose_point"] == car_sheet.COMMON["nose_corner"]  # the old number

    def corner_lag(s):  # how far the front ring's widest point sits behind its centre
        ring = car_sheet.rings(s)[0]
        centre = ring[len(ring) // 2][1]
        side = max(ring, key=lambda p: p[0])[1]
        return side - centre

    assert corner_lag(flat) < 0.02 < 0.2 < corner_lag(pointed)
    rounded = car_sheet.resolve({"style": "sedan", "nose_edge_radius": 0.1})
    y = rounded["y_front"]
    assert car_sheet.z_top(rounded, y) == pytest.approx(car_sheet.z_top(flat, y) - 0.1)
    assert car_sheet.z_top(rounded, y + 0.2) == pytest.approx(car_sheet.z_top(flat, y + 0.2))
    assert any("nose_round" in p for p in car_sheet.check({"style": "sedan", "nose_round": 9}))
    assert any("too big" in p for p in car_sheet.check({"style": "sedan", "nose_edge_radius": 0.15, "nose_z": 0.4}))


def test_headlights_follow_the_lamp_style_and_lie_on_the_nose():
    s = car_sheet.resolve({"style": "coupe"})
    aim, at = car_sheet.nose_aim(s, 0.5, s["nose_z"] - 0.09)
    assert sum(c * c for c in aim) == pytest.approx(1.0, abs=1e-3)
    assert aim[1] > 0.5 and aim[2] < 0 and aim[0] < 0  # into the nose, down onto it, in from the corner
    names = lambda sheet: {f["name"]: f for f in car_sheet.features(car_sheet.resolve(sheet))}
    projector = names({"style": "coupe"})
    assert projector["headlights"]["view"] == "aim" and "headlight_projector_inner" in projector
    popup = names({"style": "coupe", "lamp_style": "popup"})
    assert popup["headlight_lids"]["view"] == "top" and popup["headlights"]["view"] == "front"
    assert names({"style": "coupe", "lamp_style": "flush"})["headlights"]["view"] == "front"
    assert any("lamp_style" in p for p in car_sheet.check({"style": "coupe", "lamp_style": "laser"}))
    bad = {"style": "coupe", "features": [{"name": "x", "kind": "recess", "view": "aim", "outline": {"rect": [0, 0, 1, 1, 0]}}]}
    assert any("needs `aim`" in p for p in car_sheet.check(bad))


def test_side_windows_sit_in_one_black_opening_with_a_pillar_between():
    s = car_sheet.resolve({"style": "sedan"})
    dlo, glass = car_sheet.side_openings(s)
    assert len(glass) == 2
    lo = min(u for u, _ in dlo)
    hi = max(u for u, _ in dlo)
    front_end = max(u for u, _ in glass[0])
    rear_start = min(u for u, _ in glass[1])
    assert lo < min(u for u, _ in glass[0]) and max(u for u, _ in glass[1]) < hi  # the glass inside the opening
    assert rear_start - front_end >= car_sheet.B_PILLAR * 0.9
    feats = {f["name"]: f for f in car_sheet.features(s)}
    assert feats["side_window_opening"]["material"] == "trim"
    assert feats["side_glass_front"]["depth"] == 0.02


def test_four_doors_get_rear_doors_and_every_car_its_shut_lines():
    def names(sheet):
        return {f["name"] for f in car_sheet.features(car_sheet.resolve(sheet))}

    sedan, coupe = names({"style": "sedan"}), names({"style": "coupe"})
    assert {"door_line", "rear_door_line", "rear_door_handle", "hood_line", "boot_line", "fuel_flap",
            "front_bumper_joint", "rear_bumper_joint"} <= sedan
    assert "rear_door_line" not in coupe and "rear_door_line" not in names({"style": "sedan", "doors": 2})
    assert "tailgate_line" in names({"style": "suv"}) and "boot_line" not in names({"style": "suv"})
    lines = [f for f in car_sheet.features(car_sheet.resolve({"style": "sedan"}))
             if f.get("kind") == "groove" and "lamp" not in f["name"] and "headlight" not in f["name"]]  # lamps' LEDs
    assert all(f.get("optional") and f["width"] == car_sheet.SHUT_LINE for f in lines)
    assert any("doors must be" in p for p in car_sheet.check({"style": "sedan", "doors": 3}))
    s = car_sheet.resolve({"style": "hatch"})
    tailgate = next(f for f in car_sheet.features(s) if f["name"] == "tailgate_line")
    lamps = [u for f in car_sheet.features(s) if f["name"].startswith("tail_lamps") and f.get("view") == "rear" and "outline" in f
             for u, _ in car_sheet.outline_points(f["outline"])]
    assert max(abs(u) for u, _ in tailgate["path"]) < min(u for u in lamps if u > 0)  # between the lamps


def test_the_presets_lay_the_windshield_down_and_the_notes_catch_an_upright_one():
    for style in ("coupe", "sedan", "hatch", "suv"):
        assert car_sheet.proportion_notes(car_sheet.resolve({"style": style})) == [], style
    upright = car_sheet.resolve({"style": "sedan", "windshield_base": -0.55, "deck_front": 1.55})
    notes = " ".join(car_sheet.proportion_notes(upright))
    assert "hood is 38%" in notes and "cabin is 44%" in notes and "windshield_base forward" in notes
    sedan = car_sheet.resolve({"style": "sedan"})
    assert sedan["windshield_base"] - sedan["axle_front"] < 0.7  # the cowl close behind the front axle
    ws = next(f for f in car_sheet.features(sedan) if f["name"] == "windshield")
    pts = car_sheet.outline_points(ws["outline"])
    front_mid = min(v for u, v in pts if abs(u) < 0.05)
    front_corner = min(v for u, v in pts if abs(u) > 0.4)
    assert front_mid < front_corner  # the glass's lower edge bows forward in the middle


def test_fenders_arch_over_the_wheels_and_vents_follow_the_car():
    s = car_sheet.resolve({"style": "sedan", "rear_fender_rise": 0.05, "hip_length": 0.4})
    assert car_sheet.fender_rise(s, s["axle_rear"]) == pytest.approx(0.05)
    assert car_sheet.fender_rise(s, s["axle_rear"] - 0.5) > car_sheet.fender_rise(s, s["axle_rear"] + 0.5)  # hips
    assert car_sheet.fender_rise(s, 0.0) == pytest.approx(0.0)
    names = lambda sheet: {f["name"] for f in car_sheet.features(car_sheet.resolve(sheet))}
    assert {"gill_1", "gill_2", "gill_3"} <= names({"style": "coupe"})
    assert "fender_trim" in names({"style": "suv"}) and not any(n.startswith("gill") for n in names({"style": "sedan"}))
    assert {"side_intake", "hood_vents"} <= names({"style": "coupe", "vents": ["side_intake", "hood_vents"]})
    assert {"front_arch_trim", "rear_arch_trim"} <= names({"style": "suv", "arch_cladding": True})
    assert not any("flare" in n for n in names({"style": "coupe"}))  # the boxed band is a blister in the skin now
    assert any("unknown vents" in p for p in car_sheet.check({"style": "coupe", "vents": ["snorkel"]}))


def test_tail_lamps_stand_proud_with_leds_in_every_style():
    for style in car_sheet.TAIL_LAMP_STYLES:
        feats = {f["name"]: f for f in car_sheet.features(car_sheet.resolve({"style": "sedan",
                                                                             "tail_lamp_style": style}))}
        lens = feats["tail_lamps"]
        assert lens["kind"] == "raise" and lens["height"] == car_sheet.LENS, style
        glow = [f for f in feats.values() if f.get("material") == "lamp_led_rear"]
        assert glow, style
        assert "tail_lamps_outer" not in feats and "tail_lamp_panels" not in feats
    assert "tail_lamps_side" in {f["name"] for f in car_sheet.features(car_sheet.resolve({"style": "sedan"}))}
    assert "lamp_led" in car_sheet.MATERIAL_SLOTS and "lamp_led_rear" in car_sheet.MATERIAL_SLOTS
    assert any("tail_lamp_style" in p for p in car_sheet.check({"style": "sedan", "tail_lamp_style": "neon"}))


def test_headlight_shapes_and_side_window_shapes_differ_by_type():
    outlines = {}
    for shape in car_sheet.LAMP_SHAPES:
        feats = {f["name"]: f for f in car_sheet.features(car_sheet.resolve({"style": "sedan", "lamp_shape": shape}))}
        lamp = feats.get("headlights") or feats["headlight_inner"]
        outlines[shape] = str(lamp["outline"])
        assert any(f.get("material") == "lamp_led" for f in feats.values()), shape  # a running light
    assert len(set(outlines.values())) == len(outlines)

    def window(style):
        dlo, glass = car_sheet.side_openings(car_sheet.resolve({"style": "sedan", "dlo_style": style}))
        return dlo, glass

    def bottom_rise(style):  # how much the opening's lower edge climbs from its front to its rear third
        dlo, _ = window(style)
        lo, hi = min(u for u, _ in dlo), max(u for u, _ in dlo)
        third = (hi - lo) / 3
        return min(v for u, v in dlo if u > hi - third) - min(v for u, v in dlo if u < lo + third)

    assert bottom_rise("coupe") > bottom_rise("sedan") > bottom_rise("van") - 1e-6
    assert bottom_rise("van") == pytest.approx(0.0, abs=0.01)
    assert any("dlo_style" in p for p in car_sheet.check({"style": "sedan", "dlo_style": "bus"}))


CAR_AREAS = {"nose": 3, "lamps": 4, "glasshouse": 6, "doors_panels": 6, "arches_wheels": 8, "tail": 7}


def car_scores(rnd):
    return {c["id"]: 6 for c in at.load_rubric(rnd.plan)["criteria"]}


def test_a_car_review_scores_every_area_and_aims_at_the_two_weakest(tmp_path):
    rnd = car_round(tmp_path)
    *_, submit = at.critic_tools(rnd, {"turntable": "t.jpg"})
    scores = car_scores(rnd)
    change = {"target": "body", "parameter": "nose_point", "change": "rounder", "criterion": "silhouette"}
    out = submit.fn({"scores": scores, "summary": "s", "areas": {"nose": 3}, "changes": [change | {"area": "nose"}]})
    assert "missing" in out["error"] and "tail" in out["error"]
    out = submit.fn({"scores": scores, "summary": "s", "areas": CAR_AREAS, "changes": [change | {"area": "tail"}]})
    assert "two weakest areas are ['nose', 'lamps']" in out["error"]
    assert submit.fn({"scores": scores, "summary": "s", "areas": CAR_AREAS,
                      "changes": [change | {"area": "lamps"}]})["ok"]


def test_a_change_that_misses_twice_is_stuck_and_leaves_the_list(tmp_path):
    rnd = car_round(tmp_path)
    previous = [{"target": "body", "parameter": "tail edge", "change": "round the tail edge", "area": "tail",
                 "misses": 1},
                {"target": "body", "parameter": "nose_point", "change": "rounder nose", "area": "nose"}]
    *_, submit = at.critic_tools(rnd, {"turntable": "t.jpg"}, None, previous,
                                 [{"target": "materials", "change": "old stuck one"}])
    scores = car_scores(rnd)
    landed = [{"change": 1, "landed": False}, {"change": 2, "landed": False}]
    nothing = [{"target": "materials", "change": "x", "criterion": "materials", "area": "nose"}]
    out = submit.fn({"scores": scores, "summary": "s", "areas": CAR_AREAS, "changes": nothing, "landed": landed})
    assert "previous changes [2] did not land" in out["error"]  # only the first miss must come back
    again = [{"target": "body", "change": "rounder nose, really", "criterion": "silhouette", "area": "nose",
              "repeat_of": 2},
             {"target": "body", "change": "round the tail edge", "criterion": "surface", "area": "tail",
              "repeat_of": 1}]
    out = submit.fn({"scores": scores, "summary": "s", "areas": CAR_AREAS, "changes": again, "landed": landed})
    review = rnd.sink["review"]
    assert out["ok"] and [c["change"] for c in review["changes"]] == ["rounder nose, really"]
    assert review["changes"][0]["misses"] == 1
    assert [s["change"] for s in review["stuck"]] == ["old stuck one", "round the tail edge"]
    assert review["stuck"][1]["misses"] == 2


def test_a_material_change_needs_the_materials_read_and_not_already_set(tmp_path, monkeypatch):
    rnd = car_round(tmp_path)
    rnd.files.write("model.blend", "")

    async def fake_job(steps, open_blend=None, **kw):
        assert steps[0]["kind"] == "material_report"
        report = {"trim": {"base_colour": "#151515", "roughness": 0.45}, "paint": {"base_colour": "#C8102E"}}
        return type("R", (), {"ok": True, "outputs": {"material_report": report}})()

    monkeypatch.setattr(at.blender, "run_job_async", fake_job)
    _view, _measure, inspect, submit = at.critic_tools(rnd, {"turntable": "t.jpg"})
    scores = car_scores(rnd)
    trim = [{"target": "materials", "change": "trim base colour #151515", "criterion": "materials", "area": "nose"}]
    out = submit.fn({"scores": scores, "summary": "s", "areas": CAR_AREAS, "changes": trim})
    assert "inspect_materials" in out["error"]
    assert asyncio.run(inspect.fn({}))["slots"]["trim"]["base_colour"] == "#151515"
    out = submit.fn({"scores": scores, "summary": "s", "areas": CAR_AREAS, "changes": trim})
    assert out["ok"]  # refused once only; then the critic's word stands
    assert at.already_set(trim, {"trim": {"base_colour": "#161616"}}) == {1: [("#151515", "trim")]}
    assert at.already_set(trim, {"trim": {"base_colour": "#303030"}}) == {}


def test_the_body_is_told_to_keep_the_areas_that_look_right():
    review = {"changes": [{"target": "body", "parameter": "nose_point", "change": "0.1"}],
              "areas": {"nose": 3, "tail": 8, "lamps": 7}}
    notes = pipeline._route_changes(review, {"checks": {"gates": {}, "warnings": []}, "measure": {}}, ["body"])
    assert "lamps, tail" in notes["body"][-1] and "nose" not in notes["body"][-1]


def test_reference_images_are_paired_with_the_same_view(tmp_path):
    for name, color in (("ref.png", (200, 0, 0)), ("side.png", (0, 0, 200)), ("front.png", (0, 200, 0))):
        Image.new("RGB", (64, 48), color).save(tmp_path / name)
    pairs = render.reference_pairs({"1": "side", "2": "front_left", "x": "side"}, [tmp_path / "ref.png"],
                                   {"side": tmp_path / "side.png", "front_flat": tmp_path / "front.png"})
    assert pairs == [(tmp_path / "ref.png", tmp_path / "side.png", "side")]
    sheet = render.pair_sheet(pairs, tmp_path / "pairs.jpg", tile=64)
    with Image.open(sheet) as im:
        assert im.size == (128, 64)
        assert im.getpixel((32, 32))[0] > 150 and im.getpixel((96, 32))[2] > 150
    assert render.pair_sheet([(tmp_path / "missing.png", tmp_path / "side.png", "side")], tmp_path / "x.jpg") is None
    steps = render.final_steps(tmp_path, car=True)
    assert steps[-1]["views"] == render.CAR_VIEWS and len(render.final_steps(tmp_path)) == 3
