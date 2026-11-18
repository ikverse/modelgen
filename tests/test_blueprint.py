"""Reference outlines cut from car blueprints (modelgen.tools.blueprint), on drawings made here with
known answers and on the stored references."""

import json

import pytest
from PIL import Image, ImageDraw

from modelgen.tools import blueprint as bp

PUB = {"length": 4.0, "width": 1.8, "height": 1.4, "wheelbase": 2.4, "wheel_diameter": 0.64}
PX = 200  # pixels per metre in the drawings below


def _side_drawing(open_tyres=False, extras=True):
    """A boxy car side view drawn as lines, nose left: 4 m long, wheels 2.4 m apart, tyres 0.64 m;
    with a dimension line and a leader line touching it, as blueprints have."""
    w, h = 1000, 420
    img = Image.new("RGB", (w, h), "white")
    d = ImageDraw.Draw(img)
    import math
    x0, ground = 100, 380
    r = 0.32 * PX
    fx, rx = x0 + 0.8 * PX, x0 + 3.2 * PX
    sill = ground - 0.2 * PX

    def arch(cx):  # the wheel opening, back to front over the wheel, its radius the tyre's plus a gap
        ra = r + 2
        a0 = math.asin(min(1.0, (ground - r - sill) / ra))  # where the arch meets the sill line
        return [(cx + ra * math.cos(t), ground - r - ra * math.sin(t))
                for t in [a0 + (math.pi - 2 * a0) * i / 24 for i in range(25)]][::-1][::-1]

    outline = [(x0, sill), (x0, ground - 0.7 * PX), (x0 + 1.2 * PX, ground - 0.8 * PX),
               (x0 + 1.6 * PX, ground - 1.4 * PX), (x0 + 3.2 * PX, ground - 1.4 * PX), (x0 + 4.0 * PX, ground - 0.9 * PX),
               (x0 + 4.0 * PX, sill)] + arch(rx) + arch(fx) + [(x0, sill)]
    d.line(outline, fill="black", width=3)  # one closed outline, the underside cut by the arches
    for cx in (fx, rx):
        if open_tyres:  # a tyre drawn as a thin circle standing clear of its arch, as on line drawings
            d.ellipse([cx - r + 14, ground - 2 * r + 14, cx + r - 14, ground - 14], outline="black", width=2)
        else:
            d.ellipse([cx - r, ground - 2 * r, cx + r, ground], fill="black")
    if extras:
        d.line([(x0, 405), (x0 + 4.0 * PX, 405)], fill="black", width=1)  # a dimension line
        d.line([(30, 20), (x0 + 1.0 * PX, ground - 0.75 * PX)], fill="black", width=1)  # a leader line
    return img, (fx, rx, ground)


def test_a_view_is_cut_out_filled_without_its_stray_lines():
    img, _ = _side_drawing()
    mask, (ax, ay) = bp.view_mask(img, (0, 0, img.width, img.height))
    # the whole car (front bumper to tail) and nothing of the leader line reaching to the corner
    assert abs(mask.width - 4.0 * PX) <= 8 and ax <= 102
    assert mask.getpixel((round(2.0 * PX), round(0.5 * PX))) == 255  # inside the cabin: filled
    assert ay > 0.4 * PX  # the leader line from the top corner is gone


def test_the_side_view_finds_filled_tyres_and_scales_by_the_wheelbase():
    img, (fx, rx, ground) = _side_drawing()
    mask, _ = bp.view_mask(img, (0, 0, img.width, img.height))
    s = bp.side(mask, "left", PUB)
    assert s["wheels_from"] == "tyres"
    assert s["length"] == pytest.approx(4.0, abs=0.03) and s["height"] == pytest.approx(1.4, abs=0.03)
    front, rear = s["wheels"]
    assert rear["y"] - front["y"] == pytest.approx(2.4, abs=1e-6)
    assert front["y"] == pytest.approx(-1.2, abs=0.03) and front["z"] == pytest.approx(0.32, abs=1e-6)
    assert s["front_overhang"] == pytest.approx(0.8, abs=0.03)
    # the top line climbs from the bonnet to the roof; its ends are the car's ends
    assert s["top"][0][0] == pytest.approx(-2.0, abs=0.02) and max(z for _, z in s["top"]) == pytest.approx(1.4, abs=0.03)


def test_the_side_view_finds_open_tyres_by_their_arches_and_flips_a_nose_on_the_right():
    img, _ = _side_drawing(open_tyres=True)
    mask, _ = bp.view_mask(img.transpose(Image.Transpose.FLIP_LEFT_RIGHT), (0, 0, img.width, img.height))
    s = bp.side(mask, "right", PUB)
    assert s["wheels_from"] == "arches"
    assert s["length"] == pytest.approx(4.0, abs=0.06)
    assert s["front_overhang"] == pytest.approx(0.8, abs=0.04)
    assert s["top"][0][1] < s["top"][-1][1]  # flipped back: the low bonnet is at the front


