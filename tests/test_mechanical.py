"""Mechanical parts (tools\\mechanism.py, bpy_lib\\kits.py gear tools, motion.mesh_drive, the mesh check
and the part-tools path): the gear maths against the textbook, the request checks, the layout, the
loop, the mesh gates, the family words, and the /v1 API's mechanism jobs. Tests marked blender build
real gears and run a real job."""

import asyncio
import json
import math
from fractions import Fraction

import pytest
from fastapi.testclient import TestClient

from modelgen import config, db, families, jobs, pipeline, runner, server
from modelgen.tools import agent_tools as at
from modelgen.tools import blender, checks
from modelgen.tools import mechanism as mc

PAIR = {"parts": [{"name": "pinion", "type": "spur_gear", "module": 2, "teeth": 12, "meshes_with": "wheel"},
                  {"name": "wheel", "type": "spur_gear", "module": 2, "teeth": 24}], "rpm": 20}


# --- 2D helpers: two outlines turned together --------------------------------------------------

def _inside(pt, poly):
    x, y = pt
    c = False
    for i in range(len(poly)):
        (x1, y1), (x2, y2) = poly[i], poly[(i + 1) % len(poly)]
        if (y1 > y) != (y2 > y) and x < x1 + (y - y1) * (x2 - x1) / (y2 - y1):
            c = not c
    return c


def _seg(p, a, b):
    dx, dy = b[0] - a[0], b[1] - a[1]
    t = max(0.0, min(1.0, ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / (dx * dx + dy * dy)))
    return math.hypot(p[0] - a[0] - t * dx, p[1] - a[1] - t * dy)


def _turn(poly, ang, cx=0.0, cy=0.0):
    c, s = math.cos(ang), math.sin(ang)
    return [(cx + x * c - y * s, cy + x * s + y * c) for x, y in poly]


def mesh_2d(m, z1, z2, internal=False, nudge=0.0, steps=16):
    """Turn two outlines through one tooth of gear 1: (deepest cut, widest closest gap) in mm."""
    lay = mc.gear_pair_layout(m, z1, z2, internal=internal)
    g1 = mc.outline(m, z1, shift=lay["shift_a"], backlash=0.05 * m, shortening=lay["shortening"], flank=8, tip=3)
    g2 = mc.outline(m, z2, shift=lay["shift_b"], backlash=0.05 * m, internal=internal, shortening=lay["shortening"],
                    flank=8, tip=3)
    a = lay["centre_distance"] + nudge
    cx, cy = (-a, 0.0) if internal else (a, 0.0)
    deepest, widest = 0.0, 0.0
    for k in range(steps):
        th = (2 * math.pi / z1) * k / steps
        p1 = _turn(g1, th)
        th2 = math.radians(lay["phase_b_deg"]) + (th if internal else -th) * z1 / z2
        p2 = _turn(g2, th2, cx, cy)
        r2 = z2 * m / 2
        near = [p for p in p1 if r2 - 1.6 * m < math.hypot(p[0] - cx, p[1] - cy) < r2 + 1.6 * m]
        depth, gap = 0.0, 1e9
        for p in near:
            d = min(_seg(p, p2[i], p2[(i + 1) % len(p2)]) for i in range(len(p2)))
            inside = _inside(p, p2) != internal
            if inside:
                depth = max(depth, d)
            else:
                gap = min(gap, d)
        deepest = max(deepest, depth)
        if not depth:
            widest = max(widest, gap)
    return deepest, widest


# --- the gear maths ---------------------------------------------------------------------------

@pytest.mark.parametrize("m, z, x", [(2, 24, 0.0), (1.5, 40, 0.0), (2, 12, 0.294), (3, 17, 0.0)])
def test_outline_has_the_teeth_and_the_textbook_tip_and_root(m, z, x):
    pts = mc.outline(m, z, shift=x)
    radii = [math.hypot(px, py) for px, py in pts]
    c = mc.circles(m, z, shift=x)
    assert abs(c["pitch"] - m * z / 2) < 1e-12
    assert abs(max(radii) - (m * z / 2 + m * (1 + x))) < 1e-9       # tip: d_a = m (z + 2 + 2x)
    assert abs(min(radii) - (m * z / 2 - m * (1.25 - x))) < 1e-9    # root: d_f = m (z - 2.5 + 2x)
    tips = sum(1 for i in range(len(radii)) if radii[i] > max(radii) - 1e-9 and radii[i - 1] < max(radii) - 1e-9)
    assert tips == z
    angles = [math.atan2(py, px) for px, py in pts]
    back = sum(1 for i in range(1, len(angles)) if (angles[i] - angles[i - 1] + math.pi) % (2 * math.pi) - math.pi < -1e-12)
    assert back == 0  # the outline goes round once, never backwards


