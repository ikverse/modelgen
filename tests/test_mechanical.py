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
    ({"parts": [{"name": "a", "type": "flywheel", "module": 2, "teeth": 20}]}, "unknown type"),
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


# --- stage 2: the turbocharger --------------------------------------------------------------------------

TURBO = {"parts": [
    {"name": "shaft", "type": "shaft"},
    {"name": "compressor", "type": "compressor_wheel", "diameter": 50, "on_shaft": "shaft"},
    {"name": "bearing_front", "type": "bearing", "on_shaft": "shaft"},
    {"name": "bearing_back", "type": "bearing", "on_shaft": "shaft"},
    {"name": "turbine", "type": "turbine_wheel", "diameter": 45, "on_shaft": "shaft"},
    {"name": "compressor_housing", "type": "volute", "houses": "compressor"},
    {"name": "turbine_housing", "type": "volute", "houses": "turbine"},
    {"name": "centre_housing", "type": "centre_housing", "houses": "shaft"}], "rpm": 30}


def test_a_turbocharger_sheet_is_filled_in_once_and_checked():
    sheet, assumed = mc.clean(TURBO)
    assert mc.describe(sheet) == "turbocharger with housings" and sheet["driver"] == "shaft"
    assert sum(a.startswith("shaft.diameter") for a in assumed) == 1
    by = {p["name"]: p for p in sheet["parts"]}
    assert by["compressor"]["bore"] == by["shaft"]["diameter"] == by["bearing_front"]["bore"]
    with pytest.raises(mc.MechanismError, match="houses no wheel"):
        mc.clean({"parts": [{"name": "v", "type": "volute"}]})
    with pytest.raises(mc.MechanismError, match="not a compressor or turbine"):
        mc.clean({"parts": [{"name": "g", "type": "spur_gear", "module": 2, "teeth": 20},
                            {"name": "v", "type": "volute", "houses": "g"}]})
    with pytest.raises(mc.MechanismError, match="no diameter"):
        mc.clean({"parts": [{"name": "w", "type": "turbine_wheel"}]})


def test_the_turbo_layout_faces_the_wheels_apart_and_houses_them():
    sheet, _ = mc.clean(TURBO)
    lay = mc.layout(sheet)
    pl = lay["place"]
    axis = mc._col(pl["shaft"]["rotation"], 2)
    comp, turb = mc._col(pl["compressor"]["rotation"], 2), mc._col(pl["turbine"]["rotation"], 2)
    assert mc._dot(comp, axis) > 0.99 and mc._dot(turb, axis) < -0.99  # noses point away from each other
    assert pl["compressor_housing"]["centre"] == pl["compressor"]["centre"]
    gap = abs(mc._dot(mc._sub(pl["turbine"]["centre"], pl["compressor"]["centre"]), axis))
    by = {p["name"]: p for p in sheet["parts"]}
    backs = gap - (by["compressor"]["length"] + by["turbine"]["length"]) / 2000
    assert abs(backs - mc.TURBO_SPACING * 0.05) < 1e-6  # the back faces as far apart as a real turbo's
    # the shaft ends flush with both wheels' noses
    ends = sorted(mc._dot(mc._sub(pl[w]["centre"], pl["shaft"]["centre"]), axis) for w in ("compressor", "turbine"))
    half = (ends[1] - ends[0]) / 2 + (by["compressor"]["length"] + by["turbine"]["length"]) / 4000
    assert abs(pl["shaft"]["length_mm"] / 2000 - half) < 1e-6
    kinds = {(p["a"], p["b"]): p["kind"] for p in lay["pairs"]}
    assert kinds[("compressor", "compressor_housing")] == "housing"
    off = mc.exploded_offsets(sheet, lay)
    along = {n: mc._dot(v, axis) for n, v in off.items()}
    assert along["shaft"] == 0 and along["compressor_housing"] > along["compressor"] > 0 > along["turbine"] \
        > along["turbine_housing"]
    assert mc.loop_turns(sheet) == 3  # wheels repeat every turn; the bearings' balls every three


def test_the_centre_housing_fills_the_room_between_the_volutes_round_the_bearings():
    sheet, assumed = mc.clean(TURBO)
    assert "centre_housing.outer_diameter = 55 (1.1 x the compressor wheel)" in assumed
    lay = mc.layout(sheet)
    pl = lay["place"]
    axis = mc._col(pl["shaft"]["rotation"], 2)
    hs = pl["centre_housing"]["housing"]
    mid = mc._dot(pl["centre_housing"]["centre"], axis)
    front, back = mid + hs["length"] / 2000, mid - hs["length"] / 2000
    by = {p["name"]: p for p in sheet["parts"]}
    for wheel, end in (("compressor", front), ("turbine", back)):
        w = by[wheel]
        plate = mc.wheel_clearance(w["diameter"]) + 0.035 * w["diameter"]
        face = abs(mc._dot(pl[wheel]["centre"], axis) - end) * 1000
        assert abs(face - (w["length"] / 2 + plate + mc.HOUSING_GAP)) < 1e-6  # just clear of the back plate
    for b in ("bearing_front", "bearing_back"):
        s = mc._dot(pl[b]["centre"], axis)
        assert back + hs["end_wall"] / 1000 < s < front - hs["end_wall"] / 1000
        assert hs["bore_diameter"] > by[b]["outer_diameter"]
    assert mc._dot(mc._col(pl["centre_housing"]["rotation"], 0), [0, 0, 1]) > 0.99  # the oil inlet on top
    assert mc._dot(mc._col(pl["centre_housing"]["rotation"], 2), axis) > 0.99  # front: the compressor side
    assert "centre_housing" in mc.housings(sheet) and mc.loop_turns(sheet) == 3  # it stands still
    off = mc.exploded_offsets(sheet, lay)
    assert off["centre_housing"][:2] == [0.0, 0.0] and off["centre_housing"][2] > 0.03  # lifted off the bearings
    assert f"k.centre_housing(length={mc._m(hs['length'])}," in mc.part_code(by["centre_housing"], lay, by)


