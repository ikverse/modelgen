"""Fits a car sheet's side profile to a side-view reference picture. Plain Python and Pillow, no Blender.

The car's outline is cut out of the reference by its background: the colour along the picture's
edges, flooded inward, so dark windows inside the car stay car. The sheet's side outline is drawn
straight from car_sheet's maths (the top line, the body bottom and the wheels), with the sheet's
length matched to the reference's length. A pattern search then moves the side-profile numbers until
the two outlines overlap best (intersection over union). It suits clean backgrounds: a studio photo,
a render, a blueprint. On a busy street photo the cut-out is poor and the result says so.
"""

import math
import tempfile
import uuid
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageFilter

from ..bpy_lib.shells import car_sheet

WORK = 360            # the reference is scaled so its longer side is this many pixels
THRESHOLD = 38        # a pixel this far (0-255, largest channel) from the background colour is car
# The side-profile numbers the fit moves, and the first step for each (metres)
FIT_KEYS = {"height": 0.04, "wheelbase": 0.08, "front_overhang": 0.05, "wheel_diameter": 0.03, "ride_height": 0.02,
            "nose_z": 0.04, "cowl_z": 0.04, "windshield_base": 0.08, "roof_front": 0.08, "roof_rear": 0.08,
            "deck_front": 0.08, "deck_z": 0.04, "tail_z": 0.04}
MIN_STEP = 0.004


