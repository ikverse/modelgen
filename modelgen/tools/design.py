"""The Village's 2D vector designs read as mechanism sheets: the 3D part made to match the drawing.

A Village design is JSON: {"kind": "vector", "canvas": {"width", "height"}, "palette": {letter: "#rrggbb"},
"parts": [{"name", "shapes": [...], "pivot": [x, y], "z"}]}, in canvas pixels with y pointing down.
Shapes: "path" (points [x, y], with ["q", cx, cy, x, y] and ["c", c1x, c1y, c2x, c2y, x, y] curves),
"ellipse" (cx, cy, rx, ry), "circle" (cx, cy, r), "rect" (x, y, w, h), "line" (stroke only).

Each group whose filled outline is a toothed ring becomes a spur gear: its teeth counted from how
the outline's radius rises and falls round its centre, its module from its tip circle, a bore and a
hub from the round shapes drawn inside it, its colour from its fill. Gears drawn touching mesh, at
the angle they are drawn; the first gear's teeth start at the angle drawn. Everything is scaled by
one millimetres-per-pixel so the 3D set lines up with the drawing; what could not be matched is
listed with the reason. Pure Python: nothing here needs Blender.
"""
import math

STANDARD_MODULES = (0.5, 0.8, 1.0, 1.25, 1.5, 2.0, 2.5, 3.0, 4.0, 5.0, 6.0, 8.0, 10.0)
MAX_BYTES = 2_000_000
SAMPLES = 1440  # rays round a gear's centre


class DesignError(ValueError):
    """A design that cannot be read, with what is wrong."""


def _colour(value, palette):
    if value in (None, "", "none"):
        return None
    text = str(value)
    if text.startswith("#") and len(text) == 7:
        return text.lower()
    hit = palette.get(text)
    return str(hit).lower() if hit else None


def _bezier(p0, pts, steps=8):
    """A quadratic (one control point) or cubic (two) curve from p0, as points after p0."""
    out = []
    for i in range(1, steps + 1):
        t = i / steps
        if len(pts) == 2:
            (cx, cy), (x, y) = pts
            out.append(((1 - t) ** 2 * p0[0] + 2 * (1 - t) * t * cx + t * t * x,
                        (1 - t) ** 2 * p0[1] + 2 * (1 - t) * t * cy + t * t * y))
        else:
            (c1x, c1y), (c2x, c2y), (x, y) = pts
            out.append(((1 - t) ** 3 * p0[0] + 3 * (1 - t) ** 2 * t * c1x + 3 * (1 - t) * t * t * c2x + t ** 3 * x,
                        (1 - t) ** 3 * p0[1] + 3 * (1 - t) ** 2 * t * c1y + 3 * (1 - t) * t * t * c2y + t ** 3 * y))
    return out


def polygon(shape):
    """A filled shape's outline as points (canvas pixels), or None for a stroke-only shape."""
    t = shape.get("type")
    if t == "path":
        pts = []
        for p in shape.get("points") or []:
            if isinstance(p, (list, tuple)) and p and isinstance(p[0], str):
                nums = [float(v) for v in p[1:]]
                if not pts:
                    pts.append((nums[-2], nums[-1]))
                elif p[0].lower() == "q" and len(nums) == 4:
                    pts += _bezier(pts[-1], [(nums[0], nums[1]), (nums[2], nums[3])])
                elif p[0].lower() == "c" and len(nums) == 6:
                    pts += _bezier(pts[-1], [(nums[0], nums[1]), (nums[2], nums[3]), (nums[4], nums[5])])
                else:
                    pts.append((nums[-2], nums[-1]))
            elif isinstance(p, (list, tuple)) and len(p) >= 2:
                pts.append((float(p[0]), float(p[1])))
        return pts if len(pts) >= 3 else None
    if t in ("ellipse", "circle"):
        cx, cy = float(shape["cx"]), float(shape["cy"])
        rx = float(shape.get("rx", shape.get("r", 0)))
        ry = float(shape.get("ry", shape.get("r", 0)))
        return [(cx + rx * math.cos(2 * math.pi * i / 96), cy + ry * math.sin(2 * math.pi * i / 96)) for i in range(96)]
    if t == "rect":
        x, y, w, h = (float(shape[k]) for k in ("x", "y", "w", "h"))
        return [(x, y), (x + w, y), (x + w, y + h), (x, y + h)]
    return None


def _area(pts):
    return abs(sum(x0 * y1 - x1 * y0 for (x0, y0), (x1, y1) in zip(pts, pts[1:] + pts[:1]))) / 2


