"""A shared way to compare real cars: each one measured into the same set of numbers. Plain Python and
Pillow, no Blender.

A real car's reference (library/references/<name>.json, cut from its blueprint by
modelgen.tools.blueprint) becomes:

- its key proportions, in metres: length, width, height, wheelbase, overhangs, wheel size, where the
  windshield and the rear glass meet the hood and the boot lid, the roof's front and back edges, and
  the beltline (the side windows' bottom edge) at its front and back;
- the side outline's top at 64 points, lined up at the nose, the front axle, the windshield's foot, the
  rear glass's foot and the tail, so a roof is compared with a roof and a boot with a boot;
- the side outline's underside (straight across the wheels, as car_body takes it) and the top view's
  half-width at 64 points each, lined up at the nose, both axles and the tail;
- the front and rear views' half-width at 32 heights, as a fraction of each view's widest.

Two sets of lining-up points, because on some cars the rear axle is ahead of the rear glass's foot and
on others behind it: one shared set would compare the wrong parts.

Each car is fitted to its published length, height and wheelbase exactly before it is measured: the
axles are put a published wheelbase apart, the overhangs take up the rest of the published length (in
the proportion the drawing has them), and the heights are scaled to the published height. The drawing
gives the shape; the maker's figures give the size.

The glass points come from the manifest (the side view's "glass", marked by hand on the drawing's
window lines): a drawing's outline shows where the windshield meets the hood, but on most sedans the
rear glass runs into the boot lid with no corner to find.

to_reference turns the numbers back into a reference car_body.Body builds; distance says how far
apart two cars are; sample makes a new car of a type from its real ones (see sample). Run it for a
check sheet of a group of cars, or to generate some:

    python -m modelgen.tools.car_space sedan
    python -m modelgen.tools.car_space sedan --sample 20
"""

import json
import math
import random
import statistics
import sys
import tempfile
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from ..bpy_lib.shells import car_body
from . import blueprint

N_SIDE = 64   # points along the side outline's top, its underside and the plan
N_END = 32    # heights up the front and rear views
TOP_SEGMENTS = (12, 6, 31, 14)   # top outline: nose-front axle, -windshield foot, -rear glass foot, -tail
LOW_SEGMENTS = (14, 35, 14)      # underside and plan: nose-front axle, -rear axle, -tail
ROOF_BAND = 0.03  # the roof: where the outline is within this of its highest point
GLASS_MARKS = ("windshield_foot", "rear_glass_foot", "belt_front", "belt_back")
PROPORTIONS = ("length", "width", "height", "wheelbase", "front_overhang", "rear_overhang", "wheel_diameter",
               "windshield_foot_y", "windshield_foot_z", "rear_glass_foot_y", "rear_glass_foot_z",
               "roof_front_y", "roof_back_y", "roof_z", "belt_front_y", "belt_front_z", "belt_back_y", "belt_back_z")
CHECKS = blueprint.REFS / "checks"
GENERATED = blueprint.REFS.parent / "generated"
PROFILES = ("side_top", "side_bottom", "plan", "front", "rear")
DONORS = (3, 4, 5)          # a new car blends this many real cars
MAX_WEIGHT = 0.40           # none of them more than this
SHAPE_JITTER = 0.08         # plus about this much of every real car's difference from them all
SIZE_JITTER = 0.6           # sizes moved by this many of the real cars' standard deviations, on average
SIZES = ("wheelbase", "front_overhang", "rear_overhang", "height", "width", "wheel_diameter")
MIN_FROM_REAL = 0.02        # metres (distance) a new car keeps from every real one
MIN_BETWEEN = 0.012         # and generate keeps between the new cars of one batch
TRIES = 200


def group(name):
    """The manifest's cars in a group (its "group" field), in manifest order."""
    return [n for n, e in blueprint.manifest().items() if e.get("group") == name]


def _piecewise(xs, ys):
    """A function along a piecewise-linear map through the points (xs[i] -> ys[i]), xs increasing."""
    def f(x):
        for i in range(len(xs) - 1):
            if x <= xs[i + 1] or i == len(xs) - 2:
                a, b = xs[i], xs[i + 1]
                t = (x - a) / (b - a) if b != a else 0.0
                return ys[i] + (ys[i + 1] - ys[i]) * t
        return ys[-1]
    return f


def _stations(anchors, segments):
    """Positions along the car of the samples lined up at `anchors`: each segment between two anchors
    holds its count of evenly spaced samples, then the last anchor; sum(segments) + 1 in all."""
    out = []
    for (a, b), n in zip(zip(anchors, anchors[1:]), segments):
        out += [a + (b - a) * k / n for k in range(n)]
    return out + [anchors[-1]]


