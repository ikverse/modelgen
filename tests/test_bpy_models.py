"""The helper library builds a mug, a chair and a table from fixed scripts, and the geometric checks
pass. These need Blender and skip with the install command when it is absent."""

from pathlib import Path

import pytest

from modelgen.tools import blender, checks, render

FIXTURES = Path(__file__).resolve().parent / "fixtures"

MODELS = {
    "mug": {"parts": ["body", "handle"], "dimensions": {"width": 0.12, "depth": 0.084, "height": 0.095}},
    "chair": {"parts": ["seat", "leg", "back"], "dimensions": {"width": 0.44, "depth": 0.42, "height": 0.905}},
    "table": {"parts": ["top", "leg", "apron"], "dimensions": {"width": 1.2, "depth": 0.7, "height": 0.75}},
}


def model_steps(name):
    file = FIXTURES / f"{name}.py"
    steps = [blender.part_step(p, file, func=f"build_{p}") for p in MODELS[name]["parts"]]
    steps += [blender.assembly_step(file), blender.materials_step(file), blender.measure_step()]
    return steps


@pytest.mark.blender
@pytest.mark.parametrize("name", list(MODELS))
def test_fixture_builds_and_passes_checks(name):
    result = blender.run_job(model_steps(name), timeout=300)
    assert result.ok, result.traceback or result.stdout[-2000:]
    measure = result.outputs["measure"]
    found = checks.evaluate(measure, {"dimensions": MODELS[name]["dimensions"]})
    failing = {k: g["detail"] for k, g in found["gates"].items() if not g["pass"]}
    assert not failing, failing
    assert not found["warnings"], found["warnings"]
    assert {p["part"] for p in measure["parts"]} == set(MODELS[name]["parts"])


@pytest.mark.blender
def test_mug_saves_exports_and_renders(tmp_path):
    steps = model_steps("mug") + [
        {"kind": "save", "path": str(tmp_path / "mug.blend")},
        {"kind": "export", "dir": str(tmp_path / "exports"), "basename": "mug"},
        {"kind": "render", "set": "views", "key": "views", "views": ["three_quarter", "top"], "prefix": "v",
         "dir": str(tmp_path / "renders"), "size": 128, "samples": 4},
    ]
    result = blender.run_job(steps, timeout=300)
    assert result.ok, result.traceback or result.stdout[-2000:]
    assert (tmp_path / "mug.blend").is_file()
    exports = result.outputs["exports"]
    assert "errors" not in exports, exports.get("errors")
    for ext in ("glb", "fbx", "obj", "stl"):
        assert Path(exports[ext]).stat().st_size > 1000
    pictures = result.outputs["renders"]["views"]
    assert len(pictures) == 2 and all(Path(p).is_file() for p in pictures)
    sheet = render.contact_sheet(pictures, tmp_path / "sheet.jpg", columns=2, tile=128)
    assert Path(sheet).is_file()


@pytest.mark.blender
def test_motion_keeps_frame_1_and_is_carried_in_the_glb(tmp_path):
    """The chair's legs spin like wheels (one of them placed turned round), the seat bounces and smoke
    rises from the back: the checks still see the assembled pose, and the GLB holds the animation,
    the marker and its properties."""
    import json
    motion = tmp_path / "motion.py"
    motion.write_text("from bpy_lib import motion as m\n\n"
                      "def animate(parts):\n"
                      "    parts['leg'][1].rotation_euler[2] = 3.14159265\n"  # turned round, like a left-hand wheel
                      "    m.travel(speed_kmh=10)\n"
                      "    rolls = m.spin(parts['leg'], axis=(1, 0, 0))\n"
                      "    assert set(rolls.values()) == {1}, rolls\n"  # every copy rolls forward
                      "    m.bounce(parts['seat'], amplitude_m=0.01, cycles=1)\n"
                      "    m.marker('smoke', m.top(parts['back'][0]))\n", encoding="utf-8")
    steps = model_steps("chair")[:-1] + [
        blender.motion_step(motion), blender.measure_step(),
        {"kind": "export", "dir": str(tmp_path / "exports"), "basename": "chair"},
        {"kind": "render", "set": "frames", "key": "frames", "count": 2, "dir": str(tmp_path / "renders"),
         "size": 96, "samples": 2}]
    result = blender.run_job(steps, timeout=300)
    assert result.ok, result.traceback or result.stdout[-2000:]
    report = result.outputs["motion"]
    assert len([k for k in report["animated"] if k.startswith("leg")]) == 4 and "seat" in report["animated"]
    assert report["markers"][0]["kind"] == "smoke" and report["speed_mps"] == pytest.approx(10 / 3.6)
    plain = blender.run_job(model_steps("chair"), timeout=300)
    assert plain.ok
    for key in ("bbox", "min_z"):
        assert result.outputs["measure"][key] == pytest.approx(plain.outputs["measure"][key], abs=1e-5)
    glb = Path(result.outputs["exports"]["glb"]).read_bytes()
    doc = json.loads(glb[20:20 + int.from_bytes(glb[12:16], "little")])  # the GLB's JSON chunk
    assert doc.get("animations"), "the GLB carries no animation"
    extras = [n.get("extras", {}) for n in doc["nodes"]]
    assert any(e.get("mg_fx") == "smoke" and e.get("mg_fx_size") for e in extras)
    assert any(e.get("mg_motion") == "motion" and e.get("mg_speed_mps") for e in extras)
    pictures = result.outputs["renders"]["frames"]
    assert [Path(p).stem for p in pictures] == ["frame_00", "frame_01", "markers_1_smoke"]  # each marker last
    assert all(Path(p).is_file() for p in pictures)
    assert render.frame_labels(pictures)[-1] == render.marker_label("smoke")
    from PIL import Image
    with Image.open(pictures[-1]) as im:  # the dark smoke ball shows in its close-up (the chair is light oak on grey)
        dark = sum(1 for r, g, b in im.convert("RGB").getdata() if max(r, g, b) < 130)
        assert dark >= 30, dark


