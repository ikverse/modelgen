"""The details of a car built from a reference (bpy_lib.shells.car_v2), traced from the reference's own
blueprint: its black window opening, side glass, windshield and rear glass, door shut lines, fuel flap, headlights, grille,
intakes, fog lamps, tail lamps, plate and reflectors. Runs inside Blender.

modelgen.tools.blueprint.write_details writes each named detail's outlines, in the body's metres and
per view, to library/references/<type>_details.json (the manifest says which of the shapes drawn in a
view is which detail). Here each is cut into the body's skin with bpy_lib.shell.apply_feature, the
same cuts the sheet-built car uses: a recess (glass, lamps, grille...) or a groove (shut lines), from
the view the detail was drawn in. A detail that will not cut cleanly is left out and named on the body
(mg_skipped_details), never dropped silently.
"""

import json

import bmesh

import bpy

from bpy_lib import helpers as h
from bpy_lib import shell
from bpy_lib.shells import car_body
from bpy_lib.shells.car import _apply_cleanly
from bpy_lib.shells.car_sheet import SHUT_LINE, _arch_band

GLASS = dict(kind="recess", material="glass", depth=0.014, frame=0.02, frame_material="trim", frame_depth=0.006)
LAMP = dict(kind="recess", depth=0.02, frame=0.008, frame_material="chrome", frame_depth=0.01)
DARK = dict(kind="recess", material="trim")
LINE = dict(kind="groove", width=SHUT_LINE, material="trim")
# a headlight: a chrome housing in a black bezel under a clear lens that follows the body's surface
HEADLIGHT = dict(LAMP, material="chrome", frame_material="trim", lens=True)
# a white number plate in a black surround
PLATE = dict(kind="recess", material="plate", depth=0.006, frame=0.01, frame_material="trim", frame_depth=0.004)

# (view, detail) -> how it is cut
CUTS = {
    # the side windows' whole opening, B-pillar and mirror triangle in piano black, then each window's
    # glass inside it at one even depth
    ("side", "dlo"): dict(kind="recess", material="gloss_black", depth=0.003),
    ("side", "glass"): dict(kind="recess", material="glass", depth=0.008),
    ("side", "doors"): LINE,
    ("side", "fuel_flap"): dict(LINE, both_sides=False),
    ("side", "headlights"): HEADLIGHT,
    ("side", "bumper_line"): dict(LINE, edge="front"),  # the front bumper's seam with the fender
    ("side", "tail_lamps"): dict(LAMP, material="lamp_rear", frame_material="trim"),
    ("top", "glass"): GLASS,  # the windshield and rear glass, cut from above: they slope too much to cut end-on
    ("top", "hood_line"): LINE,
    ("front", "headlights"): HEADLIGHT,
    ("front", "grille"): dict(DARK, depth=0.03),
    ("front", "intakes"): dict(DARK, depth=0.04),
    # the lamps at the lower intake's ends, set into it (deeper than its floor)
    ("front", "fog_lamps"): dict(LAMP, material="lamp_front", depth=0.05),
    ("front", "vents"): dict(DARK, depth=0.02),
    ("front", "plate"): PLATE,
    ("front", "lip_line"): dict(LINE, edge="top"),  # the seam above the bumper's lower lip
    # the hood's edges sweeping down to the grille's top corners: the front view's hood shape's lower
    # edge either side of the grille, cut from above (the hood is too flat to cut end-on)
    ("front", "hood_v_lines"): dict(LINE, edge="bottom", beside="grille", as_view="top"),
    ("front", "corner_lines"): dict(LINE, edge="outer"),  # the bumper's seams from the headlights down
    ("rear", "tail_lamps"): dict(LAMP, material="lamp_rear", frame_material="trim"),
    ("rear", "plate"): PLATE,
    ("rear", "brake_lamp"): dict(LAMP, material="lamp_rear", frame_material="trim"),
    ("rear", "reflectors"): dict(LAMP, material="lamp_rear", frame_material="trim", depth=0.006),
}