def test_a_shaft_given_its_length_is_centred_on_the_parts_it_carries():
    raw = json.loads(json.dumps(TURBO))
    raw["parts"][0]["length"] = 150
    sheet, _ = mc.clean(raw)
    lay = mc.layout(sheet)
    pl = lay["place"]
    axis = mc._col(pl["shaft"]["rotation"], 2)
    mid = (mc._dot(pl["compressor"]["centre"], axis) + mc._dot(pl["turbine"]["centre"], axis)) / 2
    assert pl["shaft"]["length_mm"] == 150 and abs(mc._dot(pl["shaft"]["centre"], axis) - mid) < 0.01


@pytest.mark.parametrize("change, words", [
    ({"houses": "compressor"}, "not a shaft"),
    ({"houses": None}, "houses no shaft"),
    ({"outer_diameter": 8}, "at least"),
    ({"outer_diameter": 80}, "at most"),
])
def test_a_centre_housing_that_cannot_fit_is_refused(change, words):
    raw = json.loads(json.dumps(TURBO))
    raw["parts"][-1].update(change)
    with pytest.raises(mc.MechanismError, match=words):
        mc.clean(raw)
    one_wheel = {"parts": [{"name": "s", "type": "shaft"},
                           {"name": "c", "type": "compressor_wheel", "diameter": 50, "on_shaft": "s"},
                           {"name": "h", "type": "centre_housing", "houses": "s"}]}
    with pytest.raises(mc.MechanismError, match="one of each"):
        mc.clean(one_wheel)


def test_a_blade_into_its_housing_fails_blades_clear():
    report = {"moments": 12, "clashes": [], "pairs": [
        {"a": "turbine", "b": "turbine_housing", "kind": "housing", "found": True, "max_depth_m": 0.0004,
         "max_gap_m": 0.0, "worst_moment": 2}]}
    gates = checks.mesh_gates(report)
    assert not gates["blades_clear"]["pass"] and "turbine pass 0.40 mm into turbine_housing" in gates["blades_clear"]["detail"]
    report["pairs"][0]["max_depth_m"] = 0.0
    assert checks.mesh_gates(report)["blades_clear"]["pass"]


# --- stage 3: piston and crank --------------------------------------------------------------------------

ENGINE = {"parts": [
    {"name": "crank", "type": "crankshaft", "stroke": 80},
    {"name": "rod", "type": "connecting_rod", "on_crank": "crank"},
    {"name": "piston", "type": "piston", "bore": 80, "on_rod": "rod"},
    {"name": "rings", "type": "piston_rings", "on_piston": "piston"},
    {"name": "cylinder", "type": "cylinder", "houses": "piston"}], "rpm": 20}


def test_a_single_cylinder_is_filled_in_from_real_proportions_and_laid_out_exactly():
    sheet, assumed = mc.clean(ENGINE)
    assert mc.describe(sheet) == "single-cylinder piston and crank" and sheet["driver"] == "crank"
    assert "rod.length = 136 (1.7 x stroke)" in assumed and "piston.pin_diameter = 20 (0.25 x bore)" in assumed
    by = {p["name"]: p for p in sheet["parts"]}
    lay = mc.layout(sheet)
    pl = lay["place"]
    up = [0.0, 0.0, 1.0]
    assert abs(mc._dot(pl["piston"]["centre"], up) - (0.040 + 0.136)) < 1e-9  # top dead centre: r + l
    assert pl["rings"]["centre"] == pl["piston"]["centre"]
    foot = mc._dot(pl["cylinder"]["centre"], up)
    assert foot * 1000 > mc.crank_sweep(by["crank"]) + 1  # the cylinder stands clear of the crank's sweep
    top = foot + by["cylinder"]["length"] / 1000
    assert 0 < top - (0.176 + 0.0288) < 0.001  # the crown just inside the top at top dead centre
    low = mc.piston_grooves(80, 28.8)[-1][1] / 1000
    assert 0.040 * 0 + 0.136 - 0.040 + low > foot  # at the bottom the rings stay in the cylinder
    kinds = {p["kind"] for p in lay["pairs"]}
    assert kinds == {"crank", "pin", "ring", "liner"} and mc.loop_turns(sheet) == 1
    motion = mc.scripts(sheet)[0]["motion.py"]
    assert "m.crank_slider(parts['crank'][0], parts['rod'][0], parts['piston'][0]" in motion
    assert "followers=[parts['rings'][0]]" in motion and "mesh_drive" not in motion
    off = mc.exploded_offsets(sheet, lay)
    assert off["crank"][2] == 0 < off["rod"][2] < off["piston"][2] < off["rings"][2] < off["cylinder"][2]
    assert mc.housings(sheet) == ["cylinder"]


def test_four_throws_take_the_inline_firing_order_and_room_for_their_cylinders():
    parts = [{"name": "crank", "type": "crankshaft", "stroke": 80}]
    for i in range(4):
        parts += [{"name": f"rod{i}", "type": "connecting_rod", "on_crank": "crank"},
                  {"name": f"piston{i}", "type": "piston", "bore": 82, "on_rod": f"rod{i}"},
                  {"name": f"cyl{i}", "type": "cylinder", "houses": f"piston{i}"}]
    sheet, assumed = mc.clean({"parts": parts})
    crank = sheet["parts"][0]
    assert crank["throw_angles"] == [0, 180, 180, 0] and crank["spacing"] >= 1.32 * 82
    lay = mc.layout(sheet)
    xs = sorted(mc._dot(lay["place"][f"cyl{i}"]["centre"], [0, -1, 0]) for i in range(4))
    assert all(abs((b - a) * 1000 - crank["spacing"]) < 1e-6 for a, b in zip(xs, xs[1:]))
    tops = {i: lay["place"][f"piston{i}"]["centre"][2] for i in range(4)}
    assert abs(tops[0] - tops[3]) < 1e-9 and tops[1] < tops[0] - 0.079  # 1 and 4 up, 2 and 3 down
    assert mc.describe(sheet) == "4-cylinder piston and crank"


