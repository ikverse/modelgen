"""The car body made from a reference's views (bpy_lib.shells.car_body), checked against the reference
it is made from; and, in Blender, built (bpy_lib.shells.car_v2)."""

from pathlib import Path

import pytest

from modelgen.bpy_lib.shells import car_body
from modelgen.tools import blender

FIXTURES = Path(__file__).resolve().parent / "fixtures"


@pytest.fixture(scope="module")
def sedan():
    return car_body.body("family_sedan")


@pytest.fixture(scope="module")
def sedan_rings(sedan):
    return sedan.rings(), sedan.glass_rings()


def test_the_rings_are_even_mirrored_and_in_order(sedan_rings):
    for rings in sedan_rings:
        assert rings and len({len(r) for r in rings}) == 1 and len(rings[0]) % 2 == 0
        ys = [r[0][1] for r in rings]
        assert ys == sorted(ys) and len(set(ys)) == len(ys)
        for r in rings:
            n = len(r)
            assert r[0][0] == 0.0 and r[n // 2][0] == 0.0  # the bottom and top centre on the centre line
            for k in range(1, n // 2):  # point k mirrors point n - k
                assert r[k][0] == pytest.approx(-r[n - k][0]) and r[k][2] == pytest.approx(r[n - k][2])


def test_the_sections_follow_the_reference_side_outline_and_plan(sedan, sedan_rings):
    body_rings, glass_rings = sedan_rings
    for r in body_rings:  # the lower body: the underside to the hood, the beltline under the cabin, the deck
        y = r[0][1]
        zb, zt = sedan.bounds(y)
        assert r[0][2] == pytest.approx(zb, abs=0.01)
        # the section's highest point is the outline (over the hood, the fender tops: its centre dips)
        assert max(p[2] for p in r) == pytest.approx(zt, abs=0.01)
        assert r[len(r) // 2][2] <= zt + 0.005
        assert zt <= car_body._interp(sedan.top, y) + 1e-9
    for r in glass_rings:  # the glasshouse: its top is the reference's roof outline
        y = r[0][1]
        assert r[len(r) // 2][2] == pytest.approx(car_body._interp(sedan.top, y), abs=0.01)
    # away from the rounded ends, each section is as wide as the reference's plan there
    mid = [r for r in body_rings if abs(r[0][1]) < sedan.length / 2 - 0.5]
    for r in mid:
        assert max(p[0] for p in r) == pytest.approx(car_body._interp(sedan.plan, r[0][1]), abs=0.012)


def test_the_glasshouse_sits_on_the_shoulder_between_the_glass_feet(sedan):
    (cy, cz), (dy, dz) = sedan.cowl, sedan.deck
    # the Mazda 6: the windshield's foot about 1.25 m behind the nose, the rear glass's 0.55 m before the tail
    assert cy - sedan.y_front == pytest.approx(1.25, abs=0.1) and sedan.y_rear - dy == pytest.approx(0.55, abs=0.1)
    assert cz < dz < 1.15 and sedan.belt_front_view == pytest.approx((cz + dz) / 2, abs=0.08)
    g0, g1 = sedan.glass_span
    assert cy < g0 < g1 < dy
    for y in (g0, (g0 + g1) / 2, g1):
        z = sedan.belt(y) + 0.1
        assert sedan.glass_half_width(y, z) < sedan.half_width(y, sedan.belt(y))  # set in from the shoulder


def test_the_wheels_are_where_the_reference_puts_them(sedan):
    ref = sedan.ref["views"]["side"]
    pos = sedan.wheel_positions()
    assert [p[1] for p in pos] == [w["y"] for w in ref["wheels"] for _ in (1, -1)]
    assert all(p[2] == pytest.approx(sedan.wheel_radius) for p in pos)
    assert pos[0][0] == -pos[1][0] > sedan.well_x() > 0.15
    assert sedan.track / 2 + sedan.tyre_width / 2 < sedan.width / 2


@pytest.mark.blender
def test_the_sedan_body_builds_closed_at_its_reference_size(sedan):
    """With its glass, lamps, grille and shut lines cut in from its blueprint, still one closed solid."""
    from modelgen.tools import blueprint
    blueprint.write_details("family_sedan")
    steps = [blender.part_step("body", FIXTURES / "car_v2.py", func="build_body_family_sedan"),
             blender.measure_step()]
    result = blender.run_job(steps, timeout=600)
    assert result.ok, result.traceback or result.stdout[-2000:]
    part = next(p for p in result.outputs["measure"]["parts"] if p["part"] == "body")
    assert part["islands"] == 1 and part["non_manifold_edges"] == 0
    assert part["size"][1] == pytest.approx(sedan.length, abs=0.01)
    assert part["max"][2] == pytest.approx(sedan.height, abs=0.01)
    assert sedan.width <= part["size"][0] <= sedan.width + 2 * 0.01  # the wheel-arch lips stand 8 mm proud


def test_the_clay_is_faired_tucked_lined_and_its_hood_sits_between_the_fenders(sedan, sedan_rings, monkeypatch):
    body_rings, glass_rings = sedan_rings
    # faired: a section's points lie within a few millimetres of the mean of their neighbours'
    assert car_body.Body.bumps(glass_rings) < 0.0035
    assert car_body.Body.bumps(body_rings, (sedan.y_front + 0.5, sedan.y_rear - 0.5)) < 0.005
    y = 0.3  # mid-door
    belt, wz = sedan.belt(y), sedan.wheel_radius
    zb = car_body._interp(sedan.underside, y)
    # the side tucks in toward the sill below the wheels' centres
    assert sedan.half_width(y, zb + 0.02) < sedan.half_width(y, wz) - 0.02
    # and steps in just under the shoulder's character line, by CREASE_STEP, not above it
    line = belt - car_body.CREASE_DROP
    below, above = sedan.half_width(y, line - 0.03), sedan.half_width(y, line + 0.03)
    monkeypatch.setattr(car_body, "CREASE_STEP", 0.0)
    assert sedan.half_width(y, line - 0.03) - below == pytest.approx(0.01, abs=1e-6)
    assert sedan.half_width(y, line + 0.03) == pytest.approx(above, abs=1e-6)
    # the hood's centre sits below the fender tops either side of it
    yh = (sedan.y_front + sedan.cowl[0]) / 2
    zt = sedan.bounds(yh)[1]
    edge = sedan.half_width(yh, zt)
    assert sedan._top_at(yh, 0.0, zt, edge) == pytest.approx(zt - car_body.HOOD_DIP, abs=0.002)
    assert sedan._top_at(yh, 0.8 * edge, zt, edge) > zt - 0.004


def _true_section(b, y, n=car_body.POINTS):
    """The body's section at y as section() finds it along its rays, before any rounding."""
    import math
    zb, zt = b.bounds(y)
    zc = zb + 0.45 * (zt - zb)
    sx = max(b.half_width(y, zb + (zt - zb) * i / 24) for i in range(25))
    sz = (zt - zb) / 2
    out = []
    for j in range(n + 1):
        a = -math.pi / 2 + math.pi * j / n
        dx, dz = sx * math.cos(a), sz * math.sin(a)
        lo, hi = 0.0, 2.5
        for _ in range(28):
            mid = (lo + hi) / 2
            lo, hi = (mid, hi) if b.inside(y, mid * dx, zc + mid * dz) else (lo, mid)
        out.append((lo * dx, zc + lo * dz))
    return out


def test_the_rounded_sections_stay_close_to_the_true_shape(sedan):
    """Rounding the sections' corners moves no point more than 15 mm off the true shape, at the nose and
    tail as along the doors: crisp corners, not pillows."""
    import math

    def off(p, line):
        best = 1.0
        for (ax, az), (cx, cz) in zip(line, line[1:]):
            vx, vz = cx - ax, cz - az
            t = max(0.0, min(1.0, ((p[0] - ax) * vx + (p[1] - az) * vz) / ((vx * vx + vz * vz) or 1e-12)))
            best = min(best, math.hypot(p[0] - ax - t * vx, p[1] - az - t * vz))
        return best

    for d in (0.02, 0.05, 0.1, 0.2, 0.3, 0.6, 1.0):
        for y in (sedan.y_front + d, sedan.y_rear - d):
            true = _true_section(sedan, y)
            assert max(off(p, true) for p in sedan.section(y, part="body")) < 0.015, (d, y)
    assert max(off(p, _true_section(sedan, 0.0)) for p in sedan.section(0.0, part="body")) < 0.015


def test_the_pillars_feet_are_pillars_beside_the_glass(sedan):
    """Seen from above, the glasshouse at the windshield's and the rear glass's feet reaches no more than
    9 cm past the glass each side: a pillar's foot, not a wide fin of body beside the glass."""
    import json
    details = json.loads((car_body.REFS / "family_sedan_details.json").read_text(encoding="utf-8"))
    front, rear = sorted(details["top"]["glass"], key=lambda o: sum(p[0] for p in o))
    g0, g1 = sedan.glass_span
    checked = 0
    for ring in sedan.glass_rings():
        y = ring[0][1]
        if y < g0 + 0.3:
            glass = max(abs(p[1]) for p in front if p[0] <= y + 0.06)
        elif y > g1 - 0.3:
            glass = max(abs(p[1]) for p in rear if p[0] >= y - 0.06)
        else:
            continue
        assert max(p[0] for p in ring) - glass <= 0.09, y
        checked += 1
    assert checked >= 6


def test_the_side_windows_sit_just_above_the_shoulder(sedan):
    """The shoulder runs along the real car's window line: every side window's bottom edge stands 0.5 to
    1.5 cm above it, so the glass lies on the glasshouse's wall and never wraps over the shoulder."""
    checked = 0
    for o in sedan.details["side"]["glass"]:
        ys = [p[0] for p in o]
        y = min(ys) + 0.03
        while y <= max(ys) - 0.03:  # the window's bottom edge: its lowest crossing every 2 cm along
            z = min(z0 + (z1 - z0) * (y - y0) / (y1 - y0)
                    for (y0, z0), (y1, z1) in zip(o, o[1:] + o[:1]) if min(y0, y1) <= y <= max(y0, y1) and y0 != y1)
            if z <= min(p[1] for p in o) + 0.03:  # not a slanted front or back edge
                assert 0.005 <= z - sedan.belt(y) <= 0.015, (y, z, sedan.belt(y))
                checked += 1
            y += 0.02
    assert checked >= 40


def test_the_glasshouse_walls_do_not_curl_under(sedan):
    """Along the side windows, each glasshouse wall gets no narrower going down from 8 cm above the
    shoulder into the body: no rounded lip under the glass to catch the light."""
    (_, _), (s0, s1) = sedan.sill
    checked = 0
    for ring in sedan.glass_rings():
        y = ring[0][1]
        if not s0 <= y <= s1:
            continue
        belt = sedan.belt(y)
        widest = max(p[0] for p in ring)
        wall = sorted((p[2], p[0]) for p in ring[:len(ring) // 2 + 1]
                      if belt - 0.03 <= p[2] <= belt + 0.08 and p[0] > widest / 2)
        for (z0, x0), (z1, x1) in zip(wall, wall[1:]):
            assert x1 <= x0 + 1e-4, (y, z0, z1)
        checked += 1
    assert checked >= 20


def test_the_side_glass_leans_in_evenly(sedan):
    """Along the side windows, the glass leans in on one even line from the shoulder to 8 cm under the
    roof: its lean changes by no more than 10 degrees anywhere on the way (no bend to catch the light)."""
    import math
    (_, _), (s0, s1) = sedan.sill
    checked = 0
    for ring in sedan.glass_rings():
        y = ring[0][1]
        if not s0 + 0.05 <= y <= s1 - 0.05:
            continue
        belt, z1 = sedan.belt(y), car_body._interp(sedan.top, y) - car_body.GLASS_TOP
        widest = max(p[0] for p in ring)
        wall = sorted((p[2], p[0]) for p in ring[:len(ring) // 2 + 1] if belt <= p[2] <= z1 and p[0] > widest / 2)
        lean = [math.degrees(math.atan2(xa - xb, zb - za)) for (za, xa), (zb, xb) in zip(wall, wall[1:]) if zb - za > 1e-4]
        assert len(lean) >= 4 and max(lean) - min(lean) <= 10, (y, lean)
        checked += 1
    assert checked >= 20
