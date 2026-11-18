"""How closely a built car matches its reference, view by view. Plain Python and Pillow, no Blender.

The built car's triangles (written by bpy_lib.shells.car_v2.dump_triangles) are drawn flat into its
side, top and front silhouettes at 1 cm a pixel, in metres; the reference's views are cut from its
blueprint at the same scale (modelgen.tools.blueprint.reference_silhouette). A car seen from the
front and from behind has the same outline, so the built car's front silhouette is scored against
both the reference's front and rear views. For each view:

- iou: the overlap of the two filled outlines over their union (1 is identical);
- edge_mean_cm, edge_p95_cm: how far each outline's edge is from the other's, both ways, on
  average and at the 95th percentile, in centimetres.

An overlay picture per view shows the reference filled in blue and the built car's outline in red.
"""

import array
import json
import math

from PIL import Image, ImageChops, ImageDraw, ImageFilter

from . import blueprint

RES = 0.01  # metres per pixel
AXES = {"side": (1, 2), "top": (1, 0), "front": (0, 2), "rear": (0, 2)}  # (u, v) as indices of (x, y, z)


def load_triangles(path):
    data = array.array("f")
    with open(path, "rb") as f:
        data.frombytes(f.read())
    return [data[i:i + 9] for i in range(0, len(data), 9)]


def model_silhouette(triangles, view, res=RES):
    """The built car's filled outline in one view: (image mode "L", (u_min, v_max)) as
    blueprint.reference_silhouette gives."""
    iu, iv = AXES[view]
    us = [t[3 * c + iu] for t in triangles for c in range(3)]
    vs = [t[3 * c + iv] for t in triangles for c in range(3)]
    u_min, v_max = min(us) - 2 * res, max(vs) + 2 * res
    w = int((max(us) + 2 * res - u_min) / res) + 1
    h = int((v_max - (min(vs) - 2 * res)) / res) + 1
    img = Image.new("L", (w, h), 0)
    d = ImageDraw.Draw(img)
    for t in triangles:
        pts = [((t[3 * c + iu] - u_min) / res, (v_max - t[3 * c + iv]) / res) for c in range(3)]
        d.polygon(pts, fill=255)
    return img, (u_min, v_max)


def _common(a, b, res=RES):
    """Both silhouettes pasted onto one canvas: (A, B, (u_min, v_max))."""
    (ia, (ua, va)), (ib, (ub, vb)) = a, b
    u_min, v_max = min(ua, ub), max(va, vb)
    u_max = max(ua + ia.width * res, ub + ib.width * res)
    v_min = min(va - ia.height * res, vb - ib.height * res)
    size = (int((u_max - u_min) / res) + 2, int((v_max - v_min) / res) + 2)
    out = []
    for img, (u0, v0) in ((ia, (ua, va)), (ib, (ub, vb))):
        canvas = Image.new("L", size, 0)
        canvas.paste(img, (round((u0 - u_min) / res), round((v_max - v0) / res)))
        out.append(canvas)
    return out[0], out[1], (u_min, v_max)


def _count(img):
    return sum(1 for v in img.getdata() if v)


def _edge_points(img):
    edge = ImageChops.subtract(img, img.filter(ImageFilter.MinFilter(3)))
    w = img.width
    return [(i % w, i // w) for i, v in enumerate(edge.getdata()) if v]


def _distances(src, dst, cell=8):
    """For each point of src, the distance (pixels) to the nearest point of dst (a bucket grid)."""
    grid = {}
    for x, y in dst:
        grid.setdefault((x // cell, y // cell), []).append((x, y))
    out = []
    for x, y in src:
        cx, cy = x // cell, y // cell
        best, ring = None, 0
        while True:
            for gx in range(cx - ring, cx + ring + 1):
                for gy in range(cy - ring, cy + ring + 1):
                    if max(abs(gx - cx), abs(gy - cy)) != ring:
                        continue
                    for px, py in grid.get((gx, gy), ()):
                        d = (px - x) ** 2 + (py - y) ** 2
                        if best is None or d < best:
                            best = d
            if best is not None and math.sqrt(best) <= ring * cell:
                break
            ring += 1
            if ring > 400:
                break
        out.append(math.sqrt(best) if best is not None else float("inf"))
    return out


def compare(model, reference, res=RES, v_min=None):
    """Scores of one view: iou, edge_mean_cm, edge_p95_cm; and the two canvases for a picture.
    v_min: only what is above this height counts (the front and rear views are scored from the sills
    up: below them a drawing shows only tyres and the shadow under the car)."""
    a, b, origin = _common(model, reference, res)
    if v_min is not None:
        keep = max(0, min(a.height, round((origin[1] - v_min) / res)))
        a, b = a.crop((0, 0, a.width, keep)), b.crop((0, 0, b.width, keep))
    inter = _count(ImageChops.multiply(a, b))
    union = _count(ImageChops.lighter(a, b))
    ea, eb = _edge_points(a), _edge_points(b)
    ds = sorted(_distances(ea, eb) + _distances(eb, ea))
    cm = res * 100
    return {"iou": round(inter / max(1, union), 4),
            "edge_mean_cm": round(sum(ds) / max(1, len(ds)) * cm, 2),
            "edge_p95_cm": round(ds[int(0.95 * (len(ds) - 1))] * cm, 2) if ds else None}, (a, b)


def picture(model_canvas, ref_canvas, path, title=""):
    """The reference filled pale blue, the built car's outline in red."""
    rgb = Image.new("RGB", ref_canvas.size, "white")
    rgb.paste(Image.new("RGB", rgb.size, (170, 200, 245)), (0, 0), ref_canvas)
    edge = ImageChops.subtract(model_canvas, model_canvas.filter(ImageFilter.MinFilter(3)))
    rgb.paste(Image.new("RGB", rgb.size, (220, 20, 20)), (0, 0), edge.filter(ImageFilter.MaxFilter(3)))
    if title:
        ImageDraw.Draw(rgb).text((6, 6), title, fill=(0, 0, 0))
    rgb.save(path)
    return path


def score(name, triangles_path, out_dir=None, triangles_by_view=None):
    """Score a built car against its reference in every view the reference has. triangles_by_view
    can give other triangles for a view (the side view of a reference whose tyres are drawn open is
    scored without the wheels). Returns {view: scores} and writes pictures to out_dir."""
    tris = load_triangles(triangles_path)
    ref_views = blueprint.manifest()[name]["views"]
    ref = json.loads((blueprint.REFS / f"{name}.json").read_text(encoding="utf-8"))
    sill = (ref["views"]["side"].get("sill_z") or 0.0) + 0.02
    out = {}
    for view in ("side", "top", "front", "rear"):
        if view not in ref_views:
            continue
        t = (triangles_by_view or {}).get(view, tris)
        model = model_silhouette(t, "front" if view == "rear" else view)
        ref = blueprint.reference_silhouette(name, view)
        scores, (a, b) = compare(model, ref, v_min=sill if view in ("front", "rear") else None)
        out[view] = scores
        if out_dir:
            picture(a, b, str(out_dir / f"{view}_overlay.png"),
                    f"{name} {view}: IoU {scores['iou']:.3f}, edges {scores['edge_mean_cm']} cm mean, "
                    f"{scores['edge_p95_cm']} cm p95")
    return out
