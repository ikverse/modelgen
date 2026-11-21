"""Builds a car from its sheet, and places its separate pieces. Runs inside Blender only.

body(sheet) is the whole body as one continuous skin: the cross-sections from the sheet are skinned
into one closed mesh and smoothed (creased along its character lines for the angular look), scaled
to the sheet's exact size, the fenders bulge round the wheels (soft flares), then the bumpers, lip,
skirts, boxed flares and other raised shapes stand out of that same mesh, the glass, lamps, grille,
intakes, door lines and the rest are cut into it, then the wheel openings, and last the cabin is
hollowed out behind the glass and the glass opened into it. assemble(parts) puts the wheels on the
axles, seats the mirrors, wing and exhaust on the body's surface, and adds the part "interior" (glass
panes in the openings, dashboard, steering wheel and seats), all from the sheet stored on the body.
"""

import json
import math

import bmesh
import bpy
from mathutils import Vector

from .. import helpers as h
from .. import shell
from . import car_sheet

SHEET_KEY = "mg_sheet"


# A cut whose outline lands almost exactly on the skin's own edges can leave edges that do not close;
# the same detail moved a millimetre or two cuts cleanly.
NUDGES = ((0.0015, 0.0), (0.0, 0.0015), (-0.0015, 0.0), (0.0, -0.0015), (0.0025, 0.0025))


def _counts(me):
    return len(me.vertices), len(me.polygons)


def _no_effect(what, previous):
    """The error for a cut that changed nothing: its outline misses the body, or the detail before it
    left the body in a state no later cut can change."""
    culprit = f" the detail before it ('{previous}')" if previous else " an earlier detail"
    return ValueError(f"{what} did not change the body. Either its outline misses the body (check its view, position "
                      f"and `until`), or{culprit} broke the body so that no later cut works: an outline that runs "
                      "past the body's edge, or mirrored halves that meet at the centre line. Change that detail.")


def test_at(obj):
    """A spot on the body's underside, in the middle, for a test cut."""
    zs = [v.co.z for v in obj.data.vertices]
    return (0.0, 0.0, min(zs))


def takes_more_cuts(obj, at):
    """True when a small test cut at `at` (on the skin) still leaves a copy of the body clean. A cut
    can leave slivers that pass the check itself but make every later cut, anywhere, come out broken."""
    me = obj.data.copy()
    test = bpy.data.objects.new("test_cut_target", me)
    bpy.context.scene.collection.objects.link(test)
    cube = h.box((0.03, 0.03, 0.03), location=at, name="test_cut")
    try:
        shell._boolean(test, cube)
        return not shell.weld_problems(test.data)
    finally:
        for o in (test, cube):
            mesh = o.data
            bpy.data.objects.remove(o, do_unlink=True)
            if mesh.users == 0:
                bpy.data.meshes.remove(mesh)


def _apply_cleanly(obj, feature, inner, previous=None):
    """As _cut_cleanly; a detail with a `fallback_outline` (a plainer shape of it) gets that shape
    when its own will not cut cleanly."""
    try:
        return _cut_cleanly(obj, feature, inner, previous)
    except ValueError:
        if not feature.get("fallback_outline"):
            raise
        plain = {k: v for k, v in feature.items() if k != "fallback_outline"}
        plain["outline"] = feature["fallback_outline"]
        print(f"car body: '{feature.get('name')}' fell back to its plain outline")
        return _cut_cleanly(obj, plain, inner, previous)


