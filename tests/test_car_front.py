"""The layout of a reference-built car's front parts (bpy_lib.shells.car_front), from the sedan's traced
outlines: the grille mesh, the projectors and light strips, the intake bar."""

import json

import pytest

from modelgen.bpy_lib.shells import car_body, car_front
from modelgen.tools import blueprint


@pytest.fixture(scope="module")
def front():
    blueprint.write_details("family_sedan")
    return json.loads((car_body.REFS / "family_sedan_details.json").read_text(encoding="utf-8"))["front"]


def test_every_front_pick_is_on_the_blueprint(front):
    for detail in ("headlights", "grille", "intakes", "fog_lamps", "vents", "plate", "lip_line"):
        assert front[detail] and all(len(o) >= 3 for o in front[detail]), detail
    assert len(front["headlights"]) == 2 and len(front["fog_lamps"]) == 2 and len(front["vents"]) == 2


def test_the_grille_mesh_fills_the_grille_and_stays_inside_it(front):
    (grille,) = front["grille"]
    pieces = car_front.mesh_pieces(grille)
    assert len(pieces) > 40
    for p0, p1 in pieces:
        for p in (p0, p1, ((p0[0] + p1[0]) / 2, (p0[1] + p1[1]) / 2)):
            assert car_front.inside(p, grille)
        assert ((p1[0] - p0[0]) ** 2 + (p1[1] - p0[1]) ** 2) ** 0.5 <= car_front.MESH_PIECE + 1e-9


def test_the_projectors_and_light_strips_sit_inside_their_headlights(front):
    for lamp in front["headlights"]:
        spots = car_front.projector_spots(lamp)
        assert len(spots) == 2
        for u, v, r in spots:
            assert 0.01 <= r <= car_front.PROJECTOR
            for du, dv in ((r, 0), (-r, 0), (0, r), (0, -r)):
                assert car_front.inside((u + du, v + dv), lamp)
        strip = car_front.top_edge(lamp)
        assert len(strip) >= 5 and all(car_front.inside(p, lamp) for p in strip)


def test_the_intake_bar_runs_across_the_intake(front):
    (intake,) = front["intakes"]
    u0, u1, v = car_front.intake_bar(intake)
    us = [p[0] for p in intake]
    assert u1 - u0 > 0.9 * (max(us) - min(us)) - 0.02
    assert car_front.inside((u0 + 0.02, v), intake) and car_front.inside((u1 - 0.02, v), intake)