def _top_anchors(nose, fa, wy, gy, tail):
    """Where the side outline's top is lined up: the nose, the front axle, the windshield's foot, the
    rear glass's foot and the tail. A windshield whose foot is ahead of (or near) the front axle (a city
    car's, a mid-engined car's, a minivan's) has the second point a tenth of the way from the nose to the
    windshield's foot instead, so the points still run front to back; a sedan's are unchanged."""
    return [nose, min(fa, wy - 0.1 * (wy - nose)), wy, gy, tail]


def _sample(points, at):
    return [round(car_body._interp(points, t), 4) for t in at]


def measure(name):
    """A real car of the manifest measured into the shared numbers (see the module docstring)."""
    entry = blueprint.manifest()[name]
    pub = entry["published"]
    ref = car_body.load(name)
    side = ref["views"]["side"]
    body = car_body.Body(ref)

    # fit to the published sizes: the axles a wheelbase apart, the overhangs the rest of the length
    half = side["length"] / 2
    fa, ra = (w["y"] for w in side["wheels"])
    fo, ro = fa + half, half - ra
    k = (pub["length"] - pub["wheelbase"]) / (fo + ro)
    nose = -pub["length"] / 2
    fa2 = nose + fo * k
    ra2 = fa2 + pub["wheelbase"]
    to_y = _piecewise([-half, fa, ra, half], [nose, fa2, ra2, pub["length"] / 2])
    kz = pub["height"] / side["height"]

    def fit(points):
        return [(to_y(y), z * kz) for y, z in points]

    top = fit(side["top"])
    under = fit(body._underside(side["bottom"]))
    plan = [(to_y(y), w) for y, w in body.plan]

    to_metres = blueprint._view_frame(name, "side")[0]
    marks = {}
    for m in GLASS_MARKS:
        y, z = to_metres(*entry["views"]["side"]["glass"][m])
        marks[m] = (round(to_y(y), 4), round(z * kz, 4))
    (wy, wz), (gy, gz) = marks["windshield_foot"], marks["rear_glass_foot"]
    if not nose < wy < gy < pub["length"] / 2:
        raise ValueError(f"{name}: the glass feet must lie between the nose and the tail, in order")

    roof_z = max(z for _, z in top)
    roof = [y for y, z in top if z > roof_z - ROOF_BAND]
    props = {
        "length": pub["length"], "width": pub["width"], "height": pub["height"], "wheelbase": pub["wheelbase"],
        "front_overhang": round(fa2 - nose, 4), "rear_overhang": round(pub["length"] / 2 - ra2, 4),
        "wheel_diameter": pub["wheel_diameter"],
        "windshield_foot_y": wy, "windshield_foot_z": wz, "rear_glass_foot_y": gy, "rear_glass_foot_z": gz,
        "roof_front_y": round(roof[0], 4), "roof_back_y": round(roof[-1], 4), "roof_z": round(roof_z, 4),
        "belt_front_y": marks["belt_front"][0], "belt_front_z": marks["belt_front"][1],
        "belt_back_y": marks["belt_back"][0], "belt_back_z": marks["belt_back"][1],
    }
    top_at = _stations(_top_anchors(nose, fa2, wy, gy, pub["length"] / 2), TOP_SEGMENTS)
    low_at = _stations([nose, fa2, ra2, pub["length"] / 2], LOW_SEGMENTS)
    ends = {}
    for end in ("front", "rear"):
        prof = ref["views"][end]["half_width"]
        h, widest = prof[-1][0], max(w for _, w in prof)
        ends[end] = _sample([(z / h, w / widest) for z, w in prof], [i / (N_END - 1) for i in range(N_END)])
    return {
        "name": name, "car": entry["car"], "published": dict(pub), "proportions": props,
        "side_top": _sample(top, top_at), "side_bottom": _sample(under, low_at), "plan": _sample(plan, low_at),
        "front": ends["front"], "rear": ends["rear"],
    }


def _anchor_stations(vec):
    p = vec["proportions"]
    nose, tail = -p["length"] / 2, p["length"] / 2
    fa = nose + p["front_overhang"]
    ra = fa + p["wheelbase"]
    top_at = _stations(_top_anchors(nose, fa, p["windshield_foot_y"], p["rear_glass_foot_y"], tail), TOP_SEGMENTS)
    low_at = _stations([nose, fa, ra, tail], LOW_SEGMENTS)
    return top_at, low_at, (fa, ra)