def _cut_cleanly(obj, feature, inner, previous=None):
    """Cut or raise one detail; when the result has open edges, try again from the mesh before it with
    the detail nudged by a millimetre or two, and give up with the detail's name only if none is clean.
    A cut that removes most of the body counts as unclean; one that changes nothing is an error at once.
    previous: the name of the detail cut before this one, for the error."""
    name = feature.get("name")
    before = obj.data.copy()
    wrecked = False
    try:
        # a detail on the nose that will not cut cleanly along the surface's own direction is tried
        # again turned toward straight back (its `aim_fallbacks`), each with the same nudges
        aims = [feature.get("aim")] + list(feature.get("aim_fallbacks") or [])
        for aim, (du, dv) in ((a, n) for a in aims for n in ((0.0, 0.0),) + NUDGES):
            try:
                tried = car_sheet.nudged(feature, du, dv)
                if aim is not None and aim != feature.get("aim"):
                    tried = dict(tried, aim=aim)
                shell.apply_feature(obj, tried, inner)
            except Exception as exc:
                raise ValueError(f"feature '{name}' failed: {exc}") from exc
            if _counts(obj.data) == _counts(before):
                raise _no_effect(f"feature '{name}'", previous)
            # a detail only ever adds faces; losing half of them means the cut went through the body
            wrecked = len(obj.data.polygons) < len(before.polygons) / 2
            if not wrecked and not shell.weld_problems(obj.data) and (
                    not feature.get("optional") or takes_more_cuts(obj, test_at(obj))):
                return
            bad = obj.data
            obj.data = before.copy()
            bpy.data.meshes.remove(bad)
    finally:
        bpy.data.meshes.remove(before)
    if wrecked:
        raise ValueError(f"feature '{name}' removed most of the body: its outline probably runs past the body's edge. "
                         "Keep it inside the surface it is cut into, or remove it.")
    raise ValueError(f"feature '{name}' could not be cut cleanly: its outline is probably too thin, "
                     "crosses itself, or runs along the edge of another detail. Change or remove it.")