def _centroid(pts):
    a = cx = cy = 0.0
    for (x0, y0), (x1, y1) in zip(pts, pts[1:] + pts[:1]):
        cross = x0 * y1 - x1 * y0
        a += cross
        cx += (x0 + x1) * cross
        cy += (y0 + y1) * cross
    if abs(a) < 1e-12:
        return sum(p[0] for p in pts) / len(pts), sum(p[1] for p in pts) / len(pts)
    return cx / (3 * a), cy / (3 * a)


def radial_profile(pts, centre, samples=SAMPLES):
    """How far the outline reaches from the centre along each of `samples` rays (counterclockwise on
    the screen, starting to the right): the farthest crossing of each ray."""
    cx, cy = centre
    edges = list(zip(pts, pts[1:] + pts[:1]))
    out = []
    for i in range(samples):
        a = 2 * math.pi * i / samples
        dx, dy = math.cos(a), -math.sin(a)  # counterclockwise as seen: y points down
        best = 0.0
        for (x0, y0), (x1, y1) in edges:
            ex, ey = x1 - x0, y1 - y0
            den = dx * ey - dy * ex
            if abs(den) < 1e-12:
                continue
            ox, oy = x0 - cx, y0 - cy
            s = (ox * ey - oy * ex) / den  # along the ray
            u = (ox * dy - oy * dx) / den  # along the edge
            if s > 0 and 0 <= u <= 1:
                best = max(best, s)
        out.append(best)
    return out