def test_the_involute_flank_is_on_the_base_circle_involute():
    m, z = 2.0, 30
    c = mc.circles(m, z)
    prof = mc.tooth_profile(m, c["pitch"], c["base"], c["tip"], c["root"], math.pi / (2 * z), math.radians(20), 8, 2)
    for r, a in prof:
        if c["base"] + 1e-9 < r < c["tip"] - 1e-9:
            # half thickness at r: s/2r + inv(alpha) - inv(alpha_r)
            want = math.pi / (2 * z) + mc.inv(math.radians(20)) - mc.inv(math.acos(c["base"] / r))
            assert abs(abs(a) - want) < 1e-12


@pytest.mark.parametrize("z1, z2, internal, centre", [(12, 24, False, 36.0), (20, 40, False, 60.0),
                                                       (17, 60, False, 77.0), (18, 60, True, 42.0)])
def test_pair_layout_gives_the_textbook_centre_distance(z1, z2, internal, centre):
    lay = mc.gear_pair_layout(2.0, z1, z2, internal=internal)
    assert abs(lay["centre_distance"] - centre) < 1e-9          # m (z1 + z2) / 2, or m (z2 - z1) / 2 inside a ring
    assert abs(lay["ratio"] - z1 / z2) < 1e-12 and lay["same_direction"] is internal
    if z1 < 17 and not internal:
        assert lay["shift_a"] > 0 and abs(lay["shift_a"] + lay["shift_b"]) < 1e-12  # no undercut, same centre


@pytest.mark.parametrize("z1, z2, internal", [(12, 24, False), (20, 40, False), (8, 9, False), (18, 60, True)])
def test_a_laid_out_pair_meshes_through_a_whole_tooth(z1, z2, internal):
    depth, gap = mesh_2d(2.0, z1, z2, internal)
    assert depth == 0.0                       # never cuts into its mate
    assert gap < checks.MESH_GAP * 2.0        # never floats apart


def test_a_pair_set_too_close_or_too_far_is_caught():
    assert mesh_2d(2.0, 12, 24, nudge=-0.4)[0] > checks.MESH_DEPTH * 2.0   # too close: the teeth cut in
    assert mesh_2d(2.0, 12, 24, nudge=+1.2)[1] > checks.MESH_GAP * 2.0     # too far: they float apart


# --- a request checked and filled in ----------------------------------------------------------

def test_a_mechanism_is_filled_in_and_every_value_is_listed():
    sheet, assumed = mc.clean(PAIR)
    p, w = sheet["parts"]
    assert p["shift"] == 0.294 and w["shift"] == -0.294 and p["width"] == 16.0 and p["bore"] and w["bore"]
    assert sheet["driver"] == "pinion" and sheet["links"] == [{"kind": "mesh", "a": "pinion", "b": "wheel"}]
    assert any(a.startswith("pinion.shift = 0.294") for a in assumed) and "driver = pinion" in assumed
    assert not any(a.startswith("pinion.holes") for a in assumed)  # "none" defaults are not noise in the list
    assert sheet["colours"] == {"pinion": mc.PALETTE[0], "wheel": mc.PALETTE[1]}


