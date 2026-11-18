"""The face, tail and trim kit of generated cars (milestone 2). Plain Python and Pillow, no Blender.

So far (N1) it measures the real cars: the manifest's "kit" boxes, marked by hand round each real car's
headlamp, grille, lower intake, plate, fog lamp, tail lamp and door mirror on its blueprint (and the
height its black or grey lower trim begins), are turned into metres with blueprint._view_frame and then
into fractions of the car's own size, so cars of every size can be compared:

- front and rear views: x across as a fraction of the half-width (0 on the centre line, 1 at the
  car's widest), mirrored so a lamp drawn on either side is measured on the right; z up as a fraction
  of the height;
- side view: along the car as a fraction of the length (0 at the nose, 1 at the tail); z up as a
  fraction of the height.

face_ranges gives each element's smallest and largest across a type's real cars, and how many have it:
the room the kit's shapes are drawn in.

sample_kit (N2) draws a generated car's kit as flat outlines: one shape family per element (FAMILIES,
the N0 sheet's, without the two dropped as too close to a brand's signature), its size and place
drawn evenly between the real cars' smallest and largest, fitted inside the car's own front and rear
outline and kept apart from the other elements. Fog lamps and lower trim come as often as on the real
cars.

    python -m modelgen.tools.car_kit sedan --marks
    python -m modelgen.tools.car_kit sedan --sample
"""

import json
import math
import random
import sys

from PIL import Image, ImageDraw

from ..bpy_lib.shells import car_body
from . import blueprint
from . import car_space as cs

END_PARTS = {"front": ("headlamp", "grille", "intake", "plate", "fog"), "rear": ("tail", "plate")}
SIDE_PARTS = ("headlamp", "tail", "mirror")
PAIRED = {("front", "headlamp"), ("front", "fog"), ("rear", "tail")}   # one of a pair, on one side
COLOURS = {"headlamp": (0, 120, 255), "grille": (230, 0, 0), "intake": (255, 140, 0), "plate": (0, 170, 0),
           "fog": (170, 0, 200), "tail": (230, 0, 0), "mirror": (0, 170, 170)}


def face_marks(name):
    """One real car's kit marks as fractions of its size (see the module docstring):
    {"front": {part: {x_lo, x_hi, z_lo, z_hi} or None, "trim_z": z or None}, "rear": {...},
     "side": {part: {y_lo, y_hi, z_lo, z_hi}}}."""
    entry = blueprint.manifest()[name]
    side = car_body.load(name)["views"]["side"]
    length, height = side["length"], side["height"]
    half = entry["published"]["width"] / 2
    out = {}
    for view, parts in END_PARTS.items():
        kit = entry["views"][view]["kit"]
        to_m = blueprint._view_frame(name, view)[0]
        marks = {}
        for part in parts:
            box = kit.get(part)
            if not box:
                marks[part] = None
                continue
            (xa, za), (xb, zb) = to_m(box[0], box[3]), to_m(box[2], box[1])
            xs = sorted((xa / half, xb / half))
            if xs[0] + xs[1] < 0:  # measured on the right: mirror one drawn on the left
                xs = sorted((-xs[1], -xs[0]))
            marks[part] = {"x_lo": round(xs[0], 4), "x_hi": round(xs[1], 4),
                           "z_lo": round(min(za, zb) / height, 4), "z_hi": round(max(za, zb) / height, 4)}
        y = kit.get("trim_y")
        marks["trim_z"] = round(to_m(entry["views"][view]["box"][0], y)[1] / height, 4) if y is not None else None
        out[view] = marks
    kit = entry["views"]["side"]["kit"]
    to_m = blueprint._view_frame(name, "side")[0]
    marks = {}
    for part in SIDE_PARTS:
        box = kit.get(part)
        if not box:
            marks[part] = None
            continue
        (ya, za), (yb, zb) = to_m(box[0], box[3]), to_m(box[2], box[1])
        ys = sorted(((ya + length / 2) / length, (yb + length / 2) / length))
        marks[part] = {"y_lo": round(ys[0], 4), "y_hi": round(ys[1], 4),
                       "z_lo": round(min(za, zb) / height, 4), "z_hi": round(max(za, zb) / height, 4)}
    out["side"] = marks
    return out


