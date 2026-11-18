"""Reference outlines of real cars, cut from blueprints. Plain Python and Pillow, no Blender.

A blueprint draws one car in up to four views (side, top, front, rear) as lines on a white page.
library/references/manifest.json says, for each example car type, which blueprint, where each view
sits on it (a box that holds that view and nothing else), which way the nose points, and the real
car's published dimensions. From each view this cuts out the car's filled silhouette: the white page
is flooded in from outside the box, so everything the outline encloses is car, and thin lines that
are not car (dimension and leader lines, the ground line, an aerial) are opened away. Each view is
then measured in metres:

- side: the top and bottom outline along the car, the wheels (circles fitted to the tyres where they
  meet the ground) and the sill height; scaled by the published wheelbase, so the length and height
  it measures check the blueprint against the real car;
- top: the half-width along the car, scaled by the published length;
- front and rear: the half-width up the car, scaled by the published height.

Door mirrors stick out of the top, front and rear outlines; they are separate parts of a car, so the
width profiles drop any bump narrower than a mirror. The result is written to
library/references/<type>.json with an overlay picture (the outline drawn over the blueprint) in
library/references/overlays/, to check the cut by eye.

The car's coordinates follow car_sheet: metres, Z up from the ground, the front toward -Y, the
length centred on Y = 0.
"""

import json
import math
import sys

from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageFont, ImageOps

from .. import config
from .silhouette import _largest

REFS = config.ROOT / "library" / "references"
INK = 225          # a pixel darker than this (0-255 grey) is drawn line; the lightest blueprints draw in pale grey
PAD = 6            # white margin round each view, so the flood reaches round a car drawn up to the box edge
SAMPLES = 161      # points along each profile
MIRROR = {"top": 0.50, "front": 0.34, "rear": 0.34}  # bumps narrower than this (m) are door mirrors


def manifest():
    """The manifest: example car type to its blueprint, views and published dimensions."""
    data = json.loads((REFS / "manifest.json").read_text(encoding="utf-8"))
    return {k: v for k, v in data.items() if not k.startswith("_")}


def _odd(n):
    return n if n % 2 else n + 1


def view_mask(page, box, masks=(), close=3):
    """The car's filled silhouette in one view of a blueprint page: (mask, (x, y)), the mask mode "L"
    (255 car) cropped to the car and (x, y) its top-left corner on the page. close: how many pixels
    the lines are thickened by, to shut gaps in a faint or broken outline."""
    x0, y0, x1, y1 = box
    crop = page.crop((x0, y0, x1, y1)).convert("L")
    draw = ImageDraw.Draw(crop)
    for m in masks:  # a box [x0, y0, x1, y1], or a polygon of [x, y] points
        if m and isinstance(m[0], (list, tuple)):
            draw.polygon([(x - x0, y - y0) for x, y in m], fill=255)
        else:
            draw.rectangle([m[0] - x0, m[1] - y0, m[2] - x0, m[3] - y0], fill=255)
    ink = crop.point(lambda v: 255 if v < INK else 0).filter(ImageFilter.MaxFilter(_odd(close)))  # shut gaps
    padded = Image.new("L", (ink.width + 2 * PAD, ink.height + 2 * PAD), 0)
    padded.paste(ink, (PAD, PAD))
    ImageDraw.floodfill(padded, (0, 0), 128)  # the margin joins the whole border into one flood
    car = padded.point(lambda v: 0 if v == 128 else 255)
    k = _odd(max(5, round(min(car.size) * 0.02)))  # wider than any stray line, narrower than any car part
    car = car.filter(ImageFilter.MinFilter(k)).filter(ImageFilter.MaxFilter(k))
    car = _largest(car.convert("1")).convert("L")
    bb = car.getbbox()
    if not bb:
        raise ValueError(f"no car found in the box {box}")
    return car.crop(bb), (x0 - PAD + bb[0], y0 - PAD + bb[1])


def _columns(mask):
    """For each column of a mask, (first, last) row of car, or None."""
    w, h = mask.size
    px = mask.load()
    out = []
    for x in range(w):
        rows = [y for y in range(h) if px[x, y]]
        out.append((rows[0], rows[-1]) if rows else None)
    return out


def _rows(mask):
    """For each row of a mask, (first, last) column of car, or None."""
    return _columns(mask.transpose(Image.Transpose.TRANSPOSE))