def test_door_mirrors_are_dropped_from_a_width_profile():
    half = [10] * 40 + [16] * 6 + [10] * 40  # a narrow bump: a mirror
    assert max(bp._open(half, 12)) == 10
    wide = [10] * 20 + [16] * 40 + [10] * 20  # a wide one: real body, kept
    assert max(bp._open(wide, 12)) == 16


def test_an_end_view_is_scaled_up_by_height_and_across_by_width():
    img = Image.new("RGB", (600, 400), "white")
    d = ImageDraw.Draw(img)
    d.rectangle([150, 60, 450, 340], outline="black", width=3)  # the body, 300 px wide and 280 tall
    d.rectangle([110, 120, 150, 150], outline="black", width=3)  # mirrors
    d.rectangle([450, 120, 490, 150], outline="black", width=3)
    mask, _ = bp.view_mask(img, (0, 0, 600, 400))
    e = bp.end_view(mask, "front", PUB)
    assert max(w for _, w in e["half_width"]) == pytest.approx(0.9, abs=1e-6)  # the published half-width
    assert e["half_width"][0][0] == pytest.approx(0.0) and e["half_width"][-1][0] == pytest.approx(1.4, abs=0.01)
    assert e["width"] == pytest.approx(1.4 * 300 / 280, abs=0.03)  # the drawing's own aspect, as a check


def test_every_stored_reference_matches_its_real_car_and_credits_its_source():
    refs = bp.manifest()
    assert len([n for n in refs if not n.startswith("sedan_")]) == 9
    attribution = (bp.REFS / "ATTRIBUTION.md").read_text(encoding="utf-8")
    for name, entry in refs.items():
        assert entry["page"] in attribution and (bp.REFS / entry["file"]).is_file(), name
        rec = json.loads((bp.REFS / f"{name}.json").read_text(encoding="utf-8"))
        assert rec["license"] == "CC BY 4.0" and "side" in rec["views"], name
        side = rec["views"]["side"]
        assert len(side["top"]) == bp.SAMPLES and len(side["wheels"]) == 2, name
        # the side view, scaled by the wheelbase, comes out at the real car's height; its length
        # too, except the Town & Country drawing, which is 6% short for its wheelbase
        assert abs(rec["checks"]["side_height"]) < 0.04, name
        assert abs(rec["checks"]["side_length"]) < (0.07 if name == "minivan" else 0.03), name


def test_the_real_sedans_match_their_published_length_and_height():
    sedans = {n: e for n, e in bp.manifest().items() if e.get("group") == "sedan"}
    assert len(sedans) >= 12
    for name in sedans:
        checks = json.loads((bp.REFS / f"{name}.json").read_text(encoding="utf-8"))["checks"]
        assert abs(checks["side_length"]) <= 0.04 and abs(checks["side_height"]) <= 0.04, name


def test_a_stored_reference_is_reproduced_from_its_blueprint():
    stored = json.loads((bp.REFS / "city_car.json").read_text(encoding="utf-8"))
    entry = bp.manifest()["city_car"]
    with Image.open(bp.REFS / entry["file"]) as im:
        page = im.convert("RGB")
    mask, _ = bp.view_mask(page, entry["views"]["side"]["box"])
    side = bp.side(mask, "left", entry["published"])
    assert side["length"] == stored["views"]["side"]["length"]
    # the Fiat drawing prints its own overhangs (703 mm front, 543 mm rear): the cut agrees within 2 cm
    assert side["front_overhang"] == pytest.approx(0.703, abs=0.02)
    assert side["rear_overhang"] == pytest.approx(0.543, abs=0.02)


# --- the details drawn inside a view --------------------------------------------------------------

def test_shapes_drawn_inside_a_view_are_found_white_and_dark(tmp_path, monkeypatch):
    """A drawing made here: a body outline with two windows (white shapes closed by lines) and a lamp
    drawn solid black; regions() finds the three at their size, and nothing for the open ground line."""
    img = Image.new("RGB", (900, 400), "white")
    d = ImageDraw.Draw(img)
    d.rectangle([100, 100, 800, 330], outline="black", width=3)           # the body: 7 m x 2.3 m at 100 px/m
    d.rectangle([200, 130, 380, 220], outline="black", width=3)           # a window 1.8 m x 0.9 m
    d.rectangle([420, 130, 600, 220], outline="black", width=3)           # another
    d.rectangle([700, 250, 760, 290], fill="black")                       # a lamp drawn solid, 0.6 x 0.4 m
    d.line([(20, 360), (880, 360)], fill="black", width=2)                # a ground line, not a shape
    path = tmp_path / "drawing.gif"
    img.save(path)
    entry = {"car": "test", "file": str(path), "page": "", "published": {"length": 7.0, "width": 2.0, "height": 2.3,
             "wheelbase": 4.0, "wheel_diameter": 0.6}, "views": {"front": {"box": [50, 50, 850, 380]}}}
    monkeypatch.setattr(bp, "manifest", lambda: {"test": entry})
    monkeypatch.setattr(bp, "REFS", tmp_path)

    def frame(name, view, page=None):  # 100 px a metre, the body's outline its silhouette
        silhouette = Image.new("L", (701, 231), 255)
        return (lambda X, Y: ((X - 100) / 100, (330 - Y) / 100)), (silhouette, (100, 100)), entry
    monkeypatch.setattr(bp, "_view_frame", frame)
    shapes = bp.regions("test", "front", picture=False)
    white = [r for r in shapes if not r.get("dark")]
    dark = [r for r in shapes if r.get("dark")]
    windows = [r for r in white if 1.4 < r["area"] < 1.7]
    assert len(windows) == 2 and all(r["box"][3] == pytest.approx(2.0, abs=0.05) for r in windows)
    assert len(dark) == 1 and dark[0]["area"] == pytest.approx(0.24, abs=0.05)
    assert [r["id"] for r in shapes] == list(range(1, len(shapes) + 1)) and dark[0]["id"] > max(r["id"] for r in white)