@pytest.mark.parametrize("bad, words", [
    ({"parts": [{"name": "a", "type": "spur_gear", "module": 2}]}, "gear a has no tooth count"),
    ({"parts": [{"name": "b", "type": "spur_gear", "teeth": 20}]}, "gear b has no module"),
    ({"parts": [{"name": "a", "type": "spur_gear", "module": 2, "teeth": 20, "meshes_with": "b"},
                {"name": "b", "type": "spur_gear", "module": 2.5, "teeth": 30}]}, "different module"),
    ({"parts": [{"name": "a", "type": "spur_gear", "module": 2, "teeth": 20, "meshes_with": "c"}]},
     "no part named c"),
    ({"parts": [{"name": "a", "type": "worm", "module": 2, "teeth": 20}]}, "unknown type"),
    ({"parts": [{"name": "s", "type": "spur_gear", "module": 2, "teeth": 52, "meshes_with": "r"},
                {"name": "r", "type": "ring_gear", "module": 2, "teeth": 60}]}, "more teeth"),
    ({"parts": [{"name": "a", "type": "spur_gear", "module": 2, "teeth": 20, "meshes_with": ["b", "c"]},
                {"name": "b", "type": "spur_gear", "module": 2, "teeth": 20, "meshes_with": "c"},
                {"name": "c", "type": "spur_gear", "module": 2, "teeth": 20}]}, "loop"),
    ({"parts": [{"name": "a", "type": "spur_gear", "module": 2, "teeth": 20, "bore": 60}]}, "bore 60 mm is too big"),
])
def test_a_mechanism_that_cannot_work_is_refused_with_the_field_named(bad, words):
    with pytest.raises(mc.MechanismError, match=words):
        mc.clean(bad)


def test_the_same_numbers_give_the_same_key():
    a, _ = mc.clean(PAIR)
    b, _ = mc.clean(json.loads(json.dumps(PAIR)))
    c, _ = mc.clean({**PAIR, "rpm": 30})
    assert mc.key(a) == mc.key(b) != mc.key(c)


def test_layout_puts_the_wheel_beside_the_pinion_and_writes_the_scripts():
    sheet, _ = mc.clean(PAIR)
    files, lay = mc.scripts(sheet)
    a, b = lay["place"]["pinion"], lay["place"]["wheel"]
    assert abs(b["centre"][0] - a["centre"][0] - 0.036) < 1e-9 and b["centre"][2] == a["centre"][2]
    assert set(files) == {"parts/pinion.py", "parts/wheel.py", "assembly.py", "materials.py", "motion.py"}
    assert "k.spur_gear(module=0.002, teeth=12" in files["parts/pinion.py"]
    assert "m.mesh_drive(parts['pinion'][0], parts['wheel'][0]" in files["motion.py"]
    for code in files.values():
        compile(code, "x", "exec")
    pair = lay["pairs"][0]
    assert pair["kind"] == "mesh" and abs(pair["centre_m"] - 0.036) < 1e-9 and pair["same_direction"] is False


def test_several_unconnected_parts_share_one_job_side_by_side():
    sheet, _ = mc.clean({"parts": [
        {"name": "a", "type": "spur_gear", "module": 2, "teeth": 20, "meshes_with": "b"},
        {"name": "b", "type": "spur_gear", "module": 2, "teeth": 30},
        {"name": "lone", "type": "helical_gear", "module": 1.5, "teeth": 24},
        {"name": "bar", "type": "rack", "module": 1}]})
    lay = mc.layout(sheet)
    reach = {n: mc._radius(p) for n, p in ((p["name"], p) for p in sheet["parts"])}
    names = list(lay["place"])
    for i, x in enumerate(names):
        for y in names[i + 1:]:
            if {x, y} == {"a", "b"}:
                continue
            gap = abs(lay["place"][x]["centre"][0] - lay["place"][y]["centre"][0])
            assert gap >= reach[x] + reach[y], (x, y)  # nothing overlaps; one style and palette for all
    assert len(set(sheet["colours"].values())) == 4


def test_the_loop_is_the_shortest_stretch_that_repeats():
    sheet, _ = mc.clean(PAIR)
    assert mc.loop_turns(sheet) == Fraction(1, 12)   # a plain pair repeats after one tooth of the pinion
    keyed, _ = mc.clean({"parts": [dict(PAIR["parts"][0]), {**PAIR["parts"][1], "keyway": True}]})
    assert mc.loop_turns(keyed) == Fraction(2)       # the wheel's keyway: one wheel turn, two pinion turns
    s = mc.speeds(sheet)
    assert s["rates"]["wheel"] == Fraction(-1, 2)    # half the speed, the other way round


# --- the mesh gates -----------------------------------------------------------------------------

