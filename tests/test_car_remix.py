"""A car remixed from a base car (bpy_lib.shells.car_remix, modelgen.tools.car_request): the shape
maths and the choices without Blender, and one whole car built, exported and read back (needs
Blender and the downloaded base cars)."""

import pytest

from modelgen.bpy_lib.shells import car_remix as cr
from modelgen.tools import car_library as cl
from modelgen.tools import car_request as rq
from modelgen.tools import car_space as cs

FRAME = cr.frame_of(front_y=-1.4, rear_y=1.4, radius=0.33, belt=0.95)
EXTREME = {"wheelbase": 0.08, "front": 1.10, "rear": 1.14, "roof": 1.07, "width": 1.04, "lift": 0.05,
           "wheel": 1.05}


def test_a_shape_is_kept_inside_the_limits():
    wild = cr.clamp_shape({"wheelbase": 0.5, "front": 3.0, "rear": 0.1, "roof": 2.0, "width": 0.5, "lift": 1.0})
    for key, (lo, hi) in cr.LIMITS.items():
        assert lo <= wild[key] <= hi
    assert cr.clamp_shape(None) == cr.NEUTRAL


def test_the_neutral_shape_moves_nothing():
    for p in [(0.8, -2.2, 0.4), (0.0, 0.0, 1.4), (-0.7, 2.3, 0.9)]:
        assert cr.warp_point(p, FRAME, cr.NEUTRAL) == pytest.approx(p)


def test_the_wheel_openings_move_with_their_wheels_and_do_not_bend():
    shape = cr.clamp_shape(EXTREME)
    for axle in (FRAME["front_y"], FRAME["rear_y"]):
        (wx, wy, wz), grow = cr.warp_wheel((0.75, axle, 0.33), FRAME, shape)
        moved = [cr.warp_point((0.75, axle + d, 0.5), FRAME, shape)[1] - (axle + d) for d in (-0.45, 0.0, 0.45)]
        assert moved == pytest.approx([wy - axle] * 3)      # the whole opening shifts as one piece
        assert wz == pytest.approx(0.33 * grow)             # the tyre still stands on the ground
    top = cr.warp_point((0.75, FRAME["rear_y"], 0.70), FRAME, shape)[2] - 0.70
    assert top == pytest.approx(cr.rise(FRAME, shape))      # the opening rises ...
    grow = cr.warp_wheel((0.75, FRAME["rear_y"], 0.33), FRAME, shape)[1]
    assert 2 * 0.33 * (grow - 1) == pytest.approx(top)      # ... as much as the tyre's top does
    low = cr.clamp_shape({"lift": -0.03, "wheel": 0.95})
    sunk = cr.warp_point((0.75, FRAME["rear_y"], 0.70), FRAME, low)[2] - 0.70
    shrink = cr.warp_wheel((0.75, FRAME["rear_y"], 0.33), FRAME, low)[1]
    assert sunk < 0 and 2 * 0.33 * (shrink - 1) == pytest.approx(sunk)   # and sinks with it too


def test_a_hypercar_shape_is_lower_wider_and_longer_than_any_ordinary_one():
    hyper = cr.clamp_shape(None, "hyper")
    assert hyper["roof"] < cr.LIMITS["roof"][0] + 0.001 and hyper["width"] > cr.LIMITS["width"][1]
    assert hyper["wheelbase"] > 0 and cr.rise(FRAME, cr.clamp_shape({"wheel": 9.0}, "hyper")) < 0
    for key, (lo, hi) in cr.HYPER.items():
        assert lo <= cr.clamp_shape({key: 99.0}, "hyper")[key] <= hi


def test_a_shape_changes_the_length_the_roof_and_the_width():
    shape = cr.clamp_shape(EXTREME)
    nose = cr.warp_point((0.0, -2.3, 0.5), FRAME, shape)
    tail = cr.warp_point((0.0, 2.4, 0.5), FRAME, shape)
    assert tail[1] - nose[1] > 4.7 + 0.2
    assert cr.warp_point((0.0, 0.0, 1.45), FRAME, shape)[2] > 1.45 + cr.rise(FRAME, shape)
    assert cr.warp_point((0.0, 0.0, 0.60), FRAME, shape)[2] == pytest.approx(0.60 + cr.rise(FRAME, shape))
    assert cr.warp_point((0.9, 0.0, 0.60), FRAME, shape)[0] == pytest.approx(0.9 * 1.04)


def test_a_very_short_car_still_has_somewhere_to_stretch():
    short = cr.frame_of(front_y=-0.5, rear_y=0.5, radius=0.3, belt=0.9)
    a0, a1 = short["stretch"]
    assert a1 > a0