@pytest.mark.parametrize("change, words", [
    ({1: {"length": 90}}, "at least 2.5 x the crank radius"),
    ({1: {"length": 102}, 2: {"skirt_length": 60}}, "too short for counterweights"),
    ({2: {"on_rod": None}}, "has no on_rod"),
    ({2: {"on_rod": "crank"}}, "not a connecting rod"),
    ({0: {"stroke": None}, 2: {"bore": None}}, "no stroke"),
    ({2: {"pin_diameter": 40}}, "at most 0.4"),
    ({"driver": "piston"}, "name the crankshaft"),
])
def test_an_engine_that_cannot_run_is_refused_with_the_number_to_change(change, words):
    raw = json.loads(json.dumps(ENGINE))
    for k, v in change.items():
        if k == "driver":
            raw["driver"] = v
        else:
            raw["parts"][k].update(v)
    with pytest.raises(mc.MechanismError, match=words):
        mc.clean(raw)
    lonely = {"parts": [{"name": "crank", "type": "crankshaft", "stroke": 80},
                        {"name": "rod", "type": "connecting_rod", "on_crank": "crank"}]}
    with pytest.raises(mc.MechanismError, match="has no piston"):
        mc.clean(lonely)


def test_the_engine_gates_catch_a_rod_off_its_pin_and_a_piston_out_of_its_cylinder():
    good = {"moments": 12, "clashes": [], "pairs": [
        {"a": "rod", "b": "crank", "kind": "crank", "found": True, "max_offset_m": 0.00004},
        {"a": "rod", "b": "piston", "kind": "pin", "found": True, "max_offset_m": 0.00003},
        {"a": "piston", "b": "cylinder", "kind": "liner", "found": True, "max_off_axis_m": 0.0, "top_room_m": 0.0005,
         "bottom_room_m": 0.03, "stroke_m": 0.08, "stroke_expected_m": 0.08}]}
    gates = checks.mesh_gates(good)
    assert gates["pins_in_bores"]["pass"] and gates["piston_in_liner"]["pass"]
    bad = json.loads(json.dumps(good))
    bad["pairs"][1]["max_offset_m"] = 0.002
    bad["pairs"][2]["top_room_m"] = -0.003
    gates = checks.mesh_gates(bad)
    assert not gates["pins_in_bores"]["pass"] and "rod comes 2.00 mm off the gudgeon pin of piston" in \
        gates["pins_in_bores"]["detail"]
    assert not gates["piston_in_liner"]["pass"] and "rises 3.00 mm out of the top" in gates["piston_in_liner"]["detail"]


# --- stage 4: the remaining parts --------------------------------------------------------------------------

FASTENERS = {"parts": [{"name": "bolt", "type": "bolt", "diameter": 10, "length": 50},
                       {"name": "nut", "type": "nut", "on_bolt": "bolt"},
                       {"name": "washer", "type": "washer", "on_bolt": "bolt"}], "driver": "nut", "rpm": 30}
BELT = {"parts": [{"name": "p1", "type": "pulley", "datum_diameter": 80},
                  {"name": "p2", "type": "pulley", "datum_diameter": 160},
                  {"name": "belt", "type": "belt", "on": ["p1", "p2"]}]}
CHAIN = {"parts": [{"name": "s1", "type": "sprocket", "teeth": 18}, {"name": "s2", "type": "sprocket", "teeth": 36},
                   {"name": "chain", "type": "chain", "on": ["s1", "s2"], "centre_distance": 400}]}
CAM = {"parts": [{"name": "cam", "type": "cam", "base_diameter": 40, "lift": 10},
                 {"name": "f", "type": "follower", "on_cam": "cam"}]}
VALVE = {"parts": [{"name": "cam", "type": "cam", "base_diameter": 36, "lift": 9, "rise": 110, "dwell": 20, "fall": 110},
                   {"name": "valve", "type": "valve", "head_diameter": 32, "on_cam": "cam"},
                   {"name": "spring", "type": "spring", "on_valve": "valve"}]}
PLANETARY = {"parts": [{"name": "ps", "type": "planetary_set", "module": 2, "sun_teeth": 24, "planet_teeth": 24}],
             "rpm": 20}
WORM = {"parts": [{"name": "worm", "type": "worm", "module": 2},
                  {"name": "wheel", "type": "worm_wheel", "teeth": 30, "meshes_with": "worm"}], "rpm": 60}


def test_a_nut_sits_on_its_bolts_thread_and_screws_off_and_back():
    sheet, assumed = mc.clean(FASTENERS)
    assert "bolt.pitch = 1.5 (ISO coarse M10)" in assumed and mc.describe(sheet) == "bolt and nut"
    lay = mc.layout(sheet)
    pl = lay["place"]
    assert abs(pl["nut"]["centre"][2] - (-0.050 + 5 * 0.0015)) < 1e-9  # five pitches of thread left below it
    assert abs(pl["nut"]["phase"] - (2 * math.pi * (-42.5 / 1.5)) % (2 * math.pi)) < 1e-9  # its thread in the bolt's
    assert abs(pl["washer"]["centre"][2] - (pl["nut"]["centre"][2] + 0.0084)) < 1e-9
    assert "m.screw(parts['nut'][0], pitch=0.0015, travel=0.006, rpm=30.0)" in mc.scripts(sheet)[0]["motion.py"]
    off = mc.exploded_offsets(sheet, lay)
    assert off["nut"][2] < off["washer"][2] < 0 == off["bolt"][2]
    with pytest.raises(mc.MechanismError, match="too short for its nut"):
        mc.clean({**FASTENERS, "parts": [{**FASTENERS["parts"][0], "length": 12}] + FASTENERS["parts"][1:]})
    with pytest.raises(mc.MechanismError, match="not an ISO size"):
        mc.clean({"parts": [{"name": "b", "type": "bolt", "diameter": 11}]})