def face_ranges(type_):
    """{view: {part: {"count": n of the type's real cars that have it, "of": all, key: (min, max)}}},
    keys as face_marks gives them plus "width" and "height" (x_hi - x_lo or y_hi - y_lo, z_hi - z_lo);
    and for the front and rear views "trim_z" likewise."""
    names = cs.group(type_)
    marks = [face_marks(n) for n in names]
    out = {}
    for view in ("front", "rear", "side"):
        parts = END_PARTS.get(view, SIDE_PARTS)
        res = {}
        for part in parts:
            boxes = [m[view][part] for m in marks if m[view].get(part)]
            r = {"count": len(boxes), "of": len(names)}
            if boxes:
                keys = list(boxes[0])
                along = "x" if "x_lo" in boxes[0] else "y"
                for b in boxes:
                    b["width"] = b[f"{along}_hi"] - b[f"{along}_lo"]
                    b["height"] = b["z_hi"] - b["z_lo"]
                for k in keys + ["width", "height"]:
                    r[k] = (round(min(b[k] for b in boxes), 4), round(max(b[k] for b in boxes), 4))
            res[part] = r
        if view in END_PARTS:
            t = [m[view]["trim_z"] for m in marks if m[view]["trim_z"] is not None]
            res["trim_z"] = {"count": len(t), "of": len(names),
                             "trim_z": (min(t), max(t)) if t else None}
        out[view] = res
    return out


def marks_sheet(type_, path):
    """Each real car's front, rear and side views with its kit boxes drawn on (headlamp blue, grille and
    tail lamp red, intake orange, plate green, fog lamp purple, mirror teal, the lower trim's top brown)."""
    rows = []
    for name in cs.group(type_):
        e = blueprint.manifest()[name]
        with Image.open(blueprint.REFS / e["file"]) as im:
            page = im.convert("RGB")
        tiles = []
        for view, w in (("front", 330), ("rear", 330), ("side", 720)):
            x0, y0, x1, y1 = e["views"][view]["box"]
            crop = page.crop((x0, y0, x1, y1))
            s = w / crop.width
            crop = crop.resize((w, round(crop.height * s)), Image.LANCZOS)
            d = ImageDraw.Draw(crop)
            for k, v in e["views"][view]["kit"].items():
                if v is None:
                    continue
                if k == "trim_y":
                    d.line([0, (v - y0) * s, w, (v - y0) * s], fill=(120, 60, 0), width=2)
                    continue
                d.rectangle([(v[0] - x0) * s, (v[1] - y0) * s, (v[2] - x0) * s, (v[3] - y0) * s],
                            outline=COLOURS[k], width=3)
            tiles.append(crop)
        row = Image.new("RGB", (1400, max(t.height for t in tiles) + 24), "white")
        ImageDraw.Draw(row).text((4, 2), e["car"], fill=(0, 0, 0), font=cs._font(15))
        x = 0
        for t in tiles:
            row.paste(t, (x, 22))
            x += t.width + 10
        rows.append(row)
    sheet = Image.new("RGB", (1400, sum(r.height for r in rows)), "white")
    y = 0
    for r in rows:
        sheet.paste(r, (0, y))
        y += r.height
    path.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(path)
    return path


# --- the shape families (N0) ------------------------------------------------------------------

def _rounded(pts, rad=0.022):
    """A polygon with each corner rounded by a radius of `rad` metres (at most 40% of its shorter edge)."""
    out = []
    m = len(pts)
    for i in range(m):
        p0, p1, p2 = pts[i - 1], pts[i], pts[(i + 1) % m]
        a = (p0[0] - p1[0], p0[1] - p1[1])
        b = (p2[0] - p1[0], p2[1] - p1[1])
        la, lb = math.hypot(*a), math.hypot(*b)
        c = min(rad, 0.4 * la, 0.4 * lb)
        s = (p1[0] + a[0] / la * c, p1[1] + a[1] / la * c)
        e = (p1[0] + b[0] / lb * c, p1[1] + b[1] / lb * c)
        for k in range(5):
            t = k / 4
            out.append(((1 - t) ** 2 * s[0] + 2 * (1 - t) * t * p1[0] + t * t * e[0],
                        (1 - t) ** 2 * s[1] + 2 * (1 - t) * t * p1[1] + t * t * e[1]))
    return out