def test_a_request_is_read_for_its_type_era_and_colour():
    want = rq.parse("a dark blue 90s SUV, please")
    assert want["types"][0] == "suv" and want["eras"] == ("1990s",) and want["colour_name"] == "dark blue"
    assert rq.parse("station wagon")["types"] == ("wagon",)
    assert rq.parse("police car")["types"] == ("police",)
    assert rq.parse("something nice")["types"] is None
    hyper = rq.parse("white hypercar")
    assert hyper["types"] == ("sports",) and "no hypercar" in hyper["note"] and hyper["profile"] == "hyper"
    assert rq.parse("sports car")["profile"] is None
    assert rq.parse("sedan")["library"] == "grab3d"
    flat = rq.parse("low poly sedan")
    assert flat["library"] == "quaternius" and flat["types"][0] == "sedan"


@pytest.fixture(scope="module")
def catalogue():
    if not cl.CATALOG.is_file():
        pytest.skip("no catalogue: run python -m modelgen.tools.car_library")
    return cl.load()


def test_a_seed_gives_the_same_car_again_and_new_seeds_give_other_cars(catalogue):
    assert rq.plan("red suv", 4, catalogue) == rq.plan("red suv", 4, catalogue)
    plans = [rq.plan("sedan", s, catalogue) for s in range(1, 13)]
    assert len({(p["base"], tuple(p["shape"].values()), p["paint_name"], p["wheels"]) for p in plans}) == 12
    assert len({p["base"] for p in plans}) > 1


def test_every_plan_is_of_the_asked_type_and_colour_with_wheels_that_fit(catalogue):
    by_name = {e["name"]: e for e in catalogue}
    for seed in range(1, 21):
        p = rq.plan("green pickup truck", seed, catalogue)
        assert p["type"] == "pickup" and p["paint_name"] == "green"
        for key, (lo, hi) in cr.LIMITS.items():
            assert lo <= p["shape"][key] <= hi
        if p["wheels"]:
            base, donor = by_name[p["base"]], by_name[p["wheels"]]
            assert donor["library"] == base["library"] and donor["wheels"] == 4
            assert abs(donor["wheel_radius"] - base["wheel_radius"]) <= rq.WHEEL_FIT * base["wheel_radius"]


def test_every_car_is_of_the_one_style_unless_the_other_is_asked_for(catalogue):
    by_name = {e["name"]: e for e in catalogue}
    for request_ in ("sedan", "suv", "sports car", "taxi", "a car"):
        for seed in range(1, 16):
            assert by_name[rq.plan(request_, seed, catalogue)["base"]]["library"] == "grab3d"
    assert {by_name[rq.plan("low poly sedan", s, catalogue)["base"]]["library"] for s in range(1, 9)} == {"quaternius"}
    # the other style has no station wagon: the request still gets one
    assert rq.plan("low poly station wagon", 1, catalogue)["type"] == "wagon"


def test_borrowed_wheels_come_in_plain_colours_and_whitewalls_only_on_old_cars(catalogue):
    swapped = [p for p in (rq.plan("sedan", s, catalogue) for s in range(1, 60)) if p["wheels"]]
    assert swapped
    for p in swapped:
        rim = p["finish"]["rim"]
        assert max(rim) - min(rim) < 0.05                       # silver or dark, never a colour
        assert p["finish"]["whitewalls"] == (p["era"] in rq.WHITEWALL_ERAS)
    assert all(p["finish"] is None for p in (rq.plan("sedan", s, catalogue) for s in range(1, 60)) if not p["wheels"])


def test_a_hypercar_is_a_recent_sports_car_in_the_hypercar_shape(catalogue):
    for seed in range(1, 11):
        p = rq.plan("yellow hypercar", seed, catalogue)
        assert p["type"] == "sports" and p["era"] in rq.HYPER_ERAS and p["profile"] == "hyper"
        for key, (lo, hi) in cr.HYPER.items():
            assert lo <= p["shape"][key] <= hi


def test_a_police_car_keeps_its_livery_unless_a_colour_is_asked_for(catalogue):
    assert rq.plan("police car", 1, catalogue)["paint"] is None
    assert rq.plan("red police car", 1, catalogue)["paint"] is not None


@pytest.mark.blender
def test_a_requested_car_is_built_exported_and_reads_back_as_asked(catalogue, tmp_path):
    record = rq.build("red 90s suv", seed=3, out=tmp_path, picture=False)
    before, after = record["before"], record["after"]
    glb = tmp_path / "red_90s_suv_003.glb"
    assert glb.is_file() and (tmp_path / "red_90s_suv_003.json").is_file()
    assert record["type"] == "suv" and record["repainted"]
    assert after["wheels"] == 4 and abs(after["ground"]) < 0.01
    assert after["wheel_top_gap"] >= before["wheel_top_gap"] - 0.01     # the tyres are still inside the arches
    assert max(abs(c[0]) for c in after["wheel_centres"]) <= after["body_half_width"]
    assert sum(m["triangles"] for m in cs.glb_triangles(glb).values()) == after["triangles"] < 40000
    # the exported file, read back in Blender, is the car that was measured, in the asked colour
    lines, code = cl.run_blender(f"car_remix.catalogue({[str(glb)]!r})")
    back = cl.tagged(lines, "MG_BASE ")[0]
    assert code == 0 and "error" not in back
    assert back["length"] == pytest.approx(after["length"], abs=0.01)
    assert back["wheelbase"] == pytest.approx(after["wheelbase"], abs=0.01)
    r, g, b = back["paint"]
    assert r > 0.35 and r > 2.5 * g and r > 2.5 * b