def test_a_spring_is_never_squeezed_solid():
    sheet, assumed = mc.clean({"parts": [{"name": "s", "type": "spring"}]})
    s = sheet["parts"][0]
    assert s["free_length"] * (1 - s["squeeze"]) > (s["coils"] + 1) * s["wire_diameter"]
    assert "m.squash(parts['s'][0], share=0.3" in mc.scripts(sheet)[0]["motion.py"]
    with pytest.raises(mc.MechanismError, match="squeeze at most"):
        mc.clean({"parts": [{"name": "s", "type": "spring", "squeeze": 0.8}]})


def test_a_belt_turns_its_pulleys_the_same_way_at_the_diameter_ratio():
    sheet, assumed = mc.clean(BELT)
    lay = mc.layout(sheet)
    assert abs(lay["place"]["p2"]["centre"][0] - 0.240) < 1e-9 and lay["place"]["belt"]["centre"] == [0.0, 0.0, 0.0]
    drive = next(p for p in lay["pairs"] if p["kind"] == "drive")
    assert drive["ratio"] == 0.5 and drive["same_direction"]
    assert "m.mesh_drive(parts['p1'][0], parts['p2'][0], ratio=0.5, same_direction=True)" in \
        mc.scripts(sheet)[0]["motion.py"]
    with pytest.raises(mc.MechanismError, match="wraps only"):
        mc.clean({"parts": [{"name": "p1", "type": "pulley", "datum_diameter": 60},
                            {"name": "p2", "type": "pulley", "datum_diameter": 400},
                            {"name": "belt", "type": "belt", "on": ["p1", "p2"], "centre_distance": 250}]})


@pytest.mark.parametrize("z1, z2, centre", [(18, 18, 300), (18, 36, 400), (13, 42, 350), (17, 25, 250)])
def test_a_chain_closes_with_every_link_one_pitch_and_an_even_count(z1, z2, centre):
    lay = mc.chain_layout(12.7, z1, z2, centre)
    s = lay["slots"]
    links = [math.dist(s[i][:2], s[(i + 1) % len(s)][:2]) for i in range(len(s))]
    assert lay["links"] % 2 == 0 and max(abs(d - 12.7) for d in links) < 1e-9
    assert abs(lay["centre"] - centre) < 6.35  # within half a pitch of the distance asked for
    for i, (x, y, k, c) in enumerate(s):  # every roller on a sprocket sits on its pitch circle, in a tooth gap
        if k is not None:
            r = mc.sprocket_radius(12.7, (z1, z2)[k])
            assert abs(math.hypot(x - c[0], y - c[1]) - r) < 1e-9
            gap = (math.atan2(y - c[1], x - c[0]) - lay["phase"][k]) / (2 * math.pi / (z1, z2)[k])
            assert abs(gap - round(gap)) < 1e-9


def test_a_chain_drive_sheet_moves_its_chain_with_the_first_sprocket():
    sheet, assumed = mc.clean(CHAIN)
    assert any("90 links" in a for a in assumed)
    motion = mc.scripts(sheet)[0]["motion.py"]
    assert "m.chain_drive(parts['chain'][0], parts['s1'][0], teeth=18)" in motion
    assert "ratio=0.5, same_direction=True" in motion


def test_a_cam_lifts_its_follower_by_the_cycloidal_law_and_refuses_one_that_would_jam():
    sheet, assumed = mc.clean(CAM)
    f = sheet["parts"][1]
    assert f["table"][0] == 28.0 and abs(max(f["table"]) - 38.0) < 1e-6  # base 20 + roller 8, + the 10 mm lift
    assert mc.cam_motion(60, lift=10)[0] == pytest.approx(5.0)  # half the lift half way up the rise
    lay = mc.layout(sheet)
    assert abs(lay["place"]["f"]["centre"][2] - 0.028) < 1e-9
    assert "m.cam_drive(parts['cam'][0], parts['f'][0], table=[0.028," in mc.scripts(sheet)[0]["motion.py"]
    with pytest.raises(mc.MechanismError, match="jam"):
        mc.clean({"parts": [{"name": "cam", "type": "cam", "base_diameter": 20, "lift": 25, "rise": 40},
                            {"name": "f", "type": "follower", "on_cam": "cam"}]})


def test_a_valve_hangs_under_its_cam_with_a_spring_sized_to_its_lift():
    sheet, assumed = mc.clean(VALVE)
    by = {p["name"]: p for p in sheet["parts"]}
    lay = mc.layout(sheet)
    assert lay["place"]["valve"]["centre"][2] < 0 and abs(lay["place"]["cam"]["phase"] - math.pi) < 1e-12
    spring = by["spring"]
    assert spring["free_length"] - 9 > (spring["coils"] + 1) * spring["wire_diameter"]
    assert "spring=parts['spring'][0]" in mc.scripts(sheet)[0]["motion.py"]
    assert mc.describe(sheet) == "cam and valve"


