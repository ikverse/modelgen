"""The sedans' checks for every other car type, on the cars in library/generated/<type>/ (written by
`python -m modelgen.tools.car_space <type> --sample 10`, `car_kit <type> --sample`, `car_space <type>
--glb` and `car_lineup <type>`)."""

import json

import pytest

from modelgen.tools import blueprint as bp
from modelgen.tools import car_kit as ck
from modelgen.tools import car_space as cs

TYPES = ("city_car", "hot_hatch", "jdm_coupe", "muscle_coupe", "supercar", "boxy_suv", "crossover", "minivan")
COUNT = 10


def files(t):
    return sorted((cs.GENERATED / t).glob(f"{t}_[0-9][0-9][0-9].json"))


def cars(t):
    return [json.loads(p.read_text(encoding="utf-8"))["generated"]["numbers"] for p in files(t)]


@pytest.mark.parametrize("t", TYPES)
def test_every_type_has_three_or_more_real_cars_true_to_size(t):
    names = cs.group(t)
    assert len(names) >= 3
    for n in names:
        checks = json.loads((bp.REFS / f"{n}.json").read_text(encoding="utf-8"))["checks"]
        assert abs(checks["side_length"]) <= 0.04 and abs(checks["side_height"]) <= 0.04, n


@pytest.mark.parametrize("t", TYPES)
def test_blends_ranges_and_distances(t):
    gens, reals = cars(t), cs.real(t)
    assert len(gens) == COUNT
    for g in gens:
        assert max(g["blend"].values()) <= cs.MAX_WEIGHT, g["name"]
        for k in cs.PROPORTIONS:
            lo, hi = min(r["proportions"][k] for r in reals), max(r["proportions"][k] for r in reals)
            assert lo - 1e-4 <= g["proportions"][k] <= hi + 1e-4, (g["name"], k)
        assert min(cs.distance(g, r) for r in reals) >= cs.MIN_FROM_REAL, g["name"]
        assert min(cs.distance(g, o) for o in gens if o is not g) >= cs.MIN_BETWEEN, g["name"]


@pytest.mark.parametrize("t", TYPES)
def test_faces_differ(t):
    kits = [(json.loads(p.read_text(encoding="utf-8"))["generated"]["numbers"],
             json.loads(ck.kit_path(p).read_text(encoding="utf-8"))) for p in files(t)]
    assert len(kits) == COUNT
    for i, (va, ka) in enumerate(kits):
        for vb, kb in kits[i + 1:]:
            assert ck.face_distance(ka, va["proportions"], kb, vb["proportions"]) >= ck.MIN_FACE
            assert not ck.same_design(ka, kb)


@pytest.mark.parametrize("t", TYPES)
def test_game_meshes_closed_and_under_budget(t):
    for p in files(t):
        glb = p.with_suffix(".glb")
        assert sum(v["triangles"] for v in cs.glb_triangles(glb).values()) < 20000, glb.name
        closed, n = cs.glb_closed(glb)
        assert closed, f"{glb.name}: {n} open edges"


@pytest.mark.parametrize("t", TYPES)
def test_lineup_has_every_car(t):
    folder = cs.GENERATED / t
    renders = folder / "renders"
    for p in files(t):
        for view in ("side", "34", "back34"):
            assert (renders / f"{p.stem}_{view}.png").is_file(), (p.stem, view)
    for n in cs.group(t):
        assert (renders / f"{n}_side.png").is_file(), n
    assert (folder / "lineup.png").is_file()