@pytest.mark.blender
def test_a_wheel_rolls_at_the_travel_speed_round_its_own_hub(tmp_path):
    """A 0.3 m wheel anchored at its bottom, on a side-to-side axle: spin measures its true radius
    (not the bounding box corner), turns it round its hub, and the chosen loop keeps its rim within
    5 percent of the travel speed; the hub stays put, so the checks still see the assembled pose."""
    import math
    wheel = tmp_path / "wheel.py"
    wheel.write_text("from bpy_lib import helpers as h\n\n"
                     "def build_wheel():\n"
                     "    w = h.cylinder(0.3, 0.06, segments=64, anchor='center', name='wheel')\n"
                     "    h.place(w, rotation_deg=(0, 90, 0))\n"
                     "    h.apply_transform(w)\n"
                     "    w.data.transform(__import__('mathutils').Matrix.Translation((0, 0, 0.3)))\n"  # origin at its foot
                     "    return w\n\n"
                     "def assemble(parts):\n"
                     "    h.place(parts['wheel'], location=(0, 0, 0))\n", encoding="utf-8")
    motion = tmp_path / "motion.py"
    motion.write_text("from bpy_lib import motion as m\n\n"
                      "def animate(parts):\n"
                      "    m.travel(speed_kmh=25)\n"
                      "    m.spin(parts['wheel'])\n", encoding="utf-8")
    steps = [blender.part_step("wheel", wheel, func="build_wheel"), blender.assembly_step(wheel),
             blender.motion_step(motion), blender.measure_step()]
    result = blender.run_job(steps, timeout=300)
    assert result.ok, result.traceback or result.stdout[-2000:]
    report = result.outputs["motion"]
    spun = report["animated"]["wheel"]
    assert spun["radius_m"] == pytest.approx(0.3, rel=0.03)  # the mesh, not the box corner (0.42)
    rim = abs(spun["spin_turns"]) * 2 * math.pi * spun["radius_m"] / report["seconds"]
    assert rim == pytest.approx(report["speed_mps"], rel=0.05)
    assert abs(spun["speed_error_pct"]) <= 5 and 2 <= report["seconds"] <= 4
    measure = result.outputs["measure"]
    assert measure["min_z"] == pytest.approx(0, abs=1e-4)
    assert measure["bbox"]["size"][2] == pytest.approx(0.6, rel=0.02)


@pytest.mark.blender
def test_ink_outlines_do_not_count_as_geometry(tmp_path):
    ink = tmp_path / "ink.py"
    ink.write_text("def apply(groups):\n"
                   "    for objs in groups.values():\n"
                   "        for o in objs:\n"
                   "            m = o.modifiers.new('outline', 'SOLIDIFY')\n"
                   "            m.thickness, m.offset, m.use_flip_normals, m.use_rim = 0.006, 1, True, False\n",
                   encoding="utf-8")
    plain = blender.run_job(model_steps("mug"), timeout=300)
    inked = blender.run_job(model_steps("mug")[:-1] + [blender.materials_step(ink), blender.measure_step()],
                            timeout=300)
    assert plain.ok and inked.ok, inked.traceback or inked.stdout[-2000:]
    before, after = plain.outputs["measure"], inked.outputs["measure"]
    assert after["min_z"] == before["min_z"] and after["bbox"] == before["bbox"]
    assert after["island_count"] == before["island_count"] and not after["floaters"]