def _ellipse(cx, cy, rx, ry, n=20):
    return [(cx + rx * math.cos(2 * math.pi * i / n), cy + ry * math.sin(2 * math.pi * i / n)) for i in range(n)]


# Each family: f(w, h) -> polygons in metres in a w x h box, z up from its bottom. A paired element's
# (lamps) run from x = 0 at its inner edge outward; a centred element's (grille, intake) from -w/2 to w/2.
FAMILIES = {
    "headlamp": {
        "slim swept": lambda w, h: [_rounded([(0, 0.1 * h), (w * 0.6, 0), (w, 0.35 * h), (w, h), (w * 0.08, 0.45 * h)],
                                             0.015)],
        "tapered wedge": lambda w, h: [_rounded([(0, 0.55 * h), (w, 0), (w, h)], 0.012)],
        "tall rounded": lambda w, h: [_rounded([(0, 0), (w, 0), (w, h), (0, 0.85 * h)], 0.035)],
    },
    "grille": {
        "wide slot": lambda w, h: [_rounded([(-w / 2, 0), (w / 2, 0), (w / 2, h), (-w / 2, h)])],
        "split upper/lower": lambda w, h: [
            _rounded([(-w * 0.42, 0.72 * h), (w * 0.42, 0.72 * h), (w * 0.42, h), (-w * 0.42, h)]),
            _rounded([(-w / 2, 0), (w / 2, 0), (w / 2, 0.55 * h), (-w / 2, 0.55 * h)])],
    },
    "intake": {
        "full-width band": lambda w, h: [_rounded([(-w / 2, 0), (w / 2, 0), (w * 0.48, h), (-w * 0.48, h)])],
        "centre + side vents": lambda w, h: [
            _rounded([(-w * 0.22, 0), (w * 0.22, 0), (w * 0.2, h), (-w * 0.2, h)])] + [
            _rounded([(sx * w * 0.3, 0.1 * h), (sx * w * 0.5, 0.1 * h), (sx * w * 0.5, 0.9 * h),
                      (sx * w * 0.32, 0.9 * h)][::sx]) for sx in (1, -1)],
        "centre only": lambda w, h: [_rounded([(-w * 0.3, 0), (w * 0.3, 0), (w * 0.27, h), (-w * 0.27, h)])],
    },
    "tail": {
        "horizontal band": lambda w, h: [_rounded([(0, 0.25 * h), (w, 0), (w, h), (0, 0.75 * h)])],
        "wraparound corner": lambda w, h: [_rounded([(0, 0.1 * h), (w * 0.7, 0), (w, 0.2 * h), (w, h), (w * 0.2, h)])],
        "L-shape": lambda w, h: [_rounded([(0, 0.62 * h), (w * 0.7, 0.62 * h), (w * 0.7, 0), (w, 0), (w, h), (0, h)],
                                          0.015)],
        # the lamp from x = 0 to w, and its bar running on in to the centre line (at x = -inner)
        "band + bar across the lid": None,
    },
    "fog": {
        "round": lambda w, h: [_ellipse(w / 2, h / 2, min(w, h) / 2, min(w, h) / 2)],
        "slim": lambda w, h: [_rounded([(0, 0.3 * h), (w, 0.3 * h), (w, 0.7 * h), (0, 0.7 * h)], 0.012)],
    },
}
PLATE = (0.52, 0.11)  # a number plate, m (Europe's is 0.52 x 0.11)
MARGIN = 0.03         # every element this far inside the car's end outline (m)
GAP = 0.02            # and this far from every other element (m)
TRIES = 200
_RANGES = {}