def test_every_named_detail_of_the_sedan_is_a_real_shape_inside_the_car():
    details = bp.write_details("family_sedan")
    entry = bp.manifest()["family_sedan"]
    assert set(details) == set(entry["details"])
    ref = json.loads((bp.REFS / "family_sedan.json").read_text(encoding="utf-8"))
    side = ref["views"]["side"]
    length, height = side["length"], side["height"]
    for view, named in details.items():
        assert {d for d in named} == set(entry["details"][view]), view
        for detail, outlines in named.items():
            assert outlines, (view, detail)
            for o in outlines:
                us, vs = [p[0] for p in o], [p[1] for p in o]
                if view == "side":
                    assert -length / 2 <= min(us) and max(us) <= length / 2 and 0 <= min(vs) and max(vs) <= height
                elif view == "top":
                    assert -length / 2 <= min(us) and max(us) <= length / 2
                else:
                    assert max(abs(u) for u in us) <= entry["published"]["width"] / 2 + 0.02
    # the side glass sits above the beltline; the headlights on the nose's upper half; the traced grille
    # between them
    assert all(min(p[1] for p in o) > 0.9 for o in details["side"]["glass"])
    assert all(0.45 < min(p[1] for p in o) for o in details["front"]["headlights"])
    grille = details["front"]["grille"][0]
    assert max(abs(p[0]) for p in grille) < min(min(abs(p[0]) for p in o) for o in details["front"]["headlights"]) + 0.05


def test_a_traced_outline_loses_its_pixel_stairs_but_keeps_its_corners():
    """A right triangle whose long side is traced as 7 mm stair-steps, as a pixel trace of a sloping
    edge is: smoothed, that side runs within 1.5 mm of the straight line through the stairs, as a few
    points, and the three corners stay where they are."""
    import math
    s, leg = 0.007, 0.301
    pts, x, y = [(0.0, 0.0), (leg, 0.0)], leg, 0.0
    while x > 1e-9:
        y += s
        pts.append((x, y))
        x -= s
        pts.append((x, y))
    out = bp.smooth_outline(pts)
    assert len(out) < 20  # a straight side kept as a few points, not one per stair
    along = [(a[0] + (c[0] - a[0]) * t / 20, a[1] + (c[1] - a[1]) * t / 20)  # points all along its edges
             for a, c in zip(out, out[1:] + out[:1]) for t in range(20)]
    mid = leg + s / 2  # the straight line through the middle of the stairs: x + y = mid
    off = [abs(u + v - mid) / math.sqrt(2) for u, v in along if u > 0.03 and v > 0.03 and u + v > leg - 0.05]
    assert off and max(off) < 0.0015
    for corner in ((0.0, 0.0), (leg, 0.0), (0.0, y)):
        assert min(math.dist(corner, p) for p in out) < 0.004


def test_the_sedans_details_are_smooth_outlines_of_few_points():
    """Every traced detail of the sedan is kept to at most 120 points: the cuts along them fail on more."""
    for view in bp.write_details("family_sedan").values():
        for outlines in view.values():
            assert all(len(o) <= 120 for o in outlines)


def test_the_sedans_window_opening_surrounds_every_side_window():
    """The black window opening (the side windows, B-pillar and mirror triangle merged and grown) holds
    every side window with at least 1 cm to spare."""
    import math
    details = bp.write_details("family_sedan")["side"]
    (dlo,) = details["dlo"]

    def inside(p, poly):
        x, y, c = p[0], p[1], False
        for (x0, y0), (x1, y1) in zip(poly, poly[1:] + poly[:1]):
            if (y0 > y) != (y1 > y) and x < x0 + (y - y0) * (x1 - x0) / (y1 - y0):
                c = not c
        return c

    def to_edge(p, poly):
        best = 1.0
        for (ax, ay), (cx, cy) in zip(poly, poly[1:] + poly[:1]):
            vx, vy = cx - ax, cy - ay
            t = max(0.0, min(1.0, ((p[0] - ax) * vx + (p[1] - ay) * vy) / ((vx * vx + vy * vy) or 1e-12)))
            best = min(best, math.hypot(p[0] - ax - t * vx, p[1] - ay - t * vy))
        return best

    for window in details["glass"]:
        for p in window:
            assert inside(p, dlo) and to_edge(p, dlo) >= 0.01
