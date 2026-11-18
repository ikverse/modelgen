"""A car body made from a real car's views, the way a modeller builds one from blueprints. Plain Python
with no Blender import: the pipeline and the tests use it as it is, and bpy_lib.shells.car_v2 skins it.

A reference (library/references/<type>.json, cut from a blueprint by modelgen.tools.blueprint) gives
the car's outline in each view, in metres:

- the side view: the top and bottom outline along the car, and the wheels;
- the top view: the half-width along the car;
- the front and rear views: the half-width up the car.

The body is a run of cross-sections along the car. Each is bounded above and below by the side
outline (the underside straight across the wheels, whose openings are cut later), as wide as the top
view says there, and shaped like the front view in the front half of the car and the rear view in the
back half, blended between the axles: so the sides curve, the glasshouse narrows and the underside
tucks in as the real car's do. A section's points are found along rays from a point inside it at the
same angles on every section, so neighbouring sections' points match; its corners are rounded a
little by smoothing the distance along the rays. Sections are packed closer near the ends, where the
nose and tail turn quickly.

Conventions as car_sheet's: metres, Z up from the ground, the front toward -Y, the car's length
centred on Y = 0, the right side toward +X.
"""

import json
import math
from pathlib import Path

REFS = Path(__file__).resolve().parents[3] / "library" / "references"
STATIONS = 140      # cross-sections along the car
POINTS = 48         # points on each side of a section, from the bottom centre to the top centre
CROWN = 0.02        # a flat top (hood, deck) is this much lower at its edges than at its centre
WHEEL_GAP = 0.035   # space between the tyre and the wheel opening
WELL_CLEARANCE = 0.03  # space between the tyre's inner face and the wheel-well wall
SMOOTH_ALONG = 0.08  # the traced outlines along the car are averaged over this much (metres)
FAIR_BODY = 1       # fairing passes along the car on the lower body's sections
FAIR_GLASS = 20     # and on the glasshouse's
TUCK = 0.03         # below the wheels' centres the sides tuck in toward the sill by up to this much
CREASE_DROP = 0.10  # the shoulder's character line runs this far below the beltline,
CREASE_STEP = 0.01  # the side stepping in this much just under it
HOOD_DIP = 0.015    # the hood's centre sits this much below the fender tops either side
GLASS_RISE = 0.0    # the glasshouse starts and ends where the roof outline is this far above the beltline
GLASS_SINK = 0.02   # its end sections reach this much deeper into the body (over their last 10 cm)
PILLAR = 0.07       # the A- and C-pillars' width beside the windshield and rear glass, seen from above
SILL_GAP = 0.01     # the side glass's bottom edge stands this far above the shoulder
GLASS_FOOT = 0.06   # the glasshouse reaches this far down into the body below the shoulder
GLASS_INSET = 0.035  # the side glass stands this far in from the shoulder's edge,
GLASS_TOP = 0.08    # leans in on one line up to this far below the roof,
GLASS_BOW = 0.006   # bowed out by up to this much, as real side glass is
SMOOTH_UP = 0.03     # and the end views' half-widths over this much of height


def load(name):
    """The reference of an example car type (its json, with the views in metres)."""
    return json.loads((REFS / f"{name}.json").read_text(encoding="utf-8"))


def _interp(points, t):
    """Linear interpolation in (t, v) points sorted by t; clamped at the ends."""
    if t <= points[0][0]:
        return points[0][1]
    if t >= points[-1][0]:
        return points[-1][1]
    lo, hi = 0, len(points) - 1
    while hi - lo > 1:
        mid = (lo + hi) // 2
        if points[mid][0] <= t:
            lo = mid
        else:
            hi = mid
    (ta, va), (tb, vb) = points[lo], points[hi]
    return va if tb == ta else va + (vb - va) * (t - ta) / (tb - ta)


def _smoothed(points, window):
    """(t, v) points with v averaged over `window` (in t's units) round each point, the ends kept: a
    traced outline's pixel steps (a few millimetres) smoothed away, so glossy paint and glass do not show
    them as ripples. The first and last points stay where they are."""
    out = [points[0]]
    for i in range(1, len(points) - 1):
        t = points[i][0]
        near = [v for s, v in points if abs(s - t) <= window / 2]
        out.append((t, sum(near) / len(near)))
    out.append(points[-1])
    return out


def _smooth(t):
    t = max(0.0, min(1.0, t))
    return t * t * (3 - 2 * t)