def _tail_bar(w, h, inner):
    return [_rounded([(-inner, 0.55 * h), (0, 0.55 * h), (0, 0.15 * h), (w, 0), (w, h), (-inner, 0.8 * h)], 0.015)]


_BORROWED = {}
FALLBACK = "sedan"  # a type whose real cars have no kit marks takes the sedans' ranges (fractions of size)


def ranges(type_):
    """face_ranges of a type, worked out once per run; the sedans' (FALLBACK) when the type's real cars
    have no kit marks of their own."""
    if type_ not in _RANGES:
        names = cs.group(type_)
        marked = names and all("kit" in blueprint.manifest()[n]["views"]["front"] for n in names)
        _RANGES[type_] = face_ranges(type_ if marked else FALLBACK)
        _BORROWED[type_] = not marked
    return _RANGES[type_]


def _interp(points, t):
    return car_body._interp(points, t)


def _end_half_width(v, end, z):
    """The car's half-width at height z (m) in its front or rear view."""
    p = v["proportions"]
    shape = [(i / (len(v[end]) - 1) * p["height"], w) for i, w in enumerate(v[end])]
    return p["width"] / 2 * _interp(shape, z)


def _seg_dist(p, a, b):
    ax, az = b[0] - a[0], b[1] - a[1]
    L = ax * ax + az * az
    t = 0.0 if L == 0 else max(0.0, min(1.0, ((p[0] - a[0]) * ax + (p[1] - a[1]) * az) / L))
    return math.hypot(p[0] - a[0] - t * ax, p[1] - a[1] - t * az)


def _inside(p, poly):
    x, z = p
    inside = False
    for (x0, z0), (x1, z1) in zip(poly, poly[1:] + poly[:1]):
        if (z0 > z) != (z1 > z) and x < x0 + (z - z0) * (x1 - x0) / (z1 - z0):
            inside = not inside
    return inside


def poly_distance(a, b):
    """The least distance (m) between two polygons; 0 when they overlap."""
    if any(_inside(p, b) for p in a) or any(_inside(p, a) for p in b):
        return 0.0
    d = min(_seg_dist(p, q0, q1) for p in a for q0, q1 in zip(b, b[1:] + b[:1]))
    return min(d, min(_seg_dist(p, q0, q1) for p in b for q0, q1 in zip(a, a[1:] + a[:1])))


def _mirror(polys):
    return [[(-x, z) for x, z in reversed(poly)] for poly in polys]


def _fits(v, end, polys):
    return all(abs(x) <= _end_half_width(v, end, z) - MARGIN for poly in polys for x, z in poly)


def _pick(rng, r, key):
    lo, hi = r[key]
    return rng.uniform(lo, hi)


def _span(rng, r, start, size, end, start_key, flip=False):
    """A start and a size drawn so that start, size and the end they make all lie in the real ranges:
    the start first, then the size from its range cut to what keeps the end in its range. flip: the
    end is start - size (an inner edge from an outer one). None when no size fits."""
    s = _pick(rng, r, start_key)
    lo, hi = r[size]
    e_lo, e_hi = r[end]
    if flip:
        lo, hi = max(lo, s - e_hi), min(hi, s - e_lo)
    else:
        lo, hi = max(lo, e_lo - s), min(hi, e_hi - s)
    if lo > hi:
        return None
    return s, rng.uniform(lo, hi)


def _paired(rng, v, end, part, family, r):
    """A lamp pair: (right side's polys, box as fractions), fitted inside the outline by narrowing it
    from its outer edge toward the real narrowest; None when it will not fit."""
    p = v["proportions"]
    hw, H = p["width"] / 2, p["height"]
    across, up = _span(rng, r, "x_hi", "width", "x_lo", "x_hi", flip=True), _span(rng, r, "z_lo", "height", "z_hi", "z_lo")
    if not across or not up:
        return None
    (x_hi, width), (z_lo, height) = across, up
    while True:
        x_lo = x_hi - width
        if family == "band + bar across the lid":
            x_lo = x_hi - width
            inner = x_lo * hw  # the bar runs on to the centre line
            polys = [[(x_lo * hw + x, z_lo * H + z) for x, z in poly]
                     for poly in _tail_bar(width * hw, height * H, inner)]
        else:
            polys = [[(x_lo * hw + x, z_lo * H + z) for x, z in poly]
                     for poly in FAMILIES[part][family](width * hw, height * H)]
        box = {"x_lo": x_lo, "x_hi": x_hi, "z_lo": z_lo, "z_hi": z_lo + height, "width": width, "height": height}
        if _fits(v, end, polys):
            return polys, box
        width *= 0.92
        x_hi = x_lo + width
        if width < r["width"][0] or x_hi < r["x_hi"][0]:
            return None