def test_a_planetary_set_turns_by_willis_and_spaces_its_planets_evenly():
    sheet, assumed = mc.clean(PLANETARY)
    names = {p["name"] for p in sheet["parts"]}
    assert names == {"ps_sun", "ps_planet1", "ps_planet2", "ps_planet3", "ps_ring", "ps_carrier"}
    w = mc.planetary_rates(sheet["planetary"][0])
    assert w["carrier"] == Fraction(24, 96) and w["ring"] == 0  # sun in, ring held: 1 + 72/24 = 4 to 1
    lay = mc.layout(sheet)
    pl = lay["place"]
    assert max(abs(v) for v in pl["ps_ring"]["centre"]) < 1e-12 and max(abs(v) for v in pl["ps_carrier"]["centre"]) < 1e-12
    assert all(abs(math.dist(pl[f"ps_planet{k}"]["centre"], [0, 0, 0]) - 0.048) < 1e-9 for k in (1, 2, 3))
    train = next(p for p in lay["pairs"] if p["kind"] == "train")
    assert train["ratio"] == 0.25 and train["same_direction"]
    assert mc.describe(sheet) == "planetary gear set 24/24/72" and mc.loop_turns(sheet) == 4
    with pytest.raises(mc.MechanismError, match="spaced evenly"):
        mc.clean({"parts": [{"name": "ps", "type": "planetary_set", "module": 2, "sun_teeth": 25, "planet_teeth": 24}]})


def test_each_planet_sits_on_its_carriers_pin_and_a_part_off_its_mount_is_caught():
    sheet, assumed = mc.clean(PLANETARY)
    lay = mc.layout(sheet)
    pl = lay["place"]
    pc = pl["ps_carrier"]
    for n, local in zip(sheet["planetary"][0]["planets"], pc["pins_local_m"]):
        pin = mc._add(pc["centre"], mc._apply(pc["rotation"], local))
        assert math.dist(pin, pl[n]["centre"]) < 1e-12  # the pin stands exactly where its planet was laid out
    assert "pin_angles=[90.0, -150.0, -30.0]" in mc.scripts(sheet)[0]["parts/ps_carrier.py"]
    kinds = [p["kind"] for p in lay["pairs"]]
    assert kinds.count("pin_mount") == 3 and kinds.count("mount") == 1
    # laid out wrong (a planet moved off its pin), the sheet is refused before anything is built
    by = {p["name"]: p for p in sheet["parts"]}
    pl["ps_planet2"]["centre"] = mc._add(pl["ps_planet2"]["centre"], [0.004, 0.0, 0.0])
    with pytest.raises(mc.MechanismError, match="ps_planet2 is 4.000 mm off its pin"):
        mc._check_mounts(sheet, by, pl, sheet["links"])
    # and a gear moved beside its shaft
    s2, _ = mc.clean(TURBO)
    lay2 = mc.layout(s2)
    lay2["place"]["compressor"]["centre"] = mc._add(lay2["place"]["compressor"]["centre"], [0.0, 0.0, 0.002])
    with pytest.raises(mc.MechanismError, match="compressor is 2.000 mm off the axis of its shaft shaft"):
        mc._check_mounts(s2, {p["name"]: p for p in s2["parts"]}, lay2["place"], s2["links"])


def test_the_mount_gate_fails_a_planet_off_its_pin_even_when_nothing_clashes():
    report = {"moments": 12, "clashes": [], "pairs": [
        {"a": "planet2", "b": "carrier", "kind": "pin_mount", "found": True, "max_offset_m": 0.0124,
         "max_tilt_deg": 0.0},
        {"a": "wheel", "b": "shaft", "kind": "shaft", "found": True, "max_offset_m": 0.0, "max_tilt_deg": 0.0}]}
    gates = checks.mesh_gates(report)
    assert gates["no_clashes"]["pass"] and not gates["parts_on_their_mounts"]["pass"]
    assert "planet2 sits 12.40 mm off its pin on carrier" in gates["parts_on_their_mounts"]["detail"]
    report["pairs"][0]["max_offset_m"] = 0.00001
    assert checks.mesh_gates(report)["parts_on_their_mounts"]["pass"]


def test_a_shaft_too_short_for_its_parts_is_refused_and_caught():
    raw = json.loads(json.dumps(TURBO))
    raw["parts"][0]["length"] = 40
    with pytest.raises(mc.MechanismError, match=r"shaft shaft is too short: \w+ reaches [\d.]+ mm past its end; make "
                                                r"it at least \d+ mm long"):
        mc.clean(raw)
    report = {"moments": 12, "clashes": [], "pairs": [
        {"a": "wheel", "b": "shaft", "kind": "shaft", "found": True, "max_offset_m": 0.0, "max_tilt_deg": 0.0,
         "beyond_end_m": 0.003}]}
    gate = checks.mesh_gates(report)["parts_on_their_mounts"]
    assert not gate["pass"] and "wheel stands 3.00 mm past the end of shaft" in gate["detail"]


def test_a_worm_drives_its_wheel_one_tooth_a_turn_from_below():
    sheet, assumed = mc.clean(WORM)
    lay = mc.layout(sheet)
    assert abs(lay["place"]["wheel"]["centre"][2] - 0.040) < 1e-9  # (20 + 60) / 2 above the worm's axis
    pair = lay["pairs"][0]
    assert pair["ratio"] == pytest.approx(1 / 30) and not pair["same_direction"]
    assert mc.describe(sheet) == "worm drive 1:30"
    with pytest.raises(mc.MechanismError, match="worm wheel cannot drive"):
        mc.clean({**WORM, "driver": "wheel"})
    with pytest.raises(mc.MechanismError, match="meshes only with a worm wheel"):
        mc.clean({"parts": [{"name": "worm", "type": "worm", "module": 2},
                            {"name": "g", "type": "spur_gear", "module": 2, "teeth": 30, "meshes_with": "worm"}]})


# --- stage 5: the Village's 2D designs in ----------------------------------------------------------------