@pytest.mark.blender
def test_small_holes_are_filled_but_a_thin_sheet_stays_open(tmp_path):
    holed = tmp_path / "holed.py"
    holed.write_text(
        "import bmesh\nfrom bpy_lib import helpers as h\n\n"
        "def build_cup():\n"  # a cylinder with one of its 48 side faces cut out: a small 4-edge hole
        "    obj = h.cylinder(0.04, 0.1, segments=48)\n"
        "    bm = bmesh.new(); bm.from_mesh(obj.data)\n"
        "    side = next(f for f in bm.faces if len(f.verts) == 4 and abs(f.normal.z) < 0.1)\n"
        "    bmesh.ops.delete(bm, geom=[side], context='FACES_ONLY')\n"
        "    bm.to_mesh(obj.data); bm.free()\n"
        "    return obj\n\n"
        "def build_sheet():\n"
        "    import bpy\n"
        "    me = bpy.data.meshes.new('sheet')\n"
        "    me.from_pydata([(0, 0, 0), (0.1, 0, 0), (0.1, 0.1, 0), (0, 0.1, 0)], [], [(0, 1, 2, 3)])\n"
        "    obj = bpy.data.objects.new('sheet', me); bpy.context.scene.collection.objects.link(obj)\n"
        "    return obj\n", encoding="utf-8")
    result = blender.run_job([blender.part_step("cup", holed, func="build_cup"),
                              blender.part_step("sheet", holed, func="build_sheet"), blender.measure_step()],
                             timeout=120)
    assert result.ok, result.traceback or result.stdout[-2000:]
    parts = {p["part"]: p for p in result.outputs["measure"]["parts"]}
    assert parts["cup"]["non_manifold_edges"] == 0
    assert parts["sheet"]["non_manifold_edges"] > 0


@pytest.mark.blender
def test_a_broken_part_returns_its_traceback(tmp_path):
    bad = tmp_path / "bad.py"
    bad.write_text("from bpy_lib import helpers as h\n\ndef build():\n    return h.box((1, 1))\n", encoding="utf-8")
    result = blender.run_job([blender.part_step("bad", bad)], timeout=120)
    assert not result.ok
    assert "bad.py" in result.traceback and "build part 'bad'" == result.failed_step


# --- cars: one continuous body from a sheet ------------------------------------------------------

CAR_PARTS = ["body", "wheel", "side_mirror", "wing", "exhaust"]


def _car_plan():
    """The plan the pipeline would make for the fixture car: its SHEET and the pieces it builds."""
    from modelgen import pipeline
    from modelgen.bpy_lib.shells import car_sheet
    sheet = pipeline.body_sheet((FIXTURES / "car.py").read_text(encoding="utf-8"))
    accessories = {"wing": {"span": 1.66, "chord": 0.30, "height": 0.32}, "exhaust": {"radius": 0.05}}
    return car_sheet.make_plan({"sheet": sheet, "accessories": accessories})


@pytest.mark.blender
def test_the_car_fixture_is_one_closed_body_on_its_wheels_and_passes_checks():
    file = FIXTURES / "car.py"
    steps = [blender.part_step(p, file, func=f"build_{p}") for p in CAR_PARTS]
    steps += [blender.assembly_step(file), blender.materials_step(file), blender.measure_step()]
    result = blender.run_job(steps, timeout=600)
    assert result.ok, result.traceback or result.stdout[-2000:]
    measure = result.outputs["measure"]
    plan = _car_plan()
    found = checks.evaluate(measure, {"dimensions": plan["dimensions"]}, tolerance=0.03)
    failing = {k: g["detail"] for k, g in found["gates"].items() if not g["pass"]}
    assert not failing, failing
    assert not found["warnings"], found["warnings"]
    body = next(p for p in measure["parts"] if p["part"] == "body")
    assert body["islands"] == 1 and body["non_manifold_edges"] == 0
    assert sum(1 for p in measure["parts"] if p["part"] == "wheel") == 4
    assert sum(1 for p in measure["parts"] if p["part"] == "side_mirror") == 2
    inside = next(p for p in measure["parts"] if p["part"] == "interior")  # panes and seats in the hollow cabin
    assert inside["non_manifold_edges"] == 0 and inside["islands"] >= 6
    for entry in plan["parts"]:  # each piece comes out at the size its plan entry tells its builder
        built = next(p for p in measure["parts"] if p["object"] == entry["name"])
        want = entry["dimensions"]
        for got, key in zip(built["size"], ("width", "depth", "height")):
            assert abs(got - want[key]) <= 0.05 * want[key], (entry["name"], key, got, want[key])