def _background(img):
    """The median colour of the picture's border, and how much of the border is close to it (0 to 1)."""
    w, h = img.size
    px = img.load()
    border = [px[x, 0] for x in range(w)] + [px[x, h - 1] for x in range(w)]
    border += [px[0, y] for y in range(h)] + [px[w - 1, y] for y in range(h)]
    bg = tuple(sorted(c[i] for c in border)[len(border) // 2] for i in range(3))
    close = sum(1 for c in border if max(abs(c[i] - bg[i]) for i in range(3)) <= THRESHOLD)
    return bg, close / len(border)


def _largest(mask):
    """The largest connected white region of a mode-"1" picture, as a new mode-"1" picture."""
    w, h = mask.size
    px = mask.load()
    seen = bytearray(w * h)
    best = []
    for start in range(w * h):
        if seen[start] or not px[start % w, start // w]:
            continue
        seen[start] = 1
        region, stack = [], [start]
        while stack:
            i = stack.pop()
            region.append(i)
            x, y = i % w, i // w
            for j, ok in ((i - 1, x > 0), (i + 1, x < w - 1), (i - w, y > 0), (i + w, y < h - 1)):
                if ok and not seen[j] and px[j % w, j // w]:
                    seen[j] = 1
                    stack.append(j)
        if len(region) > len(best):
            best = region
    out = Image.new("1", (w, h), 0)
    opx = out.load()
    for i in best:
        opx[i % w, i // w] = 255
    return out


def reference_mask(path):
    """The car cut out of a side-view picture: (mask, info). The mask is mode "1", cropped to the car;
    info has the background colour, how even the background is, and warnings."""
    with Image.open(path) as im:
        img = im.convert("RGB")
    k = WORK / max(img.size)
    img = img.resize((max(1, round(img.width * k)), max(1, round(img.height * k))), Image.LANCZOS)
    img = img.filter(ImageFilter.MedianFilter(3))
    bg, even = _background(img)
    diff = ImageChops.difference(img, Image.new("RGB", img.size, bg))
    r, g, b = diff.split()
    far = ImageChops.lighter(ImageChops.lighter(r, g), b).point(lambda v: 255 if v > THRESHOLD else 0)
    # flood the background in from every edge pixel; whatever it cannot reach is the car (windows too)
    fill = far.copy()
    w, h = fill.size
    for x, y in [(x, 0) for x in range(w)] + [(x, h - 1) for x in range(w)] + \
                [(0, y) for y in range(h)] + [(w - 1, y) for y in range(h)]:
        if fill.getpixel((x, y)) == 0:
            ImageDraw.floodfill(fill, (x, y), 128)
    car = _largest(fill.point(lambda v: 0 if v == 128 else 255).convert("1"))
    box = car.getbbox()
    warnings = []
    if even < 0.6:
        warnings.append(f"the background is not one even colour (only {even:.0%} of the picture's edge matches it)")
    if not box:
        return None, {"background": bg, "even": round(even, 2), "warnings": warnings + ["no car found"]}
    area = _count(car) / (w * h)
    if area < 0.05:
        warnings.append("the car found is very small in the picture")
    if area > 0.9:
        warnings.append("the cut-out covers almost the whole picture: the background was not separated")
    crop = car.crop(box)
    aspect = crop.width / max(1, crop.height)
    if not 1.8 <= aspect <= 5.0:
        warnings.append(f"the cut-out is {aspect:.1f} times as long as it is tall, unlike a car's side view")
    return crop, {"background": bg, "even": round(even, 2), "aspect": round(aspect, 2), "warnings": warnings}


def outline(s):
    """The sheet's side silhouette as polygons in metres (y along the car, z up): the body from its
    bottom to its top line, and the two wheels on the near side."""
    n = 80
    ys = [s["y_front"] + (s["y_rear"] - s["y_front"]) * i / n for i in range(n + 1)]
    top = [(y, max(car_sheet.z_top(s, y), car_sheet.edge(s, y)[1])) for y in ys]
    body = [(s["y_front"], s["ride_height"])] + top + [(s["y_rear"], s["ride_height"])]
    r = s["wheel_radius"]
    wheels = [[(axle + r * math.cos(a / 24 * 2 * math.pi), r + r * math.sin(a / 24 * 2 * math.pi))
               for a in range(24)] for axle in (s["axle_front"], s["axle_rear"])]
    return [body] + wheels


def _draw(s, size, ppm):
    """The sheet's silhouette on a canvas of `size`, the nose at x = 0 and the floor at the bottom."""
    w, h = size
    img = Image.new("1", size, 0)
    draw = ImageDraw.Draw(img)
    for poly in outline(s):
        draw.polygon([((y - s["y_front"]) * ppm, h - 1 - z * ppm) for y, z in poly], fill=255)
    return img


def _count(img):
    """How many pixels of a mode-"1" picture are white."""
    return img.width * img.height - img.histogram()[0]


def _iou(a, b):
    both = _count(ImageChops.logical_and(a, b))
    either = _count(ImageChops.logical_or(a, b))
    return both / either if either else 0.0


def _canvas(ref, s):
    """The reference placed on a canvas in the sheet's metres: its length is the sheet's length."""
    ppm = ref.width / s["length"]
    h = max(ref.height, round(s["height"] * ppm * 1.6)) + 4
    canvas = Image.new("1", (ref.width, h), 0)
    canvas.paste(ref, (0, h - 1 - ref.height))
    return canvas, ppm


def _score(sheet, canvas, ppm):
    if car_sheet.check(sheet):
        return -1.0
    return _iou(canvas, _draw(car_sheet.resolve(sheet), canvas.size, ppm))


def fit(path, sheet, keys=None):
    """Fit the sheet's side profile to the side-view picture at `path`. Returns a dict: `sheet` (the
    sheet with the fitted numbers), `changed` ({key: [before, after]}), `overlap_before` and
    `overlap_after` (0 to 1), `flipped` (the picture's nose pointed right), `confident` and `warnings`."""
    ref, info = reference_mask(path)
    if ref is None:
        return {"ok": False, "confident": False, "warnings": info["warnings"]}
    s = car_sheet.resolve(sheet)
    keys = {k: FIT_KEYS[k] for k in (keys or FIT_KEYS)}
    best, best_score, flipped = None, -1.0, False
    for flip in (False, True):  # the studio's side view has the nose to the left
        canvas, ppm = _canvas(ref.transpose(Image.FLIP_LEFT_RIGHT) if flip else ref, s)
        score = _score(sheet, canvas, ppm)
        if score > best_score:
            best, best_score, flipped = (canvas, ppm), score, flip
    canvas, ppm = best
    start = best_score
    current = {**sheet, **{k: s[k] for k in keys}}
    steps = dict(keys)
    while max(steps.values()) >= MIN_STEP:
        improved = False
        for k in keys:
            if steps[k] < MIN_STEP:
                continue
            for sign in (1, -1):
                trial = {**current, k: round(current[k] + sign * steps[k], 4)}
                score = _score(trial, canvas, ppm)
                if score > best_score + 1e-4:
                    current, best_score, improved = trial, score, True
                    break
        if not improved:
            steps = {k: v / 2 for k, v in steps.items()}
    changed = {k: [round(s[k], 3), round(current[k], 3)] for k in keys if abs(current[k] - s[k]) >= 0.005}
    fitted = {**sheet, **{k: round(current[k], 3) for k in changed}}
    warnings = list(info["warnings"])
    if best_score < 0.8:
        warnings.append(f"the outlines overlap only {best_score:.0%} after the fit")
    return {"ok": True, "sheet": fitted, "changed": changed, "overlap_before": round(start, 3),
            "overlap_after": round(best_score, 3), "flipped": flipped, "confident": not warnings,
            "warnings": warnings, "_canvas": canvas, "_ppm": ppm}


def overlay(result, sheet_before, out_path):
    """A picture of a fit: the reference cut-out in grey, the sheet's outline before the fit in blue and
    after it in red. Returns the path."""
    canvas, ppm = result["_canvas"], result["_ppm"]
    w, h = canvas.size
    pic = Image.new("RGB", (w, h), (255, 255, 255))
    pic.paste((200, 200, 200), mask=canvas)
    draw = ImageDraw.Draw(pic)
    for sheet, colour in ((sheet_before, (40, 90, 220)), (result["sheet"], (220, 30, 30))):
        s = car_sheet.resolve(sheet)
        for poly in outline(s):
            pts = [((y - s["y_front"]) * ppm, h - 1 - z * ppm) for y, z in poly]
            draw.line(pts + pts[:1], fill=colour, width=2)
    pic = pic.resize((w * 2, h * 2), Image.LANCZOS)
    pic.save(out_path)
    return out_path


def fit_with_picture(path, sheet, keys=None, out_dir=None):
    """fit() plus its overlay picture, without the working images: what the car architect's tool returns."""
    result = fit(path, sheet, keys)
    if not result.get("ok"):
        return result
    out_dir = Path(out_dir or Path(tempfile.gettempdir()) / "modelgen-fits")
    out_dir.mkdir(parents=True, exist_ok=True)
    picture = overlay(result, sheet, out_dir / f"fit_{uuid.uuid4().hex[:10]}.png")
    return {**{k: v for k, v in result.items() if not k.startswith("_")}, "picture": str(picture)}
