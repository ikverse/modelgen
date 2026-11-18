import pytest

from modelgen.bpy_lib.shells import car_body
from modelgen.tools import blueprint as bp
from modelgen.tools import car_space as cs

SEDANS = cs.group("sedan")


@pytest.fixture(scope="module")
def vecs():
    return {n: cs.measure(n) for n in SEDANS}


def test_every_real_sedan_measures_into_the_same_numbers_at_its_published_size(vecs):
    assert len(SEDANS) >= 12
    for name, v in vecs.items():
        assert (len(v["side_top"]), len(v["side_bottom"]), len(v["plan"])) == (cs.N_SIDE,) * 3, name
        assert (len(v["front"]), len(v["rear"])) == (cs.N_END,) * 2, name
        p, pub = v["proportions"], v["published"]
        for k in ("length", "height", "wheelbase"):
            assert p[k] == pytest.approx(pub[k], abs=0.001), (name, k)
        assert p["front_overhang"] + p["wheelbase"] + p["rear_overhang"] == pytest.approx(pub["length"], abs=0.001)
        assert max(v["side_top"]) == pytest.approx(pub["height"], abs=0.001), name


def test_the_hand_marked_glass_points_sit_where_the_drawing_has_them(vecs):
    for name, v in vecs.items():
        p = v["proportions"]
        # the windshield's foot: where the side outline turns up into the glass, as Body finds it
        body = car_body.Body(car_body.load(name))
        to_metres = bp._view_frame(name, "side")[0]
        y, _ = to_metres(*bp.manifest()[name]["views"]["side"]["glass"]["windshield_foot"])
        assert abs(y - body.cowl[0]) <= 0.08, name
        # the rear glass's foot: behind the roof, ahead of the tail
        assert p["roof_back_y"] < p["rear_glass_foot_y"] < p["length"] / 2, name
        # the beltline: above the wheels, below the roof
        for k in ("belt_front_z", "belt_back_z"):
            assert p["wheel_diameter"] < p[k] < p["roof_z"], (name, k)


def test_the_numbers_make_back_into_a_reference_body_builds(vecs):
    for name, v in vecs.items():
        ref = cs.to_reference(v)
        body = car_body.Body(ref)
        p = v["proportions"]
        assert body.length == pytest.approx(p["length"]) and body.height == pytest.approx(p["height"])
        assert body.wheels[1][0] - body.wheels[0][0] == pytest.approx(p["wheelbase"], abs=0.001), name
        top_at, low_at, _ = cs._anchor_stations(v)
        side = ref["views"]["side"]
        back = [car_body._interp(side["top"], y) for y in top_at]
        assert cs._rms(back, v["side_top"]) < 0.015, name
        back = [car_body._interp(side["bottom"], y) for y in low_at]
        assert cs._rms(back, v["side_bottom"]) < 0.015, name
        # the glass feet come back where they were measured; Body finds the windshield's from the outline
        assert side["glass"]["windshield_foot"][0] == pytest.approx(p["windshield_foot_y"], abs=0.01)
        assert side["glass"]["rear_glass_foot"][0] == pytest.approx(p["rear_glass_foot_y"], abs=0.01)
        assert abs(body.cowl[0] - p["windshield_foot_y"]) <= 0.08, name


@pytest.fixture(scope="module")
def gens():
    return [cs.sample_numbers("sedan", seed) for seed in range(1, 9)]


def test_a_seed_always_gives_the_same_car_and_seeds_differ(gens):
    assert cs.sample_numbers("sedan", 1) == gens[0]
    for i, a in enumerate(gens):
        for b in gens[i + 1:]:
            assert cs.distance(a, b) > 0


def test_a_new_car_blends_three_to_five_real_cars_at_most_forty_percent_each(gens):
    for g in gens:
        ws = list(g["blend"].values())
        assert 3 <= len(ws) <= 5 and max(ws) <= cs.MAX_WEIGHT and sum(ws) == pytest.approx(1, abs=0.002)


def test_a_new_car_stays_inside_the_real_range_and_apart_from_every_real_car(gens, vecs):
    reals = list(vecs.values())
    for g in gens:
        for k in cs.PROPORTIONS:
            lo, hi = min(r["proportions"][k] for r in reals), max(r["proportions"][k] for r in reals)
            assert lo - 1e-4 <= g["proportions"][k] <= hi + 1e-4, (g["name"], k)
        assert min(cs.distance(g, r) for r in reals) >= cs.MIN_FROM_REAL, g["name"]


def test_a_new_car_builds_a_body_at_its_own_size():
    ref = cs.sample("sedan", 3)
    body = car_body.Body(ref)
    p = ref["generated"]["numbers"]["proportions"]
    assert body.length == pytest.approx(p["length"]) and body.height == pytest.approx(p["height"])
    assert body.wheels[1][0] - body.wheels[0][0] == pytest.approx(p["wheelbase"], abs=0.001)


def test_style_words_are_not_taken_yet():
    with pytest.raises(ValueError):
        cs.sample("sedan", 1, style="sporty")


def test_distance_is_zero_to_itself_symmetric_and_apart_between_real_cars(vecs):
    vs = list(vecs.values())
    for a in vs:
        assert cs.distance(a, a) == 0
        for b in vs:
            if b is not a:
                assert cs.distance(a, b) == pytest.approx(cs.distance(b, a))
                assert cs.distance(a, b) > 0.01, (a["name"], b["name"])