def to_reference(vec):
    """The numbers made back into a reference in library/references/<type>.json's format, which
    car_body.Body builds. The side view also carries the glass points ("glass", in metres)."""
    p = vec["proportions"]
    n = blueprint.SAMPLES
    top_at, low_at, (fa, ra) = _anchor_stations(vec)
    top = list(zip(top_at, vec["side_top"]))
    under = list(zip(low_at, vec["side_bottom"]))
    plan = list(zip(low_at, vec["plan"]))
    ys = [-p["length"] / 2 + p["length"] * i / (n - 1) for i in range(n)]
    r = p["wheel_diameter"] / 2
    between = sorted(car_body._interp(under, y) for y in ys if fa + 1.3 * r < y < ra - 1.3 * r)
    side = {
        "length": p["length"], "height": p["height"], "wheel_radius": round(r, 4), "wheelbase": p["wheelbase"],
        "front_overhang": p["front_overhang"], "rear_overhang": p["rear_overhang"],
        "wheels": [{"y": round(fa, 4), "z": round(r, 4), "radius": round(r, 4)},
                   {"y": round(ra, 4), "z": round(r, 4), "radius": round(r, 4)}],
        "sill_z": round(between[len(between) // 2], 4) if between else None,
        "wheels_from": "car_space",
        "top": [[round(y, 4), round(car_body._interp(top, y), 4)] for y in ys],
        "bottom": [[round(y, 4), round(car_body._interp(under, y), 4)] for y in ys],
        "glass": {m: [p[f"{m}_y"], p[f"{m}_z"]] for m in GLASS_MARKS},
    }
    views = {"side": side,
             "top": {"width": p["width"], "half_width": [[round(y, 4), round(car_body._interp(plan, y), 4)]
                                                         for y in ys]}}
    for end in ("front", "rear"):
        prof = [(i / (N_END - 1), w) for i, w in enumerate(vec[end])]
        views[end] = {"width": p["width"],
                      "half_width": [[round(p["height"] * i / (n - 1), 4),
                                      round(car_body._interp(prof, i / (n - 1)) * p["width"] / 2, 4)]
                                     for i in range(n)]}
    return {"car": vec.get("car", ""), "source": "car_space", "published": dict(vec["published"]),
            "views": views}


def _rms(a, b):
    return math.sqrt(sum((x - y) ** 2 for x, y in zip(a, b)) / len(a))


def distance(a, b):
    """How far apart two measured cars are, in metres: the average of the root-mean-square differences
    of their side top, underside, plan, end shapes (as half-widths in metres) and key proportions."""
    def ends(v):
        hw = v["proportions"]["width"] / 2
        return [w * hw for w in v["front"] + v["rear"]]
    parts = [_rms(a["side_top"], b["side_top"]), _rms(a["side_bottom"], b["side_bottom"]),
             _rms(a["plan"], b["plan"]), _rms(ends(a), ends(b)),
             _rms([a["proportions"][k] for k in PROPORTIONS], [b["proportions"][k] for k in PROPORTIONS])]
    return sum(parts) / len(parts)


# --- new cars ----------------------------------------------------------------------------------

_REAL = {}


def real(type_):
    """The real cars of a type (its manifest group), measured once per run."""
    if type_ not in _REAL:
        names = group(type_)
        if not names:
            raise ValueError(f"no real cars of type '{type_}' in the manifest")
        _REAL[type_] = [measure(n) for n in names]
    return _REAL[type_]


def _combine(weights, vecs):
    out = {"proportions": {k: sum(w * v["proportions"][k] for w, v in zip(weights, vecs)) for k in PROPORTIONS}}
    for f in PROFILES:
        out[f] = [sum(w * v[f][i] for w, v in zip(weights, vecs)) for i in range(len(vecs[0][f]))]
    return out


def sample_numbers(type_, seed, style=None):
    """A new car of a type, as numbers (measure's format) with "blend": {real car: weight}.

    1. Blend: 3-5 of the type's real cars picked at random, with random weights of at most 40% each
       adding up to 100%: the weighted average of their numbers (every car lined up at the same points,
       so noses average with noses).
    2. Shape: plus a little (SHAPE_JITTER) of every real car's difference from them all.
    3. Size: the wheelbase, overhangs, height, width and wheel size each moved by a random amount
       (SIZE_JITTER of the real cars' spread, on average), kept within the real cars' smallest and
       largest; the outlines follow, the windshield's foot keeping its distance from the front axle and
       the rear glass's from the tail.
    4. A draw goes again (from the same seed, up to TRIES times) if any key proportion is outside the
       real cars' range or the car is within MIN_FROM_REAL of a real one. The same seed always gives the
       same car."""
    if style is not None:
        raise ValueError("style words come later: sample takes style=None for now")
    reals = real(type_)
    lo = {k: min(v["proportions"][k] for v in reals) for k in PROPORTIONS}
    hi = {k: max(v["proportions"][k] for v in reals) for k in PROPORTIONS}
    sd = {k: statistics.pstdev(v["proportions"][k] for v in reals) for k in SIZES}
    rng = random.Random(f"{type_}:{seed}")
    for _ in range(TRIES):
        donors = rng.sample(range(len(reals)), min(rng.choice(DONORS), len(reals)))
        while True:
            g = [rng.gammavariate(1.5, 1.0) for _ in donors]
            weights = [x / sum(g) for x in g]
            if max(weights) <= MAX_WEIGHT:
                break
        full = [0.0] * len(reals)
        for d, w in zip(donors, weights):
            full[d] = w
        jitter = [rng.gauss(0, SHAPE_JITTER) for _ in reals]
        mean = sum(jitter) / len(jitter)
        v = _combine([f + j - mean for f, j in zip(full, jitter)], reals)
        p = v["proportions"]

        front_axle = -p["length"] / 2 + p["front_overhang"]
        windshield_from_axle = p["windshield_foot_y"] - front_axle
        rear_glass_from_tail = p["length"] / 2 - p["rear_glass_foot_y"]
        belt_front_from_ws = p["belt_front_y"] - p["windshield_foot_y"]
        belt_back_from_rg = p["belt_back_y"] - p["rear_glass_foot_y"]
        roof = (p["roof_front_y"] - p["windshield_foot_y"], p["roof_back_y"] - p["windshield_foot_y"])
        height, width = max(v["side_top"]), p["width"]
        for k in SIZES:
            p[k] = min(hi[k], max(lo[k], p[k] + rng.gauss(0, SIZE_JITTER * sd[k])))
        p["length"] = p["front_overhang"] + p["wheelbase"] + p["rear_overhang"]
        front_axle = -p["length"] / 2 + p["front_overhang"]
        p["windshield_foot_y"] = front_axle + windshield_from_axle
        p["rear_glass_foot_y"] = p["length"] / 2 - rear_glass_from_tail
        p["belt_front_y"] = p["windshield_foot_y"] + belt_front_from_ws
        p["belt_back_y"] = p["rear_glass_foot_y"] + belt_back_from_rg
        p["roof_front_y"] = p["windshield_foot_y"] + roof[0]
        p["roof_back_y"] = p["windshield_foot_y"] + roof[1]
        kz = p["height"] / height
        v["side_top"] = [z * kz for z in v["side_top"]]
        v["side_bottom"] = [z * kz for z in v["side_bottom"]]
        for k in ("windshield_foot_z", "rear_glass_foot_z", "roof_z", "belt_front_z", "belt_back_z"):
            p[k] *= kz
        v["plan"] = [w * p["width"] / width for w in v["plan"]]

        for f in PROFILES:
            v[f] = [round(x, 4) for x in v[f]]
        for k in PROPORTIONS:
            p[k] = round(p[k], 4)
        if not all(lo[k] - 1e-4 <= p[k] <= hi[k] + 1e-4 for k in PROPORTIONS):
            continue
        if not -p["length"] / 2 < p["windshield_foot_y"] < p["rear_glass_foot_y"] < p["length"] / 2:
            continue
        if min(distance(v, r) for r in reals) < MIN_FROM_REAL:
            continue
        v.update(name=f"{type_}_{seed:03d}" if isinstance(seed, int) else f"{type_}_{seed}",
                 car=f"generated {type_} {seed}",
                 published={k: p[k] for k in ("length", "width", "height", "wheelbase", "wheel_diameter")},
                 blend={reals[d]["car"]: round(w, 4) for d, w in zip(donors, weights)})
        return v
    raise RuntimeError(f"no {type_} within the real cars' range after {TRIES} draws for seed {seed}")


def sample(type_, seed, style=None):
    """A new car of a type as a reference (to_reference's format, which car_body.Body builds), with
    "generated": its type, seed, blend of real cars and its numbers. See sample_numbers."""
    v = sample_numbers(type_, seed, style)
    ref = to_reference(v)
    ref["car"] = v["car"]
    ref["generated"] = {"type": type_, "seed": seed, "blend": v["blend"], "numbers": v}
    return ref


# --- check sheets ------------------------------------------------------------------------------

COLOURS = [(230, 25, 75), (60, 180, 75), (0, 130, 200), (245, 130, 48), (145, 30, 180), (70, 160, 160),
           (240, 50, 230), (130, 130, 0), (0, 0, 128), (128, 0, 0), (100, 100, 100), (170, 110, 40)]


def _font(size):
    try:
        return ImageFont.truetype("arial.ttf", size)
    except OSError:
        return ImageFont.load_default()


def space_sheet(vecs, path):
    """The cars drawn over each other: side and top outlines in metres, then the side outline's top and
    underside lined up (the lining-up points as grey lines), with a key."""
    W, M = 1600, 40
    S = (W - 2 * M - 260) / 5.3        # pixels per metre
    rows = [("side outlines, in metres (front to the left)", 1.7), ("top view half-widths, in metres", 1.1),
            ("side outlines lined up: top at nose, front axle, windshield foot, rear glass foot, tail; "
             "underside at nose, axles, tail", 1.7)]
    H = int(sum(h * S + 60 for _, h in rows)) + M
    pic = Image.new("RGB", (W, H), "white")
    d = ImageDraw.Draw(pic)
    f, small = _font(18), _font(14)
    cx = M + (W - 2 * M - 260) / 2
    y0 = M
    refs = [to_reference(v) for v in vecs]

    d.text((M, y0 - 30), rows[0][0], fill=(0, 0, 0), font=f)
    base = y0 + rows[0][1] * S
    d.line([M, base, W - M - 260, base], fill=(200, 200, 200))
    for v, r, c in zip(vecs, refs, COLOURS):
        s = r["views"]["side"]
        pts = [(cx + y * S, base - z * S) for y, z in s["top"]] + \
              [(cx + y * S, base - z * S) for y, z in reversed(s["bottom"])]
        d.line(pts + [pts[0]], fill=c, width=2)
        for w in s["wheels"]:
            d.ellipse([cx + (w["y"] - w["radius"]) * S, base - 2 * w["radius"] * S, cx + (w["y"] + w["radius"]) * S,
                       base], outline=c, width=1)
    y0 = base + 70

    d.text((M, y0 - 30), rows[1][0], fill=(0, 0, 0), font=f)
    mid = y0 + rows[1][1] * S
    for v, r, c in zip(vecs, refs, COLOURS):
        hw = r["views"]["top"]["half_width"]
        d.line([(cx + y * S, mid - w * S) for y, w in hw], fill=c, width=2)
    d.line([M, mid, W - M - 260, mid], fill=(200, 200, 200))
    y0 = mid + 70

    d.text((M, y0 - 30), rows[2][0], fill=(0, 0, 0), font=f)
    base = y0 + rows[2][1] * S
    x_of = _piecewise([0, N_SIDE - 1], [M, W - M - 260])
    for i in [0, *[sum(TOP_SEGMENTS[:k]) for k in range(1, 5)]]:
        d.line([x_of(i), y0, x_of(i), base - 0.75 * S], fill=(200, 200, 200))
    for i in [0, *[sum(LOW_SEGMENTS[:k]) for k in range(1, 4)]]:
        d.line([x_of(i), base - 0.6 * S, x_of(i), base + 6], fill=(200, 200, 200))
    for v, c in zip(vecs, COLOURS):
        d.line([(x_of(i), base - z * S) for i, z in enumerate(v["side_top"])], fill=c, width=2)
        d.line([(x_of(i), base - z * S) for i, z in enumerate(v["side_bottom"])], fill=c, width=2)

    kx = W - M - 240
    for i, (v, c) in enumerate(zip(vecs, COLOURS)):
        d.rectangle([kx, M + i * 26, kx + 18, M + i * 26 + 14], fill=c)
        d.text((kx + 26, M + i * 26 - 1), v["car"], fill=(0, 0, 0), font=small)
    path.parent.mkdir(parents=True, exist_ok=True)
    pic.save(path)


def marks_sheet(names, path):
    """Each car's side view with its hand-marked glass points: windshield foot red, rear glass foot
    blue, the beltline's ends green with the line between them."""
    tiles = []
    for name in names:
        e = blueprint.manifest()[name]
        with Image.open(blueprint.REFS / e["file"]) as im:
            page = im.convert("RGB")
        x0, y0, x1, y1 = e["views"]["side"]["box"]
        crop = page.crop((x0, y0, x1, y0 + int((y1 - y0) * 0.72)))
        s = 760 / crop.width
        crop = crop.resize((760, round(crop.height * s)), Image.LANCZOS)
        d = ImageDraw.Draw(crop)
        g = {k: ((X - x0) * s, (Y - y0) * s) for k, (X, Y) in e["views"]["side"]["glass"].items()}
        d.line([*g["belt_front"], *g["belt_back"]], fill=(0, 160, 0), width=2)
        for k, col in (("windshield_foot", (230, 0, 0)), ("rear_glass_foot", (0, 70, 230)),
                       ("belt_front", (0, 160, 0)), ("belt_back", (0, 160, 0))):
            x, y = g[k]
            d.ellipse([x - 6, y - 6, x + 6, y + 6], outline=col, width=3)
        d.text((6, 4), e["car"], fill=(0, 0, 0), font=_font(16))
        tiles.append(crop)
    cols = 2
    row_h = [max(t.height for t in tiles[i:i + cols]) for i in range(0, len(tiles), cols)]
    sheet = Image.new("RGB", (cols * 770, sum(row_h) + 10 * len(row_h)), "white")
    y = 0
    for r, i in enumerate(range(0, len(tiles), cols)):
        for c, t in enumerate(tiles[i:i + cols]):
            sheet.paste(t, (c * 770, y))
        y += row_h[r] + 10
    path.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(path)


def samples_sheet(reals, gens, path):
    """Real cars then generated ones, one tile each at the same scale: the side outline with its wheels
    and, under it, the top view (both halves), with each generated car's blend written below."""
    cols, tw, S = 4, 420, 72          # tiles across, tile width (px), pixels per metre
    th = int(1.6 * S + 1.95 * S + 150)
    tiles = [(v, False) for v in reals] + [(v, True) for v in gens]
    rows = (len(tiles) + cols - 1) // cols
    pic = Image.new("RGB", (cols * tw, rows * th), "white")
    d = ImageDraw.Draw(pic)
    f, small = _font(15), _font(12)
    for i, (v, gen) in enumerate(tiles):
        ox, oy = (i % cols) * tw, (i // cols) * th
        ink = (20, 60, 200) if gen else (90, 90, 90)
        d.rectangle([ox + 2, oy + 2, ox + tw - 3, oy + th - 3], outline=(225, 225, 225))
        d.text((ox + 8, oy + 6), v["car"], fill=ink, font=f)
        r = to_reference(v)
        s = r["views"]["side"]
        cx, base = ox + tw / 2, oy + 28 + 1.6 * S
        pts = [(cx + y * S, base - z * S) for y, z in s["top"]] + \
              [(cx + y * S, base - z * S) for y, z in reversed(s["bottom"])]
        d.line(pts + [pts[0]], fill=ink, width=2)
        for w in s["wheels"]:
            d.ellipse([cx + (w["y"] - w["radius"]) * S, base - 2 * w["radius"] * S,
                       cx + (w["y"] + w["radius"]) * S, base], outline=ink, width=1)
        mid = base + 12 + 0.98 * S
        hw = r["views"]["top"]["half_width"]
        plan = [(cx + y * S, mid - w * S) for y, w in hw] + [(cx + y * S, mid + w * S) for y, w in reversed(hw)]
        d.line(plan + [plan[0]], fill=ink, width=2)
        p = v["proportions"]
        lines = [f"L {p['length']:.2f}  WB {p['wheelbase']:.2f}  H {p['height']:.2f}  W {p['width']:.2f}"]
        if gen:
            lines += [f"{w:.0%} {c}" for c, w in sorted(v["blend"].items(), key=lambda kv: -kv[1])]
        d.multiline_text((ox + 8, mid + 0.98 * S + 8), "\n".join(lines), fill=(0, 0, 0), font=small, spacing=2)
    path.parent.mkdir(parents=True, exist_ok=True)
    pic.save(path)


def generate(type_, count):
    """Seeds 1..count of a type: library/generated/<type>/<type>_NNN.json each, samples.png, a table."""
    out = GENERATED / type_
    out.mkdir(parents=True, exist_ok=True)
    reals = real(type_)
    gens = []
    seed = 0
    while len(gens) < count:  # a seed whose car is too close to an earlier one is skipped
        seed += 1
        ref = sample(type_, seed)
        v = ref["generated"]["numbers"]
        near = [g["name"] for g in gens if distance(v, g) < MIN_BETWEEN]
        if near:
            print(f"seed {seed} skipped: within {MIN_BETWEEN} m of {near[0]}")
            continue
        gens.append(v)
        (out / f"{type_}_{seed:03d}.json").write_text(json.dumps(ref, indent=1), encoding="utf-8")
    print(f"{'car':22}{'L':>7}{'WB':>7}{'OHf':>7}{'OHr':>7}{'H':>7}{'W':>7}  near real  near gen  blend")
    for v in gens:
        p = v["proportions"]
        dr = min(distance(v, r) for r in reals)
        dg = min(distance(v, g) for g in gens if g is not v) if len(gens) > 1 else float("nan")
        blend = ", ".join(f"{c} {w:.0%}" for c, w in sorted(v["blend"].items(), key=lambda kv: -kv[1]))
        print(f"{v['name']:22}" + "".join(f"{p[k]:7.3f}" for k in ("length", "wheelbase", "front_overhang",
                                                                  "rear_overhang", "height", "width"))
              + f"  {dr:9.3f} {dg:9.3f}  {blend}")
    samples_sheet(reals, gens, out / "samples.png")
    print("written:", out)


WRAPPER = '''from bpy_lib.shells import car_game, car_kit_mesh
REF = car_game.load({ref!r})
KIT = car_kit_mesh.load_kit({ref!r})


def build_body():
    return car_game.build_body(REF, KIT)


def build_kit():
    return car_kit_mesh.build_kit(REF, KIT)


def build_mirrors():
    return car_kit_mesh.build_mirrors(REF, KIT)


def build_wheel_fr():
    return car_game.build_wheel(REF, 0)


def build_wheel_fl():
    return car_game.build_wheel(REF, 1)


def build_wheel_rr():
    return car_game.build_wheel(REF, 2)


def build_wheel_rl():
    return car_game.build_wheel(REF, 3)


def apply(groups):
    car_game.finish(groups, REF)
'''
GAME_PARTS = ("body", "wheel_fr", "wheel_fl", "wheel_rr", "wheel_rl")


def glb_triangles(path):
    """Triangles per mesh in a .glb (from its JSON chunk: each primitive's index count / 3), and the
    materials each mesh uses: {mesh: {"triangles": n, "materials": [names]}}."""
    data = Path(path).read_bytes()
    length = int.from_bytes(data[12:16], "little")
    gltf = json.loads(data[20:20 + length])
    mats = [m.get("name", "") for m in gltf.get("materials", [])]
    out = {}
    for mesh in gltf.get("meshes", []):
        tris, used = 0, []
        for prim in mesh["primitives"]:
            acc = gltf["accessors"][prim["indices"]]
            tris += acc["count"] // 3
            if "material" in prim:
                used.append(mats[prim["material"]])
        out[mesh.get("name", "")] = {"triangles": tris, "materials": used}
    return out


def _glb(path):
    data = Path(path).read_bytes()
    length = int.from_bytes(data[12:16], "little")
    gltf = json.loads(data[20:20 + length])
    at = 20 + length
    bin_len = int.from_bytes(data[at:at + 4], "little")
    return gltf, data[at + 8:at + 8 + bin_len]


def _accessor(gltf, binary, index):
    """An accessor's values as tuples (VEC3) or numbers (SCALAR), for float, uint16 and uint32 data."""
    import struct
    acc = gltf["accessors"][index]
    view = gltf["bufferViews"][acc["bufferView"]]
    fmt, size = {5126: ("f", 4), 5123: ("H", 2), 5125: ("I", 4)}[acc["componentType"]]
    width = {"SCALAR": 1, "VEC2": 2, "VEC3": 3, "VEC4": 4}[acc["type"]]
    stride = view.get("byteStride") or size * width
    base = view.get("byteOffset", 0) + acc.get("byteOffset", 0)
    out = []
    for i in range(acc["count"]):
        vals = struct.unpack_from("<" + fmt * width, binary, base + i * stride)
        out.append(vals if width > 1 else vals[0])
    return out


def glb_closed(path, mesh_prefix="body"):
    """Whether the .glb's meshes named mesh_prefix... are one closed solid: once points at the same
    place are joined (the exporter splits them where the shading or the material changes), every edge
    is shared by exactly two triangles. Returns (closed, open edge count)."""
    gltf, binary = _glb(path)
    edges = {}
    cells, points = {}, []
    tol, cell = 1e-6, 1e-5

    def weld(p):
        """The index of the point within tol (1 µm, as Blender welds) of p, joined once and for all (looked up in the grid cells
        around it, so two copies either side of a cell's edge still meet)."""
        c = tuple(int(math.floor(v / cell)) for v in p)
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                for dz in (-1, 0, 1):
                    for i in cells.get((c[0] + dx, c[1] + dy, c[2] + dz), ()):
                        q = points[i]
                        if abs(q[0] - p[0]) <= tol and abs(q[1] - p[1]) <= tol and abs(q[2] - p[2]) <= tol:
                            return i
        points.append(p)
        cells.setdefault(c, []).append(len(points) - 1)
        return len(points) - 1

    for mesh in gltf.get("meshes", []):
        if not mesh.get("name", "").startswith(mesh_prefix):
            continue
        for prim in mesh["primitives"]:
            pos = _accessor(gltf, binary, prim["attributes"]["POSITION"])
            key = [weld(p) for p in pos]
            idx = _accessor(gltf, binary, prim["indices"])
            for t in range(0, len(idx), 3):
                tri = [key[idx[t]], key[idx[t + 1]], key[idx[t + 2]]]
                if len(set(tri)) < 3:
                    continue
                for a, b in ((tri[0], tri[1]), (tri[1], tri[2]), (tri[2], tri[0])):
                    e = (a, b) if a < b else (b, a)
                    edges[e] = edges.get(e, 0) + 1
    bad = sum(1 for n in edges.values() if n != 2)
    return bad == 0 and bool(edges), bad


def build_glb(ref_path, glb_path, timeout=900):
    """Build one car's game mesh in Blender from a reference json and export it as .glb; returns the
    Blender result."""
    from . import blender

    folder = Path(tempfile.mkdtemp(prefix="mg_car_game_"))
    wrapper = folder / "car_game_job.py"
    wrapper.write_text(WRAPPER.format(ref=str(ref_path)), encoding="utf-8")
    parts = list(GAME_PARTS)
    if (Path(ref_path).parent / "kits" / Path(ref_path).name).is_file():  # a generated car's kit
        parts += ["kit", "mirrors"]
    steps = [blender.part_step(p, wrapper, func=f"build_{p}") for p in parts]
    steps += [blender.materials_step(wrapper), blender.live_step(glb_path)]
    return blender.run_job(steps, timeout=timeout)


def kit_report(result):
    """The MG_KIT line car_kit_mesh.build_kit printed in a Blender run: {"skipped", "gap_mm", "pieces"},
    or None when the car had no kit."""
    for line in result.stdout.splitlines():
        if line.startswith("MG_KIT "):
            return json.loads(line[7:])
    return None


def build_all(type_):
    """Every generated car of a type in library/generated/<type>/ built and exported as .glb."""
    folder = GENERATED / type_
    for ref_path in sorted(folder.glob(f"{type_}_*.json")):
        glb = ref_path.with_suffix(".glb")
        result = build_glb(ref_path, glb)
        if not result.ok or "live" not in result.outputs:
            print(f"{ref_path.stem}: FAILED {result.failed_step}\n{result.traceback or result.outputs.get('live_error')}")
            continue
        tris = glb_triangles(glb)
        body = sum(v["triangles"] for k, v in tris.items() if k.startswith("body"))
        wheels = [v["triangles"] for k, v in tris.items() if k.startswith("wheel")]
        kit = sum(v["triangles"] for k, v in tris.items() if k.startswith(("kit", "mirrors")))
        rep = kit_report(result)
        note = f"  kit {kit}  left out {rep['skipped']}  gap {rep['gap_mm']} mm" if rep else ""
        print(f"{ref_path.stem}: body {body}  wheels {wheels}{note}  total {body + sum(wheels) + kit}  "
              f"({result.seconds:.0f} s)")


def main(args):
    if "--glb" in args:
        rest = [a for a in args if a != "--glb"]
        build_all(rest[0] if rest else "sedan")
        return
    if "--sample" in args:
        i = args.index("--sample")
        count = int(args[i + 1])
        rest = args[:i] + args[i + 2:]
        generate(rest[0] if rest else "sedan", count)
        return
    name = args[0] if args else "sedan"
    names = group(name)
    vecs = [measure(n) for n in names]
    cols = [("length", "L"), ("wheelbase", "WB"), ("front_overhang", "OHf"), ("rear_overhang", "OHr"),
            ("height", "H"), ("width", "W"), ("wheel_diameter", "wheel"), ("windshield_foot_y", "wsY"),
            ("rear_glass_foot_y", "rgY"), ("roof_z", "roof"), ("belt_front_z", "beltF"), ("belt_back_z", "beltB")]
    print(f"{'car':38}" + "".join(f"{h:>7}" for _, h in cols) + "  nearest real car (distance, m)")
    for v in vecs:
        others = [(distance(v, o), o["car"]) for o in vecs if o is not v]
        dist, near = min(others)
        print(f"{v['car'][:37]:38}" + "".join(f"{v['proportions'][k]:7.3f}" for k, _ in cols)
              + f"  {near} ({dist:.3f})")
    print(f"{'range':38}" + "".join(f"{min(v['proportions'][k] for v in vecs):7.3f}" for k, _ in cols))
    print(f"{'':38}" + "".join(f"{max(v['proportions'][k] for v in vecs):7.3f}" for k, _ in cols))
    space_sheet(vecs, CHECKS / f"{name}_space.png")
    marks_sheet(names, CHECKS / f"{name}_marks.png")
    print("sheets:", CHECKS / f"{name}_space.png", CHECKS / f"{name}_marks.png")


if __name__ == "__main__":
    main(sys.argv[1:])