class Body:
    """A reference made into the body's measurements: the outlines, the wheels and the sections."""

    def __init__(self, ref, details=None):
        self.ref = ref
        self.details = details or {}  # the details traced from its blueprint (blueprint.write_details)
        pub = ref["published"]
        side = ref["views"]["side"]
        self.length = side["length"]
        self.y_front, self.y_rear = -self.length / 2, self.length / 2
        self.height = side["height"]
        self.width = pub["width"]
        self.top = _smoothed(side["top"], SMOOTH_ALONG)
        self.wheels = [(w["y"], w["z"], w["radius"]) for w in side["wheels"]]
        self.wheel_radius = side["wheel_radius"]
        self.tyre_width = round(0.12 * self.width, 3)          # a tyre about an eighth of the car's width
        self.track = round(self.width - 1.6 * self.tyre_width, 3)  # the tyres just inside the body
        self.underside = self._underside(_smoothed(side["bottom"], SMOOTH_ALONG))
        # the plan: the top view, stretched to the side view's length (both are the same car)
        top = ref["views"].get("top")
        if top:
            k = self.length / (top["half_width"][-1][0] - top["half_width"][0][0])
            self.plan = _smoothed([(y * k, w) for y, w in top["half_width"]], SMOOTH_ALONG)
        else:  # no top view: a plan rounded at the ends from the published width
            self.plan = self._plain_plan()
        # the cross-section shapes: each end view's half-width up the car, as a fraction of its widest,
        # its heights stretched to the side view's height
        self.shapes = {}
        for end in ("front", "rear"):
            view = ref["views"].get(end) or ref["views"].get("rear" if end == "front" else "front")
            prof = view["half_width"]
            widest = max(w for _, w in prof) or 1.0
            kz = self.height / prof[-1][0]
            self.shapes[end] = _smoothed([(z * kz, w / widest) for z, w in prof], SMOOTH_UP)
        fa, ra = self.wheels[0][0], self.wheels[1][0]
        self.blend = (fa, ra)
        self.cowl, self.deck = self._cabin_ends()
        self.belt_front_view = self._belt_from_end_views()
        self.sill = self._sill_line()
        g = [y for y, z in self.top if self.cowl[0] <= y <= self.deck[0] and z - self.belt(y) > GLASS_RISE]
        self.glass_span = (g[0], g[-1]) if len(g) > 1 else None
        self.pillars = self._pillars()
        # the character line: from behind the front wheel opening to the tail lamps
        self.crease_span = (self.wheels[0][0] + self.wheel_radius + WHEEL_GAP + 0.1, self.y_rear - 0.3)

    def _cabin_ends(self):
        """Where the glasshouse meets the body along the top outline: ((y, z) of the windshield's foot,
        (y, z) of the rear glass's foot). Each is the elbow of the outline between the roof and that end
        (the point furthest from the straight line joining them): where the glass turns into the gentler
        hood or deck. The search stops short of the nose and tail faces, which are steeper still and not
        glass; an outline that stays within 3 cm of that line (a hatch's or an SUV's straight tailgate)
        has its glass running all the way to where the search stops."""
        pts = self.top
        n = len(pts)
        zs = [z for _, z in pts]
        top_z = max(zs)
        roof = [i for i in range(n) if zs[i] > top_z - 0.03]
        r0, r1 = roof[0], roof[-1]
        front_lim = min(range(n), key=lambda k: abs(pts[k][0] - (self.wheels[0][0] - 0.3)))
        rear_lim = min(range(n), key=lambda k: abs(pts[k][0] - (self.y_rear - 0.3)))

        def elbow(a, b):
            """Index between a and b furthest from the chord a-b, or b when none is 3 cm off it."""
            if abs(b - a) < 2:
                return b
            (ya, za), (yb, zb) = pts[a], pts[b]
            length = math.hypot(yb - ya, zb - za) or 1e-6
            best, best_d = b, 0.03
            for k in range(min(a, b) + 1, max(a, b)):
                yk, zk = pts[k]
                d = abs((yb - ya) * (za - zk) - (ya - yk) * (zb - za)) / length
                if d > best_d:
                    best, best_d = k, d
            return best

        i = elbow(r0, min(front_lim, r0))
        j = elbow(r1, max(rear_lim, r1))
        return (pts[i][0], pts[i][1]), (pts[j][0], pts[j][1])

    def _belt_from_end_views(self):
        """The shoulder's height as the end views show it: the lowest height above mid-body where they
        step in to under 90% of their widest. Reported as a check on the beltline taken from the cowl and
        the deck."""
        out = []
        for prof in self.shapes.values():
            for z, w in prof:
                if z > 0.4 * self.height and w < 0.9:
                    out.append(z)
                    break
        return round(sum(out) / len(out), 3) if out else None

    def belt(self, y):
        """The beltline (the shoulder, where the side glass starts): a straight line from the windshield's
        foot to the rear glass's, as on most cars; along the side windows, SILL_GAP under the blueprint's
        window bottoms (sill), easing back up to those feet over the whole way from the first and last."""
        (y0, z0), (y1, z1) = self.cowl, self.deck
        t = max(0.0, min(1.0, (y - y0) / max(1e-6, y1 - y0)))
        z = z0 + (z1 - z0) * t
        if self.sill:  # from the window line, climbing gently to the windshield's and rear glass's feet
            (a, k), (s0, s1) = self.sill
            fade = _smooth((y - y0) / max(1e-6, s0 - y0)) * _smooth((y1 - y) / max(1e-6, y1 - s1))
            z -= max(0.0, z - (a + k * y - SILL_GAP)) * fade
        return z

    def _sill_line(self):
        """The side windows' bottom edge in the blueprint's side view, as a straight line fitted to it:
        ((z at y = 0, slope), (first window's front, last window's back)); None when the blueprint's side
        glass is not traced. A window's bottom edge: its points within 3 cm of its lowest, away from its
        rounded front and back corners."""
        side = (getattr(self, "details", None) or {}).get("side", {}).get("glass") or []
        pts = []
        for o in side:
            ys = [p[0] for p in o]
            zmin = min(p[1] for p in o)
            lo, hi = min(ys) + 0.03, max(ys) - 0.03
            pts += [(y, z) for y, z in o if z <= zmin + 0.03 and lo <= y <= hi]
        if len(pts) < 2:
            return None
        n = len(pts)
        my, mz = sum(y for y, _ in pts) / n, sum(z for _, z in pts) / n
        var = sum((y - my) ** 2 for y, _ in pts)
        k = sum((y - my) * (z - mz) for y, z in pts) / var if var > 1e-9 else 0.0
        span = (min(p[0] for o in side for p in o), max(p[0] for o in side for p in o))
        return (mz - k * my, k), span

    def body_top(self, y):
        """The lower body's top: the side outline (hood, deck, tail), cut to the beltline under the
        glasshouse."""
        zt = _interp(self.top, y)
        if self.glass_span and self.cowl[0] <= y <= self.deck[0]:
            return min(zt, self.belt(y))
        return zt

    def _underside(self, bottom):
        """The body's lower outline: the side view's, straight across each wheel (there the outline is
        the tyre), from just before its opening to just after."""
        out = list(bottom)
        for wy, _, r in self.wheels:
            reach = r + WHEEL_GAP + 0.06
            a, b = wy - reach, wy + reach
            za, zb = _interp(bottom, a), _interp(bottom, b)
            out = [(y, za + (zb - za) * (y - a) / (b - a)) if a < y < b else (y, z) for y, z in out]
        return out

    def _plain_plan(self):
        """A plan for a reference without a top view: the full half-width, rounded off over the last
        40 cm at each end."""
        hw = self.width / 2
        n = 161
        out = []
        for i in range(n):
            y = self.y_front + self.length * i / (n - 1)
            d = min(y - self.y_front, self.y_rear - y)
            out.append((y, hw * (0.8 + 0.2 * math.sqrt(max(0.0, min(1.0, d / 0.4))))))
        return out

    # --- one cross-section ---------------------------------------------------------------------

    def bounds(self, y):
        """(bottom, top) of the lower body's section at y."""
        return _interp(self.underside, y), self.body_top(y)

    def glass_bounds(self, y):
        """(bottom, top) of the glasshouse's section at y: from a little inside the body (so the two
        overlap and join cleanly) up to the roof outline."""
        return self.belt(y) - GLASS_FOOT - GLASS_SINK * self._glass_end(y, 0.10), _interp(self.top, y)

    def _glass_end(self, y, reach):
        """1 at either end of the glasshouse, falling smoothly to 0 `reach` along it."""
        g = self.glass_span
        if not g:
            return 0.0
        return 1.0 - _smooth(min(y - g[0], g[1] - y) / reach)

    def glass_half_width(self, y, z):
        """How far out the glasshouse reaches at (y, z). From the shoulder (set GLASS_INSET in from its
        edge) the side glass leans in on one straight line, bowed out by up to GLASS_BOW, to the end views'
        width GLASS_TOP below the roof; above that it follows the end views (the pillars converging into
        the roof). Below the shoulder its walls run straight down into the body. Held in at the windshield's
        and rear glass's feet by the pillars (_at_pillars)."""
        belt = self.belt(y)
        x0 = self.half_width(y, belt) - GLASS_INSET
        z1 = _interp(self.top, y) - GLASS_TOP
        if z <= belt:
            hw = x0
        elif z < z1 and z1 > belt + 0.02:
            x1 = min(self._end_view_width(y, z1), x0)
            t = (z - belt) / (z1 - belt)
            hw = x0 + (x1 - x0) * t + GLASS_BOW * 4 * t * (1 - t)
        else:
            hw = self._end_view_width(y, max(z, belt))
        return max(0.05, self._at_pillars(y, min(hw, x0)))

    def _end_view_width(self, y, z):
        """The end views' half-width at height z, blended from the front view to the rear view along the
        car."""
        fa, ra = self.blend
        t = _smooth((y - fa) / max(1e-6, ra - fa))
        return self.width / 2 * ((1 - t) * _interp(self.shapes["front"], z) + t * _interp(self.shapes["rear"], z))

    def _pillars(self):
        """How the windshield and the rear glass narrow the glasshouse, from the blueprint's top and side
        views: for each, its outline seen from above as (y, |x|) points, which way the glass runs from its
        foot (+1 back, -1 forward), and where the side glass begins (y). Empty when the blueprint's glass
        is not traced."""
        top = self.details.get("top", {}).get("glass") or []
        side = self.details.get("side", {}).get("glass") or []
        if not top or not side:
            return []
        side_y = [p[0] for o in side for p in o]
        out = []
        for o in top:  # traced as (y, x)
            pts = [(p[0], abs(p[1])) for p in o]
            if sum(y for y, _ in pts) / len(pts) < 0:
                out.append((pts, 1, min(side_y)))   # the windshield, its foot forward
            else:
                out.append((pts, -1, max(side_y)))  # the rear glass, its foot back
        return out

    def _at_pillars(self, y, hw):
        """The glasshouse's half-width hw at y, held to PILLAR wider than the windshield (or rear glass)
        there, seen from above, so the pillar's foot is a pillar and not a wide fin of body beside the
        glass; easing back to hw over the 20 cm before the side glass begins."""
        for pts, direction, side_end in self.pillars:
            if direction == 1:
                near = [x for py, x in pts if py <= y + 0.06]  # the glass from its foot to just past here
                free = _smooth((y - (side_end - 0.2)) / 0.2)
            else:
                near = [x for py, x in pts if py >= y - 0.06]
                free = _smooth(((side_end + 0.2) - y) / 0.2)
            if near and free < 1.0:
                hw = (1 - free) * min(hw, max(near) + PILLAR) + free * hw
        return hw

    def glass_inside(self, y, x, z):
        zb, zt = self.glass_bounds(y)
        if z < zb:
            return False
        edge = self.glass_half_width(y, zt)
        top = zt - CROWN * min(1.0, (abs(x) / edge) ** 2) if edge > 1e-6 else zt
        return z <= top and abs(x) <= self.glass_half_width(y, z)

    def half_width(self, y, z):
        """How far out the body reaches at (y, z): the plan's half-width times the end views' shape there;
        below the wheels' centres the side tucks in toward the sill (TUCK), and along the doors it steps
        in just under the shoulder (CREASE_STEP), the crisp character line most cars have."""
        fa, ra = self.blend
        t = _smooth((y - fa) / max(1e-6, ra - fa))
        shape = (1 - t) * _interp(self.shapes["front"], z) + t * _interp(self.shapes["rear"], z)
        hw = _interp(self.plan, y) * shape
        wz = self.wheel_radius  # the wheels' centres' height
        zb = _interp(self.underside, y)
        if z < wz:  # the end views cannot show this tuck: down there their outline is the tyres
            hw -= TUCK * _smooth((wz - z) / max(1e-6, wz - zb))
        c0, c1 = self.crease_span
        if c0 <= y <= c1:
            line = self.belt(y) - CREASE_DROP
            fade = _smooth((y - c0) / 0.15) * _smooth((c1 - y) / 0.15)  # the line starts and ends softly
            hw -= CREASE_STEP * fade * _smooth((line - z) / 0.015)
        return max(0.0, hw)

    def _top_at(self, y, x, zt, edge):
        """The top surface's height a distance x out from the centre line, where the centre-line top is zt
        and the section's top edge is `edge` out: crowned (lower toward its edges), except on the hood,
        whose centre sits HOOD_DIP below the fender tops either side, in a soft valley."""
        if edge <= 1e-6:
            return zt
        u = min(1.0, abs(x) / edge)
        hood = _smooth((y - (self.y_front + 0.15)) / 0.25) * _smooth((self.cowl[0] - 0.02 - y) / 0.15)
        crowned = zt - CROWN * u * u
        valley = zt - HOOD_DIP * (1 - _smooth((u - 0.1) / 0.6)) - 2 * CROWN * max(0.0, (u - 0.82) / 0.18) ** 2
        return (1 - hood) * crowned + hood * valley

    def inside(self, y, x, z):
        zb, zt = self.bounds(y)
        if z < zb:
            return False
        return z <= self._top_at(y, x, zt, self.half_width(y, zt)) and abs(x) <= self.half_width(y, z)

    def whole_bounds(self, y):
        """(bottom, top) of the whole car's section at y: the body and the glasshouse on it."""
        return _interp(self.underside, y), _interp(self.top, y)

    def whole_inside(self, y, x, z):
        """Inside the lower body, or inside the glasshouse standing on its shoulder."""
        if self.inside(y, x, z):
            return True
        g = self.glass_span
        return bool(g) and g[0] <= y <= g[1] and self.glass_inside(y, x, z)

    def section(self, y, n=POINTS, part="whole"):
        """The right half of the section at y of the whole car (part "whole": the lower body and the
        glasshouse together, the step in at the shoulder part of its outline), of the lower body alone
        ("body") or of the glasshouse alone ("glass"): n + 1 points (x, z), from the bottom centre (angle
        -90) to the top centre (+90), along rays from a point inside it, with its corners rounded. The
        rays' angles are taken in the section's own box (its widest half-width across, half its height
        up), so corners fall on the same points along the car and the skin does not twist. The last
        10 cm at each end are rounded a little more."""
        bounds, width, inside = {"glass": (self.glass_bounds, self.glass_half_width, self.glass_inside),
                                 "body": (self.bounds, self.half_width, self.inside),
                                 "whole": (self.whole_bounds, self.half_width, self.whole_inside)}[part]
        zb, zt = bounds(y)
        zc = zb + 0.45 * (zt - zb)
        sx = max(width(y, zb + (zt - zb) * i / 24) for i in range(25)) or 1e-3
        sz = max(1e-3, (zt - zb) / 2)
        rs = []
        angles = [-math.pi / 2 + math.pi * j / n for j in range(n + 1)]
        for a in angles:
            dx, dz = sx * math.cos(a), sz * math.sin(a)
            lo, hi = 0.0, 2.5
            for _ in range(28):  # the boundary along the ray, by bisection
                mid = (lo + hi) / 2
                if inside(y, mid * dx, zc + mid * dz):
                    lo = mid
                else:
                    hi = mid
            rs.append(lo)
        # round the corners: a light smoothing of the distance along neighbouring rays (the ends kept),
        # one pass, and a second in the last 10 cm of the body, where the nose and tail fold round
        end = min(y - self.y_front, self.y_rear - y)
        passes = 1 + round(1 - _smooth(end / 0.10))
        # the glasshouse is rounded only at its roof corners: below the middle its walls run straight down
        # into the body, which hides its bottom; rounded there, it would curl under above the shoulder
        first = n // 2 if part == "glass" else 1
        for _ in range(passes):
            rs = rs[:first] + [0.25 * rs[j - 1] + 0.5 * rs[j] + 0.25 * rs[j + 1] for j in range(first, n)] + [rs[-1]]
        pts = []
        for j, (a, r) in enumerate(zip(angles, rs)):
            x = 0.0 if j in (0, n) else max(0.0, r * sx * math.cos(a))
            pts.append((round(x, 5), round(zc + r * sz * math.sin(a), 5)))
        return pts

    def stations(self, n=STATIONS, span=None):
        """Where the sections are along the car (or along span = (y0, y1)): closer together near the ends."""
        if span:
            y0, y1 = span
        else:  # from where the nose is 4 cm tall and 16 cm wide to where the tail is: at the very tips the
            # outline closes to a point, and a section there would be a sliver of points crowded together
            def tall(y):
                zb, zt = self.bounds(y)
                return zt - zb >= 0.04 and _interp(self.plan, y) >= 0.08
            y0, y1 = self.y_front + 0.002, self.y_rear - 0.002
            while not tall(y0) and y0 < 0:
                y0 += 0.002
            while not tall(y1) and y1 > 0:
                y1 -= 0.002
        out = []
        for i in range(n):
            u = i / (n - 1)
            u = 0.5 * u + 0.5 * (1 - math.cos(math.pi * u)) / 2
            out.append(y0 + (y1 - y0) * u)
        return out

    @staticmethod
    def _ring(y, half):
        right = [(x, round(y, 5), z) for x, z in half]
        left = [(-x, round(y, 5), z) for x, z in reversed(half[1:-1])]
        return right + left

    @staticmethod
    def cap_lines(rings):
        """The end caps' centre lines for bpy_lib.shell.skin: points evenly spaced up the middle of the
        first and last ring. Taken from the ring's own points instead, the many points along a flat
        underside or top would put centre-line points microns apart, which tears the cap when welded."""
        out = []
        for ring in (rings[0], rings[-1]):
            half = len(ring) // 2
            (_, y, zb), (_, _, zt) = ring[0], ring[half]
            out.append([(0.0, y, zb + (zt - zb) * k / half) for k in range(1, half)])
        return out

    def rings(self, n_stations=STATIONS, n_points=POINTS, part="body"):
        """Closed rings (x, y, z) front to back for bpy_lib.shell.skin, each from the bottom centre up the
        right side and down the left: of the lower body (part "body"; the glasshouse is skinned apart,
        glass_rings, and joined to it), or of the whole car in one skin ("whole"; its shoulder is softer
        and its roof twists, so the builder joins two skins)."""
        rings = [self._ring(y, self.section(y, n_points, part)) for y in self.stations(n_stations)]
        return self._fair(rings, FAIR_BODY)

    def glass_rings(self, n_stations=90, n_points=32):
        """The glasshouse's closed rings, from the windshield's foot to the rear glass's (empty when the
        car has no glasshouse above its shoulder), faired along the car: its sections near the glass's
        feet are only centimetres tall, and their points would otherwise jump from one to the next."""
        if not self.glass_span:
            return []
        rings = [self._ring(y, self.section(y, n_points, part="glass"))
                 for y in self.stations(n_stations, self.glass_span)]
        return self._fair(rings, FAIR_GLASS)

    @staticmethod
    def _fair(rings, passes):
        """Fair the skin along the car: each ring's points pulled halfway toward the mean of the same
        points on the rings either side, `passes` times; the first and last ring stay as they are, and
        every point keeps its ring's place along the car."""
        out = [list(r) for r in rings]
        for _ in range(passes):
            new = [out[0]]
            for a, b, c in zip(out, out[1:], out[2:]):
                new.append([(round(0.5 * p[0] + 0.25 * (q[0] + s[0]), 5), p[1], round(0.5 * p[2] + 0.25 * (q[2] + s[2]), 5))
                            for q, p, s in zip(a, b, c)])
            new.append(out[-1])
            out = new
        return out

    @staticmethod
    def bumps(rings, span=None):
        """The fairness of a skin: the largest distance (m) of a ring's point from the mean of the same
        point on the rings either side, over the rings inside span = (y0, y1) (all when empty)."""
        worst = 0.0
        for a, b, c in zip(rings, rings[1:], rings[2:]):
            y = b[0][1]
            if span and not span[0] <= y <= span[1]:
                continue
            for q, p, s in zip(a, b, c):
                worst = max(worst, abs(p[0] - (q[0] + s[0]) / 2), abs(p[2] - (q[2] + s[2]) / 2))
        return worst

    # --- the wheels ----------------------------------------------------------------------------

    def wheel_positions(self):
        """(x, y, z) of each wheel's centre: front right, front left, rear right, rear left."""
        out = []
        for wy, wz, _ in self.wheels:
            for side in (1, -1):
                out.append((side * self.track / 2, wy, wz))
        return out

    def arch_radius(self):
        return self.wheel_radius + WHEEL_GAP

    def well_x(self):
        """How far out from the centre line each wheel well's inner wall is."""
        return self.track / 2 - self.tyre_width / 2 - WELL_CLEARANCE


def body(name_or_ref):
    """The Body of an example car type's reference (a name) or of a reference given whole; by name, with
    the details traced from its blueprint when they have been written."""
    if not isinstance(name_or_ref, str):
        return Body(name_or_ref)
    path = REFS / f"{name_or_ref}_details.json"
    details = json.loads(path.read_text(encoding="utf-8")) if path.exists() else None
    return Body(load(name_or_ref), details)