def _drawn_gear(cx, cy, teeth, module_px, turn_deg=0.0):
    """A gear's outline as the Village draws one: a path of straight-flanked teeth, y pointing down."""
    tip, root = module_px * (teeth + 2) / 2, module_px * (teeth - 2.5) / 2
    pitch = 2 * math.pi / teeth
    pts = []
    for k in range(teeth):
        base = math.radians(turn_deg) + k * pitch
        for f, r in ((-0.5, root), (-0.3, root), (-0.16, tip), (0.16, tip), (0.3, root)):
            a = base + f * pitch
            pts.append([round(cx + r * math.cos(a), 3), round(cy - r * math.sin(a), 3)])
    return pts


def village_gears():
    """A Village vector design of a 24-tooth gear driving a 12-tooth pinion (drawn up and to the right of
    it, at 20 degrees), each with a bore and the big one a hub, plus a shadow and a label line."""
    m = 5.0
    a, b = (140.0, 150.0), (140 + 90 * math.cos(math.radians(20)), 150 - 90 * math.sin(math.radians(20)))
    return {"kind": "vector", "label": "two gears for the gear-ratio video", "canvas": {"width": 400, "height": 300},
            "palette": {"A": "#000000", "B": "#e4572e", "C": "#2a1c2c", "D": "#f2c14e", "E": "#ffffff",
                        "F": "#b8442a"},
            "parts": [
                {"name": "shadow", "shapes": [{"type": "ellipse", "cx": 180, "cy": 260, "rx": 120, "ry": 6,
                                               "fill": "A", "opacity": 0.35}], "pivot": [180, 260], "z": 0},
                {"name": "big gear", "shapes": [
                    {"type": "path", "points": _drawn_gear(*a, 24, m, 5.0), "fill": "B", "stroke": "C", "width": 2},
                    {"type": "ellipse", "cx": a[0], "cy": a[1], "rx": 22, "ry": 22, "fill": "F"},
                    {"type": "ellipse", "cx": a[0], "cy": a[1], "rx": 8, "ry": 8, "fill": "E"}],
                 "pivot": list(a), "z": 1},
                {"name": "pinion", "shapes": [
                    {"type": "path", "points": _drawn_gear(*b, 12, m, 3.0), "fill": "D", "stroke": "C", "width": 2},
                    {"type": "circle", "cx": b[0], "cy": b[1], "r": 6, "fill": "E"}], "pivot": list(b), "z": 2},
                {"name": "label", "shapes": [{"type": "line", "points": [[40, 280], [360, 280]], "stroke": "C",
                                              "width": 2}], "pivot": [200, 280], "z": 3}]}


def test_a_village_gear_design_becomes_the_matching_mechanism():
    from modelgen.tools import design as dz
    raw, report = dz.to_mechanism(village_gears())
    by = {p["name"]: p for p in raw["parts"]}
    assert set(by) == {"big_gear", "pinion"} and raw["driver"] == "big_gear"
    assert by["big_gear"]["teeth"] == 24 and by["pinion"]["teeth"] == 12
    assert by["big_gear"]["module"] == 2.5 and report["mm_per_px"] == pytest.approx(0.5, rel=1e-3)  # 5 px teeth
    assert by["big_gear"]["meshes_with"] == ["pinion"] and abs(by["pinion"]["at"] - 20) < 0.5
    assert by["big_gear"]["bore"] == 8.0 and by["pinion"]["bore"] == 6.0 and by["big_gear"]["hub_diameter"] == 22.0
    assert by["big_gear"]["colour"] == "#e4572e" and by["pinion"]["colour"] == "#f2c14e"
    assert abs((by["big_gear"]["phase"] - 5.0 + 7.5) % 15 - 7.5) < 0.6  # a drawn tooth's middle, 15 degrees apart
    assert {s["part"] for s in report["skipped"]} == {"shadow", "label"}
    sheet, assumed = mc.clean(raw)
    lay = mc.layout(sheet)
    pin = lay["place"]["pinion"]["centre"]
    assert abs(math.degrees(math.atan2(pin[2], pin[0])) - 20) < 0.5  # placed where it is drawn
    assert abs(math.hypot(pin[0], pin[2]) * 1000 - 45.0) < 0.6  # 90 px apart at 0.5 mm a px (plus profile shift)
    raw2, report2 = dz.to_mechanism(village_gears(), module=4)
    assert raw2["parts"][0]["module"] == 4 and report2["mm_per_px"] == pytest.approx(0.8, rel=1e-3)


def test_a_design_with_no_part_the_tools_make_is_refused_with_the_reason(api):
    from modelgen.tools import design as dz
    person = {"kind": "vector", "canvas": {"width": 220, "height": 278}, "palette": {"B": "#5c6276"},
              "parts": [{"name": "leg_r", "shapes": [{"type": "path", "points": [[89, 166], [111, 166], [108, 262],
                                                                                 [92, 262], ["q", 90, 220, 89, 166]],
                                                      "fill": "B"}]},
                        {"name": "head", "shapes": [{"type": "ellipse", "cx": 110, "cy": 60, "rx": 22, "ry": 26,
                                                     "fill": "B"}]}]}
    with pytest.raises(dz.DesignError, match="no part in this design is one the tools make"):
        dz.to_mechanism(person)
    r = api.post("/v1/jobs", json={"client": "village", "design": person}, headers=api.headers)
    assert r.status_code == 400 and "design: no part in this design" in r.json()["detail"]
    r = api.post("/v1/jobs", json={"client": "village", "design": village_gears(), "mechanism": PAIR},
                 headers=api.headers)
    assert r.status_code == 400 and "not both" in r.json()["detail"]


def test_a_job_can_send_a_village_design_and_gets_the_match_back(api):
    r = api.post("/v1/jobs", json={"client": "village", "design": village_gears(), "outputs": ["motion"]},
                 headers=api.headers)
    assert r.status_code in (200, 202), r.text
    job = r.json()
    assert job["object"] == "spur gear pair 24/12"
    assert job["design"]["mm_per_px"] == pytest.approx(0.5, rel=1e-3) and job["design"]["parts"]["pinion"]["teeth"] == 12
    assert any(a.startswith("from the design: big_gear has 24 teeth") for a in job["assumed"])


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