def _report(**row):
    base = {"a": "pinion", "b": "wheel", "kind": "mesh", "found": True, "ratio": -0.5, "centre_m": 0.036,
            "ratio_expected": 0.5, "same_direction_expected": False, "centre_expected_m": 0.036,
            "max_depth_m": 0.0, "max_gap_m": 0.00005, "module_m": 0.002, "worst_moment": 3}
    return {"moments": 12, "pairs": [{**base, **row}], "clashes": []}


def test_the_mesh_gates_pass_a_correct_pair_and_fail_a_wrong_one():
    assert all(g["pass"] for g in checks.mesh_gates(_report()).values())
    deep = checks.mesh_gates(_report(max_depth_m=0.0004))
    assert not deep["teeth_clear"]["pass"] and "pinion and wheel cut" in deep["teeth_clear"]["detail"]
    assert deep["teeth_clear"]["route"] == "assembly"
    apart = checks.mesh_gates(_report(max_gap_m=0.0012))
    assert not apart["teeth_engaged"]["pass"]
    off = checks.mesh_gates(_report(centre_m=0.0372))
    assert not off["centre_distance"]["pass"]
    wrong = checks.mesh_gates(_report(ratio=0.5))  # the right speed, the wrong way round
    assert not wrong["speed_ratio"]["pass"] and wrong["speed_ratio"]["route"] == "motion"
    clash = checks.mesh_gates({**_report(), "clashes": [{"a": "wheel", "b": "shaft", "max_depth_m": 0.002,
                                                          "moment": 4}]})
    assert not clash["no_clashes"]["pass"]


# --- words: a part is a part ----------------------------------------------------------------------

@pytest.mark.parametrize("text", ["car engine piston", "spur gear pair", "truck gearbox", "bicycle sprocket",
                                  "turbocharger for a sedan", "two gears, the small one drives the big one"])
def test_a_mechanical_word_beats_the_car_generator_and_other_families(text):
    assert families.guess(text) == "mechanical"
    assert not at.looks_like_car(text)


def test_cars_and_vehicles_are_left_as_they_were():
    assert at.looks_like_car("red sedan") and families.guess("school bus") == "vehicle"
    assert families.guess("fire hydrant") == "general"


def test_a_mechanical_request_skips_the_ready_made_catalogue(monkeypatch):
    proj = db.create_project("gear", "a spur gear")
    monkeypatch.setattr(pipeline, "_library_on", lambda: True)
    monkeypatch.setattr(pipeline.asset_catalog, "search", lambda *a, **k: pytest.fail("no catalogue search"))
    monkeypatch.setattr(pipeline.asset_catalog, "load", lambda: [])
    assert asyncio.run(pipeline._try_library(proj["name"], "a spur gear with 30 teeth", None, False)) is None


def test_the_mechanical_rubric_is_used_for_a_mechanism():
    assert at.rubric_file(None, {"family": "mechanical"}).name == "rubric_mechanical.yaml"
    assert at.rubric_file(None, {"family": "furniture"}).name == "rubric.yaml"


# --- the engineer's tool ----------------------------------------------------------------------------

def test_the_engineer_tool_checks_the_sheet_and_names_the_field():
    sink = {}
    submit = at.engineer_tools(sink)[0]
    bad = submit.fn({"mechanism": {"parts": [{"name": "a", "type": "spur_gear", "module": 2}]}})
    assert bad["ok"] is False and "tooth count" in bad["error"] and not sink
    good = submit.fn({"mechanism": PAIR, "notes": "a 2:1 reduction"})
    assert good["ok"] and sink["mechanism"]["driver"] == "pinion" and sink["custom"] == []
    assert good["sizes_mm"]["wheel"]["pitch_diameter"] == 48.0


# --- the /v1 API --------------------------------------------------------------------------------------

@pytest.fixture
def api(monkeypatch):
    monkeypatch.setattr(server.app.state, "run_jobs", False)
    with TestClient(server.app) as c:
        c.headers["X-ModelGen-Key"] = jobs.api_key()
        yield c