def _open(values, window):
    """A 1-D opening: every bump narrower than `window` samples is cut down to its surroundings."""
    n, r = len(values), max(1, window // 2)
    low = [min(values[max(0, i - r):i + r + 1]) for i in range(n)]
    return [max(low[max(0, i - r):i + r + 1]) for i in range(n)]


def _resample(points, n=SAMPLES):
    """(t, v) points sorted by t, resampled to n evenly spaced t by linear interpolation."""
    t0, t1 = points[0][0], points[-1][0]
    out, j = [], 0
    for i in range(n):
        t = t0 + (t1 - t0) * i / (n - 1)
        while j < len(points) - 2 and points[j + 1][0] < t:
            j += 1
        (ta, va), (tb, vb) = points[j], points[j + 1]
        v = va if tb == ta else va + (vb - va) * (t - ta) / (tb - ta)
        out.append([round(t, 4), round(v, 4)])
    return out


def _fit_circle(points):
    """Least-squares circle (cx, cy, r) through (x, y) points (Kasa's method)."""
    n = len(points)
    mx = sum(p[0] for p in points) / n
    my = sum(p[1] for p in points) / n
    u = [(p[0] - mx, p[1] - my) for p in points]
    suu = sum(a * a for a, _ in u)
    svv = sum(b * b for _, b in u)
    suv = sum(a * b for a, b in u)
    suuu = sum(a ** 3 for a, _ in u)
    svvv = sum(b ** 3 for _, b in u)
    suvv = sum(a * b * b for a, b in u)
    svuu = sum(b * a * a for a, b in u)
    det = suu * svv - suv * suv
    if abs(det) < 1e-9:
        raise ValueError("the tyre outline is too flat to fit a circle")
    uc = (0.5 * (suuu + suvv) * svv - 0.5 * (svvv + svuu) * suv) / det
    vc = (0.5 * (svvv + svuu) * suu - 0.5 * (suuu + suvv) * suv) / det
    return mx + uc, my + vc, math.sqrt(uc * uc + vc * vc + (suu + svv) / n)


def side(mask, nose, published):
    """The side view measured: profiles, wheels and sizes in metres (see the module docstring)."""
    if nose == "right":
        mask = ImageOps.mirror(mask)
    cols = _columns(mask)
    w = len(cols)
    lowest = max(c[1] for c in cols if c)

    def runs_where(test, min_len):
        out, start = [], None
        for x, c in enumerate(cols + [None]):
            on = c is not None and test(c)
            if on and start is None:
                start = x
            elif not on and start is not None:
                if x - start >= min_len:
                    out.append((start, x - 1))
                start = None
        return sorted(sorted(out, key=lambda r: r[1] - r[0], reverse=True)[:2])

    # tyres drawn open leave the wheel arches as two notches in the underside, rising far above its
    # usual height (the median: sills, bumpers, a chassis hanging lower never do): the wheels are
    # centred on circles fitted to the arches, the ground a tyre radius below. Filled tyres instead
    # meet the ground in two runs of columns: the wheels are centred over them, a tyre radius up.
    rough_k = published["length"] / w  # metres per pixel, roughly
    rough_d = published["wheel_diameter"] / rough_k

    def wheelbase_like(xs):  # two wheels count only about a published wheelbase apart
        return len(xs) == 2 and abs((xs[1] - xs[0]) * rough_k / published["wheelbase"] - 1) < 0.15

    usual = sorted(c[1] for c in cols if c)[len([c for c in cols if c]) // 2]
    arches = runs_where(lambda c: c[1] < usual - 0.2 * rough_d, max(3, round(w * 0.05)))
    centres = []
    for a, b in arches:
        cx, cy, _ = _fit_circle([(x, cols[x][1]) for x in range(a, b + 1)])
        centres.append((cx, cy))
    found = "arches"
    if not wheelbase_like([cx for cx, _ in centres]):
        found = "tyres"
        tol = max(2, round(mask.height * 0.012))
        contact = runs_where(lambda c: c[1] >= lowest - tol, max(3, round(w * 0.03)))
        centres = [((a + b) / 2, None) for a, b in contact]
        if not wheelbase_like([cx for cx, _ in centres]):
            raise ValueError("found neither two wheel arches nor two tyres on the ground a wheelbase apart")
    k = published["wheelbase"] / (centres[1][0] - centres[0][0])  # metres per pixel
    r_px = published["wheel_diameter"] / 2 / k
    if found == "tyres":
        ground = lowest
    else:
        ground = round(sum(cy for _, cy in centres) / 2 + r_px)
    wheels = [(cx, ground - r_px, r_px) for cx, _ in centres]
    x_mid = (w - 1) / 2

    def y_of(x):
        return (x - x_mid) * k

    def z_of(row):
        return (ground - row) * k

    top = [(y_of(x), z_of(c[0])) for x, c in enumerate(cols) if c]
    bottom = [(y_of(x), z_of(c[1])) for x, c in enumerate(cols) if c]
    # the sill: the body's underside between the wheels, away from the tyres
    (fx, _, fr), (rx, _, rr) = wheels
    between = [z_of(cols[x][1]) for x in range(round(fx + 1.3 * fr), round(rx - 1.3 * rr)) if cols[x]]
    between.sort()
    return {
        "length": round(w * k, 3),
        "height": round((ground - min(c[0] for c in cols if c)) * k, 3),
        "wheel_radius": round(published["wheel_diameter"] / 2, 3),
        "wheelbase": round(published["wheelbase"], 3),
        "front_overhang": round((fx + 0.5) * k, 3),
        "rear_overhang": round((w - 0.5 - rx) * k, 3),
        "wheels": [{"y": round(y_of(cx), 3), "z": round(z_of(cy), 3), "radius": round(r * k, 3)}
                   for cx, cy, r in wheels],
        "sill_z": round(between[len(between) // 2], 3) if between else None,
        "wheels_from": found,
        "top": _resample(top),
        "bottom": _resample(bottom),
        "_pixels": {"k": k, "wheels": wheels, "mirrored": nose == "right", "ground": ground, "x_mid": x_mid},
    }


def top_view(mask, nose, published):
    """The top view measured: the half-width along the car. Along the car it is scaled by the published
    length, across by the published width (a view drawn too wide or too narrow still gives the plan
    shape); `width` is what the view itself measures at the length's scale, as a check."""
    if nose == "right":
        mask = ImageOps.mirror(mask)
    cols = _columns(mask)
    w = len(cols)
    k = published["length"] / w
    centre = sorted((c[0] + c[1]) / 2 for c in cols if c)[len([c for c in cols if c]) // 2]
    half = [max(centre - c[0], c[1] - centre) if c else 0.0 for c in cols]  # the wider side, against a tilt
    half = _open(half, round(MIRROR["top"] / k))
    kw = published["width"] / (2 * max(half))
    x_mid = (w - 1) / 2
    prof = [((x - x_mid) * k, hw * kw) for x, hw in enumerate(half)]
    return {"width": round(2 * max(half) * k, 3), "half_width": _resample(prof),
            "_pixels": {"k": k, "kw": kw, "centre": centre, "mirrored": nose == "right", "x_mid": x_mid}}


def end_view(mask, name, published):
    """A front or rear view measured: the half-width up the car, scaled up the car by the published
    height and across by the published width; `width` is what the view itself measures at the
    height's scale, as a check."""
    rows = _rows(mask)
    h = len(rows)
    k = published["height"] / h
    centre = sorted((r[0] + r[1]) / 2 for r in rows if r)[len([r for r in rows if r]) // 2]
    half = [max(centre - r[0], r[1] - centre) if r else 0.0 for r in rows]
    half = _open(half, round(MIRROR[name] / k))
    kw = published["width"] / (2 * max(half))
    prof = [((h - 1 - y) * k, hw * kw) for y, hw in enumerate(half)]
    prof.reverse()  # from the ground up
    return {"width": round(2 * max(half) * k, 3), "half_width": _resample(prof),
            "_pixels": {"k": k, "kw": kw, "centre": centre, "rows": h}}


def extract(name, entry=None, overlay=True):
    """Cut and measure every view of one example car's blueprint; writes <name>.json (and an overlay
    picture) to library/references and returns the record."""
    entry = entry or manifest()[name]
    pub = entry["published"]
    with Image.open(REFS / entry["file"]) as im:
        page = im.convert("RGB")
    record = {"car": entry["car"], "source": entry["page"], "license": "CC BY 4.0", "published": pub, "views": {}}
    cuts = {}
    for view, spec in entry["views"].items():
        mask, at = view_mask(page, spec["box"], spec.get("masks", ()), spec.get("close", 3))
        cuts[view] = (mask, at)
        if view == "side":
            record["views"]["side"] = side(mask, spec.get("nose", "left"), pub)
        elif view == "top":
            record["views"]["top"] = top_view(mask, spec.get("nose", "left"), pub)
        else:
            record["views"][view] = end_view(mask, view, pub)
    record["checks"] = checks(record)
    if overlay:
        overlay_picture(page, cuts, record, REFS / "overlays" / f"{name}.png")
    clean = json.loads(json.dumps(record))
    for v in clean["views"].values():
        v.pop("_pixels", None)
    (REFS / f"{name}.json").write_text(json.dumps(clean, indent=1), encoding="utf-8")
    return record


def reference_silhouette(name, view, res=0.01):
    """A reference view's filled outline at `res` metres per pixel, in the same metres the body built from
    the reference (bpy_lib.shells.car_body) has: (image mode "L", (u_min, v_max)), column c at
    u = u_min + c * res and row r at v = v_max - r * res. u, v are (y, z) for the side view, (y, x) for
    the top view and (x, z) for the front and rear views. The body is built to the side view's length
    and height, so the top view is stretched to that length and the end views to that height."""
    entry = manifest()[name]
    pub = entry["published"]
    with Image.open(REFS / entry["file"]) as im:
        page = im.convert("RGB")

    def cut(v):
        spec = entry["views"][v]
        return view_mask(page, spec["box"], spec.get("masks", ()), spec.get("close", 3))[0], spec

    smask, sspec = cut("side")
    s = side(smask, sspec.get("nose", "left"), pub)
    if view == "side":
        px = s["_pixels"]
        mask = ImageOps.mirror(smask) if px["mirrored"] else smask
        ku = kv = px["k"]
        origin = (-px["x_mid"] * ku, px["ground"] * kv)
        size = (max(1, round(mask.width * ku / res)), max(1, round(mask.height * kv / res)))
        return mask.resize(size, Image.BILINEAR).point(lambda v: 255 if v >= 128 else 0), origin
    # the top and end views: the cut drawing, inside the outline of their measured half-widths (so door
    # mirrors are dropped while the gap under the car between the tyres stays), as the body is built
    if view == "top":
        mask, spec = cut("top")
        meas = top_view(mask, spec.get("nose", "left"), pub)
        px = meas["_pixels"]
        if px["mirrored"]:
            mask = ImageOps.mirror(mask)
        k = s["length"] / pub["length"]
        ku, kv = px["k"] * k, px["kw"]
        raw_origin = (-px["x_mid"] * ku, px["centre"] * kv)
        outline = [(y * k, w) for y, w in meas["half_width"]] + [(y * k, -w) for y, w in reversed(meas["half_width"])]
    else:
        mask, _ = cut(view)
        meas = end_view(mask, view, pub)
        px = meas["_pixels"]
        k = s["height"] / pub["height"]
        ku, kv = px["kw"], px["k"] * k
        raw_origin = (-px["centre"] * ku, (px["rows"] - 1) * kv)
        outline = [(w, z * k) for z, w in meas["half_width"]] + [(-w, z * k) for z, w in reversed(meas["half_width"])]
    us, vs = [u for u, _ in outline], [v for _, v in outline]
    origin = (min(us) - 2 * res, max(vs) + 2 * res)
    size = (int((max(us) - min(us)) / res) + 5, int((max(vs) - min(vs)) / res) + 5)
    img = Image.new("L", size, 0)
    ImageDraw.Draw(img).polygon([((u - origin[0]) / res, (origin[1] - v) / res) for u, v in outline], fill=255)
    raw = mask.resize((max(1, round(mask.width * ku / res)), max(1, round(mask.height * kv / res))), Image.BILINEAR)
    raw = raw.point(lambda v: 255 if v >= 128 else 0)
    placed = Image.new("L", size, 0)
    placed.paste(raw, (round((raw_origin[0] - origin[0]) / res), round((origin[1] - raw_origin[1]) / res)))
    return ImageChops.multiply(img, placed), origin


def _view_frame(name, view, page=None):
    """How one view of a reference maps to the body's metres: (to_metres, (mask, (ax, ay)), entry), where
    to_metres(X, Y) takes a page pixel to (u, v) as reference_silhouette has them: (y, z) for the side
    view, (y, x) for the top, (x, z) for the front and rear (world X, as bpy_lib.shell's cuts take it:
    on the right of a front view, on the left of a rear one)."""
    entry = manifest()[name]
    pub = entry["published"]
    if page is None:
        with Image.open(REFS / entry["file"]) as im:
            page = im.convert("RGB")

    def cut(v):
        spec = entry["views"][v]
        return view_mask(page, spec["box"], spec.get("masks", ()), spec.get("close", 3)), spec

    (smask, sat), sspec = cut("side")
    s = side(smask, sspec.get("nose", "left"), pub)
    if view == "side":
        mask, (ax, ay), px = smask, sat, s["_pixels"]

        def to_metres(X, Y):
            c = X - ax
            if px["mirrored"]:
                c = mask.width - 1 - c
            return (c - px["x_mid"]) * px["k"], (px["ground"] - (Y - ay)) * px["k"]
    elif view == "top":
        (mask, (ax, ay)), spec = cut("top")
        px = top_view(mask, spec.get("nose", "left"), pub)["_pixels"]
        k = px["k"] * s["length"] / pub["length"]

        def to_metres(X, Y):
            c = X - ax
            if px["mirrored"]:
                c = mask.width - 1 - c
            return (c - px["x_mid"]) * k, (px["centre"] - (Y - ay)) * px["kw"]
    else:
        (mask, (ax, ay)), spec = cut(view)
        px = end_view(mask, view, pub)["_pixels"]
        k = px["k"] * s["height"] / pub["height"]
        sign = 1 if view == "front" else -1  # seen from in front +X is on the right; from behind, on the left

        def to_metres(X, Y):
            return sign * ((X - ax) - px["centre"]) * px["kw"], (px["rows"] - 1 - (Y - ay)) * k
    return to_metres, (mask, (ax, ay)), entry


def regions(name, view, min_area=0.0004, picture=True):
    """The closed shapes drawn inside the car in one view of its blueprint (windows, door panels, handles,
    lamps, grille, intakes, the plate...): a list of {id, area (m²), centre (u, v), box (u0, v0, u1,
    v1), outline [(u, v)...]} in the body's metres (see _view_frame), largest first. A shape is the white
    space a run of drawn lines closes off inside the car's outline. With picture=True a numbered picture
    of them goes to library/references/regions/<name>_<view>.png, to name them by eye in the manifest."""
    entry = manifest()[name]
    spec = entry["views"][view]
    with Image.open(REFS / entry["file"]) as im:
        page = im.convert("RGB")
    to_metres, (mask, (ax, ay)), _ = _view_frame(name, view, page)
    x0, y0, x1, y1 = spec["box"]
    crop = page.crop((x0, y0, x1, y1)).convert("L")
    draw = ImageDraw.Draw(crop)
    for m in spec.get("masks", ()):
        if m and isinstance(m[0], (list, tuple)):
            draw.polygon([(x - x0, y - y0) for x, y in m], fill=255)
        else:
            draw.rectangle([m[0] - x0, m[1] - y0, m[2] - x0, m[3] - y0], fill=255)
    ink = crop.point(lambda v: 255 if v < INK else 0).filter(ImageFilter.MaxFilter(_odd(spec.get("close", 3))))
    inside = Image.new("L", crop.size, 0)
    inside.paste(mask.filter(ImageFilter.MinFilter(5)), (ax - x0, ay - y0))  # a little inside the outline
    free = ImageChops.multiply(ImageChops.invert(ink), inside)
    w, h = free.size
    data = bytearray(1 if v else 0 for v in free.getdata())
    car_area = sum(1 for v in mask.getdata() if v)
    k_area = None
    out = []
    for start in range(w * h):
        if data[start] != 1:
            continue
        data[start] = 2
        stack, pix = [start], []
        while stack:
            i = stack.pop()
            pix.append(i)
            x, y = i % w, i // w
            for j, ok in ((i - 1, x > 0), (i + 1, x < w - 1), (i - w, y > 0), (i + w, y < h - 1)):
                if ok and data[j] == 1:
                    data[j] = 2
                    stack.append(j)
        if len(pix) > 0.5 * car_area:
            continue
        rows = {}
        for i in pix:
            rows.setdefault(i // w, []).append(i % w)
        if k_area is None:  # square metres per page pixel, from the view's scale
            (ua, va), (ub, vb) = to_metres(x0, y0), to_metres(x0 + 1, y0 + 1)
            k_area = abs((ub - ua) * (vb - va))
        if len(pix) * k_area < min_area:
            continue
        ys = sorted(rows)
        step = max(1, len(ys) // 40)
        left = [(min(rows[y]), y) for y in ys[::step]] + [(min(rows[ys[-1]]), ys[-1])]
        right = [(max(rows[y]) + 1, y) for y in ys[::step]] + [(max(rows[ys[-1]]) + 1, ys[-1])]
        poly = [to_metres(x0 + x, y0 + y) for x, y in left] + [to_metres(x0 + x, y0 + y) for x, y in reversed(right)]
        us, vs = [u for u, _ in poly], [v for _, v in poly]
        cx = sum(i % w for i in pix) / len(pix)
        cy = sum(i // w for i in pix) / len(pix)
        out.append({"area": round(len(pix) * k_area, 4), "centre": [round(c, 3) for c in to_metres(x0 + cx, y0 + cy)],
                    "box": [round(min(us), 3), round(min(vs), 3), round(max(us), 3), round(max(vs), 3)],
                    "outline": [[round(u, 4), round(v, 4)] for u, v in poly], "_px": (cx, cy, pix)})
    out.sort(key=lambda r: -r["area"])
    for n, r in enumerate(out, 1):
        r["id"] = n
    # areas drawn dark (a black grille opening, black glass, or a grille drawn as dense hatching): where
    # more than 45% of the page is ink, averaged over a few pixels (a single outline line stays well under
    # that); numbered after the white shapes, so their numbers stay put
    r_blur = max(3, round(min(w, h) * 0.012))
    raw_ink = crop.point(lambda v: 255 if v < INK else 0)
    solid = raw_ink.filter(ImageFilter.BoxBlur(r_blur)).point(lambda v: 255 if v > 115 else 0)
    solid = ImageChops.multiply(solid, inside)
    sdata = bytearray(1 if v else 0 for v in solid.getdata())
    dark = []
    for start in range(w * h):
        if sdata[start] != 1:
            continue
        sdata[start] = 2
        stack, pix = [start], []
        while stack:
            i = stack.pop()
            pix.append(i)
            x, y = i % w, i // w
            for j, ok in ((i - 1, x > 0), (i + 1, x < w - 1), (i - w, y > 0), (i + w, y < h - 1)):
                if ok and sdata[j] == 1:
                    sdata[j] = 2
                    stack.append(j)
        if k_area is None:
            (ua, va), (ub, vb) = to_metres(x0, y0), to_metres(x0 + 1, y0 + 1)
            k_area = abs((ub - ua) * (vb - va))
        if len(pix) * k_area < max(min_area, 0.008) or len(pix) > 0.5 * car_area:  # not the crossings of lines
            continue
        rows = {}
        for i in pix:
            rows.setdefault(i // w, []).append(i % w)
        if len(pix) < 0.7 * sum(max(c) - min(c) + 1 for c in rows.values()):
            continue  # a ring of thick outline, not a filled area
        ys = sorted(rows)
        step = max(1, len(ys) // 40)
        left = [(min(rows[y]), y) for y in ys[::step]] + [(min(rows[ys[-1]]), ys[-1])]
        right = [(max(rows[y]) + 1, y) for y in ys[::step]] + [(max(rows[ys[-1]]) + 1, ys[-1])]
        poly = [to_metres(x0 + x, y0 + y) for x, y in left] + [to_metres(x0 + x, y0 + y) for x, y in reversed(right)]
        us, vs = [u for u, _ in poly], [v for _, v in poly]
        cx = sum(i % w for i in pix) / len(pix)
        cy = sum(i // w for i in pix) / len(pix)
        dark.append({"area": round(len(pix) * k_area, 4), "dark": True,
                     "centre": [round(c, 3) for c in to_metres(x0 + cx, y0 + cy)],
                     "box": [round(min(us), 3), round(min(vs), 3), round(max(us), 3), round(max(vs), 3)],
                     "outline": [[round(u, 4), round(v, 4)] for u, v in poly], "_px": (cx, cy, pix)})
    dark.sort(key=lambda r: -r["area"])
    for n, r in enumerate(dark, len(out) + 1):
        r["id"] = n
    out += dark
    if picture:
        pic = crop.convert("RGB")
        tint = Image.new("RGB", pic.size, (0, 0, 0))
        hues = [(230, 60, 60), (60, 160, 60), (60, 90, 230), (220, 150, 30), (160, 60, 200), (30, 170, 180)]
        tpx = tint.load()
        for r in out:
            col = hues[r["id"] % len(hues)]
            for i in r["_px"][2]:
                tpx[i % w, i // w] = col
        pic = Image.blend(pic, tint, 0.35)
        d = ImageDraw.Draw(pic)
        try:
            font = ImageFont.truetype("arialbd.ttf", max(12, round(max(w, h) / 60)))
        except OSError:
            font = ImageFont.load_default()
        for r in out:
            cx, cy, _ = r["_px"]
            d.text((cx, cy), str(r["id"]), fill=(0, 0, 0), font=font, anchor="mm", stroke_width=2,
                   stroke_fill=(255, 255, 255))
        path = REFS / "regions" / f"{name}_{view}.png"
        path.parent.mkdir(parents=True, exist_ok=True)
        pic.save(path)
    for r in out:
        del r["_px"]
    return out


def _hull(points):
    """The convex hull of (u, v) points, counter-clockwise (Andrew's monotone chain)."""
    pts = sorted(set((round(u, 4), round(v, 4)) for u, v in points))
    if len(pts) < 3:
        return [list(p) for p in pts]

    def cross(o, a, b):
        return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])

    lower, upper = [], []
    for p in pts:
        while len(lower) >= 2 and cross(lower[-2], lower[-1], p) <= 0:
            lower.pop()
        lower.append(p)
    for p in reversed(pts):
        while len(upper) >= 2 and cross(upper[-2], upper[-1], p) <= 0:
            upper.pop()
        upper.append(p)
    return [list(p) for p in lower[:-1] + upper[:-1]]


def smooth_outline(outline, spacing=0.005, window=0.02, corner_deg=60.0, tolerance=0.0005):
    """A closed outline traced from a blueprint's pixels, with its stair-steps smoothed out: resampled
    to points `spacing` apart, then each point pulled toward its neighbours' mean over about `window`
    (less on a small shape), except at the outline's real corners, where it turns by more than
    `corner_deg` measured over `window` either side: those stay sharp. Then thinned to as few points as
    keep it within `tolerance`. In the outline's units (metres)."""
    pts = [tuple(p) for p in outline]
    if len(pts) > 1 and pts[0] == pts[-1]:
        pts.pop()
    if len(pts) < 3:
        return [list(p) for p in pts]
    loop = pts + [pts[0]]
    lengths = [math.dist(a, b) for a, b in zip(loop, loop[1:])]
    perimeter = sum(lengths)
    if perimeter <= 0:
        return [list(p) for p in pts]
    n = max(12, round(perimeter / spacing))
    step = perimeter / n
    out, seg, along = [], 0, 0.0  # along: the distance from the start of segment `seg`
    for i in range(n):
        target = i * step
        while seg < len(lengths) - 1 and along + lengths[seg] < target:
            along += lengths[seg]
            seg += 1
        t = 0.0 if lengths[seg] == 0 else min(1.0, (target - along) / lengths[seg])
        a, b = loop[seg], loop[seg + 1]
        out.append((a[0] + t * (b[0] - a[0]), a[1] + t * (b[1] - a[1])))
    window = min(window, perimeter / 12)
    k = max(1, round(window / step))

    def turn(i):
        p, a, b = out[i], out[(i - k) % n], out[(i + k) % n]
        d1 = math.atan2(p[1] - a[1], p[0] - a[0])
        d2 = math.atan2(b[1] - p[1], b[0] - p[0])
        return abs((d2 - d1 + math.pi) % (2 * math.pi) - math.pi)

    turns = [turn(i) for i in range(n)]
    limit = math.radians(corner_deg)
    corners = set()
    for i in range(n):  # a corner: the sharpest point of a run of points that turn past the limit
        if turns[i] > limit and turns[i] >= turns[(i - 1) % n] and turns[i] > turns[(i + 1) % n]:
            corners.add(i)
    passes = max(1, k * k // 2)  # pulling toward the neighbours' mean: spread about half `window` each way
    for _ in range(passes):
        out = [out[i] if i in corners else
               (0.25 * out[i - 1][0] + 0.5 * out[i][0] + 0.25 * out[(i + 1) % n][0],
                0.25 * out[i - 1][1] + 0.5 * out[i][1] + 0.25 * out[(i + 1) % n][1]) for i in range(n)]
    # then as few points as keep that smooth shape (within `tolerance`): a cut along a detail's outline
    # fails on hundreds of points millimetres apart
    far = max(range(n), key=lambda i: math.dist(out[0], out[i]))
    a = _simplify(out[:far + 1], tolerance)
    b = _simplify(out[far:] + [out[0]], tolerance)
    return [[round(u, 4), round(v, 4)] for u, v in a[:-1] + b[:-1]]


def _simplify(path, tolerance):
    """An open path with the points dropped that lie within `tolerance` of the straight line between
    the points kept either side of them (Douglas-Peucker); the ends stay."""
    if len(path) < 3:
        return list(path)
    a, b = path[0], path[-1]
    length = math.dist(a, b) or 1e-12
    worst, at = 0.0, 0
    for i in range(1, len(path) - 1):
        p = path[i]
        d = abs((b[0] - a[0]) * (a[1] - p[1]) - (a[0] - p[0]) * (b[1] - a[1])) / length
        if d > worst:
            worst, at = d, i
    if worst <= tolerance:
        return [a, b]
    return _simplify(path[:at + 1], tolerance)[:-1] + _simplify(path[at:], tolerance)


def union_outline(outlines, grow=0.0, res=0.002):
    """One closed outline round several (u, v) outlines grown by `grow` (metres) each way, so shapes up
    to twice that apart merge into one: drawn at `res` metres per pixel, then traced row by row from its
    left and right edges (a shape each row of which is one run, as a car's whole window opening is)."""
    us = [u for o in outlines for u, _ in o]
    vs = [v for o in outlines for _, v in o]
    pad = grow + 4 * res
    u0, v1 = min(us) - pad, max(vs) + pad
    w = int((max(us) + pad - u0) / res) + 1
    h = int((v1 - (min(vs) - pad)) / res) + 1
    img = Image.new("L", (w, h), 0)
    draw = ImageDraw.Draw(img)
    r = round(grow / res)

    def px(p):
        return ((p[0] - u0) / res, (v1 - p[1]) / res)

    for o in outlines:
        pts = [px(p) for p in o]
        draw.polygon(pts, fill=255)
        if r > 0:  # grown: the outline drawn as a line 2r wide, with round ends and joins
            draw.line(pts + [pts[0]], fill=255, width=2 * r + 1, joint="curve")
            for x, y in pts:
                draw.ellipse([x - r, y - r, x + r, y + r], fill=255)
    data = img.load()
    rows = []
    for y in range(h):
        xs = [x for x in range(w) if data[x, y]]
        if xs:
            rows.append((y, xs[0], xs[-1] + 1))
    left = [(u0 + x0 * res, v1 - y * res) for y, x0, _ in rows]
    right = [(u0 + x1 * res, v1 - y * res) for y, _, x1 in rows]
    return [[round(u, 4), round(v, 4)] for u, v in left + list(reversed(right))]


def write_details(name):
    """The named details of an example car (the manifest's `details`: per view, which numbered shapes of
    regions() are which) with their outlines in the body's metres, written to
    library/references/<name>_details.json for the Blender build: {view: {detail: [outline, ...]}}.
    A detail is a list of numbered shapes, of {"union": [n, ...], "grow": metres} (those shapes merged
    into one outline, grown that much), of {"poly": [[X, Y], ...]} outlines traced by hand in the
    blueprint's pixels, or of {"hull": [u0, v0, u1, v1]} boxes (the outline round every shape inside).
    A number that names no shape is an error, so a stale pick cannot pass unnoticed."""
    entry = manifest()[name]
    out = {}
    for view, picks in (entry.get("details") or {}).items():
        shapes = regions(name, view)
        found = {r["id"]: r for r in shapes}
        everything = None
        out[view] = {}
        to_metres = _view_frame(name, view)[0]
        for detail, items in picks.items():
            outlines = []
            for item in (items if isinstance(items, list) else [items]):
                if isinstance(item, int):  # a numbered shape of regions()
                    if item not in found:
                        raise ValueError(f"{name} {view} view: {detail} names shape {item}, which regions() did not find")
                    outlines.append(smooth_outline(found[item]["outline"]))
                elif "union" in item:  # numbered shapes merged into one, grown by "grow" metres
                    missing = [i for i in item["union"] if i not in found]
                    if missing:
                        raise ValueError(f"{name} {view} view: {detail} names shapes {missing}, which regions() did not find")
                    outlines.append(smooth_outline(union_outline([found[i]["outline"] for i in item["union"]],
                                                                 item.get("grow", 0.0))))
                elif "poly" in item:  # traced by hand in the blueprint's own pixels, where a drawing does not
                    # close a detail into a shape (a grille drawn as fine hatching)
                    outlines.append([[round(c, 4) for c in to_metres(x, y)] for x, y in item["poly"]])
                else:  # {"hull": [u0, v0, u1, v1]}: one outline round every shape in the box, tiny ones too
                    if everything is None:
                        everything = regions(name, view, min_area=0.0, picture=False)
                    u0, v0, u1, v1 = item["hull"]
                    pts = [p for r in everything if u0 <= r["centre"][0] <= u1 and v0 <= r["centre"][1] <= v1
                           for p in r["outline"]]
                    if len(pts) < 3:
                        raise ValueError(f"{name} {view} view: no shapes inside the box given for {detail}")
                    outlines.append(_hull(pts))
            out[view][detail] = outlines
    path = REFS / f"{name}_details.json"
    path.write_text(json.dumps(out), encoding="utf-8")
    return out


def checks(record):
    """How far each measured size is from the published one, as a fraction (0.02 = 2%)."""
    pub, views = record["published"], record["views"]
    out = {}
    if "side" in views:
        out["side_length"] = round(views["side"]["length"] / pub["length"] - 1, 3)
        out["side_height"] = round(views["side"]["height"] / pub["height"] - 1, 3)
    for v in ("top", "front", "rear"):
        if v in views:
            out[f"{v}_width"] = round(views[v]["width"] / pub["width"] - 1, 3)
    return out


def overlay_picture(page, cuts, record, path):
    """The blueprint with each cut outline in red, the fitted wheels in blue and the checks written on."""
    pic = page.copy()
    draw = ImageDraw.Draw(pic)
    lw = max(2, round(max(pic.size) / 700))
    for view, (mask, (ax, ay)) in cuts.items():
        edge = mask.filter(ImageFilter.FIND_EDGES).point(lambda v: 255 if v else 0)
        red = Image.new("RGB", edge.size, (230, 20, 20))
        pic.paste(red, (ax, ay), edge.filter(ImageFilter.MaxFilter(_odd(lw))))
        if view == "side":
            px = record["views"]["side"]["_pixels"]
            for cx, cy, r in px["wheels"]:
                if px["mirrored"]:
                    cx = mask.width - 1 - cx
                draw.ellipse([ax + cx - r, ay + cy - r, ax + cx + r, ay + cy + r], outline=(20, 60, 230), width=lw)
    try:
        font = ImageFont.truetype("arial.ttf", max(14, round(max(pic.size) / 55)))
    except OSError:
        font = ImageFont.load_default()
    lines = [record["car"]] + [f"{k}: {v:+.1%}" for k, v in record["checks"].items()]
    draw.multiline_text((8, 8), "\n".join(lines), fill=(0, 140, 0), font=font)
    scale = min(1.0, 1800 / max(pic.size))
    if scale < 1:
        pic = pic.resize((round(pic.width * scale), round(pic.height * scale)), Image.LANCZOS)
    path.parent.mkdir(parents=True, exist_ok=True)
    pic.save(path)


def main(names):
    for name in names or list(manifest()):
        r = extract(name)
        s = r["views"].get("side", {})
        print(f"{name:13} length {s.get('length')} height {s.get('height')} "
              f"overhangs {s.get('front_overhang')}/{s.get('rear_overhang')} "
              f"wheels r {[w['radius'] for w in s.get('wheels', [])]} sill {s.get('sill_z')} checks {r['checks']}")


if __name__ == "__main__":
    main(sys.argv[1:])