def build_compressor():
    return k.compressor_wheel(0.05, bore=0.004, name="compressor")

def build_turbine():
    return k.turbine_wheel(0.045, bore=0.004, name="turbine")

def build_volute():
    return k.volute(0.05, 0.021, 0.035, name="volute")

def build_bearing():
    return k.bearing(0.004, 0.0104, 0.00312, name="bearing")

def build_centre():
    return k.centre_housing(0.031, 0.004, 0.0108, 0.035, 0.055, 0.0495, 0.003, name="centre")

def build_crank():
    return k.crankshaft(0.08, 0.044, 0.052, 0.026, 0.014, 0.024, throw_angles=(0, 180, 180, 0), name="crank")

def build_rod():
    return k.connecting_rod(0.136, 0.04404, 0.02003, 0.0252, 0.0246, name="rod")

def build_piston():
    return k.piston(0.08, 0.0288, 0.02, 0.0264, boss_gap=0.025, name="piston")

def build_rings():
    return k.piston_rings(0.08, 0.0288, name="rings")

def build_liner():
    return k.cylinder(0.08, 0.135, name="liner")

def build_block():
    return k.cylinder(0.08, 0.135, style="block", name="block")

def build_bolt():
    return k.bolt(0.010, 0.040, name="bolt")

def build_nut():
    return k.nut(0.010, name="nut")

def build_washer():
    return k.washer(0.010, name="washer")

def build_spring():
    return k.spring(0.003, 0.024, 0.060, 8, name="spring")

def build_pulley():
    return k.pulley(0.1, grooves=2, section="A", bore=0.02, name="pulley")

def build_belt():
    return k.belt(0.05, 0.1, 0.3, grooves=2, name="belt")

def build_sprocket():
    return k.sprocket(0.0127, 18, bore=0.016, hub=(0.04, 0.01), name="sprocket")

def build_cam():
    table = [0.028 + k._mech().cam_motion(2 * i, lift=0.008)[0] for i in range(180)]
    return k.cam(table, kind="roller", roller=0.008, bore=0.012, name="cam")

def build_valve():
    return k.valve(0.032, 0.006, 0.1, name="valve")

def build_carrier():
    return k.carrier(0.036, 3, 0.008, 0.016, name="carrier")

def build_worm():
    return k.worm(0.003, 1, 0.03, 0.06, name="worm")
'''


@pytest.mark.blender
def test_every_gear_tool_makes_one_closed_solid_at_its_real_size(tmp_path):
    file = tmp_path / "kit.py"
    file.write_text(KIT, encoding="utf-8")
    names = ["spur", "helical", "herring", "holes", "spokes", "ring", "rack", "bevel", "shaft", "centre", "crank",
             "rod", "liner", "block", "bolt", "nut", "washer", "spring", "pulley", "sprocket", "cam", "valve", "carrier",
             "worm"]
    several = ["compressor", "turbine", "volute", "bearing", "piston", "rings", "belt"]  # several closed solids
    result = blender.run_job([blender.part_step(n, file, func=f"build_{n}") for n in names + several]
                             + [blender.measure_step()], timeout=600)
    assert result.ok, result.traceback
    parts = {p["part"]: p for p in result.outputs["measure"]["parts"]}
    for n in names + several:
        assert parts[n]["non_manifold_edges"] == 0, (n, parts[n])
        assert parts[n]["islands"] == 1 or n in several, (n, parts[n])
        assert parts[n]["triangles"] < 16000, (n, parts[n]["triangles"])
    # the box across X is a little under the diameter: no blade tip lies exactly on X
    assert 0.048 < parts["compressor"]["size"][0] <= 0.0501 and parts["compressor"]["islands"] == 13  # hub, 6 + 6
    tip = 2 * mc.circles(2.0, 12, shift=0.294)["tip"] / 1000
    assert abs(parts["spur"]["size"][0] - tip) < 0.0002 and abs(parts["spur"]["size"][2] - 0.016) < 1e-5
    assert abs(parts["ring"]["size"][0] - (2 * 62.5 + 12) / 1000) < 0.0005  # root diameter 125 mm + 6 modules
    assert abs(parts["rack"]["size"][0] - 12 * math.pi * 0.002) < 1e-5
    assert parts["piston"]["islands"] == 2 and parts["rings"]["islands"] == 3  # piston and pin; three rings
    assert abs(parts["piston"]["size"][0] - 0.08) < 0.0002 and abs(parts["rings"]["size"][0] - 0.08) < 0.0001
    assert abs(parts["liner"]["size"][2] - 0.135) < 1e-6 and abs(parts["block"]["size"][2] - 0.135) < 1e-6
    assert abs(parts["nut"]["size"][0] - 0.016) < 0.0002  # M10: 16 mm across flats
    assert abs(parts["bolt"]["size"][2] - (0.040 + 0.0065)) < 0.0002 and abs(parts["washer"]["size"][0] - 0.02) < 1e-4
    assert abs(parts["spring"]["size"][2] - 0.060) < 0.0005


@pytest.mark.blender
@pytest.mark.parametrize("raw, gate", [(FASTENERS, "no_clashes"), (BELT, "speed_ratio"), (CHAIN, "no_clashes"),
                                       (CAM, "follower_contact"), (VALVE, "follower_contact"),
                                       (PLANETARY, "parts_on_their_mounts"), (WORM, "teeth_clear")],
                         ids=["fasteners", "belt", "chain", "cam", "valve", "planetary", "worm"])
def test_every_stage_4_drive_builds_and_runs_with_every_check_passed(raw, gate):
    sheet, assumed = mc.clean(raw)
    proj = db.create_project(mc.describe(sheet), mc.describe(sheet), options={"mechanism": sheet, "assumed": assumed})
    out = asyncio.run(pipeline._run_parts(proj["name"], sheet, assumed))
    folder = pipeline.version_dir(proj["name"], out["version"])
    gates = json.loads((folder / "checks.json").read_text(encoding="utf-8"))["gates"]
    assert out["ok"] and out["passed"], {k: v for k, v in gates.items() if not v["pass"]}
    assert gate in gates and gates[gate]["pass"]


@pytest.mark.blender
def test_a_real_single_cylinder_runs_on_its_pins_inside_its_cylinder_and_cuts_open():
    sheet, assumed = mc.clean(ENGINE)
    proj = db.create_project("engine", "single cylinder", options={"mechanism": sheet, "assumed": assumed})
    out = asyncio.run(pipeline._run_parts(proj["name"], sheet, assumed))
    folder = pipeline.version_dir(proj["name"], out["version"])
    gates = json.loads((folder / "checks.json").read_text(encoding="utf-8"))["gates"]
    assert out["ok"] and out["passed"], gates
    assert gates["pins_in_bores"]["pass"] and gates["piston_in_liner"]["pass"] and gates["no_clashes"]["pass"]
    rows = {r["kind"]: r for r in json.loads((folder / "mesh_check.json").read_text(encoding="utf-8"))["pairs"]}
    assert abs(rows["liner"]["stroke_m"] - 0.08) < 0.0002 and rows["liner"]["top_room_m"] >= 0
    report = json.loads((folder / "motion_report.json").read_text(encoding="utf-8"))
    assert report["frames"] == 72  # one turn at 20 rpm, 24 frames a second
    assert asyncio.run(jobs.render_motion(proj["name"], out["version"], "explainer", "persp", "front", None,
                                          "cutaway", "auto")) is None
    info = json.loads((folder / "cutaway" / "explainer-persp" / "cutaway.json").read_text(encoding="utf-8"))
    assert info["cut_parts"] == ["cylinder"]
    assert asyncio.run(jobs.render_motion(proj["name"], out["version"], "explainer", "persp", "front", None,
                                          "exploded")) is None


AXIS_KIT = '''from bpy_lib import kits as k


