"""The generated sedans as they stand in library/generated/sedan/ (written by
`python -m modelgen.tools.car_space sedan --sample 20` and `--glb`, and by modelgen.tools.car_lineup)."""

import json

import pytest

from modelgen.tools import car_space as cs

FOLDER = cs.GENERATED / "sedan"
FILES = sorted(FOLDER.glob("sedan_*.json"))


@pytest.fixture(scope="module")
def cars():
    return [json.loads(p.read_text(encoding="utf-8"))["generated"]["numbers"] for p in FILES]


@pytest.fixture(scope="module")
def reals():
    return cs.real("sedan")


def test_there_are_twenty_and_no_blend_weight_is_over_forty_percent(cars):
    assert len(cars) == 20
    for c in cars:
        assert max(c["blend"].values()) <= cs.MAX_WEIGHT, c["name"]


def test_every_key_proportion_is_inside_the_real_cars_range(cars, reals):
    for k in cs.PROPORTIONS:
        lo, hi = min(r["proportions"][k] for r in reals), max(r["proportions"][k] for r in reals)
        for c in cars:
            assert lo - 1e-4 <= c["proportions"][k] <= hi + 1e-4, (c["name"], k)


def test_each_car_is_apart_from_every_real_car_and_from_the_others(cars, reals):
    for c in cars:
        assert min(cs.distance(c, r) for r in reals) >= cs.MIN_FROM_REAL, c["name"]
        assert min(cs.distance(c, o) for o in cars if o is not c) >= cs.MIN_BETWEEN, c["name"]


def test_each_whole_car_is_under_twenty_thousand_triangles_and_its_body_closed():
    for p in FILES:
        glb = p.with_suffix(".glb")
        tris = cs.glb_triangles(glb)
        assert sum(v["triangles"] for v in tris.values()) < 20000, glb.name
        closed, open_edges = cs.glb_closed(glb)
        assert closed, f"{glb.name}: {open_edges} edges not shared by exactly two triangles"


def test_the_lineup_picture_has_every_car():
    renders = FOLDER / "renders"
    names = [p.stem for p in FILES] + [p.stem for p in (FOLDER / "real").glob("*.json")]
    assert len(names) == 32
    for n in names:
        assert (renders / f"{n}_side.png").is_file() and (renders / f"{n}_34.png").is_file(), n
    for p in FILES:  # a generated car's tail shows too, from behind
        assert (renders / f"{p.stem}_back34.png").is_file(), p.stem
    assert (FOLDER / "lineup.png").is_file()