def _centred(rng, v, part, family, r):
    p = v["proportions"]
    hw, H = p["width"] / 2, p["height"]
    # symmetric about the centre line: its half-width within both edges' ranges
    lo = max(r["width"][0], -2 * r["x_lo"][1], 2 * r["x_hi"][0])
    hi = min(r["width"][1], -2 * r["x_lo"][0], 2 * r["x_hi"][1])
    up = _span(rng, r, "z_lo", "height", "z_hi", "z_lo")
    if lo > hi or not up:
        return None
    width = rng.uniform(lo, hi)
    z_lo, height = up
    polys =[[(x, z_lo * H + z) for x, z in poly] for poly in FAMILIES[part][family](width * hw, height * H)]
    box = {"x_lo": -width / 2, "x_hi": width / 2, "z_lo": z_lo, "z_hi": z_lo + height, "width": width, "height": height}
    return polys, box


def _plate(rng, v, r, below=None, above=None):
    """The number plate, centred, its bottom drawn from the real range; on the front between the lower
    intake's top (above) and the grille's bottom (below), GAP clear of both, when that gap is there;
    None when there is no room."""
    p = v["proportions"]
    hw, H = p["width"] / 2, p["height"]
    w, h = PLATE
    lo, hi = r["z_lo"]
    if above is not None:
        lo = max(lo, above + GAP / H)
    if below is not None:
        hi = min(hi, below - (h + GAP) / H)
    if lo > hi:
        return None
    z_lo = rng.uniform(lo, hi)
    poly = _rounded([(-w / 2, z_lo * H), (w / 2, z_lo * H), (w / 2, z_lo * H + h), (-w / 2, z_lo * H + h)], 0.01)
    box = {"x_lo": -w / 2 / hw, "x_hi": w / 2 / hw, "z_lo": z_lo, "z_hi": z_lo + h / H,
           "width": w / hw, "height": h / H}
    return [poly], box


PLATE_FIXED = ("x_lo", "x_hi", "z_hi", "width", "height")  # a plate's size is real-world, not a fraction


def _in_range(box, r, skip=()):
    return all(r[k][0] - 1e-6 <= box[k] <= r[k][1] + 1e-6 for k in box if k in r and k not in skip)


def _apart(elements, allow=()):
    """Whether every two elements keep GAP apart (pairs of names in `allow` may touch)."""
    for i, (na, pa) in enumerate(elements):
        for nb, pb in elements[i + 1:]:
            if (na, nb) in allow or (nb, na) in allow:
                continue
            if min(poly_distance(a, b) for a in pa for b in pb) < GAP:
                return False
    return True


FACE_PARTS = (("front", "headlamp"), ("front", "grille"), ("front", "intake"), ("rear", "tail"))
MAIN_SHAPES = ("headlamp", "grille", "intake", "tail")
MIN_FACE = 0.024   # m: the closest two real sedans' faces (face_distance of their marks)
REDRAWS = 50


def face_distance(kit_a, prop_a, kit_b, prop_b):
    """How far apart two faces are, in metres: the root-mean-square difference of the edges of their
    headlamp, grille, lower intake and tail-lamp boxes (each car's fractions times its own half-width
    and height). Kits as sample_kit gives them (or face_marks, whose boxes are the same fractions)."""
    def edges(kit, p):
        hw, H = p["width"] / 2, p["height"]
        out = []
        for end, name in FACE_PARTS:
            b = kit[end][name]
            b = b.get("box", b)
            out += [b["x_lo"] * hw, b["x_hi"] * hw, b["z_lo"] * H, b["z_hi"] * H]
        return out
    a, b = edges(kit_a, prop_a), edges(kit_b, prop_b)
    return math.sqrt(sum((x - y) ** 2 for x, y in zip(a, b)) / len(a))