def _on_the_nose(skin, feats):
    """The details placed on the nose by the sheet's estimate (`on_nose`), put on the built skin
    instead: each one's `at` moved to the real surface in front of it and its `aim` turned to that
    surface's inward normal, so the cut goes straight in instead of grazing a nose the estimate got
    wrong. Details that share a place (a lamp's housing, lenses and strip) share the measurement and
    stay lined up; where the surface is not found the sheet's own numbers are kept."""
    found = {}
    out = []
    for f in feats:
        if not f.get("on_nose") or f.get("view") != "aim":
            out.append(f)
            continue
        key = tuple(round(float(c), 4) for c in f["at"])
        if key not in found:
            x, y, z = key
            pts = car_sheet.outline_points(f["outline"]) if f.get("outline") else [tuple(p) for p in f["path"]]
            pts = pts[::max(1, len(pts) // 8)]
            found[key] = None
            # a detail whose outline would run over the nose's top edge onto the hood (the sheet's
            # estimate put it too high) slides down the nose until it is all on the nose
            for drop in (0.0, 0.015, 0.03, 0.045, 0.06, 0.075, 0.09):
                hit = shell.surface_point(skin, (x, y - 1.0, z - drop), (0, 1, 0))
                if hit is None or hit[1].y > -0.2:
                    continue
                at, inward = hit[0], -hit[1]
                # a wide detail lies on more than one facet: the surface's direction is taken over its
                # whole outline, not at its middle alone
                view = shell.Aim(tuple(inward), tuple(at))
                total, n, over = Vector(inward), 1, False
                for u, v in pts:
                    p = view.to_world(u, v, 0.0)
                    more = shell.surface_point(skin, (p.x, p.y - 1.0, p.z), (0, 1, 0))
                    if more is None or more[1].z > 0.8:
                        over = True  # past the nose's edge: on the hood, or off the body
                    elif more[1].y < -0.2:
                        total, n = total - more[1], n + 1
                inward = (total / n).normalized()
                # should the cut still graze the nose, it is tried again turned toward straight back
                back = Vector((0.0, 1.0, 0.0))
                place = ([round(c, 4) for c in at], [round(c, 4) for c in inward],
                         [[round(c, 4) for c in (inward * (1 - k) + back * k).normalized()] for k in (0.5, 1.0)])
                if found[key] is None:
                    found[key] = place  # where the sheet put it, unless a lower place is all on the nose
                if not over:
                    found[key] = place
                    break
        if found[key] is None:
            out.append(f)
        else:
            at, aim, turned = found[key]
            out.append(dict(f, at=at, aim=aim, aim_fallbacks=turned))
    return out


def _skin(s, feats, name="body"):
    """The clean skin of a resolved sheet, before any detail: skinned, fitted to size, flared."""
    obj = shell.skin(car_sheet.rings(s), levels=int(s["detail"]), name=name, end_crease=car_sheet.end_crease(s),
                     creases=car_sheet.creases(s), ring_creases=car_sheet.ring_creases(s),
                     end_centre_lines=car_sheet.end_centre_lines(s))
    front, rear = car_sheet.end_protrusion(feats)  # the bumpers bring the ends back out to the full length
    bumper = car_sheet.rear_bumper(s)  # the rear one swells out of the skin itself, below
    y_end = s["y_rear"] - rear
    shell.fit(obj, half_width=s["half_width"], y_range=(s["y_front"] + front, y_end - (bumper["out"] if bumper else 0)),
              z_range=(s["ride_height"], s["height"]))
    # the fenders: a soft swell, or a blister (boxed) whose raised edge rolls off quickly but smoothly;
    # the front one fades forward into the bumper, the rear haunch reaches forward into the door
    axles = [(s["axle_front"], s["wheel_radius"]), (s["axle_rear"], s["wheel_radius"])]
    r = s["arch_radius"]
    stretch = [(s["flare_reach"], 1.15), (1.0 + s["hip_length"] / r, 1.3)]
    if s["flare_style"] == "boxed":
        shell.flare(obj, axles, r, s["flare"], 0.07, x_from=s["half_width"] * 0.6, z_top=s["belt_z"] - 0.04,
                    stretch=stretch, plateau=s["flare_width"])
    else:
        shell.flare(obj, axles, r, s["flare"], s["flare_spread"], x_from=s["half_width"] * 0.6,
                    z_top=s["belt_z"] - 0.04, stretch=stretch)
    if bumper:  # built short by the bumper's depth, then the bumper swells out to the sheet's length
        shell.bumper(obj, y_end - bumper["out"], 1.0, bumper["out"], bumper["top"], bumper["edge"], bumper["wrap"])
        shell.fit(obj, y_range=(s["y_front"] + front, y_end))
    return obj


CABIN_KEY = "mg_cabin"
CABIN_WALL = 0.03   # how thick the body stays round the hollow cabin
PANE = 0.004        # the glass panes' thickness


def _glass_details(feats):
    """The glass details of a car (windshield, side and rear glass): recesses with the glass slot."""
    return [f for f in feats if f.get("kind", "recess") == "recess" and f.get("material") == "glass"
            and f.get("outline") is not None]


OPENING_MARGIN = 0.004  # the opening stays this far inside the glass floor's edge: no shared walls


def _glass_cut(feature, margin=0.0, side_reach=0.4):
    """(view, outline, spans) of each opening through one glass detail: the glass floor's outline (inside
    its frame, and `margin` further in) and the spans along the view a cutter through it fills: for
    side glass from each side in to |x| = side_reach, from `until` outward otherwise."""
    f = dict(feature)
    view = shell.Aim(f["aim"], f["at"]) if f.get("view") == "aim" else f.get("view", "side")
    outline = car_sheet.outline_points(f["outline"])
    inset = float(f.get("frame", 0.0)) + margin
    if inset > 0:
        outline = shell.inset_outline(outline, inset) or outline
    until = float(f.get("until", 0.0))
    if view == "side":
        spans = [(side_reach, shell.BIG), (-shell.BIG, -side_reach)]
    else:
        spans = shell._ranges(view, until, False)
    return [(v, o, spans) for v, o in shell._mirrored(view, outline, f.get("mirror_x", False))]


def _through_glass_spans(feature, margin=0.0, side_reach=0.4):
    """Every (view, outline, w0, w1) a glass detail's openings are cut with, in order."""
    return [(view, outline, w0, w1) for view, outline, spans in _glass_cut(feature, margin, side_reach)
            for w0, w1 in spans]


def _through_glass(feature, volume, margin=0.0, side_reach=0.4, only=None):
    """Prisms through a glass detail's floor, each cut down to `volume` (an inner copy of the skin):
    the space just inside that glass. only: just the opening of that index. Returns the new objects."""
    out = []
    for k, (view, outline, w0, w1) in enumerate(_through_glass_spans(feature, margin, side_reach)):
        if only is not None and k != only:
            continue
        prism = shell._prism(view, shell._ccw(outline), w0, w1, name="through_glass")
        shell._set_material(prism, "trim")
        shell._boolean(prism, volume, "INTERSECT")
        out.append(prism)
    return out


def cabin_box(s):
    """The hollow cabin's extent: (half_width, y0, y1, z0, z1), from behind the windshield's foot to the
    rear seat (or the load space of a tall tail), from under the beltline up into the roof. A low car
    whose cabin floor comes down beside the wheel openings gets a cabin narrower than the wheel wells."""
    y0 = s["windshield_base"] + 0.02
    tall_tail = s["deck_z"] > s["height"] - 0.2 or s["style"] in ("hatch", "suv", "van")
    y1 = s["deck_front"] - 0.1 if tall_tail else min(s["deck_front"] - 0.05, s["roof_rear"] + 0.25)
    if s.get("dlo_end") is not None:  # a mid-engine car: the cabin ends just behind its side glass
        y1 = min(y1, s["dlo_end"] + 0.1)
    z0 = max(s["belt_z"] - 0.32, s["ride_height"] + 0.15)
    half = 2 * s["width"]
    if z0 < s["wheel_radius"] + s["arch_radius"] + s["boxed_width"] + 0.03:
        half = s["well_x"] - 0.03
    return half, y0, y1, z0, s["height"] + 1.0


def _carve_cabin(obj, s, feats, inner):
    """Hollow the cabin out behind the glass and open the glass through to it, so a see-through glass
    shows the interior (car.assemble adds the panes and the seats). The body stays one closed solid:
    a window whose opening would not cut cleanly keeps its solid glass, and if the hollow itself would
    not, the body is left solid and says so. Records the openings made in obj[CABIN_KEY]."""
    glass = _glass_details(feats)
    half, y0, y1, z0, z1 = cabin_box(s)
    if not glass or y1 - y0 < 0.4 or s["belt_z"] - z0 < 0.08:
        return
    before = obj.data.copy()
    made, opened, kept_solid = [], [], []
    try:
        box = h.box((2 * half, y1 - y0, z1 - z0), location=(0.0, (y0 + y1) / 2, (z0 + z1) / 2), name="cabin")
        shell._set_material(box, "trim")
        made.append(box)
        shell._boolean(box, inner.get(CABIN_WALL, "trim"), "INTERSECT")
        shell._boolean(obj, box)
        if shell.weld_problems(obj.data) or _counts(obj.data) == _counts(before):
            raise ValueError("the hollow did not cut cleanly")
        for f in glass:  # from just above the glass floor (never on it) down into the cabin
            volume = inner.get(float(f.get("depth", 0.014)) - 0.002, "trim")
            n = len(_through_glass_spans(f))
            # a window's openings on both sides as ONE cutter, nudged together if it cuts badly: cut one
            # side at a time and the first cut can leave something the second trips over
            for du, dv in ((0.0, 0.0),) + NUDGES:
                prisms = _through_glass(car_sheet.nudged(f, du, dv), volume, OPENING_MARGIN, s["half_width"] * 0.45)
                made += prisms
                cutter = prisms[0]
                if len(prisms) > 1:
                    cutter = bpy.data.objects.new("through_glass_all", prisms[0].data.copy())
                    bpy.context.scene.collection.objects.link(cutter)
                    made.append(cutter)
                    bm = bmesh.new()
                    bm.from_mesh(cutter.data)
                    for p in prisms[1:]:
                        bm.from_mesh(p.data)
                    bm.to_mesh(cutter.data)
                    bm.free()
                kept = obj.data.copy()
                shell._boolean(obj, cutter)
                if not shell.weld_problems(obj.data) and _counts(obj.data) != _counts(kept):
                    bpy.data.meshes.remove(kept)
                    opened += [[f.get("name"), k] for k in range(n)]
                    break
                bad = obj.data
                obj.data = kept
                bpy.data.meshes.remove(bad)
            else:
                kept_solid.append(f.get("name"))
        if not opened:
            raise ValueError("no window opened cleanly")
        faces = len(obj.data.polygons)
        shell.keep_largest(obj)
        if shell.weld_problems(obj.data) or len(obj.data.polygons) < faces * 0.995:
            raise ValueError("the cabin did not cut cleanly")
        obj[CABIN_KEY] = json.dumps(opened)
        if kept_solid:
            print("car body: kept solid glass in", ", ".join(sorted(set(kept_solid))))
    except ValueError as exc:
        broken = obj.data
        obj.data = before.copy()
        if broken.users == 0:
            bpy.data.meshes.remove(broken)
        print("car body: kept the cabin solid:", exc)
    finally:
        bpy.data.meshes.remove(before)
        for o in made:
            bpy.data.objects.remove(o, do_unlink=True)


def interior(body_obj):
    """The glass panes in a hollowed body's window openings and a simple dark interior (dashboard,
    steering wheel, seats), as one object to tag as the part "interior". None when the body was
    left solid."""
    if not body_obj.get(CABIN_KEY):
        return None
    opened = {(name, k) for name, k in json.loads(body_obj[CABIN_KEY])}
    s = sheet_of(body_obj)
    feats = car_sheet.features(s)
    skin = _skin(s, feats, name="interior_skin")
    if body_obj.get(STRETCH_KEY):
        lo, k = json.loads(body_obj[STRETCH_KEY])
        _stretch(skin.data, s, lo, k)
    inner = shell._Inner(skin)
    pieces = []
    try:
        for f in _glass_details(feats):
            depth = float(f.get("depth", 0.014)) + 0.001  # just under the old glass floor's place
            panes = [_through_glass(f, inner.get(depth, "glass"), 0.0, s["half_width"] * 0.45, only=k)[0]
                     for k in range(len(_through_glass_spans(f))) if (f.get("name"), k) in opened]
            for pane in panes:
                shell._boolean(pane, inner.get(depth + PANE, "glass"))
                shell._set_material(pane, "glass")  # glass on every face, its edges too
                if len(pane.data.polygons):
                    pieces.append(pane)
                else:
                    bpy.data.objects.remove(pane, do_unlink=True)
    finally:
        inner.free()
        me = skin.data
        bpy.data.objects.remove(skin, do_unlink=True)
        bpy.data.meshes.remove(me)
    pieces += _cabin_furniture(s, body_obj)
    return pieces


def _down_to(body_obj, x, y, z_from, fallback):
    """The height of the cabin floor (or whatever the body has) straight under (x, y)."""
    hit = shell.surface_point(body_obj, (x, y, z_from), (0, 0, -1))
    return hit[0].z if hit else fallback


def _cabin_furniture(s, body_obj):
    """Dashboard, steering wheel and seats, dark trim, each sunk a little into the cabin's floor or
    front wall so it stands on the body."""
    half, y0, y1, z0, _ = cabin_box(s)
    hw = s["half_width"] - s["shoulder"]
    belt = s["belt_z"]
    trim = []
    dash_top = min(s["cowl_z"] - 0.03, belt + 0.06)
    dash_depth = 0.3
    dash = h.box((2 * min(hw - 0.1, half + 0.005), dash_depth + 0.01, dash_top - z0 + 0.01),
                 location=(0.0, y0 + dash_depth / 2 - 0.01, (dash_top + z0) / 2 - 0.005), name="dashboard")
    h.bevel(dash, 0.03, segments=3)
    trim.append(dash)
    # the steering wheel on the left (+X: the car faces -Y), tilted toward the driver, on its column
    wx, wy, wz = hw * 0.42, y0 + dash_depth + 0.1, belt + 0.02
    tilt = (115, 0, 0)  # the wheel's axis runs forward and down into the dashboard
    d = (0.0, -math.cos(math.radians(25)), -math.sin(math.radians(25)))
    ring = h.torus(0.17, 0.015, location=(wx, wy, wz), rotation_deg=tilt, name="steering_wheel")
    spoke = h.box((0.34, 0.035, 0.02), location=(wx, wy, wz), rotation_deg=tilt, name="steering_spoke")
    column = h.cylinder(0.025, 0.26, segments=16, location=(wx, wy + d[1] * 0.13, wz + d[2] * 0.13),
                        rotation_deg=tilt, name="steering_column")
    trim += [ring, spoke, column]
    split = _lerp(s["roof_front"], s["roof_rear"], s["window_split"])
    front_seat = split + 0.12 if int(s["doors"]) == 4 else _lerp(s["roof_front"], s["roof_rear"], 0.6)
    front_seat = min(front_seat, y1 - 0.15)
    seat_top = min(s["height"] - 0.12, belt + 0.32)
    rows = [(front_seat, [hw * 0.42, -hw * 0.42], 0.48)]
    if int(s["doors"]) == 4 and y1 - front_seat > 0.75:
        rows.append((min(s["roof_rear"] - 0.05, y1 - 0.15), [0.0], 2 * hw * 0.8))
    for y, xs, width in rows:
        for x in xs:
            floor = _down_to(body_obj, x, y, belt, z0)
            back = h.box((width, 0.11, seat_top - floor + 0.01), location=(x, y, (seat_top + floor) / 2 - 0.005),
                         rotation_deg=(-10, 0, 0), name="seat_back")
            cushion = h.box((width, 0.45, 0.14), location=(x, y - 0.25, floor + 0.06), name="seat_cushion")
            for piece in (back, cushion):
                h.bevel(piece, 0.03, segments=3)
            trim += [back, cushion]
    for piece in trim:
        shell._set_material(piece, "trim")
    return trim


def _lerp(a, b, t):
    return a + (b - a) * t


def body(sheet):
    """The car body from a sheet (see car_sheet for every number). Returns one mesh object."""
    problems = car_sheet.check(sheet)
    if problems:
        raise ValueError("the car sheet cannot be built: " + "; ".join(problems))
    s = car_sheet.resolve(sheet)
    feats = car_sheet.features(s)
    obj = _skin(s, feats)
    feats = _on_the_nose(obj, feats)
    axles = [(s["axle_front"], s["wheel_radius"]), (s["axle_rear"], s["wheel_radius"])]
    inner = shell._Inner(obj)
    previous, skipped = None, []
    wells = False

    def cut_wells(previous):
        """The wheel openings, their radius nudged by a millimetre or two when a cut is not clean."""
        before = obj.data.copy()
        try:
            for nudge in (0.0, 0.0015, -0.0015, 0.003):
                shell.cut_wells(obj, axles, s["arch_radius"] + nudge, s["well_x"])
                if _counts(obj.data) == _counts(before):
                    raise _no_effect("cutting the wheel openings", previous)
                if not shell.weld_problems(obj.data):
                    return
                bad = obj.data
                obj.data = before.copy()
                bpy.data.meshes.remove(bad)
            raise ValueError("the wheel openings could not be cut cleanly: check the wheel size, track and "
                             "wheel_gap against the body, and the details round the wheels")
        finally:
            bpy.data.meshes.remove(before)

    try:
        for feature in feats:
            # the wheel openings right after the raised shapes (the arch trim must stand round them first)
            # and before the fine details, whose many small cuts can leave a mesh a big cut fails on
            if not wells and feature.get("kind") != "raise":
                _true_length(obj, s, inner)
                cut_wells(previous)
                wells = True
            if feature.get("optional"):  # a fine line that may not cut cleanly everywhere: left out then
                kept = obj.data.copy()
                try:
                    _apply_cleanly(obj, feature, inner, previous)
                    # a fine line can chip off a sliver where it runs along an edge: drop a tiny chip,
                    # but a line that cut off a real piece of the body is left out
                    faces = len(obj.data.polygons)
                    shell.keep_largest(obj)
                    if len(obj.data.polygons) < faces * 0.995:
                        raise ValueError(f"feature '{feature.get('name')}' cut a piece off the body")
                except ValueError:
                    broken = obj.data
                    obj.data = kept
                    if broken.users == 0:
                        bpy.data.meshes.remove(broken)
                    skipped.append(feature.get("name"))
                    continue
                bpy.data.meshes.remove(kept)
            else:
                _apply_cleanly(obj, feature, inner, previous)
                # any cut can chip a sliver off as a tiny shell of its own: a skin is one piece, so the
                # chip goes; a cut that took a real piece off the body is an error
                faces = len(obj.data.polygons)
                shell.keep_largest(obj)
                if len(obj.data.polygons) < faces * 0.99:
                    raise ValueError(f"feature '{feature.get('name')}' cut a piece off the body: keep its "
                                     "outline inside the surface it is cut into, or remove it.")
            previous = feature.get("name")
        if not wells:
            cut_wells(previous)
        # a cut at the very tip of the nose or tail (an intake's mouth, the plate) takes a centimetre or
        # two off the length: the finished body is brought back to the sheet's length
        _true_length(obj, s, inner)
        _carve_cabin(obj, s, feats, inner)  # last: nothing is cut after the hollow
    finally:
        inner.free()
    if skipped:
        obj["mg_skipped_details"] = ", ".join(skipped)
        print("car body: left out details that did not cut cleanly:", ", ".join(skipped))
    obj[SHEET_KEY] = json.dumps(sheet)
    return obj


STRETCH_KEY = "mg_stretch"


def _stretch(me, s, lo, k):
    for v in me.vertices:
        v.co.y = s["y_front"] + (v.co.y - lo) * k
    me.update()


def _true_length(obj, s, inner):
    """Stretch the body, once its raised shapes stand (bumpers, lip), so it is exactly the sheet's
    length: how far a raised shape ends up past the skin depends on the skin's slope under it, so
    the skin alone cannot be sized for it. The clean skin the details are cut against is stretched
    the same way, so their depths stay true."""
    ys = [v.co.y for v in obj.data.vertices]
    lo, hi = min(ys), max(ys)
    if abs(lo - s["y_front"]) < 1e-4 and abs(hi - s["y_rear"]) < 1e-4:
        return
    k = (s["y_rear"] - s["y_front"]) / (hi - lo)
    if obj.get(STRETCH_KEY):  # stretched before: the two stretches as one, from the first one's start
        lo1, k1 = json.loads(obj[STRETCH_KEY])
        obj[STRETCH_KEY] = json.dumps([lo1 - (s["y_front"] - lo) / k1, k1 * k])
    else:
        # (kept as text: Blender crashes when a list property is set a second time)
        obj[STRETCH_KEY] = json.dumps([lo, k])  # car.interior builds the glass panes on a skin stretched the same way
    _stretch(obj.data, s, lo, k)
    _stretch(inner.source, s, lo, k)
    for cached in inner.cache.values():  # rebuilt from the stretched source when next asked for
        me = cached.data
        bpy.data.objects.remove(cached, do_unlink=True)
        bpy.data.meshes.remove(me)
    inner.cache = {}


def sheet_of(body_obj):
    """The resolved sheet a body was built from."""
    raw = body_obj.get(SHEET_KEY)
    if not raw:
        raise ValueError("the body has no car sheet on it: build it with car.body(SHEET)")
    return car_sheet.resolve(json.loads(raw))


def _seat(body_obj, origin, direction, fallback, embed=0.006):
    hit = shell.surface_point(body_obj, origin, direction)
    if hit is None:
        return fallback
    loc, normal = hit
    return tuple(loc - normal * embed)


def _popup_pods(body_obj, s):
    """The raised pop-up lamps of a "popup_open" sheet, each standing on its lid on the hood: its front
    face's foot and its hinge seated on the hood surface, so the lid slopes back into the hood."""
    pieces = []
    for pod in car_sheet.popup_pods(s):
        x, y0 = pod["x"], pod["y"]
        front = shell.surface_point(body_obj, (x, y0 + 0.01, 5.0), (0, 0, -1))
        back = shell.surface_point(body_obj, (x, y0 + pod["length"], 5.0), (0, 0, -1))
        if not front or not back:
            continue
        zf = front[0].z
        made = shell.popup_pod(pod["width"], pod["length"], pod["face"], rise=back[0].z - zf)
        for m in made:
            m.location = (x, y0, zf)
        pieces += made
    return pieces


BAR_STEP = 0.025   # a grille bar follows the opening's floor through a point this often


def _grille_bars(body_obj, s):
    """The bars across the openings that have a pattern (car_sheet.grille_bars): each a square rod on
    the floor of its recessed opening, following that floor as it curves round the nose, standing
    proud of it by about the bar's depth. A bar breaks where the floor is not there (a wall, a miss)."""
    pieces = []
    for bar in car_sheet.grille_bars(s):
        out = -1.0 if bar["view"] == "front" else 1.0  # which way along Y is outward
        u0, u1, v0, v1 = bar["u0"], bar["u1"], bar["v0"], bar["v1"]
        along_u = (u1 - u0) >= (v1 - v0)
        length = (u1 - u0) if along_u else (v1 - v0)
        half = min(u1 - u0, v1 - v0) / 2
        lift = max(half, min(bar["depth"], 0.012) / 2)  # the rod's centre this far out from the floor
        count = max(1, math.ceil(length / BAR_STEP))
        runs, run = [], []
        for k in range(count + 1):
            t = (u0 if along_u else v0) + length * k / count
            u, v = (t, (v0 + v1) / 2) if along_u else ((u0 + u1) / 2, t)
            hit = shell.surface_point(body_obj, (u, out * 5.0, v), (0, -out, 0))
            if hit is None or abs(hit[1].y) < 0.5:  # missed, or met a wall of the opening
                if len(run) >= 2:
                    runs.append(run)
                run = []
                continue
            run.append((u, hit[0].y + out * (lift - 0.003), v))
        if len(run) >= 2:
            runs.append(run)
        for points in runs:
            rod = h.sweep(points, radius=max(half, lift), smooth=False, profile_segments=4, name="grille_bar")
            shell._set_material(rod, "gloss_black")
            pieces.append(rod)
    return pieces


def assemble(parts):
    """Place every separate piece of a car from the sheet stored on parts["body"]."""
    body_obj = parts["body"]
    s = sheet_of(body_obj)
    inside = interior(body_obj)
    if inside:  # the glass panes and the seats of a hollowed body: a part of their own
        piece = h.finalize_part(inside, "interior")
        h.attach(piece, "body")
    if "wheel" in parts:
        wheel = parts["wheel"]
        radius = h.size(wheel).z / 2  # the wheel as built, so the tyres stand on the floor whatever its size
        for i, (x, y, _) in enumerate(car_sheet.wheel_positions(s)):
            z = radius
            piece = wheel if i == 0 else h.instance(wheel)
            h.place(piece, location=(x, y, z), rotation_deg=(0, 0, 0 if x > 0 else 180))
            h.attach(piece, "body")
    if "side_mirror" in parts:
        right = parts["side_mirror"]
        x, y, z = _seat(body_obj, (5.0, s["mirror_y"], s["mirror_z"]), (-1, 0, 0),
                        (s["half_width"], s["mirror_y"], s["mirror_z"]))
        h.place(right, location=(x, y, z))
        h.attach(right, "body")
        left = h.instance(right)
        h.place(left, location=(-x, y, z), scale=(-1, 1, 1))
        h.attach(left, "body")
    pods = _popup_pods(body_obj, s)
    if pods:  # lamp_style "popup_open": the raised lamps stand on their lids, a part of their own
        piece = h.finalize_part(pods, "popup_lamps")
        h.attach(piece, "body")
    bars = _grille_bars(body_obj, s)
    if bars:  # the grille bars across the openings, a part of their own
        piece = h.finalize_part(bars, "grilles")
        h.attach(piece, "body")
    if "wing" in parts:
        zs = []
        for x in (-s["wing_spacing"] / 2, s["wing_spacing"] / 2):
            hit = shell.surface_point(body_obj, (x, s["wing_y"], 5.0), (0, 0, -1))
            if hit:
                zs.append(hit[0].z)
        z = (min(zs) if zs else s["deck_z"]) - 0.008
        h.place(parts["wing"], location=(0, s["wing_y"], z))
        h.attach(parts["wing"], "body")
    if "exhaust" in parts:
        x, z = s["exhaust_x"], s["exhaust_z"]
        hit = shell.surface_point(body_obj, (x, 5.0, z), (0, -1, 0))
        y = (hit[0].y if hit else s["y_rear"]) - 0.05
        h.place(parts["exhaust"], location=(x, y, z))
        h.attach(parts["exhaust"], "body")