def load(name):
    path = car_body.REFS / f"{name}_details.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def features(name, b):
    """The details of an example car as feature dicts for bpy_lib.shell.apply_feature, in cutting order:
    glass first, then lamps and openings, then the shut lines. b: its car_body.Body."""
    front_stop = b.wheels[0][0] - b.arch_radius() - 0.05   # front cuts stop ahead of the front wheel well
    rear_stop = b.wheels[1][0] + b.arch_radius() + 0.05

    def until(view, detail):
        if view == "side":
            return 0.0
        if view == "top":  # from above, down to just under the windshield's and rear glass's feet
            return min(b.cowl[1], b.deck[1]) - 0.05
        return front_stop if view == "front" else rear_stop

    out = []
    traced = load(name)
    for view, details in traced.items():
        for detail, outlines in details.items():
            spec = CUTS.get((view, detail))
            if spec is None:
                raise ValueError(f"no cut is defined for the {detail} of the {view} view")
            for i, outline in enumerate(outlines):
                if view == "top":  # traced as (y, x); bpy_lib.shell's top view takes (x, y)
                    outline = [(x, y) for y, x in outline]
                base = dict(spec, view=view, until=until(view, detail))
                if spec["kind"] == "groove":
                    if spec.get("edge"):
                        runs = [edge(outline, spec["edge"])]
                        if spec.get("beside"):  # only either side of another detail, not across it
                            runs = _beside(runs[0], traced[view].get(spec["beside"], []))
                        runs = [_smoothed_path(r) for r in runs if len(r) >= 2]
                        if spec.get("as_view") == "top":
                            base = dict(base, view="top", until=until("top", detail))
                            runs = [_front_to_top(b, r) for r in runs]
                    elif view == "side":
                        runs = _above_sill(b, outline)
                    else:
                        runs = [list(outline) + [outline[0]]]
                    for j, run in enumerate(runs):
                        out.append(dict(base, name=f"{view}_{detail}_{i + 1}_{j + 1}", path=[tuple(p) for p in run]))
                else:
                    out.append(dict(base, name=f"{view}_{detail}_{i + 1}", outline={"poly": [tuple(p) for p in outline]}))
    def rank(f):  # the window opening, the glass, then the lamps and openings, the shut lines last
        if "_dlo_" in f["name"]:
            return -1
        if "_glass_" in f["name"]:
            return 0
        return 2 if f["kind"] == "groove" else 1

    out.sort(key=rank)
    return out


def edge(outline, which, step=0.01):
    """One edge of a closed (u, v) outline as a path: "top" or "bottom" (its highest or lowest crossing
    every `step` across u), "front" or "back" (its lowest or highest u every `step` up v), or "outer"
    (front or back, whichever is further from u = 0: a shape's edge toward the car's side)."""
    if which == "outer":
        which = "front" if sum(p[0] for p in outline) < 0 else "back"
    pts = list(outline) + [outline[0]]
    across = which in ("top", "bottom")
    a = 0 if across else 1   # the axis stepped along
    lo, hi = min(p[a] for p in outline), max(p[a] for p in outline)
    out = []
    t = lo + step / 2
    while t < hi:
        hits = []
        for p, q in zip(pts, pts[1:]):
            if min(p[a], q[a]) <= t <= max(p[a], q[a]) and p[a] != q[a]:
                hits.append(p[1 - a] + (q[1 - a] - p[1 - a]) * (t - p[a]) / (q[a] - p[a]))
        if hits:
            c = max(hits) if which in ("top", "back") else min(hits)
            out.append((t, c) if across else (c, t))
        t += step
    return out


def _beside(path, others, margin=0.01):
    """The runs of a (u, v) path outside the u range of each of the other outlines (by `margin`)."""
    spans = [(min(p[0] for p in o) - margin, max(p[0] for p in o) + margin) for o in others]
    runs, run = [], []
    for p in path:
        if any(a <= p[0] <= c for a, c in spans):
            if run:
                runs.append(run)
            run = []
        else:
            run.append(p)
    if run:
        runs.append(run)
    return runs


def _front_to_top(b, path, step=0.002):
    """A front-view (x, z) path as the top-view (x, y) points where the body's surface is, seen from the
    front: the first y from the nose back that is inside the body at that x and z."""
    out = []
    for x, z in path:
        y = b.y_front
        while y < 0 and not b.whole_inside(y, x, z):
            y += step
        if y < 0:
            out.append((x, y))
    return out


def _above_sill(b, outline, margin=0.08):
    """A traced side-view outline (closed) as the runs of it more than `margin` above the body's
    underside: a door's shut line, without the jagged sill line a drawing closes it with."""
    pts = list(outline) + [outline[0]]
    keep = [z > car_body._interp(b.underside, y) + margin for y, z in pts]
    runs, run = [], []
    for p, k in zip(pts, keep):
        if k:
            run.append(p)
        elif run:
            runs.append(run)
            run = []
    if run:
        if runs and keep[0]:  # the run that wraps past the outline's start joins the first one
            runs[0] = run + runs[0]
        else:
            runs.append(run)
    return [_smoothed_path(r) for r in runs if len(r) >= 2]