def same_design(kit_a, kit_b):
    """Whether two kits share all four main shapes (headlamp, grille, intake, tail lamp)."""
    return all(kit_a["choices"][k] == kit_b["choices"][k] for k in MAIN_SHAPES)


def sample_kit(v, seed, type_="sedan", draw=0):
    """A generated car's kit (see the module docstring) from its numbers (car_space.sample_numbers) and
    seed: {"seed", "choices": {element: family}, "front"/"rear": {element: {"box": fractions,
    "polys": [polygons in metres, (x, z), the whole width]}, "trim_z": fraction or None},
    "side": {element: {"box": {y_lo, y_hi, z_lo, z_hi}}}}. The same seed (and draw) always gives the
    same kit; draw > 0 is another kit for the same car (sample_all redraws one too like an earlier car's)."""
    R = ranges(type_)
    rng = random.Random(f"kit:{type_}:{seed}" if not draw else f"kit:{type_}:{seed}:{draw}")
    H = v["proportions"]["height"]
    for _ in range(TRIES):
        choices = {part: rng.choice(sorted(FAMILIES[part])) for part in ("headlamp", "grille", "intake", "tail")}
        has_fog = rng.random() < R["front"]["fog"]["count"] / R["front"]["fog"]["of"]
        if has_fog:
            choices["fog"] = rng.choice(sorted(FAMILIES["fog"]))
        front, rear = {}, {}
        head = _paired(rng, v, "front", "headlamp", choices["headlamp"], R["front"]["headlamp"])
        tail = _paired(rng, v, "rear", "tail", choices["tail"], R["rear"]["tail"])
        fog = _paired(rng, v, "front", "fog", choices["fog"], R["front"]["fog"]) if has_fog else None
        if not head or not tail or (has_fog and not fog):
            continue
        for d, name, (polys, box) in ((front, "headlamp", head), (rear, "tail", tail)) + \
                                     (((front, "fog", fog),) if fog else ()):
            d[name] = {"box": box, "polys": polys + _mirror(polys)}
        centred = [_centred(rng, v, name, choices[name], R["front"][name]) for name in ("grille", "intake")]
        if None in centred:
            continue
        for name, (polys, box) in zip(("grille", "intake"), centred):
            front[name] = {"box": box, "polys": polys}
        plates = (_plate(rng, v, R["front"]["plate"], below=front["grille"]["box"]["z_lo"],
                         above=front["intake"]["box"]["z_hi"]),
                  _plate(rng, v, R["rear"]["plate"], below=rear["tail"]["box"]["z_lo"]))
        if None in plates:
            continue
        for d, (polys, box) in zip((front, rear), plates):
            d["plate"] = {"box": box, "polys": polys}
        # a borrowed range's plate size is a fraction of another type's cars: only its place is checked
        borrowed = _BORROWED.get(type_, False)
        ok = all(_in_range(e["box"], R[end][name], PLATE_FIXED if name == "plate" and borrowed else ())
                 for end, d in (("front", front), ("rear", rear)) for name, e in d.items())
        ok = ok and all(_fits(v, end, e["polys"]) for end, d in (("front", front), ("rear", rear)) for e in d.values())
        # each lamp of a pair is its own element; the tail bars meet at the centre line
        def parts(d):
            out = []
            for name, e in d.items():
                if name in ("headlamp", "tail", "fog"):
                    n = len(e["polys"]) // 2
                    out += [(name + "_r", e["polys"][:n]), (name + "_l", e["polys"][n:])]
                else:
                    out.append((name, e["polys"]))
            return out
        ok = ok and _apart(parts(front)) and _apart(parts(rear), allow={("tail_r", "tail_l")})
        if not ok:
            continue
        for end, d in (("front", front), ("rear", rear)):
            t = R[end]["trim_z"]
            d["trim_z"] = (round(rng.uniform(*t["trim_z"]), 4)
                           if t["trim_z"] and rng.random() < t["count"] / t["of"] else None)
        # the side view: the lamps at the heights the end views put them, the mirror where the real ones are
        S = R["side"]
        side = {}
        for name, ref in (("headlamp", front["headlamp"]["box"]), ("tail", rear["tail"]["box"])):
            lo, hi = _pick(rng, S[name], "y_lo"), None
            hi = lo + _pick(rng, S[name], "width")
            side[name] = {"box": {"y_lo": round(lo, 4), "y_hi": round(min(1.0, hi), 4),
                                  "z_lo": round(ref["z_lo"], 4), "z_hi": round(ref["z_hi"], 4)}}
        lo = _pick(rng, S["mirror"], "y_lo")
        zl = _pick(rng, S["mirror"], "z_lo")
        side["mirror"] = {"box": {"y_lo": round(lo, 4), "y_hi": round(lo + _pick(rng, S["mirror"], "width"), 4),
                                  "z_lo": round(zl, 4), "z_hi": round(zl + _pick(rng, S["mirror"], "height"), 4)}}
        for d in (front, rear):
            for e in d.values():
                if isinstance(e, dict):
                    e["box"] = {k: round(x, 4) for k, x in e["box"].items()}
                    e["polys"] = [[(round(x, 4), round(z, 4)) for x, z in poly] for poly in e["polys"]]
        return {"seed": seed, "draw": draw, "type": type_, "choices": choices, "front": front, "rear": rear,
                "side": side}
    raise RuntimeError(f"no kit fits {v.get('name')} after {TRIES} draws")