@pytest.mark.blender
def test_a_slot_can_still_be_replaced_after_car_finish(tmp_path):
    """car_finish fills the slots, then assign_slot overrides one: the override must land (it once
    matched nothing, because car_finish's materials lost the slot tag)."""
    finish = tmp_path / "finish.py"
    finish.write_text(
        "from bpy_lib import materials as m\n\n"
        "def apply(parts):\n"
        "    m.car_finish(parts, paint=(0.78, 0.06, 0.18), style='realistic')\n"
        "    dark = m.matte(color=(0.07, 0.09, 0.12), roughness=0.05, name='dark_glass')\n"
        "    body = parts['body'][0]\n"
        "    filled = m.assign_slot(body, 'glass', dark)\n"
        "    assert filled > 0, 'assign_slot after car_finish filled no slot'\n"
        "    assert 'dark_glass' in [s.name for s in body.data.materials if s]\n",
        encoding="utf-8")
    steps = [blender.part_step("body", FIXTURES / "car.py", func="build_body"), blender.materials_step(finish)]
    result = blender.run_job(steps, timeout=300)
    assert result.ok, result.traceback or result.stdout[-2000:]


@pytest.mark.blender
def test_every_car_style_and_the_detail_tools_keep_one_closed_mesh():
    from modelgen.bpy_lib.shells import car_sheet
    file = FIXTURES / "car.py"
    names = [f"style_{s}" for s in car_sheet.STYLES] + ["details"]
    steps = [blender.part_step(n, file, func=f"build_{n}") for n in names] + [blender.measure_step()]
    result = blender.run_job(steps, timeout=900)
    assert result.ok, result.traceback or result.stdout[-2000:]
    parts = {p["part"]: p for p in result.outputs["measure"]["parts"]}
    for name in names:
        assert parts[name]["islands"] == 1, name
        assert parts[name]["non_manifold_edges"] == 0, name
    for style in car_sheet.STYLES:
        s = car_sheet.resolve({"style": style})
        size = parts[f"style_{style}"]["size"]
        # the lip and bumpers reach a little under the body, so the roof height is what the sheet fixes
        assert abs(size[1] - s["length"]) < 0.01 and abs(parts[f"style_{style}"]["max"][2] - s["height"]) < 0.01, style
        assert abs(size[0] - s["width"]) < 0.03, style  # the flares bring it out to the full width


@pytest.mark.blender
@pytest.mark.parametrize("name, says", [
    ("detail_that_wrecks_the_body", "feature 'plate' removed most of the body"),
    ("detail_that_leaves_a_hole", "feature 'plate' could not be cut cleanly"),
])
def test_a_detail_that_breaks_the_body_stops_the_build_and_is_named(name, says):
    result = blender.run_job([blender.part_step(name, FIXTURES / "car.py", func=f"build_{name}")], timeout=300)
    assert not result.ok
    assert says in result.traceback, result.traceback[-1500:]


@pytest.mark.blender
def test_every_example_car_sheet_builds_a_closed_body_at_its_size():
    from modelgen.bpy_lib.shells import car_archetypes, car_sheet
    file = FIXTURES / "car.py"
    names = list(car_archetypes.ARCHETYPES)
    steps = [blender.part_step(n, file, func=f"build_example_{n}") for n in names] + [blender.measure_step()]
    result = blender.run_job(steps, timeout=900)
    assert result.ok, result.traceback or result.stdout[-2000:]
    parts = {p["part"]: p for p in result.outputs["measure"]["parts"]}
    for name in names:
        assert parts[name]["islands"] == 1 and parts[name]["non_manifold_edges"] == 0, name
        s = car_sheet.resolve(car_archetypes.ARCHETYPES[name]["sheet"])
        # the lip and bumpers reach a little under the body, so the roof height is what the sheet fixes
        assert abs(parts[name]["size"][1] - s["length"]) < 0.01 and abs(parts[name]["max"][2] - s["height"]) < 0.01, name