def _smoothed_path(path, passes=3):
    """A traced path with its stair-steps smoothed out (each inner point pulled toward its neighbours'
    mean; the ends stay), so a shut line runs clean instead of zig-zagging."""
    pts = [tuple(p) for p in path]
    for _ in range(passes):
        pts = [pts[0]] + [((a[0] + 2 * b[0] + c[0]) / 4, (a[1] + 2 * b[1] + c[1]) / 4)
                          for a, b, c in zip(pts, pts[1:], pts[2:])] + [pts[-1]]
    return pts


def arch_lips(b):
    """A thin lip standing round each wheel opening, in the body's colour (cut back to the opening when
    the wells are cut)."""
    r = b.arch_radius()
    return [dict(name=f"arch_lip_{i + 1}", kind="raise", view="side", until=b.well_x(), height=0.008, material="paint",
                 outline=_arch_band(wy, b.wheel_radius, r - 0.02, r + 0.035, a0=-8.0, a1=188.0))
            for i, (wy, _, _) in enumerate(b.wheels)]


def _open_edges(obj):
    """How many edges of the mesh are not shared by exactly two faces (holes, seams, fins)."""
    bm = bmesh.new()
    bm.from_mesh(obj.data)
    n = sum(1 for e in bm.edges if not e.is_manifold)
    bm.free()
    return n


def apply(obj, feats, inner):
    """Cut each detail; one that will not cut cleanly (fails, chips a piece off, or leaves the body open
    where two cuts meet) is put back as it was and named. Returns the names left out."""
    skipped, previous = [], None
    for f in feats:
        if f.get("lens"):
            lens(f, inner)
        kept = obj.data.copy()
        open_before = _open_edges(obj)
        try:
            _apply_cleanly(obj, f, inner, previous)
            faces = len(obj.data.polygons)
            shell.keep_largest(obj)  # a cut that chipped a sliver off: drop the chip, but not a real piece
            if len(obj.data.polygons) < faces * 0.995:
                raise ValueError(f"feature '{f['name']}' cut a piece off the body")
            if _open_edges(obj) > open_before:
                raise ValueError(f"feature '{f['name']}' left the body open where it meets another detail")
        except ValueError as exc:
            broken = obj.data
            obj.data = kept
            if broken.users == 0:
                bpy.data.meshes.remove(broken)
            skipped.append(f["name"])
            print("car details: left out", f["name"], "-", str(exc)[:120], flush=True)
            continue
        bpy.data.meshes.remove(kept)
        previous = f["name"]
    return skipped


LENS_GAP = 0.0005    # a lamp's clear cover sits this far under the body's surface,
LENS_THICK = 0.002   # and is this thick


def lens(f, inner):
    """The clear cover of a lamp cut as feature f: a shell LENS_THICK thick just under the clean skin
    (inner's), over the whole of the lamp's outline, as its own object ("lamp_lens", material "lens"),
    so the lamp reads as a housing under glass, not a hole. Returns the objects made."""
    out = []
    from bpy_lib.shells.car_sheet import outline_points
    view, outline = f["view"], outline_points(f["outline"])
    for w0, w1 in shell._ranges(view, f.get("until", 0.0), f.get("both_sides", True)):
        piece = shell._prism(view, shell._ccw(outline), w0, w1, name="lamp_lens")
        shell._boolean(piece, inner.get(LENS_GAP, "lens"), "INTERSECT")
        shell._boolean(piece, inner.get(LENS_GAP + LENS_THICK, "lens"))
        shell._set_material(piece, "lens")
        if len(piece.data.polygons) == 0:
            bpy.data.objects.remove(piece, do_unlink=True)
            continue
        piece["mg_lens"] = f["name"]
        out.append(piece)
    return out


def mirrors(b):
    """A door mirror each side, on the shoulder just behind the windshield's foot."""
    y = b.cowl[0] + 0.14
    z = b.belt(y) + 0.06
    x = b.half_width(y, b.belt(y)) - 0.03
    right = shell.mirror_housing()
    right = h.finalize_part(right, "side_mirror") if isinstance(right, (list, tuple)) else right
    h.place(right, location=(x, y, z))
    left = h.instance(right)
    h.place(left, location=(-x, y, z), scale=(-1, 1, 1))
    return [right, left]