def test_get_parts_lists_every_tool_with_its_fields(api):
    out = api.get("/v1/parts").json()
    assert {"spur_gear", "helical_gear", "ring_gear", "rack", "bevel_gear", "shaft"} <= set(out["types"])
    module = out["types"]["spur_gear"]["fields"]["module"]
    assert module["unit"] == "mm" and module["min"] == 0.3
    assert "explainer" in out["styles"] and out["angles"] == ["front", "front34", "side"]
    assert api.get("/v1/parts", headers={"X-ModelGen-Key": "wrong"}).status_code == 401


def test_a_mechanism_job_is_checked_and_refused_by_name(api):
    bad = api.post("/v1/jobs", json={"client": "village", "mechanism": {"parts": [
        {"name": "a", "type": "spur_gear", "module": 2, "teeth": 20, "meshes_with": "b"},
        {"name": "b", "type": "spur_gear", "teeth": 30}]}})
    assert bad.status_code == 400 and "gear b has no module" in bad.json()["detail"]
    ok = api.post("/v1/jobs", json={"client": "village", "mechanism": PAIR, "outputs": ["motion"],
                                    "style": "explainer", "colours": {"wheel": "#123456"}}).json()
    assert ok["status"] == "queued" and ok["object"] == "spur gear pair 12/24"
    assert any(a.startswith("pinion.shift") for a in ok["assumed"])
    assert ok["request"]["mechanism"]["colours"]["wheel"] == "#123456"
    assert api.post("/v1/jobs", json={"client": "village", "mechanism": PAIR,
                                      "colours": {"axle": "#123456"}}).status_code == 400


def _fake_parts(monkeypatch):
    calls = []

    async def build(project, sheet, assumed, note=""):
        calls.append(project)
        n = db.highest_version_number(project) + 1
        folder = pipeline.version_dir(project, n)
        (folder / "game").mkdir(parents=True)
        (folder / "game" / f"{project}_game.glb").write_bytes(b"glb")
        (folder / "plan.json").write_text(json.dumps({"kind": "parts", "parts": [], "mechanism": sheet}),
                                          encoding="utf-8")
        (folder / "mechanism.json").write_text(json.dumps({"sheet": sheet, "assumed": assumed, "key": mc.key(sheet),
                                                           "colours": sheet["colours"]}), encoding="utf-8")
        db.add_version(project, n)
        db.update_version(project, n, status="done", passed=1)
        return {"ok": True, "parts": True, "version": n, "score": None, "passed": True}

    monkeypatch.setattr(pipeline, "_run_parts", build)
    monkeypatch.setattr(config, "blender_path", lambda: "blender.exe")

    async def no_agent(*a, **k):
        pytest.fail("no agent may run for a mechanism the tools build")

    monkeypatch.setattr(runner, "run_agent", no_agent)
    return calls


def test_a_mechanism_job_builds_with_no_agent_and_reuses_the_same_numbers(api, monkeypatch):
    calls = _fake_parts(monkeypatch)
    first = api.post("/v1/jobs", json={"client": "village", "mechanism": PAIR, "outputs": ["game"]}).json()
    asyncio.run(jobs.run(db.get_job(first["id"])))
    out = jobs.public(db.get_job(first["id"]))
    assert out["status"] == "done" and out["source"] == "part_tools" and out["passed"] and len(calls) == 1
    assert out["cost_usd"] == 0.0 and any(a.startswith("wheel.bore") for a in out["assumed"])
    assert out["main"]["game"].endswith("_game.glb") and out["mechanism"]["key"] == mc.key(mc.clean(PAIR)[0])
    again = api.post("/v1/jobs", json={"client": "village", "mechanism": PAIR, "outputs": ["game"],
                                       "request_key": "second"}).json()
    asyncio.run(jobs.run(db.get_job(again["id"])))
    out2 = jobs.public(db.get_job(again["id"]))
    assert out2["status"] == "done" and out2["source"] == "existing" and out2["project"] == out["project"]
    assert len(calls) == 1  # no second build, no caster


# --- real Blender --------------------------------------------------------------------------------------