def count_teeth(profile):
    """The teeth on a radial profile: how many times it rises through the middle between its root and
    tip (with a margin, so a wobble is not a tooth), and the angle (degrees, counterclockwise from the
    right) of the first tooth's middle. (0, None) for a round outline."""
    lo, hi = min(profile), max(profile)
    if hi <= 0 or (hi - lo) / hi < 0.04:
        return 0, None
    up, down = lo + 0.6 * (hi - lo), lo + 0.4 * (hi - lo)
    n = len(profile)
    start = min(range(n), key=lambda i: profile[i])  # begin in a gap
    state, teeth, tops, run = "low", 0, [], []
    for k in range(n + 1):
        i = (start + k) % n
        r = profile[i]
        if state == "low" and r > up:
            state, teeth, run = "high", teeth + 1, [i]
        elif state == "high":
            if r < down:
                state = "low"
                tops.append(run)
            else:
                run.append(i)
    if not tops:
        return teeth, None
    first = tops[0]
    mid = first[len(first) // 2]
    return teeth, 360.0 * mid / n


def _round(shape_pts, centre, r_out):
    """Whether a shape is a circle round the centre: (its radius) or None."""
    c = _centroid(shape_pts)
    rs = [math.dist(c, p) for p in shape_pts]
    r = sum(rs) / len(rs)
    if r <= 0 or max(rs) - min(rs) > 0.06 * r or math.dist(c, centre) > 0.08 * r_out:
        return None
    return r


def analyse(design):
    """Every group of a design read: for each, a gear's numbers in pixels (teeth, tip radius, centre,
    first tooth's angle, bore and hub radii, colour) or the reason it is not a gear."""
    if not isinstance(design, dict) or not isinstance(design.get("parts"), list):
        raise DesignError("design must be a Village vector design: an object with a list of parts")
    palette = design.get("palette") or {}
    gears, skipped = [], []
    for g in design["parts"]:
        name = str(g.get("name") or f"part{len(gears) + len(skipped) + 1}")
        filled = []
        for s in g.get("shapes") or []:
            pts = polygon(s)
            if pts and _colour(s.get("fill"), palette) and float(s.get("opacity", 1)) > 0.5:
                filled.append((pts, _colour(s.get("fill"), palette)))
        if not filled:
            skipped.append({"part": name, "why": "nothing filled in it (lines only)"})
            continue
        body, colour = max(filled, key=lambda f: _area(f[0]))
        centre = _centroid(body)
        profile = radial_profile(body, centre)
        teeth, first = count_teeth(profile)
        tip, root = max(profile), min(profile)
        if teeth < 6:
            skipped.append({"part": name, "why": "not toothed: no tool part matches it" if teeth == 0
                            else f"{teeth} bumps round it, too few for a gear"})
            continue
        depth = (tip - root) / tip
        if depth > 0.4:
            skipped.append({"part": name, "why": f"{teeth} blades or arms, not gear teeth (they reach in "
                                                 f"{depth:.0%} of the way): no tool part matches it"})
            continue
        inner = sorted(r for r in (_round(pts, centre, tip) for pts, c in filled if pts is not body) if r and r < root)
        bore = inner[0] if inner and inner[0] < 0.6 * root else None
        hub = next((r for r in reversed(inner) if r > (bore or 0) * 1.3 and r < 0.85 * root), None)
        gears.append({"part": name, "teeth": teeth, "tip_px": tip, "root_px": root, "centre_px": list(centre),
                      "first_tooth_deg": first, "bore_px": bore, "hub_px": hub, "colour": colour,
                      "module_px": 2 * tip / (teeth + 2)})
    return gears, skipped


def _safe(name, taken):
    out = "".join(ch if ch.isalnum() else "_" for ch in name.lower()).strip("_")[:36] or "gear"
    base, k = out, 2
    while out in taken:
        out, k = f"{base}_{k}", k + 1
    taken.add(out)
    return out


def to_mechanism(design, module=None, mm_per_px=None):
    """A mechanism sheet (as a job would send it) from a Village design, and what was matched:
    {"mm_per_px", "parts": {sheet name: what it came from and its numbers}, "skipped": [...]}.
    module (mm) sets the largest gear's module, mm_per_px the scale outright; otherwise the standard
    module nearest half its drawn module in pixels (as if the drawing were about 2 px a millimetre).
    The drawing keeps its proportions either way. Raises DesignError when nothing in it is a part
    the tools make."""
    gears, skipped = analyse(design)
    if not gears:
        why = "; ".join(f"{s['part']}: {s['why']}" for s in skipped[:6])
        raise DesignError(f"no part in this design is one the tools make ({why})")
    gears.sort(key=lambda g: -g["tip_px"])
    first = gears[0]
    if mm_per_px is None:
        if module is None:
            want = first["module_px"] * 0.5
            module = min(STANDARD_MODULES, key=lambda m: abs(math.log(m / want)))
        mm_per_px = module / first["module_px"]
    module = round(first["module_px"] * mm_per_px, 4)
    taken, parts, report = set(), [], {}
    names = {}
    for g in gears:
        n = names[g["part"]] = _safe(g["part"], taken)
        p = {"name": n, "type": "spur_gear", "module": module, "teeth": g["teeth"]}
        if g["colour"]:
            p["colour"] = g["colour"]
        if g["bore_px"]:
            p["bore"] = round(2 * g["bore_px"] * mm_per_px * 2) / 2
        root_mm = module * (g["teeth"] - 2.5)
        if g["hub_px"] and 2 * g["hub_px"] * mm_per_px < root_mm - 2.5 * module:  # inside the tooth roots
            p["hub_diameter"] = round(2 * g["hub_px"] * mm_per_px, 1)
            p["hub_length"] = round(11 * module, 1)
        parts.append(p)
        report[n] = {"design_part": g["part"], "teeth": g["teeth"], "centre_px": [round(v, 2) for v in g["centre_px"]],
                     "tip_radius_px": round(g["tip_px"], 2), "drawn_module_px": round(g["module_px"], 3),
                     "module_mm": module, "colour": g["colour"]}
        if abs(g["module_px"] * mm_per_px - module) > 0.12 * module:
            report[n]["note"] = (f"drawn with teeth {g['module_px'] / first['module_px']:.2f} x the size of "
                                 f"{first['part']}'s; built with the same module so they can mesh")
    # gears drawn touching mesh: the larger first, never round a loop
    parent = {g["part"]: g["part"] for g in gears}

    def find(x):
        while parent[x] != x:
            x = parent[x]
        return x

    by = {g["part"]: g for g in gears}
    for i, a in enumerate(gears):
        for b in gears[i + 1:]:
            d = math.dist(a["centre_px"], b["centre_px"])
            want = (a["teeth"] + b["teeth"]) * first["module_px"] / 2
            if abs(d - want) <= 0.15 * want and find(a["part"]) != find(b["part"]):
                parent[find(b["part"])] = find(a["part"])
                pa = next(p for p in parts if p["name"] == names[a["part"]])
                pb = next(p for p in parts if p["name"] == names[b["part"]])
                pa.setdefault("meshes_with", []).append(pb["name"])
                dx, dy = b["centre_px"][0] - a["centre_px"][0], a["centre_px"][1] - b["centre_px"][1]
                pb["at"] = round(math.degrees(math.atan2(dy, dx)), 3)
                report[pb["name"]]["meshes_with"] = pa["name"]
    del by
    lead = parts[0]
    if first["first_tooth_deg"] is not None:
        lead["phase"] = round(first["first_tooth_deg"], 3)
    alone = [p["name"] for p in parts[1:] if find(next(g["part"] for g in gears if names[g["part"]] == p["name"]))
             != find(first["part"])]
    out = {"parts": parts, "driver": lead["name"]}
    return out, {"mm_per_px": round(mm_per_px, 6), "parts": report, "skipped": skipped, "unconnected": alone,
                 "label": design.get("label"), "canvas": design.get("canvas")}