def _closed_on_axis(obj, ends):
    """Raise unless the part has a single vertex on its axis at each end (no hairline hole)."""
    for z in ends:
        hits = [v for v in obj.data.vertices if v.co.xy.length < 1e-9 and abs(v.co.z - z) < 1e-7]
        if len(hits) != 1:
            raise AssertionError(f"{obj.name}: {len(hits)} vertices on its axis at z = {z}")
    return obj

def build_valve():
    return _closed_on_axis(k.valve(0.032, 0.006, 0.1, name="valve"), (0.1,))

def build_worm():
    w = k.worm(0.003, 1, 0.03, 0.06, name="worm")
    return _closed_on_axis(w, (-0.03 - 0.018, 0.03 + 0.018))

def build_bolt():
    return _closed_on_axis(k.bolt(0.010, 0.040, name="bolt"), (0.0065, -0.040))

def build_pulley():
    p = k.pulley(0.1, grooves=1, section="A", bore=0.0, name="pulley")
    w = p["mg_gear"] and __import__("json").loads(p["mg_gear"])["width"]
    return _closed_on_axis(p, (-w / 2, w / 2))
'''


@pytest.mark.blender
def test_turned_parts_close_on_their_axis_with_no_hairline_hole(tmp_path):
    file = tmp_path / "axis_kit.py"
    file.write_text(AXIS_KIT, encoding="utf-8")
    names = ["valve", "worm", "bolt", "pulley"]
    result = blender.run_job([blender.part_step(n, file, func=f"build_{n}") for n in names]
                             + [blender.measure_step()], timeout=600)
    assert result.ok, result.traceback
    for p in result.outputs["measure"]["parts"]:
        assert p["non_manifold_edges"] == 0 and p["islands"] == 1, p


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


@pytest.mark.blender
def test_a_real_turbocharger_clears_its_housings_and_renders_a_cutaway_and_an_exploded_view():
    from PIL import Image
    sheet, assumed = mc.clean(TURBO)
    proj = db.create_project("turbo", "turbocharger", options={"mechanism": sheet, "assumed": assumed})
    out = asyncio.run(pipeline._run_parts(proj["name"], sheet, assumed))
    assert out["ok"] and out["passed"], out
    folder = pipeline.version_dir(proj["name"], out["version"])
    gates = json.loads((folder / "checks.json").read_text(encoding="utf-8"))["gates"]
    assert gates["blades_clear"]["pass"] and gates["no_clashes"]["pass"]
    assert asyncio.run(jobs.render_motion(proj["name"], out["version"], "explainer", "persp", "side", None,
                                          "cutaway", "auto")) is None
    cut = folder / "cutaway" / "explainer-persp"
    info = json.loads((cut / "cutaway.json").read_text(encoding="utf-8"))
    assert info["cut_parts"] == ["compressor_housing", "turbine_housing", "centre_housing"]
    flows = json.loads((cut / "parts.json").read_text(encoding="utf-8"))["flows"]
    assert {f["name"] for f in flows} == {"intake", "compressed", "exhaust_in", "exhaust", "oil"}
    with Image.open(cut / "frame_000.png") as im:
        accent = sum(1 for px in im.convert("RGB").getdata() if px[0] > 220 and px[1] < 90 and 60 < px[2] < 140)
    assert accent > 500  # the cut faces show in the accent colour
    assert asyncio.run(jobs.render_motion(proj["name"], out["version"], "explainer", "persp", "side", None,
                                          "exploded")) is None
    ex = json.loads((folder / "exploded" / "explainer-persp" / "exploded.json").read_text(encoding="utf-8"))
    assert ex["frames"] == 48 and ex["offsets_m"]["shaft"] == [0.0, 0.0, 0.0]