KIT = '''from bpy_lib import kits as k

def build_spur():
    return k.spur_gear(0.002, 12, 0.016, bore=0.006, shift=0.294, keyway=True, name="spur")

def build_helical():
    return k.helical_gear(0.002, 20, 0.02, bore=0.008, helix_angle=25, name="helical")

def build_herring():
    return k.helical_gear(0.002, 24, 0.024, bore=0.008, helix_angle=25, herringbone=True, name="herring")

def build_holes():
    return k.spur_gear(0.002, 40, 0.016, bore=0.012, hub=(0.03, 0.026), holes=6, name="holes")

def build_spokes():
    return k.spur_gear(0.002, 48, 0.016, bore=0.012, hub=(0.03, 0.026), spokes=5, name="spokes")

def build_ring():
    return k.ring_gear(0.002, 60, 0.016, name="ring")

def build_rack():
    return k.rack(0.002, 12, 0.016, name="rack")

def build_bevel():
    return k.bevel_gear(0.002, 20, 30, bore=0.008, hub=(0.02, 0.01), keyway=True, name="bevel")

def build_shaft():
    return k.shaft(0.008, 0.08, keyway=True, name="shaft")
'''


@pytest.mark.blender
def test_every_gear_tool_makes_one_closed_solid_at_its_real_size(tmp_path):
    file = tmp_path / "kit.py"
    file.write_text(KIT, encoding="utf-8")
    names = ["spur", "helical", "herring", "holes", "spokes", "ring", "rack", "bevel", "shaft"]
    result = blender.run_job([blender.part_step(n, file, func=f"build_{n}") for n in names] + [blender.measure_step()],
                             timeout=600)
    assert result.ok, result.traceback
    parts = {p["part"]: p for p in result.outputs["measure"]["parts"]}
    for n in names:
        assert parts[n]["non_manifold_edges"] == 0 and parts[n]["islands"] == 1, (n, parts[n])
        assert parts[n]["triangles"] < 12000, (n, parts[n]["triangles"])
    tip = 2 * mc.circles(2.0, 12, shift=0.294)["tip"] / 1000
    assert abs(parts["spur"]["size"][0] - tip) < 0.0002 and abs(parts["spur"]["size"][2] - 0.016) < 1e-5
    assert abs(parts["ring"]["size"][0] - (2 * 62.5 + 12) / 1000) < 0.0005  # root diameter 125 mm + 6 modules
    assert abs(parts["rack"]["size"][0] - 12 * math.pi * 0.002) < 1e-5


@pytest.mark.blender
def test_a_real_12_24_pair_meshes_through_its_loop_and_renders_a_transparent_loop(tmp_path):
    from PIL import Image
    sheet, assumed = mc.clean(PAIR)
    proj = db.create_project("gear pair", "spur gear pair 12/24", options={"mechanism": sheet, "assumed": assumed})

    async def no_agent(*a, **k):
        pytest.fail("no agent may run")

    runner_run = runner.run_agent
    runner.run_agent = no_agent
    try:
        out = asyncio.run(pipeline._run_parts(proj["name"], sheet, assumed))
    finally:
        runner.run_agent = runner_run
    assert out["ok"] and out["passed"], out
    folder = pipeline.version_dir(proj["name"], out["version"])
    mesh = json.loads((folder / "mesh_check.json").read_text(encoding="utf-8"))["pairs"][0]
    assert mesh["max_depth_m"] == 0 and mesh["max_gap_m"] < 0.0002 and abs(mesh["ratio"] + 0.5) < 1e-3
    assert abs(mesh["centre_m"] - 0.036) < 1e-5
    error = asyncio.run(jobs.render_motion(proj["name"], out["version"], "explainer", "persp", "front34",
                                           sheet["colours"]))
    assert error is None
    made = folder / "motion" / "explainer-persp"
    record = json.loads((made / "motion.json").read_text(encoding="utf-8"))
    assert record["frames"] == 12 and record["fps"] == 24 and len(record["files"]) == 12
    where = json.loads((made / "parts.json").read_text(encoding="utf-8"))["frames"]
    assert len(where) == 12 and {p["name"] for p in where[0]["parts"]} == {"pinion", "wheel"}
    assert where[0]["parts"][0]["colour"] == mc.PALETTE[0]
    with Image.open(made / "frame_000.png") as im:
        assert im.mode == "RGBA" and im.getpixel((1, 1))[3] == 0
        box = where[0]["parts"][1]["box_px"]
        cx, cy = (box[0] + box[2]) / 2, (box[1] + box[3]) / 2
        assert im.getpixel((int(cx), int(box[1] + 0.2 * (box[3] - box[1]))))[3] == 255