def test_a_change_of_colour_repaints_the_same_car_and_any_other_change_gives_another(catalogue):
    first = rq.plan("red sedan", 5, catalogue)
    blue = rq.changed(first, "make it dark blue", catalogue)
    assert blue["paint_name"] == "dark blue" and blue["seed"] == 5
    assert (blue["base"], blue["shape"], blue["wheels"]) == (first["base"], first["shape"], first["wheels"])
    suv = rq.changed(blue, "an SUV instead", catalogue)
    assert suv["type"] == "suv" and suv["paint_name"] == "dark blue"      # the colour asked earlier stays
    other = rq.changed(first, "I do not like this one", catalogue)
    assert other["seed"] == 6 and other["type"] == "sedan" and other["paint_name"] == "red"
    again = rq.another(first, catalogue)
    assert again["seed"] == 6 and again["request"] == "red sedan"
    # a plan survives being written to a version's plan.json and read back
    import json
    back = rq.another(json.loads(json.dumps(first)), catalogue)
    assert json.loads(json.dumps(back)) == json.loads(json.dumps(again))


def test_a_car_request_goes_to_the_generator_and_anything_else_to_the_agents(catalogue, monkeypatch):
    from modelgen import config, db, pipeline
    car = db.create_project("red suv", "a red 90s SUV")["name"]
    mug = db.create_project("mug", "a ceramic mug")["name"]
    assert pipeline.uses_remix(car) and not pipeline.uses_remix(mug)
    get = config.get
    monkeypatch.setattr(config, "get", lambda s, k, d=None: "agents" if (s, k) == ("cars", "generator") else get(s, k, d))
    assert not pipeline.uses_remix(car)


@pytest.mark.blender
def test_a_car_asked_for_in_a_project_becomes_a_finished_version_without_agents(catalogue, monkeypatch):
    import asyncio

    from modelgen import config, db, pipeline, runner
    get = config.get
    small = {("render", "size"): 256, ("render", "samples"): 8, ("render", "turntable_angles"): 4}
    monkeypatch.setattr(config, "get", lambda s, k, d=None: small.get((s, k), get(s, k, d)))

    async def no_agent(*a, **k):
        raise AssertionError("an agent was run for a generated car")
    monkeypatch.setattr(runner, "run_agent", no_agent)
    name = db.create_project("red suv", "a red 90s SUV")["name"]
    out = asyncio.run(pipeline.run(name, on_done=no_agent))
    assert out["ok"] and out["remix"] and out["version"] == 1 and out["passed"]
    folder = pipeline.version_dir(name, 1)
    assert (folder / "exports" / f"{name}.glb").is_file() and (folder / "model.blend").is_file()
    assert (folder / "renders" / "sheet_turntable.jpg").is_file()
    v = db.get_version(name, 1)
    assert v["status"] == "done" and v["passed"] and v["score"] is None
    assert db.get_project(name)["status"] == "done"
    assert any("v1 is ready" in m["text"] for m in db.messages(50) if m["project"] == name)
    first = pipeline.remix_car(name, 1)
    assert first["type"] == "suv" and first["paint_name"] == "red"
    # the page's "Build next version" is offered, and typed changes are applied to the same car
    assert pipeline.open_changes(name) == 1 and pipeline.next_changes(name)["items"][0]["target"] == "car"
    out = asyncio.run(pipeline.run(name, changes="make it blue"))
    second = pipeline.remix_car(name, 2)
    assert out["ok"] and out["version"] == 2
    assert second["paint_name"] == "blue" and second["base"] == first["base"] and second["shape"] == first["shape"]


@pytest.mark.blender
def test_a_hypercar_comes_out_lower_and_wider_and_a_heavy_car_lighter(catalogue, tmp_path):
    seed = next(s for s in range(1, 50) if rq.plan("hypercar", s, catalogue)["base"] == "grab3d/sports-car")
    record = rq.build("hypercar", seed=seed, out=tmp_path, picture=False)
    before, after = record["before"], record["after"]
    assert before["triangles"] > rq.MAX_TRIANGLES              # this base is one of the heavy ones
    assert after["height"] < before["height"] - 0.03
    assert after["body_half_width"] > before["body_half_width"] * 1.04
    assert after["wheelbase"] > before["wheelbase"] * 1.04
    assert after["wheel_top_gap"] >= before["wheel_top_gap"] - 0.015
    if record["max_triangles"]:
        assert record["lightened"] and after["triangles"] <= rq.MAX_TRIANGLES * 1.1