INK = {"headlamp": (60, 60, 70), "grille": (20, 20, 22), "intake": (20, 20, 22), "plate": (80, 80, 80),
       "tail": (150, 15, 20), "fog": (90, 90, 100), "mirror": (60, 60, 70)}
FILL = {"headlamp": (225, 230, 236), "grille": (55, 55, 60), "intake": (55, 55, 60), "plate": (245, 245, 240),
        "tail": (205, 40, 45), "fog": (230, 232, 236), "mirror": (200, 202, 206)}


def kit_sheet(cars, path):
    """Each car's front and rear outline with its kit drawn on, and a side strip with its headlamp, tail
    lamp and mirror; cars: [(numbers, kit)]."""
    S, cols = 120, 4
    tw = int(1.95 * S) * 2 + 40
    th = int(1.55 * S) + int(1.55 * S * 0.6) + 80
    rows = (len(cars) + cols - 1) // cols
    pic = Image.new("RGB", (cols * tw, rows * th), "white")
    d = ImageDraw.Draw(pic)
    f = cs._font(12)
    for i, (v, kit) in enumerate(cars):
        ox, oy = (i % cols) * tw, (i // cols) * th
        p = v["proportions"]
        H, hw = p["height"], p["width"] / 2
        for j, end in enumerate(("front", "rear")):
            cx, base = ox + 20 + int(1.95 * S / 2) + j * (int(1.95 * S) + 10), oy + 18 + int(1.55 * S)
            n = len(v[end])
            right = [(hw * v[end][k], H * k / (n - 1)) for k in range(n)]
            body = right + [(-x, z) for x, z in reversed(right)]
            d.polygon([(cx + x * S, base - z * S) for x, z in body], fill=(200, 202, 206), outline=(90, 90, 90))
            t = kit[end].get("trim_z")
            if t is not None:  # the lower trim: the outline below that height, dark
                low = [(x, z) for x, z in body if z <= t * H]
                if len(low) > 2:
                    d.polygon([(cx + x * S, base - z * S) for x, z in low], fill=(70, 70, 74))
            for name, e in kit[end].items():
                if not isinstance(e, dict):
                    continue
                for poly in e["polys"]:
                    d.polygon([(cx + x * S, base - z * S) for x, z in poly], fill=FILL[name], outline=INK[name])
        # side strip at 0.6 the scale
        k = S * 0.6
        ref = cs.to_reference(v)["views"]["side"]
        base = oy + 26 + int(1.55 * S) + int(1.55 * k)
        sx = ox + tw / 2
        pts = [(sx + y * k, base - z * k) for y, z in ref["top"]] + \
              [(sx + y * k, base - z * k) for y, z in reversed(ref["bottom"])]
        d.polygon(pts, fill=(200, 202, 206), outline=(90, 90, 90))
        L = p["length"]
        for name, e in kit["side"].items():
            b = e["box"]
            y0, y1 = -L / 2 + b["y_lo"] * L, -L / 2 + b["y_hi"] * L
            d.rectangle([sx + y0 * k, base - b["z_hi"] * H * k, sx + y1 * k, base - b["z_lo"] * H * k],
                        fill=FILL[name], outline=INK[name])
        c = kit["choices"]
        label = f"{v['name']}: {c['headlamp']} / {c['grille']} / {c['intake']}" + \
                (f" / {c['fog']} fogs" if "fog" in c else "") + f"\n  tail: {c['tail']}" + \
                ("  / front trim" if kit["front"]["trim_z"] is not None else "") + \
                ("  / rear trim" if kit["rear"]["trim_z"] is not None else "")
        d.multiline_text((ox + 8, base + 6), label, fill=(0, 0, 0), font=f)
    path.parent.mkdir(parents=True, exist_ok=True)
    pic.save(path)
    return path


def kit_path(ref_path):
    """Where a generated car's kit is kept: kits/<its name>.json beside its reference (in a folder of its
    own, so nothing that looks for the cars' <type>_*.json takes a kit for a car)."""
    return ref_path.parent / "kits" / ref_path.name


def sample_all(type_):
    """Every generated car of the type in library/generated/<type>/: its kit to kits/<name>.json, and
    the sheet kit_samples.png. A kit whose face is within MIN_FACE of an earlier car's, or that shares
    all four main shapes with one, is drawn again (up to REDRAWS times), and the redraw printed."""
    folder = cs.GENERATED / type_
    cars = []
    for path in sorted(folder.glob(f"{type_}_[0-9][0-9][0-9].json")):
        ref = json.loads(path.read_text(encoding="utf-8"))
        v = ref["generated"]["numbers"]
        for draw in range(REDRAWS + 1):
            kit = sample_kit(v, ref["generated"]["seed"], type_, draw)
            clash = next((w["name"] for w, k in cars if same_design(kit, k) or
                          face_distance(kit, v["proportions"], k, w["proportions"]) < MIN_FACE), None)
            if clash is None:
                break
            print(f"{path.stem}: kit draw {draw} too like {clash}'s, drawn again", flush=True)
        else:
            raise RuntimeError(f"{path.stem}: no kit apart from the earlier cars' after {REDRAWS} redraws")
        out = kit_path(path)
        out.parent.mkdir(exist_ok=True)
        out.write_text(json.dumps(kit, indent=1), encoding="utf-8")
        cars.append((v, kit))
        print(path.stem, kit["choices"], "trim", kit["front"]["trim_z"], kit["rear"]["trim_z"], flush=True)
    print("written:", kit_sheet(cars, folder / "kit_samples.png"))


def main(args):
    type_ = next((a for a in args if not a.startswith("--")), "sedan")
    if "--sample" in args:
        sample_all(type_)
        return
    ranges = face_ranges(type_)
    for view, parts in ranges.items():
        axes = ("y: fraction of the length from the nose" if view == "side"
                else "x: fraction of the half-width from the centre line")
        print(f"--- {view} view ({axes}; z: of the height)")
        for part, r in parts.items():
            vals = "  ".join(f"{k} {v[0]:.2f}-{v[1]:.2f}" for k, v in r.items()
                             if isinstance(v, tuple))
            print(f"{part:9} {r['count']:2}/{r['of']}  {vals}")
    if "--marks" in args:
        print("written:", marks_sheet(type_, blueprint.REFS / "checks" / f"{type_}_face_marks.png"))


if __name__ == "__main__":
    main(sys.argv[1:])