@pytest.mark.blender
def test_a_part_is_measured_and_shown_in_the_whole_model(tmp_path):
    """The builder's in-model check and the critic's measure_part: the mug's handle touches the body,
    has a real wall thickness, and is rendered in place (close-ups, then whole views with it orange).
    The same measurement works on a saved .blend opened again, as the critic uses it."""
    blend = tmp_path / "mug.blend"
    steps = model_steps("mug") + [blender.part_measure_step("handle"),
                                  render.context_step(tmp_path / "ctx", "handle"),
                                  {"kind": "save", "path": str(blend)}]
    for s in steps:
        if s["kind"] == "render":
            s.update(size=96, samples=2)
    result = blender.run_job(steps, timeout=300)
    assert result.ok, result.traceback or result.stdout[-2000:]
    report = result.outputs["part_measure"]["handle"]
    assert report["found"] and report["pieces"] >= 1 and report["pieces_touching_nothing"] == 0
    assert "body" in report["touches"]
    lo, mid, hi = report["wall_thickness_min_median_max"]
    assert 0 < lo <= mid <= hi < 0.1
    pictures = result.outputs["renders"]["context"]
    assert len(pictures) == 4 and all(Path(p).is_file() for p in pictures)
    again = blender.run_job([blender.part_measure_step("body")], open_blend=blend, timeout=300)
    assert again.ok, again.traceback or again.stdout[-2000:]
    body = next(p for p in result.outputs["measure"]["parts"] if p["part"] == "body")
    assert again.outputs["part_measure"]["body"]["size"] == pytest.approx(body["size"], abs=0.002)
    missing = blender.run_job([blender.part_measure_step("lid")], open_blend=blend, timeout=300)
    assert missing.ok and not missing.outputs["part_measure"]["lid"]["found"]


PLATE = '''from bpy_lib import helpers as h

def build():
    plate = h.box((0.12, 0.08, 0.01), name="plate")
    holes = [h.cylinder(0.006, 0.03, segments=32, location=(x, y, 0)) for x in (-0.04, 0, 0.04) for y in (-0.02, 0.02)]
    return h.boolean(plate, holes)

def assemble(parts):
    """Every corner of the big flat top face must shade straight up, so the panel renders flat."""
    me = parts["plate"].data
    top = [p for p in me.polygons if p.normal.z > 0.999 and p.area > 1e-4]
    assert top, "no flat top face found"
    normals = me.corner_normals if hasattr(me, "corner_normals") else None
    if normals is None:
        me.calc_normals_split()
    worst = 1.0
    for p in top:
        for li in p.loop_indices:
            n = normals[li].vector if normals is not None else me.loops[li].normal
            worst = min(worst, n.z)
    assert worst > 0.9999, f"the flat top face shades bent: a corner normal has z {worst:.4f}"
'''


@pytest.mark.blender
def test_a_flat_panel_cut_with_holes_shades_flat(tmp_path):
    """After boolean holes, a part's flat faces keep straight-up normals (no light and dark streaks)."""
    file = tmp_path / "plate.py"
    file.write_text(PLATE, encoding="utf-8")
    result = blender.run_job([blender.part_step("plate", file), blender.assembly_step(file)], timeout=120)
    assert result.ok, result.traceback or result.stdout[-2000:]


SWATCH = '''import bpy
from bpy_lib import materials as m

def build():
    bpy.ops.mesh.primitive_cube_add(size=1)
    o = bpy.context.active_object
    o.scale = (0.09, 0.03, 0.15)
    o.location = (0, 0, 0.075)
    bpy.ops.object.transform_apply(scale=True, location=True)
    return o

def assemble(parts):
    pass

def apply(groups):
    mat = m.plastic(color=(0.5019, 0.5019, 0.5019), roughness=0.55, name="grey")
    for objs in groups.values():
        for o in objs:
            o.data.materials.clear()
            o.data.materials.append(mat)
'''


@pytest.mark.blender
def test_the_studio_renders_a_colour_close_to_its_true_value(tmp_path):
    """A mid-grey (#808080) block seen from the front renders close to #808080, not a stop lighter:
    the critic judges colours from the renders, so the studio must show them as they were set."""
    from PIL import Image
    file = tmp_path / "swatch.py"
    file.write_text(SWATCH, encoding="utf-8")
    steps = [blender.part_step("swatch", file), blender.assembly_step(file), blender.materials_step(file),
             {"kind": "render", "set": "views", "key": "v", "views": ["front"], "prefix": "grey",
              "dir": str(tmp_path), "size": 200, "samples": 32}]
    result = blender.run_job(steps, timeout=300)
    assert result.ok, result.traceback or result.stdout[-2000:]
    im = Image.open(result.outputs["renders"]["v"][0]).convert("RGB")
    grey = sum(sum(im.getpixel((x, y))) / 3 for x in range(90, 110) for y in range(90, 110)) / 400
    assert 112 <= grey <= 150, f"mid grey rendered as {grey:.0f} of 255 (true value 128)"
