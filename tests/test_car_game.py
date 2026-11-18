"""The game-ready car (bpy_lib.shells.car_game): its sections without Blender, and a whole generated
sedan built and exported with it (needs Blender)."""

import pytest

from modelgen.bpy_lib.shells import car_game as cg
from modelgen.tools import car_space as cs


@pytest.fixture(scope="module")
def built():
    ref = cs.sample("sedan", 1)
    b = cg.GameBody(ref)
    rings, ys = b.game_rings()
    return ref, b, rings, ys


def test_every_section_has_the_same_points_in_the_same_runs(built):
    _, _, rings, _ = built
    assert {len(r) for r in rings} == {2 * cg.HALF}
    for r in rings:  # each starts at the bottom centre and is mirror-symmetric
        assert r[0][0] == 0 and r[cg.HALF][0] == 0
        for i in range(1, cg.HALF):
            assert r[i][0] == pytest.approx(-r[2 * cg.HALF - i][0]) and r[i][2] == pytest.approx(r[2 * cg.HALF - i][2])


def test_the_glass_edge_is_the_same_point_on_every_section_over_the_glasshouse(built):
    _, b, rings, ys = built
    foot = cg.LOW + cg.LEDGE
    over = [(y, r) for y, r in zip(ys, rings) if b._in_glass(y)]
    assert len(over) > 20
    for y, r in over:
        assert abs(r[foot][2] - b.belt(y)) < 0.04, y   # at the beltline (fairing moves it a little)
        assert r[foot][2] < r[foot + cg.SIDE][2]       # with the side glass rising above it


def test_the_shoulder_is_one_sharp_line_of_points_along_the_car(built):
    import math
    _, b, rings, ys = built
    fa, ra = b.blend
    doors = [(y, r) for y, r in zip(ys, rings) if fa + 0.6 < y < ra - 0.6]
    assert len(doors) > 8
    for y, r in doors:
        below, at, after = r[cg.SHOULDER - 1], r[cg.SHOULDER], r[cg.SHOULDER + 1]
        assert abs(at[2] - (b.body_top(y) - 0.02)) < 0.04, y   # at the body's top edge
        up = math.atan2(at[2] - below[2], at[0] - below[0])     # the side, rising to the shoulder,
        inward = math.atan2(after[2] - at[2], after[0] - at[0])  # turns in toward the glass
        assert math.degrees(inward - up) % 360 > 40, y          # by a real corner, not a rounded one


def test_the_lower_run_rises_from_the_sill_to_the_shoulder_on_every_section(built):
    _, _, rings, _ = built
    for r in rings:
        zs = [p[2] for p in r[cg.SILL_OUT:cg.SHOULDER + 1]]
        assert all(a < b for a, b in zip(zs, zs[1:]))
        assert r[cg.SILL_IN][2] == pytest.approx(r[0][2], abs=0.02)  # the underside runs flat out to the sill


def test_the_nose_and_tail_end_in_faces(built):
    _, b, rings, _ = built
    for ring in (rings[0], rings[-1]):
        zs = [p[2] for p in ring]
        assert max(zs) - min(zs) > 0.15       # a face, not a point
    assert rings[0][0][1] < b.y_front + 0.03 and rings[-1][0][1] > b.y_rear - 0.03  # the length kept


def test_the_tyres_stand_just_outside_the_body_above_the_openings(built):
    _, b, _, _ = built
    face = b.track / 2 + b.tyre_width / 2
    top = b.wheel_radius + b.arch_radius()
    side = min(b.half_width(wy, top) for wy, _, _ in b.wheels)
    assert face == pytest.approx(side + cg.TYRE_OUT, abs=0.001)
    assert b.well_x() > 0.3


def test_the_b_pillar_is_marked_between_the_side_windows(built):
    _, b, _, ys = built
    pillars, glass = b.pillar_faces(ys), b.glass_faces(ys)
    assert 0 < sum(pillars) < sum(glass)
    assert not any(p and g for p, g in zip(pillars, glass))


def test_the_glasshouse_begins_and_ends_at_the_glass_points(built):
    ref, b, _, _ = built
    g = ref["views"]["side"]["glass"]
    assert b.cowl[0] == pytest.approx(g["windshield_foot"][0]) and b.deck[0] == pytest.approx(g["rear_glass_foot"][0])
    assert abs(b.glass_span[0] - g["windshield_foot"][0]) < 0.03
    assert abs(b.glass_span[1] - g["rear_glass_foot"][0]) < 0.03
    faces = b.glass_faces(ys := b.game_stations())
    assert 0 < sum(faces) < len(faces) / 4


@pytest.mark.blender
def test_a_generated_sedan_builds_a_closed_game_mesh_under_budget(tmp_path):
    ref_path = tmp_path / "sedan_001.json"
    import json
    ref_path.write_text(json.dumps(cs.sample("sedan", 1)), encoding="utf-8")
    glb = tmp_path / "sedan_001.glb"
    result = cs.build_glb(ref_path, glb)  # build_body fails the job if the body is not one closed solid
    assert result.ok and "live" in result.outputs, result.traceback or result.outputs.get("live_error")
    assert glb.stat().st_size > 10000
    tris = cs.glb_triangles(glb)
    body = [v for k, v in tris.items() if k.startswith("body")]
    wheels = [v for k, v in tris.items() if k.startswith("wheel")]
    assert len(body) == 1 and len(wheels) == 4
    assert body[0]["triangles"] <= cg.MAX_BODY_TRIS
    assert {"paint", "glass", "gloss_black"} <= set(body[0]["materials"])  # the windows' black frames
    assert all(w["triangles"] <= 1600 for w in wheels)
    assert body[0]["triangles"] + sum(w["triangles"] for w in wheels) < 20000
