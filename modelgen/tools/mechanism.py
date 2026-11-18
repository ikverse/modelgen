"""Mechanical parts built straight from their engineering numbers, with no agents.

A mechanism is a list of parts (a spur gear, a ring gear, a shaft...) with their numbers and how
they connect: which gears mesh, which sit on a shaft, which part drives. `clean` checks a request
and fills in what it leaves out (every filled-in value is listed in `assumed`); `layout` works out
where every part goes so meshing teeth sit in each other's gaps from the first frame; `scripts`
writes the part, assembly, materials and motion scripts Blender runs, from the kit tools
(bpy_lib.kits). The same sheet goes to every agent when a part has no tool and the team builds it.

Lengths in a request and in the sheet are millimetres, angles degrees; the scripts are in metres.
The gear maths at the top uses nothing but `math`, so bpy_lib.kits loads this file inside Blender
and the tools and the checks agree on every number.
"""

import hashlib
import json
import math
from fractions import Fraction

# --- gear maths (any unit in, the same unit out) -------------------------------------------

def inv(a):
    """The involute function of an angle in radians."""
    return math.tan(a) - a


def solve_inv(value):
    """The angle whose involute is value (Newton's method)."""
    a = max(1e-6, (3 * value) ** (1 / 3))
    for _ in range(40):
        step = (inv(a) - value) / (math.tan(a) ** 2)
        a -= step
        if abs(step) < 1e-14:
            break
    return a


def transverse(module, pressure_deg=20.0, helix_deg=0.0):
    """(transverse module, transverse pressure angle in radians) of a gear cut with this normal
    module and pressure angle at this helix angle (a spur gear: the same numbers)."""
    beta = math.radians(helix_deg)
    alpha_n = math.radians(pressure_deg)
    return module / math.cos(beta), math.atan(math.tan(alpha_n) / math.cos(beta))


def circles(module, teeth, pressure_deg=20.0, helix_deg=0.0, shift=0.0, internal=False, shortening=0.0):
    """The circles of one gear: pitch, base, tip and root radius. An internal (ring) gear's tip is
    inside its pitch circle and its root outside. shortening: the tip shortening coefficient of a
    pair whose shifts change the centre distance (pair_geometry)."""
    mt, at = transverse(module, pressure_deg, helix_deg)
    rp = mt * teeth / 2
    rb = rp * math.cos(at)
    if internal:
        ra, rf = rp - module, rp + 1.25 * module
    else:
        ra, rf = rp + module * (1 + shift - shortening), rp - module * (1.25 - shift)
    return {"pitch": rp, "base": rb, "tip": ra, "root": rf, "transverse_module": mt, "transverse_pressure": at}


def virtual_teeth(teeth, helix_deg=0.0):
    return teeth / math.cos(math.radians(helix_deg)) ** 3


def auto_shift(teeth, mate=None, helix_deg=0.0):
    """Profile shift coefficients (x, x_mate) that keep a small pinion from being undercut (fewer
    than 17 teeth at 20 degrees). Where the mate can give up the same amount (x + x_mate = 0) the
    centre distance stays the textbook one; otherwise the mate keeps 0 and the centre distance
    grows a little (pair_geometry works it out)."""
    need = max(0.0, (17 - virtual_teeth(teeth, helix_deg)) / 17)
    if mate is None:
        return round(need, 3), None
    need_mate = max(0.0, (17 - virtual_teeth(mate, helix_deg)) / 17)
    if need == 0 and need_mate == 0:
        return 0.0, 0.0
    if need >= need_mate:
        x = round(need, 3)
        give = virtual_teeth(mate, helix_deg) >= 17 * (1 + x)
        return x, (-x if give else round(need_mate, 3))
    xm = round(need_mate, 3)
    give = virtual_teeth(teeth, helix_deg) >= 17 * (1 + xm)
    return (-xm if give else round(need, 3)), xm


def pair_geometry(module, teeth_a, teeth_b, pressure_deg=20.0, helix_deg=0.0, shift_a=0.0, shift_b=0.0,
                  internal=False):
    """The centre distance of a meshing pair, its working pressure angle (degrees) and the tip
    shortening coefficient both gears need when their shifts do not add up to 0. internal: gear b
    is a ring gear with gear a inside it."""
    mt, at = transverse(module, pressure_deg, helix_deg)
    if internal:
        return {"centre": mt * (teeth_b - teeth_a) / 2, "working_pressure_deg": math.degrees(at), "shortening": 0.0}
    a0 = mt * (teeth_a + teeth_b) / 2
    total = shift_a + shift_b
    if abs(total) < 1e-9:
        return {"centre": a0, "working_pressure_deg": math.degrees(at), "shortening": 0.0}
    aw = solve_inv(2 * math.tan(math.radians(pressure_deg)) * total / (teeth_a + teeth_b) + inv(at))
    a = a0 * math.cos(at) / math.cos(aw)
    return {"centre": a, "working_pressure_deg": math.degrees(aw), "shortening": max(0.0, total - (a - a0) / module)}


def _fillet(module, r_root, angle, sign, size):
    """A rounded corner where a flank meets the root circle: points going from the flank side
    (sign -1 for a left flank) onto the root."""
    if size <= 0:
        return [(r_root, angle)]
    out = []
    for t in (0.0, 0.29, 1.0):
        # a quarter circle from (r_root + size, angle) on the flank to (r_root, angle + sign * size /
        # r_root) on the root circle, away from the tooth
        a = math.radians(90 * t)
        out.append((r_root + size * (1 - math.sin(a)), angle + sign * (size / r_root) * (1 - math.cos(a))))
    return out


def tooth_profile(module, rp, rb, r_tip, r_root, half_pitch, alpha_ref, flank=5, tip=2, fillet=True):
    """One tooth centred on angle 0 as polar points (radius, angle) in increasing angle, from where
    its left flank leaves the root circle to where its right flank meets it again. half_pitch is the
    tooth's half thickness at the pitch circle as an angle. Flanks are true involutes of the base
    circle; below the base circle a flank runs straight down to the root (with a small fillet)."""
    inv_ref = inv(alpha_ref)

    def half(r):
        return half_pitch + inv_ref - inv(math.acos(min(1.0, rb / r)))

    r0 = max(rb, r_root)
    if half(r_tip) <= 0.02 * half_pitch:  # a pointed tooth: cut the tip where it is still a little wide
        lo, hi = r0, r_tip
        for _ in range(60):
            mid = (lo + hi) / 2
            lo, hi = (mid, hi) if half(mid) > 0.02 * half_pitch else (lo, mid)
        r_tip = lo
    radii = [r0 + (r_tip - r0) * k / flank for k in range(flank + 1)]
    left = [(r, -half(r)) for r in radii]
    if r_root < rb - 1e-12:
        # below the base circle: a straight flank down to the root, rounded into it
        size = min(0.25 * module, 0.45 * (rb - r_root)) if fillet else 0.0
        left = list(reversed(_fillet(module, r_root, -half(rb), -1, size))) + left
    tip_pts = [(r_tip, -half(r_tip) + 2 * half(r_tip) * k / tip) for k in range(1, tip)]
    right = [(r, -a) for r, a in reversed(left)]
    return left + tip_pts + right


def outline(module, teeth, pressure_deg=20.0, helix_deg=0.0, shift=0.0, backlash=0.0, internal=False,
            shortening=0.0, flank=5, tip=2, root=3):
    """The closed outline of a gear in its transverse plane: a list of (x, y) points in increasing
    angle with tooth 0 centred on angle 0 (on +X). An external gear's teeth point outward; a ring
    gear's point inward and tooth 0 is centred on angle 0 as well. backlash: the gap left between
    meshing teeth at the pitch circle (each gear of a pair takes half off its own teeth)."""
    c = circles(module, teeth, pressure_deg, helix_deg, shift, internal, shortening)
    rp, rb = c["pitch"], c["base"]
    pitch = 2 * math.pi / teeth
    alpha = c["transverse_pressure"]
    if internal:
        # a ring gear's space is shaped like an external gear's tooth: draw the spaces, centred half a
        # pitch round so the ring's teeth sit on angle 0
        half_space = math.pi / (2 * teeth) + backlash / (4 * rp)
        prof = tooth_profile(module, rp, rb, rp + 1.25 * module, rp - module, half_space, alpha, flank, tip)
        offset = pitch / 2
    else:
        half_tooth = math.pi / (2 * teeth) + 2 * shift * math.tan(math.radians(pressure_deg)) / teeth \
            - backlash / (4 * rp)
        prof = tooth_profile(module, rp, rb, c["tip"], c["root"], half_tooth, alpha, flank, tip)
        offset = 0.0
    r_root = prof[0][0]
    start, end = prof[0][1], prof[-1][1]
    pts = []
    for k in range(teeth):
        base = offset + k * pitch
        pts += [(r, base + a) for r, a in prof]
        gap = (base + pitch + start) - (base + end)
        pts += [(r_root, base + end + gap * j / root) for j in range(1, root)]
    return [(r * math.cos(a), r * math.sin(a)) for r, a in pts]


def rack_profile(module, teeth, pressure_deg=20.0, backlash=0.0, base=None):
    """A rack's outline in its own plane: (x, z) points along the toothed top from left to right
    (pitch line at z = 0, teeth pointing up), and the bar's bottom z. Teeth are centred on
    x = (k - (teeth - 1) / 2) * pitch."""
    p = math.pi * module
    t = math.tan(math.radians(pressure_deg))
    half_pitch = (p / 2 - backlash / 2) / 2
    top, root = module, -1.25 * module

    def half(z):
        return half_pitch - z * t

    pts = [(-(teeth / 2) * p, root)]
    for k in range(teeth):
        xc = (k - (teeth - 1) / 2) * p
        pts += [(xc - half(root), root), (xc - half(top), top), (xc + half(top), top), (xc + half(root), root)]
    pts.append(((teeth / 2) * p, root))
    return pts, root - (2.5 * module if base is None else base)


def bevel_numbers(module, teeth, mate_teeth, width=None):
    """A bevel gear meshing at 90 degrees: pitch radius, cone angle (degrees), cone distance and
    face width (a third of the cone distance at most, 10 modules at most)."""
    delta = math.atan2(teeth, mate_teeth)
    rp = module * teeth / 2
    cone = rp / math.sin(delta)
    face = width if width else min(cone / 3.2, 10 * module)
    return {"pitch": rp, "cone_deg": math.degrees(delta), "cone_distance": cone, "face": face,
            "virtual_teeth": teeth / math.cos(delta)}


def gear_pair_layout(module, teeth_a, teeth_b, pressure_deg=20.0, helix_deg=0.0, internal=False,
                     direction_deg=0.0, phase_a_deg=0.0, shift_a=None, shift_b=None):
    """Where the second gear of a meshing pair goes: the exact centre distance, its centre (x, y)
    with gear a at (0, 0) and the line of centres at direction_deg, the profile shifts both gears
    are cut with, and the angle gear b starts at so a tooth of one sits in a gap of the other.
    Every gear's tooth 0 is centred on its local angle 0; gear a starts at phase_a_deg. internal:
    gear b is a ring gear round gear a (b's centre is then at a's minus the centre distance along
    direction_deg). Also the speed ratio (b's turns per turn of a) and whether b turns the same way."""
    if internal:
        xa, xb = 0.0, 0.0
    else:
        auto_a, auto_b = auto_shift(teeth_a, teeth_b, helix_deg)
        xa = auto_a if shift_a is None else shift_a
        xb = auto_b if shift_b is None else shift_b
    geo = pair_geometry(module, teeth_a, teeth_b, pressure_deg, helix_deg, xa, xb, internal)
    psi = math.radians(direction_deg)
    a = geo["centre"]
    pa, pb = 2 * math.pi / teeth_a, 2 * math.pi / teeth_b
    phase_a = math.radians(phase_a_deg)
    delta = phase_a - psi  # how far gear a has turned from having a tooth at the contact
    if internal:
        centre = (-a * math.cos(psi), -a * math.sin(psi))
        # the contact is at psi as seen from both centres; the ring needs a gap there
        phase_b = psi - pb / 2 + delta * teeth_a / teeth_b
        same = True
    else:
        centre = (a * math.cos(psi), a * math.sin(psi))
        phase_b = psi + math.pi - pb / 2 - delta * teeth_a / teeth_b
        same = False
    phase_b = math.fmod(phase_b, pb)
    return {"centre_distance": a, "centre_b": centre, "phase_b_deg": math.degrees(phase_b), "shift_a": xa,
            "shift_b": xb, "working_pressure_deg": geo["working_pressure_deg"], "shortening": geo["shortening"],
            "ratio": teeth_a / teeth_b, "same_direction": same}


# --- the part types the tools build (GET /v1/parts) -----------------------------------------

def _f(unit, low, high, default=None, about=""):
    return {"type": "number", "unit": unit, "min": low, "max": high, "default": default, "about": about}


GEAR_FIELDS = {
    "module": _f("mm", 0.3, 50, None, "tooth size: pitch diameter = module x teeth (required)"),
    "teeth": {"type": "integer", "min": 6, "max": 400, "default": None, "about": "number of teeth (required)"},
    "width": _f("mm", 0.5, 2000, "8 x module", "face width along the axis"),
    "bore": _f("mm", 0, 2000, "about a quarter of the pitch diameter", "hole for the shaft (0: none)"),
    "pressure_angle": _f("deg", 14.5, 30, 20, "tooth flank angle"),
    "shift": _f("", -1, 1.2, "automatic", "profile shift coefficient; small pinions get one so they are not undercut"),
    "backlash": _f("mm", 0, 5, "0.05 x module", "gap left between meshing teeth"),
    "chamfer": _f("mm", 0, 10, "0.1 x module", "chamfer on the tooth tips"),
    "hub_diameter": _f("mm", 0, 3000, 0, "boss round the bore (0: none)"),
    "hub_length": _f("mm", 0, 3000, 0, "the boss's whole length along the axis (more than width)"),
    "web": {"type": "string", "enum": ["solid", "holes", "spokes"], "default": "solid",
            "about": "the body between rim and hub"},
    "holes": {"type": "integer", "min": 0, "max": 24, "default": "6 when web is holes", "about": "lightening holes"},
    "spokes": {"type": "integer", "min": 0, "max": 12, "default": "5 when web is spokes", "about": "spoke count"},
    "keyway": {"type": "boolean", "default": False, "about": "a key slot in the bore (a visible mark: the loop is a "
                                                              "whole turn)"},
    "colour": {"type": "string", "default": "the palette", "about": "#rrggbb for the explainer style"},
    "phase": _f("deg", -360, 360, 0, "where a tooth's middle starts, counterclockwise from the right as seen (the "
                                     "driving gear only; the others follow it in mesh)"),
}
LINK_FIELDS = {
    "meshes_with": {"type": "string or list", "about": "the part(s) this gear meshes with"},
    "on_shaft": {"type": "string", "about": "the shaft part this gear is fixed to (gears on one shaft turn together)"},
    "at": _f("deg", -360, 360, 0, "direction from the part it meshes with (0: to the right, 90: above)"),
}


def _without(d, *keys):
    return {k: v for k, v in d.items() if k not in keys}


TYPES = {
    "spur_gear": {"about": "straight-cut external gear", "fields": dict(GEAR_FIELDS)},
    "helical_gear": {"about": "external gear with teeth along a helix; meshing helical gears have opposite hands",
                     "fields": {**GEAR_FIELDS,
                                "helix_angle": _f("deg", 5, 45, 20, "tooth angle to the axis"),
                                "hand": {"type": "string", "enum": ["right", "left"], "default": "automatic",
                                         "about": "a meshing pair gets opposite hands by itself"},
                                "herringbone": {"type": "boolean", "default": False,
                                                "about": "two opposite helices meeting in the middle"}}},
    "ring_gear": {"about": "internal gear: teeth on the inside of a ring; a spur gear meshes inside it",
                  "fields": {**_without(GEAR_FIELDS, "bore", "hub_diameter", "hub_length", "web", "holes", "spokes",
                                        "keyway", "shift"),
                             "teeth": {"type": "integer", "min": 20, "max": 400, "default": None,
                                       "about": "number of teeth (required)"},
                             "rim_diameter": _f("mm", 1, 5000, "root diameter + 6 x module", "outside diameter")}},
    "rack": {"about": "straight toothed bar a spur gear drives back and forth",
             "fields": {"module": GEAR_FIELDS["module"],
                        "teeth": {"type": "integer", "min": 3, "max": 200, "default": 12, "about": "teeth along it"},
                        "width": GEAR_FIELDS["width"], "pressure_angle": GEAR_FIELDS["pressure_angle"],
                        "backlash": GEAR_FIELDS["backlash"],
                        "height": _f("mm", 0.5, 2000, "2.5 x module", "bar below the tooth roots"),
                        "colour": GEAR_FIELDS["colour"]}},
    "bevel_gear": {"about": "conical gear; two of them mesh with their shafts at 90 degrees",
                   "fields": {**_without(GEAR_FIELDS, "shift", "web", "holes", "spokes", "helix_angle"),
                              "width": _f("mm", 0.5, 2000, "a third of the cone distance", "face width")}},
    "shaft": {"about": "round shaft the gears on it turn with",
              "fields": {"diameter": _f("mm", 0.5, 2000, "the bore of the first gear on it", "diameter"),
                         "length": _f("mm", 1, 10000, "the gears on it plus some each side", "length"),
                         "keyway": {"type": "boolean", "default": False, "about": "a key slot along it"},
                         "chamfer": _f("mm", 0, 50, "0.1 x diameter", "chamfer on both ends"),
                         "colour": GEAR_FIELDS["colour"]}},
    "compressor_wheel": {"about": "a turbocharger's compressor wheel: blades curving from the nose (inducer) to "
                                  "the rim, splitter blades between; faces the front of the shaft",
                         "fields": {"diameter": _f("mm", 10, 2000, None, "rim (exducer) diameter (required)"),
                                    "blades": {"type": "integer", "min": 3, "max": 20, "default": 6,
                                               "about": "full blades"},
                                    "splitters": {"type": "boolean", "default": True,
                                                  "about": "a shorter blade between each two"},
                                    "length": _f("mm", 3, 2000, "0.42 x diameter", "along the axis"),
                                    "inducer_diameter": _f("mm", 3, 2000, "0.7 x diameter", "blade tips at the nose"),
                                    "backsweep": _f("deg", 0, 60, 30, "blade angle back at the rim"),
                                    "bore": _f("mm", 0, 500, "the shaft's", "hole for the shaft"),
                                    "colour": GEAR_FIELDS["colour"]}},
    "turbine_wheel": {"about": "a turbocharger's radial turbine wheel: exhaust in at the rim, out along the "
                               "axis; faces the back of the shaft",
                      "fields": {"diameter": _f("mm", 10, 2000, None, "rim diameter (required)"),
                                 "blades": {"type": "integer", "min": 5, "max": 20, "default": 11, "about": "blades"},
                                 "length": _f("mm", 3, 2000, "0.38 x diameter", "along the axis"),
                                 "exducer_diameter": _f("mm", 3, 2000, "0.86 x diameter", "blade tips at the nose"),
                                 "bore": _f("mm", 0, 500, "the shaft's", "hole for the shaft"),
                                 "colour": GEAR_FIELDS["colour"]}},
    "volute": {"about": "the snail housing round a compressor or turbine wheel: a scroll that grows round the "
                        "wheel, an axial pipe and a tangential pipe with flanges",
               "fields": {"wall": _f("mm", 0.3, 100, "3.5 percent of the wheel", "wall thickness"),
                          "flanges": {"type": "boolean", "default": True, "about": "flanges on the pipes"},
                          "colour": GEAR_FIELDS["colour"]}},
    "bearing": {"about": "a deep-groove ball bearing on a shaft: races, balls and cage (the balls run round at "
                         "the cage's speed)",
                "fields": {"bore": _f("mm", 1, 1000, "the shaft's diameter", "inner diameter"),
                           "outer_diameter": _f("mm", 3, 2000, "2.6 x bore", "outer diameter"),
                           "width": _f("mm", 1, 500, "0.3 x outer diameter", "width"),
                           "balls": {"type": "integer", "min": 5, "max": 40, "default": "as many as fit",
                                     "about": "balls"},
                           "colour": GEAR_FIELDS["colour"]}},
    "centre_housing": {"about": "a turbocharger's centre (bearing) housing: the cast body between the two volutes "
                                "that carries the bearings, an oil inlet on top and a drain below; it houses the "
                                "shaft (houses: the shaft's name) and stands still",
                       "fields": {"wall": _f("mm", 0.5, 100, "0.12 x the bearings' outer diameter",
                                             "wall round the bearings"),
                                  "outer_diameter": _f("mm", 3, 3000, "0.7 x the compressor wheel (at least the "
                                                       "bearings and the wall)", "body diameter"),
                                  "oil_ports": {"type": "boolean", "default": True,
                                                "about": "the oil inlet on top and the drain below"},
                                  "colour": GEAR_FIELDS["colour"]}},
    "crankshaft": {"about": "an engine's crankshaft: main journals on its axis, a crankpin per throw between two webs "
                            "with counterweights; it turns, and the rods on it drive the pistons",
                   "fields": {"stroke": _f("mm", 5, 2000, "the piston's bore", "the piston's travel (twice the "
                                           "crank radius)"),
                              "crankpin_diameter": _f("mm", 2, 1000, "0.55 x stroke", "crankpin diameter"),
                              "journal_diameter": _f("mm", 2, 1000, "0.65 x stroke", "main journal diameter"),
                              "throw_angles": {"type": "array", "items": "number", "default": "an even firing order",
                                               "about": "each throw's angle in degrees, front to back"},
                              "counterweights": {"type": "boolean", "default": True,
                                                 "about": "counterweights opposite the crankpins"},
                              "colour": GEAR_FIELDS["colour"]}},
    "connecting_rod": {"about": "joins a crankpin (big end) to a piston's gudgeon pin (small end); on_crank: its "
                                "crankshaft",
                       "fields": {"length": _f("mm", 10, 5000, "1.7 x stroke", "centre to centre"),
                                  "throw": {"type": "integer", "min": 0, "max": 11, "default": "the next free one",
                                            "about": "which throw it is on, 0 at the front"},
                                  "colour": GEAR_FIELDS["colour"]}},
    "piston": {"about": "a piston with its ring grooves, skirt and gudgeon pin; on_rod: its connecting rod",
               "fields": {"bore": _f("mm", 5, 2000, "the crank's stroke", "cylinder bore (the piston's diameter)"),
                          "compression_height": _f("mm", 2, 1000, "0.36 x bore", "pin centre to crown"),
                          "pin_diameter": _f("mm", 1, 500, "0.25 x bore", "gudgeon pin diameter"),
                          "skirt_length": _f("mm", 1, 1000, "0.33 x bore", "skirt below the pin centre"),
                          "crown": {"enum": ["flat", "dish", "dome"], "default": "flat", "about": "crown shape"},
                          "colour": GEAR_FIELDS["colour"]}},
    "piston_rings": {"about": "the rings in a piston's grooves (two compression rings and an oil ring); on_piston: "
                              "its piston",
                     "fields": {"colour": GEAR_FIELDS["colour"]}},
    "cylinder": {"about": "the cylinder a piston runs in (houses: the piston): a liner, or a block section with a "
                          "water jacket; it stands still",
                 "fields": {"style": {"enum": ["liner", "block"], "default": "liner", "about": "liner or block"},
                            "wall": _f("mm", 0.5, 200, "0.08 x bore", "wall round the bore"),
                            "colour": GEAR_FIELDS["colour"]}},
    # --- stage 4 -----------------------------------------------------------------------------------
    "bolt": {"about": "a hexagon-head bolt with an ISO metric thread; stands still (a nut screws on it)",
             "fields": {"diameter": _f("mm", 3, 36, 10, "nominal diameter (M size)"),
                        "length": _f("mm", 5, 400, "4 x diameter", "under the head to the tip"),
                        "pitch": _f("mm", 0.2, 6, "ISO coarse for the diameter", "thread pitch"),
                        "colour": GEAR_FIELDS["colour"]}},
    "nut": {"about": "a hexagon nut on a bolt (on_bolt); in motion it screws off and back on",
            "fields": {"colour": GEAR_FIELDS["colour"]}},
    "washer": {"about": "a plain washer on a bolt (on_bolt), under the nut", "fields": {"colour": GEAR_FIELDS["colour"]}},
    "spring": {"about": "a helical spring of round wire; alone it is squeezed and let go, on a valve (on_valve) it "
                        "closes the valve",
               "fields": {"kind": {"enum": ["compression", "extension"], "default": "compression", "about": "kind"},
                          "wire_diameter": _f("mm", 0.2, 50, "0.12 x mean diameter", "wire diameter"),
                          "mean_diameter": _f("mm", 2, 500, 24, "coil diameter through the wire's middle"),
                          "free_length": _f("mm", 3, 2000, "2.5 x mean diameter", "length unloaded"),
                          "coils": _f("turns", 2, 60, 8, "turns of wire"),
                          "squeeze": _f("share", 0.05, 0.9, 0.3, "how much shorter it gets when squeezed"),
                          "colour": GEAR_FIELDS["colour"]}},
    "pulley": {"about": "a V-belt pulley; two joined by a belt turn the same way at the ratio of their diameters",
               "fields": {"datum_diameter": _f("mm", 20, 2000, None, "the belt's pitch diameter (required)"),
                          "grooves": {"type": "integer", "min": 1, "max": 8, "default": 1, "about": "belts side by side"},
                          "section": {"enum": ["Z", "A", "B"], "default": "A", "about": "V-belt section"},
                          "bore": _f("mm", 0, 500, "0.2 x datum diameter", "shaft hole"),
                          "colour": GEAR_FIELDS["colour"]}},
    "belt": {"about": "a V-belt round two pulleys (on: [driver, driven])",
             "fields": {"centre_distance": _f("mm", 10, 10000, "the two diameters added", "pulley centre to centre"),
                        "colour": GEAR_FIELDS["colour"]}},
    "sprocket": {"about": "a roller-chain sprocket; two joined by a chain turn the same way at their tooth ratio",
                 "fields": {"teeth": {"type": "integer", "min": 9, "max": 120, "default": None,
                                      "about": "teeth (required)"},
                            "pitch": _f("mm", 4, 80, 12.7, "the chain's pitch (12.7: an 08B / ANSI 40 chain)"),
                            "bore": _f("mm", 0, 500, "0.25 x pitch diameter", "shaft hole"),
                            "colour": GEAR_FIELDS["colour"]}},
    "chain": {"about": "a roller chain round two sprockets (on: [driver, driven]); its links run round them",
              "fields": {"centre_distance": _f("mm", 10, 10000, "35 pitches", "sprocket centre to centre (set to "
                                               "the nearest a whole, even number of links allows)"),
                         "colour": GEAR_FIELDS["colour"]}},
    "cam": {"about": "a disc cam; its follower (or valve) rises, dwells, falls and rests once a turn",
            "fields": {"base_diameter": _f("mm", 5, 1000, None, "the cam's smallest diameter (required)"),
                       "lift": _f("mm", 0.5, 500, None, "how far the follower rises (required)"),
                       "rise": _f("deg", 20, 300, 120, "turn over which it rises (cycloidal)"),
                       "dwell": _f("deg", 0, 300, 60, "turn it stays up"),
                       "fall": _f("deg", 20, 300, 120, "turn over which it falls"),
                       "width": _f("mm", 2, 500, "0.3 x base diameter", "thickness"),
                       "bore": _f("mm", 0, 500, "0.25 x base diameter", "shaft hole"),
                       "colour": GEAR_FIELDS["colour"]}},
    "follower": {"about": "a translating cam follower (on_cam), above the cam: a roller or a flat face",
                 "fields": {"kind": {"enum": ["roller", "flat"], "default": "roller", "about": "contact"},
                            "roller_diameter": _f("mm", 2, 500, "0.4 x the cam's base diameter", "roller"),
                            "colour": GEAR_FIELDS["colour"]}},
    "valve": {"about": "a poppet valve with its bucket tappet, opened by a cam above it (on_cam)",
              "fields": {"head_diameter": _f("mm", 5, 500, None, "head diameter (required)"),
                         "stem_diameter": _f("mm", 1, 100, "0.2 x head diameter", "stem diameter"),
                         "length": _f("mm", 10, 1000, "3.2 x head diameter", "tip to head face"),
                         "colour": GEAR_FIELDS["colour"]}},
    "planetary_set": {"about": "an epicyclic gear set: sun, planets on a carrier, ring; made as its own parts "
                               "<name>_sun, <name>_planet1.., <name>_ring, <name>_carrier",
                      "fields": {"module": GEAR_FIELDS["module"],
                                 "sun_teeth": {"type": "integer", "min": 9, "max": 200, "default": None,
                                               "about": "sun teeth (required)"},
                                 "planet_teeth": {"type": "integer", "min": 9, "max": 200, "default": None,
                                                  "about": "planet teeth (required)"},
                                 "planets": {"type": "integer", "min": 2, "max": 8, "default": 3, "about": "planets"},
                                 "fixed": {"enum": ["ring", "sun", "carrier"], "default": "ring",
                                           "about": "the member held still"},
                                 "input": {"enum": ["sun", "carrier", "ring"], "default": "sun",
                                           "about": "the member the motion drives"},
                                 "width": _f("mm", 0.5, 2000, "8 x module", "face width"),
                                 "colour": GEAR_FIELDS["colour"]}},
    "worm": {"about": "a worm (a screw that drives a worm wheel at right angles)",
             "fields": {"module": _f("mm", 0.3, 50, None, "axial module (required)"),
                        "starts": {"type": "integer", "min": 1, "max": 6, "default": 1, "about": "threads"},
                        "pitch_diameter": _f("mm", 3, 2000, "10 x module", "pitch diameter"),
                        "length": _f("mm", 5, 5000, "(11 + 0.06 x wheel teeth) x module", "thread length"),
                        "hand": {"enum": ["right", "left"], "default": "right", "about": "thread hand"},
                        "colour": GEAR_FIELDS["colour"]}},
    "worm_wheel": {"about": "the gear a worm drives (meshes_with: the worm); turns starts / teeth per worm turn",
                   "fields": {"teeth": {"type": "integer", "min": 18, "max": 200, "default": None,
                                        "about": "teeth (required)"},
                              "width": _f("mm", 1, 2000, "0.6 x the worm's pitch diameter", "face width"),
                              "bore": _f("mm", 0, 2000, "0.25 x pitch diameter", "shaft hole"),
                              "colour": GEAR_FIELDS["colour"]}},
}
TYPES["carrier"] = {"about": "a planetary set's planet carrier (made by planetary_set)",
                    "fields": {"colour": GEAR_FIELDS["colour"]}}
ENGINE = ("crankshaft", "connecting_rod", "piston", "piston_rings", "cylinder")
STAGE4 = ("bolt", "nut", "washer", "spring", "pulley", "belt", "sprocket", "chain", "cam", "follower", "valve",
          "worm", "worm_wheel", "carrier")
LINK_FIELDS["on_bolt"] = {"type": "string", "about": "a nut or washer: its bolt"}
LINK_FIELDS["on"] = {"type": "array", "about": "a belt or chain: the two pulleys or sprockets it runs round, the "
                                                "driver first"}
LINK_FIELDS["on_cam"] = {"type": "string", "about": "a follower or valve: its cam"}
LINK_FIELDS["on_valve"] = {"type": "string", "about": "a spring: the valve it closes"}
LINK_FIELDS["on_crank"] = {"type": "string", "about": "a connecting rod: its crankshaft"}
LINK_FIELDS["on_rod"] = {"type": "string", "about": "a piston: its connecting rod"}
LINK_FIELDS["on_piston"] = {"type": "string", "about": "piston rings: their piston"}
ENGINE_LINKS = ("crank", "pin", "ring", "liner")
GEARS = ("spur_gear", "helical_gear", "ring_gear", "bevel_gear", "rack")
EXTERNAL = ("spur_gear", "helical_gear")
WHEELS = ("compressor_wheel", "turbine_wheel")
ON_SHAFT = ("spur_gear", "helical_gear", "bevel_gear", "compressor_wheel", "turbine_wheel", "bearing", "pulley",
            "sprocket", "cam", "worm", "worm_wheel")
LINK_FIELDS["houses"] = {"type": "string", "about": "a volute: the wheel it houses; a centre_housing: the shaft "
                                                    "it carries; a cylinder: its piston"}
STILL = ("volute", "centre_housing", "cylinder")  # housings: they stand still round the parts that move
PALETTE = ["#e4572e", "#2e86ab", "#f2c14e", "#4cb944", "#8e5bd6", "#f28c28", "#17bebb", "#e84a8a"]
DEFAULT_RPM = 20
MIN_TEETH_DIFFERENCE = 10  # a spur gear inside a ring gear needs this many fewer teeth, or the tips clash


def catalogue():
    """Every part type the tools build, with its fields, units, ranges and defaults (GET /v1/parts)."""
    return {"units": {"length": "mm", "angle": "deg", "speed": "rpm"},
            "types": {name: {"about": t["about"], "fields": t["fields"]} for name, t in TYPES.items()},
            "links": LINK_FIELDS,
            "mechanism": {"parts": "list of parts, each {name, type, ...its fields, ...links}",
                          "driver": "the part that drives (default: the first gear)",
                          "rpm": {"min": 0.5, "max": 600, "default": DEFAULT_RPM, "about": "the driver's speed"},
                          "direction": {"enum": ["clockwise", "counterclockwise"], "default": "clockwise",
                                        "about": "the driver's turn as seen from the front"}},
            "palette": PALETTE}


# --- checking a request ----------------------------------------------------------------------

class MechanismError(ValueError):
    """A mechanism that cannot be built, with the field named."""


def _num(part, key, low, high, kind=float, where=""):
    value = part.get(key)
    try:
        out = kind(value)
    except (TypeError, ValueError):
        raise MechanismError(f"{where}{key} must be a number")
    if kind is int and float(value) != out:
        raise MechanismError(f"{where}{key} must be a whole number")
    if not low <= out <= high:
        raise MechanismError(f"{where}{key} must be between {low} and {high}")
    return out


def _names(value):
    if value in (None, "", []):
        return []
    return [str(v) for v in (value if isinstance(value, list) else [value])]


def _hex(value, where):
    text = str(value).strip().lower()
    if len(text) == 7 and text[0] == "#" and all(ch in "0123456789abcdef" for ch in text[1:]):
        return text
    raise MechanismError(f"{where}colour must be #rrggbb")


def _part(raw, i, assumed):
    if not isinstance(raw, dict):
        raise MechanismError(f"mechanism.parts[{i}] must be an object")
    name = str(raw.get("name") or "").strip()
    kind = str(raw.get("type") or "").strip().lower()
    label = name or f"parts[{i}]"
    if not kind:
        raise MechanismError(f"part {label} has no type (one of {', '.join(TYPES)})")
    if kind not in TYPES:
        raise MechanismError(f"part {label}: unknown type {kind!r} (the tools build {', '.join(TYPES)})")
    if not name:
        name = f"{kind}_{i + 1}"
        assumed.append(f"parts[{i}].name = {name}")
    if not all(ch.isalnum() or ch == "_" for ch in name) or len(name) > 40:
        raise MechanismError(f"part {name}: a name is letters, digits and _ only (up to 40)")
    where = f"part {name}: "
    fields = TYPES[kind]["fields"]
    unknown = set(raw) - set(fields) - set(LINK_FIELDS) - {"name", "type", "driver", "_set"}
    if unknown:
        raise MechanismError(f"{where}unknown field(s) {', '.join(sorted(unknown))} for a {kind}")
    out = {"name": name, "type": kind}

    def take(key, default, low, high, kind_=float, note=None):
        if raw.get(key) not in (None, ""):
            out[key] = _num(raw, key, low, high, kind_, where)
        else:
            out[key] = default
            if default not in (None, 0, 0.0, False):
                assumed.append(f"{name}.{key} = {round(default, 3) if isinstance(default, float) else default}"
                               + (f" ({note})" if note else ""))

    if kind == "shaft":
        take("diameter", None, 0.5, 2000)
        take("length", None, 1, 10000)
        out["keyway"] = bool(raw.get("keyway", False))
        take("chamfer", None, 0, 50)
    elif kind in WHEELS:
        if raw.get("diameter") in (None, ""):
            raise MechanismError(f"wheel {name} has no diameter")
        d = out["diameter"] = _num(raw, "diameter", 10, 2000, float, where)
        turbine = kind == "turbine_wheel"
        take("blades", 11 if turbine else 6, 3, 20, int)
        take("length", round((0.38 if turbine else 0.42) * d, 2), 3, 2000,
             note=f"{0.38 if turbine else 0.42} x diameter")
        tip_key = "exducer_diameter" if turbine else "inducer_diameter"
        take(tip_key, round((0.86 if turbine else 0.7) * d, 2), 3, 2000)
        if out[tip_key] >= d:
            raise MechanismError(f"{where}{tip_key} must be under the diameter ({d:g} mm)")
        if not turbine:
            out["splitters"] = bool(raw.get("splitters", True))
            take("backsweep", 30.0, 0, 60)
        take("bore", None, 0, 500)
    elif kind == "volute":
        take("wall", None, 0.3, 100)
        out["flanges"] = bool(raw.get("flanges", True))
        out["houses"] = str(raw.get("houses") or "") or None
        if not out["houses"]:
            raise MechanismError(f"volute {name} houses no wheel: give houses, the wheel's name")
    elif kind == "centre_housing":
        take("wall", None, 0.5, 100)
        take("outer_diameter", None, 3, 3000)
        out["oil_ports"] = bool(raw.get("oil_ports", True))
        out["houses"] = str(raw.get("houses") or "") or None
        if not out["houses"]:
            raise MechanismError(f"centre_housing {name} houses no shaft: give houses, the shaft's name")
    elif kind in ENGINE:
        _engine_part(raw, kind, name, where, out, take)
    elif kind in STAGE4:
        _stage4_part(raw, kind, name, where, out, take)
    elif kind == "bearing":
        take("bore", None, 1, 1000)
        take("outer_diameter", None, 3, 2000)
        take("width", None, 1, 500)
        take("balls", None, 5, 40, int)
    else:
        for key in ("module", "teeth"):
            if raw.get(key) in (None, "") and not (kind == "rack" and key == "teeth"):
                what = {"module": "module (the tooth size in mm)", "teeth": "tooth count"}[key]
                raise MechanismError(f"gear {name} has no {what}")
        out["module"] = _num(raw, "module", 0.3, 50, float, where)
        m = out["module"]
        tlow = 20 if kind == "ring_gear" else 3 if kind == "rack" else 6
        take("teeth", 12 if kind == "rack" else None, tlow, 400, int)
        take("pressure_angle", 20.0, 14.5, 30)
        take("backlash", round(0.05 * m, 4), 0, 5, note="0.05 x module")
        if kind == "bevel_gear":
            take("width", None, 0.5, 2000)
        else:
            take("width", round(8 * m, 3), 0.5, 2000, note="8 x module")
        if kind in EXTERNAL or kind == "bevel_gear":
            take("bore", None, 0, 2000)
            take("chamfer", round(0.1 * m, 4), 0, 10, note="0.1 x module")
            take("hub_diameter", 0.0, 0, 3000)
            take("hub_length", 0.0, 0, 3000)
            out["keyway"] = bool(raw.get("keyway", False))
        if kind in EXTERNAL:
            take("phase", 0.0, -360, 360)
            take("shift", None, -1, 1.2)
            web = str(raw.get("web") or "solid").lower()
            if web not in ("solid", "holes", "spokes"):
                raise MechanismError(f"{where}web must be solid, holes or spokes")
            out["web"] = web
            take("holes", 6 if web == "holes" else 0, 0, 24, int)
            take("spokes", 5 if web == "spokes" else 0, 0, 12, int)
        if kind == "helical_gear":
            take("helix_angle", 20.0, 5, 45)
            hand = raw.get("hand")
            if hand not in (None, "", "right", "left"):
                raise MechanismError(f"{where}hand must be right or left")
            out["hand"] = hand or None
            out["herringbone"] = bool(raw.get("herringbone", False))
        if kind == "ring_gear":
            take("chamfer", round(0.1 * m, 4), 0, 10, note="0.1 x module")
            take("rim_diameter", None, 1, 5000)
        if kind == "rack":
            take("height", round(2.5 * m, 3), 0.5, 2000, note="2.5 x module")
    if raw.get("colour") not in (None, ""):
        out["colour"] = _hex(raw["colour"], where)
    out["meshes_with"] = _names(raw.get("meshes_with"))
    out["on_shaft"] = str(raw.get("on_shaft") or "") or None
    out["driver"] = bool(raw.get("driver"))
    if raw.get("at") not in (None, ""):
        out["at"] = _num(raw, "at", -360, 360, float, where)
    return out


def _engine_part(raw, kind, name, where, out, take):
    """An engine part's own fields; the sizes that follow from its neighbours are filled in later."""
    def link(key, what):
        out[key] = str(raw.get(key) or "") or None
        if not out[key]:
            raise MechanismError(f"{kind} {name} has no {key}: give {what}")

    if kind == "crankshaft":
        take("stroke", None, 5, 2000)
        take("crankpin_diameter", None, 2, 1000)
        take("journal_diameter", None, 2, 1000)
        angles = raw.get("throw_angles")
        if angles not in (None, "", []):
            if not isinstance(angles, list) or not all(isinstance(a, (int, float)) for a in angles):
                raise MechanismError(f"{where}throw_angles must be a list of angles in degrees")
            if len(angles) > 12:
                raise MechanismError(f"{where}throw_angles: at most 12 throws")
            out["throw_angles"] = [float(a) % 360 for a in angles]
        else:
            out["throw_angles"] = None
        out["counterweights"] = bool(raw.get("counterweights", True))
    elif kind == "connecting_rod":
        take("length", None, 10, 5000)
        take("throw", None, 0, 11, int)
        link("on_crank", "the crankshaft's name")
    elif kind == "piston":
        take("bore", None, 5, 2000)
        take("compression_height", None, 2, 1000)
        take("pin_diameter", None, 1, 500)
        take("skirt_length", None, 1, 1000)
        crown = str(raw.get("crown") or "flat").lower()
        if crown not in ("flat", "dish", "dome"):
            raise MechanismError(f"{where}crown must be flat, dish or dome")
        out["crown"] = crown
        link("on_rod", "the connecting rod's name")
    elif kind == "piston_rings":
        link("on_piston", "the piston's name")
    elif kind == "cylinder":
        style = str(raw.get("style") or "liner").lower()
        if style not in ("liner", "block"):
            raise MechanismError(f"{where}style must be liner or block")
        out["style"] = style
        take("wall", None, 0.5, 200)
        link("houses", "the piston's name")


def _stage4_part(raw, kind, name, where, out, take):
    """The fields of a fastener, spring, belt or chain drive, cam train or worm drive part."""
    def link(key, what, many=False):
        value = raw.get(key)
        if many:
            out[key] = _names(value)
            if len(out[key]) != 2:
                raise MechanismError(f"{kind} {name}: give {key}: [{what}], two names")
        else:
            out[key] = str(value or "") or None
            if not out[key]:
                raise MechanismError(f"{kind} {name} has no {key}: give {what}")

    def choice(key, options, default):
        value = str(raw.get(key) or default)
        if value not in options:
            raise MechanismError(f"{where}{key} must be one of {', '.join(options)}")
        out[key] = value

    def required(key, low, high, what, kind_=float):
        if raw.get(key) in (None, ""):
            raise MechanismError(f"{kind} {name} has no {key} ({what})")
        out[key] = _num(raw, key, low, high, kind_, where)

    if kind == "bolt":
        take("diameter", 10.0, 3, 36)
        take("length", None, 5, 400)
        take("pitch", None, 0.2, 6)
    elif kind in ("nut", "washer"):
        link("on_bolt", "the bolt's name")
    elif kind == "spring":
        choice("kind", ("compression", "extension"), "compression")
        take("mean_diameter", None, 2, 500)
        take("wire_diameter", None, 0.2, 50)
        take("free_length", None, 3, 2000)
        take("coils", None, 2, 60)
        take("squeeze", None, 0.05, 0.9)
        out["on_valve"] = str(raw.get("on_valve") or "") or None
    elif kind == "pulley":
        required("datum_diameter", 20, 2000, "the belt's pitch diameter in mm")
        take("grooves", 1, 1, 8, int)
        choice("section", ("Z", "A", "B"), "A")
        take("bore", None, 0, 500)
    elif kind == "sprocket":
        required("teeth", 9, 120, "how many teeth", int)
        take("pitch", 12.7, 4, 80)
        take("bore", None, 0, 500)
    elif kind in ("belt", "chain"):
        take("centre_distance", None, 10, 10000)
        link("on", "the driver's and the driven's names", many=True)
    elif kind == "cam":
        required("base_diameter", 5, 1000, "the cam's smallest diameter in mm")
        required("lift", 0.5, 500, "how far the follower rises, mm")
        take("rise", 120.0, 20, 300)
        take("dwell", 60.0, 0, 300)
        take("fall", 120.0, 20, 300)
        take("width", None, 2, 500)
        take("bore", None, 0, 500)
        if out["rise"] + out["dwell"] + out["fall"] > 360:
            raise MechanismError(f"{where}rise + dwell + fall must be at most 360 degrees")
    elif kind == "follower":
        choice("kind", ("roller", "flat"), "roller")
        take("roller_diameter", None, 2, 500)
        link("on_cam", "the cam's name")
    elif kind == "valve":
        required("head_diameter", 5, 500, "the head's diameter in mm")
        take("stem_diameter", None, 1, 100)
        take("length", None, 10, 1000)
        link("on_cam", "the cam's name")
    elif kind == "worm":
        required("module", 0.3, 50, "the axial module in mm")
        take("starts", 1, 1, 6, int)
        take("pitch_diameter", None, 3, 2000)
        take("length", None, 5, 5000)
        choice("hand", ("right", "left"), "right")
    elif kind == "worm_wheel":
        required("teeth", 18, 200, "how many teeth", int)
        take("width", None, 1, 2000)
        take("bore", None, 0, 2000)
    elif kind == "carrier":
        if not raw.get("_set"):
            raise MechanismError(f"part {name}: a carrier comes with a planetary_set; ask for the planetary_set")
        out["_set"] = raw["_set"]


def _expand_planetary(items, assumed):
    """Each planetary_set entry as its own parts: sun, planets (on the carrier's pins, spaced evenly),
    ring and carrier. Returns the new list of raw parts and the sets' records."""
    out, sets = [], []
    for i, raw in enumerate(items):
        if not isinstance(raw, dict) or str(raw.get("type") or "").lower() != "planetary_set":
            out.append(raw)
            continue
        name = str(raw.get("name") or f"planetary_{i + 1}")
        where = f"part {name}: "
        unknown = set(raw) - set(TYPES["planetary_set"]["fields"]) - {"name", "type", "driver"}
        if unknown:
            raise MechanismError(f"{where}unknown field(s) {', '.join(sorted(unknown))} for a planetary_set")
        for key in ("module", "sun_teeth", "planet_teeth"):
            if raw.get(key) in (None, ""):
                raise MechanismError(f"planetary_set {name} has no {key}")
        m = _num(raw, "module", 0.3, 50, float, where)
        zs = _num(raw, "sun_teeth", 9, 200, int, where)
        zp = _num(raw, "planet_teeth", 9, 200, int, where)
        n = _num(raw, "planets", 2, 8, int, where) if raw.get("planets") not in (None, "") else 3
        zr = zs + 2 * zp
        if (zs + zr) % n:
            raise MechanismError(f"planetary_set {name}: {n} planets cannot be spaced evenly (sun + ring teeth, "
                                 f"{zs + zr}, must divide by the number of planets)")
        if (zs + zp) * m * math.sin(math.pi / n) <= m * (zp + 2) + 0.5:  # neighbouring planets' tips clear
            raise MechanismError(f"planetary_set {name}: {n} planets of {zp} teeth do not fit round the sun")
        fixed = str(raw.get("fixed") or "ring")
        inp = str(raw.get("input") or "sun")
        if fixed not in ("ring", "sun", "carrier") or inp not in ("sun", "carrier", "ring") or fixed == inp:
            raise MechanismError(f"planetary_set {name}: fixed and input must be two different of sun, carrier, ring")
        width = _num(raw, "width", 0.5, 2000, float, where) if raw.get("width") not in (None, "") else 8 * m
        pin = round(max(1.0, 0.3 * m * zp), 2)
        colour = raw.get("colour")
        common = {"module": m, "width": width, **({"colour": colour} if colour else {})}
        planets = [f"{name}_planet{k + 1}" for k in range(n)]
        out.append({"name": f"{name}_sun", "type": "spur_gear", "teeth": zs, "meshes_with": planets, **common})
        for k, pn in enumerate(planets):
            out.append({"name": pn, "type": "spur_gear", "teeth": zp, "bore": pin + 0.2,
                        "at": (90 + 360 * k / n + 180) % 360 - 180, **common})
        out.append({"name": f"{name}_ring", "type": "ring_gear", "teeth": zr, "meshes_with": planets[0], **common})
        out.append({"name": f"{name}_carrier", "type": "carrier", "_set": name})
        sets.append({"name": name, "sun": f"{name}_sun", "planets": planets, "ring": f"{name}_ring",
                     "carrier": f"{name}_carrier", "zs": zs, "zp": zp, "zr": zr, "fixed": fixed, "input": inp,
                     "radius": m * (zs + zp) / 2, "pin": pin, "width": width, "driver": bool(raw.get("driver"))})
        assumed.append(f"{name}: ring {zr} teeth (sun + 2 planets), {n} planets, {fixed} held, {inp} driven")
    return out, sets


def planetary_rates(ps):
    """Each member's turns per turn of the input (Fractions): Willis's equation with the fixed member
    at rest; each planet's absolute turn rate as well."""
    zs, zr, zp = ps["zs"], ps["zr"], ps["zp"]
    # (w_s - w_c) zs = -(w_r - w_c) zr  and  (w_p - w_c) zp = -(w_s - w_c) zs
    w = {ps["fixed"]: Fraction(0), ps["input"]: Fraction(1)}
    if "carrier" not in w:  # sun and ring known
        ws, wr = w["sun"], w["ring"]
        w["carrier"] = (ws * zs + wr * zr) / (zs + zr)
    elif "sun" not in w:
        wc, wr = w["carrier"], w["ring"]
        w["sun"] = wc - (wr - wc) * Fraction(zr, zs)
    else:
        ws, wc = w["sun"], w["carrier"]
        w["ring"] = wc - (ws - wc) * Fraction(zs, zr)
    w["planet"] = w["carrier"] - (w["sun"] - w["carrier"]) * Fraction(zs, zp)
    return w


def clean(raw):
    """A mechanism request checked and completed: (sheet, assumed). Raises MechanismError with the
    field named when it cannot be built."""
    if not isinstance(raw, dict):
        raise MechanismError("mechanism must be an object with a list of parts")
    items = raw.get("parts")
    if not isinstance(items, list) or not items:
        raise MechanismError("mechanism.parts must be a list of at least one part")
    assumed = []
    items, sets = _expand_planetary(items, assumed)
    if len(items) > 24:
        raise MechanismError("mechanism.parts: at most 24 parts in one mechanism (a planetary set counts as its sun, "
                             "planets, ring and carrier)")
    parts = [_part(p, i, assumed) for i, p in enumerate(items)]
    by = {}
    for p in parts:
        if p["name"] in by:
            raise MechanismError(f"two parts are named {p['name']}")
        by[p["name"]] = p
    links = []
    seen = set()
    for p in parts:
        for other in p["meshes_with"]:
            if other == p["name"]:
                raise MechanismError(f"gear {p['name']} cannot mesh with itself")
            if other not in by:
                raise MechanismError(f"gear {p['name']} meshes_with {other}, but there is no part named {other}")
            key = tuple(sorted((p["name"], other)))
            if key not in seen:
                seen.add(key)
                links.append({"kind": "mesh", "a": p["name"], "b": other})
        if p["on_shaft"]:
            s = by.get(p["on_shaft"])
            if s is None:
                raise MechanismError(f"part {p['name']} is on_shaft {p['on_shaft']}, but there is no part named so")
            if s["type"] != "shaft":
                raise MechanismError(f"part {p['name']} is on_shaft {s['name']}, which is a {s['type']}, not a shaft")
            if p["type"] not in ON_SHAFT:
                raise MechanismError(f"a {p['type']} ({p['name']}) cannot sit on a shaft")
            links.append({"kind": "shaft", "a": p["name"], "b": s["name"]})
        if p.get("houses") and p["type"] == "centre_housing":
            s = by.get(p["houses"])
            if s is None or s["type"] != "shaft":
                raise MechanismError(f"centre_housing {p['name']} houses {p['houses']}, which is not a shaft of this "
                                     "mechanism")
            if any(l["kind"] == "case" and l["b"] == s["name"] for l in links):
                raise MechanismError(f"shaft {s['name']} has two centre housings")
            links.append({"kind": "case", "a": p["name"], "b": s["name"]})
        elif p["type"] in ENGINE:
            _engine_links(p, by, links)
        elif p["type"] in STAGE4:
            _stage4_links(p, by, links)
        elif p.get("houses"):
            w = by.get(p["houses"])
            if w is None or w["type"] not in WHEELS:
                raise MechanismError(f"volute {p['name']} houses {p['houses']}, which is not a compressor or turbine "
                                     "wheel of this mechanism")
            if any(l["kind"] == "housing" and l["b"] == w["name"] for l in links):
                raise MechanismError(f"wheel {w['name']} has two volutes")
            links.append({"kind": "housing", "a": p["name"], "b": w["name"]})
        no_teeth = WHEELS + ("bearing",) + STILL + ENGINE + tuple(t for t in STAGE4 if t not in ("worm", "worm_wheel"))
        if p["type"] in no_teeth and p["meshes_with"]:
            raise MechanismError(f"a {p['type']} ({p['name']}) has no teeth to mesh with")
    for link in [l for l in links if l["kind"] == "mesh"]:
        kinds = {by[link["a"]]["type"], by[link["b"]]["type"]}
        if kinds & {"worm", "worm_wheel"}:
            if kinds != {"worm", "worm_wheel"}:
                raise MechanismError(f"{link['a']} and {link['b']} cannot mesh: a worm meshes only with a worm wheel")
            link["kind"] = "worm"
            if by[link["a"]]["type"] == "worm_wheel":
                link["a"], link["b"] = link["b"], link["a"]  # the worm first: it drives
            continue
        _check_mesh(by[link["a"]], by[link["b"]])
    _check_no_loop(parts, links)
    _fill_bores_and_shafts(parts, links, assumed)
    _fill_pair_numbers(parts, links, by, assumed)
    _fill_centre_housings(parts, links, by, assumed)
    _fill_engine(parts, links, by, assumed)
    _fill_stage4(parts, links, by, assumed)
    driver = raw.get("driver") or next((p["name"] for p in parts if p.get("driver")), None)
    for ps in sets:
        if driver in (None, "") or ps["driver"]:
            driver = ps[ps["input"]]
    if driver in (None, ""):
        first = (("crankshaft",), ("worm", "pulley", "sprocket", "cam", "nut", "spring"), GEARS[:4], ("shaft",), WHEELS,
                 ("bearing",))
        driver = next((p["name"] for kinds in first for p in parts if p["type"] in kinds), parts[0]["name"])
        assumed.append(f"driver = {driver}")
    if driver not in by:
        raise MechanismError(f"driver {driver} is not one of the parts")
    if by[driver]["type"] in ("bolt", "washer", "belt", "chain", "follower", "valve", "worm_wheel") or (
            by[driver]["type"] == "spring" and by[driver].get("on_valve")):
        what = {"worm_wheel": "a worm wheel cannot drive its worm (the drive locks); name the worm"}.get(
            by[driver]["type"], "it is moved by another part; name the part that drives")
        raise MechanismError(f"driver {driver} is a {by[driver]['type'].replace('_', ' ')}: {what}")
    if sets and any(by[driver]["name"] in (ps["sun"], ps["ring"], ps["carrier"], *ps["planets"]) and
                    driver != ps[ps["input"]] for ps in sets):
        raise MechanismError(f"driver {driver}: a planetary set is driven by its input member; set input instead")
    if by[driver]["type"] in ("rack",) + STILL:
        raise MechanismError(f"driver {driver} is a {by[driver]['type']} and cannot drive; name a gear, wheel or shaft")
    if by[driver]["type"] in ENGINE[1:]:
        raise MechanismError(f"driver {driver} is a {by[driver]['type']}: name the crankshaft, the motion is worked out "
                             "from the crank")
    if raw.get("rpm") in (None, ""):
        rpm = DEFAULT_RPM
        assumed.append(f"rpm = {DEFAULT_RPM}")
    else:
        rpm = _num(raw, "rpm", 0.5, 600, float, "")
    direction = str(raw.get("direction") or "clockwise").lower()
    if direction not in ("clockwise", "counterclockwise"):
        raise MechanismError("direction must be clockwise or counterclockwise")
    for p in parts:
        p.pop("driver", None)
    sheet = {"parts": parts, "links": links, "driver": driver, "rpm": rpm, "direction": direction}
    if sets:
        sheet["planetary"] = sets
        for ps in sets:  # the carrier's numbers from its set
            c = by[ps["carrier"]]
            c.update(radius=ps["radius"], planets=len(ps["planets"]), pin=ps["pin"], width=ps["width"])
            by[ps["ring"]]["at"] = by[ps["planets"][0]].get("at", 90.0)  # round its first planet, on the sun
    sheet["colours"] = colours(sheet)
    lay = layout(sheet)  # also proves the parts can be placed
    for p in parts:
        if p["type"] == "shaft" and p["length"] is None:
            assumed.append(f"{p['name']}.length = {lay['place'][p['name']]['length_mm']:g} (the gears on it plus "
                           "some each side)")
    return sheet, assumed


FIRING = {1: [0], 2: [0, 180], 3: [0, 240, 120], 4: [0, 180, 180, 0], 5: [0, 144, 288, 72, 216],
          6: [0, 240, 120, 120, 240, 0]}  # inline engines' usual throw angles, front to back
ENGINE_GAP = 0.2  # mm: a rod's big end and small end clear the webs and the pin bosses by this, each side


def _engine_links(p, by, links):
    t, n = p["type"], p["name"]

    def target(name, kind, verb):
        q = by.get(name)
        if q is None or q["type"] != kind:
            raise MechanismError(f"{t} {n} {verb} {name}, which is not a {kind.replace('_', ' ')} of this mechanism")
        return q

    def once(kind, a, what):
        if any(l["kind"] == kind and l["a"] == a for l in links):
            raise MechanismError(f"{a} has two {what}")

    if t == "connecting_rod":
        links.append({"kind": "crank", "a": n, "b": target(p["on_crank"], "crankshaft", "is on_crank")["name"]})
    elif t == "piston":
        rod = target(p["on_rod"], "connecting_rod", "is on_rod")["name"]
        once("pin", rod, "pistons")
        links.append({"kind": "pin", "a": rod, "b": n})
    elif t == "piston_rings":
        piston = target(p["on_piston"], "piston", "are on_piston")["name"]
        once("ring", piston, "sets of rings")
        links.append({"kind": "ring", "a": piston, "b": n})
    elif t == "cylinder":
        piston = target(p["houses"], "piston", "houses")["name"]
        once("liner", piston, "cylinders")
        links.append({"kind": "liner", "a": piston, "b": n})


def _stage4_links(p, by, links):
    t, n = p["type"], p["name"]

    def target(name, kinds, verb):
        q = by.get(name)
        if q is None or q["type"] not in kinds:
            raise MechanismError(f"{t} {n} {verb} {name}, which is not a {' or '.join(k.replace('_', ' ') for k in kinds)} "
                                 "of this mechanism")
        return q

    def once(kind, a, what):
        if any(l["kind"] == kind and l["a"] == a and by[l["b"]]["type"] == t for l in links):
            raise MechanismError(f"{a} has two {what}")

    if t in ("nut", "washer"):
        bolt = target(p["on_bolt"], ("bolt",), "is on_bolt")["name"]
        once("bolt", bolt, t + "s")
        links.append({"kind": "bolt", "a": bolt, "b": n})
    elif t in ("belt", "chain"):
        wheel = "pulley" if t == "belt" else "sprocket"
        a, b = (target(x, (wheel,), "runs on")["name"] for x in p["on"])
        if a == b:
            raise MechanismError(f"{t} {n} runs on {a} twice: give two different {wheel}s")
        links += [{"kind": t, "a": n, "b": a}, {"kind": t, "a": n, "b": b}]
    elif t in ("follower", "valve"):
        cam = target(p["on_cam"], ("cam",), "is on_cam")["name"]
        if any(l["kind"] == "cam" and l["a"] == cam for l in links):
            raise MechanismError(f"cam {cam} has two followers")
        links.append({"kind": "cam", "a": cam, "b": n})
    elif t == "spring" and p.get("on_valve"):
        valve = target(p["on_valve"], ("valve",), "is on_valve")["name"]
        once("spring", valve, "springs")
        links.append({"kind": "spring", "a": valve, "b": n})
    elif t == "carrier":
        links.append({"kind": "carrier", "a": n, "b": f"{p['_set']}_sun"})


def cam_table(cam, kind, roller, samples=180):
    """The radius from the cam's axis to its follower's contact (a roller's centre, a flat face) at
    each of `samples` even turns of the cam, mm."""
    base = cam["base_diameter"] / 2 + (roller / 2 if kind == "roller" else 0.0)
    return [base + cam_motion(360.0 * i / samples, (0.0, cam["rise"]), cam["dwell"], cam["fall"], cam["lift"])[0]
            for i in range(samples)]


def _fill_stage4(parts, links, by, assumed):
    """Real proportions for fasteners, springs, belt and chain drives, cams, valves and worm drives,
    and the checks that each can work: a spring squeezed past its solid length, a belt wrapping too
    little of its small pulley, a cam that would undercut its roller or jam it are refused."""
    def note(p, key, value, why):
        p[key] = value
        assumed.append(f"{p['name']}.{key} = {value:g} ({why})")

    def linked(kind, a, t=None):
        return [by[l["b"]] for l in links if l["kind"] == kind and l["a"] == a and (t is None or by[l["b"]]["type"] == t)]

    for p in parts:
        t = p["type"]
        if t == "bolt":
            d = p["diameter"]
            iso = METRIC.get(round(d)) if abs(d - round(d)) < 1e-9 else None
            if p["pitch"] is None:
                if not iso:
                    raise MechanismError(f"bolt {p['name']}: {d:g} mm is not an ISO size ({', '.join(map(str, METRIC))});"
                                         " give its pitch")
                note(p, "pitch", iso[0], f"ISO coarse M{round(d)}")
            p["across_flats"] = iso[1] if iso else round(1.6 * d, 2)
            nut = linked("bolt", p["name"], "nut")
            washer = linked("bolt", p["name"], "washer")
            m = 0.84 * d
            w = 0.2 * d if washer else 0.0
            if p["length"] is None:
                note(p, "length", round(4 * d, 1), "4 x diameter")
            stick = 5 * p["pitch"]  # the thread left below the nut: it screws off this far and back
            p["thread_length"] = round(min(p["length"], 2 * d + 6), 3)
            if nut and p["thread_length"] < stick + m:
                p["thread_length"] = round(min(p["length"], stick + m + p["pitch"]), 3)
            if nut and p["length"] < stick + m + w + 0.1 * d:
                raise MechanismError(f"bolt {p['name']}: {p['length']:g} mm is too short for its nut; make it at "
                                     f"least {math.ceil(stick + m + w + 0.1 * d)} mm")
            p["nut_at"] = round(-p["length"] + stick, 4) if nut else None  # the nut's bottom face, below the head
        elif t == "spring" and not p.get("on_valve"):
            if p["mean_diameter"] is None:
                note(p, "mean_diameter", 24.0, "a hand-sized spring")
            if p["wire_diameter"] is None:
                note(p, "wire_diameter", round(0.12 * p["mean_diameter"], 2), "0.12 x mean diameter")
            w = p["wire_diameter"]
            if p["kind"] == "extension":
                if p["coils"] is None:
                    note(p, "coils", 14.0, "a close-wound extension spring")
                p["free_length"] = round((p["coils"] + 1) * w * 1.02, 3)
            else:
                if p["coils"] is None:
                    note(p, "coils", 8.0, "6 working turns and a closed end each side")
                if p["free_length"] is None:
                    note(p, "free_length", round(max(2.5 * p["mean_diameter"], (p["coils"] + 3) * w * 1.2), 2),
                         "2.5 x mean diameter")
            if p["squeeze"] is None:
                note(p, "squeeze", 0.3, "squeezed to 0.7 of its length" if p["kind"] != "extension" else
                     "pulled out by 0.3 of its length")
            solid = (p["coils"] + 1) * w
            if p["kind"] != "extension" and p["free_length"] * (1 - p["squeeze"]) < solid * 1.05:
                most = max(0.0, 1 - solid * 1.05 / p["free_length"])
                raise MechanismError(f"spring {p['name']}: squeezed that far its coils would touch (solid length "
                                     f"{solid:g} mm); squeeze at most {most:.2f}")
            if w >= 0.4 * p["mean_diameter"]:
                raise MechanismError(f"spring {p['name']}: the wire is too thick for the coil (under 0.4 x mean diameter)")
        elif t == "pulley":
            if p["bore"] is None:
                note(p, "bore", round(max(8.0, 0.2 * p["datum_diameter"])), "0.2 x datum diameter")
        elif t == "sprocket":
            R = sprocket_radius(p["pitch"], p["teeth"])
            if p["bore"] is None:
                note(p, "bore", float(max(6, round(0.25 * 2 * R))), "0.25 x pitch diameter")
            p["hub"] = [round(min(2 * R - 1.6 * p["pitch"], max(p["bore"] * 1.7, p["bore"] + 10)), 2),
                        round(0.6 * p["pitch"], 2)]
        elif t == "cam":
            if p["width"] is None:
                note(p, "width", round(0.3 * p["base_diameter"], 2), "0.3 x base diameter")
            if p["bore"] is None:
                note(p, "bore", round(max(4.0, 0.25 * p["base_diameter"]), 1), "0.25 x base diameter")
        elif t == "worm":
            if p["pitch_diameter"] is None:
                note(p, "pitch_diameter", round(10 * p["module"], 3), "10 x module")
    for p in parts:
        t = p["type"]
        if t in ("belt", "chain"):
            a, b = by[p["on"][0]], by[p["on"][1]]
            if t == "belt":
                for key in ("section", "grooves"):
                    if a[key] != b[key]:
                        raise MechanismError(f"belt {p['name']}: pulleys {a['name']} and {b['name']} have different {key}")
                d1, d2 = a["datum_diameter"], b["datum_diameter"]
                if p["centre_distance"] is None:
                    note(p, "centre_distance", round(d1 + d2, 1), "the two diameters added")
                C = p["centre_distance"]
                if C <= (d1 + d2) / 2 + V_SECTIONS[a["section"]][0]:
                    raise MechanismError(f"belt {p['name']}: the pulleys overlap at {C:g} mm")
                wrap = 180 - 2 * math.degrees(math.asin(abs(d2 - d1) / (2 * C)))
                if wrap < 120:
                    need = abs(d2 - d1) / (2 * math.sin(math.radians(30)))
                    raise MechanismError(f"belt {p['name']}: it wraps only {wrap:.0f} degrees of the small pulley (120 "
                                         f"at least): make centre_distance at least {math.ceil(need)} mm")
                p["section"], p["grooves"] = a["section"], a["grooves"]
            else:
                if abs(a["pitch"] - b["pitch"]) > 1e-9:
                    raise MechanismError(f"chain {p['name']}: sprockets {a['name']} and {b['name']} have different "
                                         "pitches")
                pitch = a["pitch"]
                wanted = p["centre_distance"] if p["centre_distance"] is not None else 35 * pitch
                lay = chain_layout(pitch, a["teeth"], b["teeth"], wanted)
                if p["centre_distance"] is None or abs(lay["centre"] - wanted) > 1e-6:
                    assumed.append(f"{p['name']}.centre_distance = {lay['centre']:.2f} ({lay['links']} links, the "
                                   f"nearest a whole, even number of links allows)")
                p["centre_distance"] = round(lay["centre"], 4)
                p["links"], p["pitch"] = lay["links"], pitch
        elif t in ("follower", "valve"):
            cam = by[p["on_cam"]]
            if t == "follower":
                if p["roller_diameter"] is None and p["kind"] == "roller":
                    note(p, "roller_diameter", round(0.4 * cam["base_diameter"], 2), "0.4 x the cam's base diameter")
                kind, roller = p["kind"], p["roller_diameter"] or 0.0
            else:
                if p["stem_diameter"] is None:
                    note(p, "stem_diameter", round(0.2 * p["head_diameter"], 2), "0.2 x head diameter")
                if p["length"] is None:
                    note(p, "length", round(3.2 * p["head_diameter"], 1), "3.2 x head diameter")
                kind, roller = "flat", 0.0
            table = cam_table(cam, kind, roller)
            n = len(table)
            step = 2 * math.pi / n
            slope = [(table[(i + 1) % n] - table[i - 1]) / (2 * step) for i in range(n)]
            curve = [(table[(i + 1) % n] - 2 * table[i] + table[i - 1]) / step ** 2 for i in range(n)]
            if kind == "roller":
                worst = max(math.degrees(math.atan2(abs(s), r)) for s, r in zip(slope, table))
                if worst > 35:
                    raise MechanismError(f"cam {cam['name']}: the roller would be pushed at {worst:.0f} degrees and jam "
                                         "(35 at most): make the rise longer or base_diameter bigger")
                rho = min((r * r + s * s) ** 1.5 / max(r * r + 2 * s * s - r * c, 1e-9)
                          for r, s, c in zip(table, slope, curve))
                if rho <= roller / 2 + 0.5:
                    raise MechanismError(f"cam {cam['name']}: the profile would undercut a {roller:g} mm roller: make "
                                         "the roller smaller, the rise longer or base_diameter bigger")
            else:
                if min(r + c for r, c in zip(table, curve)) <= 0.5:
                    raise MechanismError(f"cam {cam['name']}: a flat follower cannot follow this lift (the profile would "
                                         "fold): make the rise longer or base_diameter bigger")
                face = round(2 * max(abs(s) for s in slope) + 6, 2)
                if t == "follower":
                    p["face_diameter"] = face
                else:
                    p["bucket_diameter"] = round(max(1.1 * p["head_diameter"], face), 2)
            p["table"] = [round(r, 5) for r in table]
            p["contact_kind"] = kind
        elif t == "spring" and p.get("on_valve"):
            v = by[p["on_valve"]]
            bucket = v.get("bucket_diameter") or 1.1 * v["head_diameter"]
            if p["mean_diameter"] is None:
                p["mean_diameter"] = round(0.62 * bucket, 2)
            if p["wire_diameter"] is None:
                p["wire_diameter"] = round(0.1 * p["mean_diameter"], 2)
            installed = round(0.4 * v["length"], 2)
            p["free_length"] = installed
            if p["coils"] is None:
                p["coils"] = float(max(5, round(installed / (0.35 * p["mean_diameter"]))))
            lift = by[v["on_cam"]]["lift"]
            solid = (p["coils"] + 1) * p["wire_diameter"]
            if installed - lift < solid * 1.05:
                raise MechanismError(f"spring {p['name']}: the valve's {lift:g} mm lift would squeeze it solid; ask for "
                                     "less lift or a longer valve")
            p["squeeze"] = round(lift / installed, 4)
            assumed.append(f"{p['name']}: {p['mean_diameter']:g} mm coil of {p['wire_diameter']:g} mm wire, "
                           f"{p['coils']:g} turns, {installed:g} mm fitted (sized to the valve)")
        elif t == "worm_wheel":
            worm = next((by[l["a"]] for l in links if l["kind"] == "worm" and l["b"] == p["name"]), None)
            if worm is None:
                raise MechanismError(f"worm_wheel {p['name']} meshes with no worm")
            m = worm["module"]
            if worm["length"] is None:
                note(worm, "length", round((11 + 0.06 * p["teeth"]) * m * (1 + 0.15 * (worm["starts"] - 1)), 2),
                     "(11 + 0.06 x wheel teeth) x module")
            if p["width"] is None:
                note(p, "width", round(0.6 * worm["pitch_diameter"], 2), "0.6 x the worm's pitch diameter")
            if p["bore"] is None:
                note(p, "bore", float(max(4, round(0.25 * m * p["teeth"]))), "0.25 x pitch diameter")
            p["module"], p["lead_angle"] = m, round(math.degrees(math.atan2(worm["starts"] * m,
                                                                            worm["pitch_diameter"])), 5)
            p["hand"] = worm["hand"]
            if worm["pitch_diameter"] < 2 * 1.2 * m + 2:
                raise MechanismError(f"worm {worm['name']}: pitch_diameter too small for module {m:g}")
    for p in parts:  # a worm with no wheel still needs a length
        if p["type"] == "worm" and p["length"] is None:
            note(p, "length", round(12 * p["module"], 2), "12 x module")


def _fill_engine(parts, links, by, assumed):
    """An engine's sizes from real proportions: the stroke from the bore (or the other way round),
    the rod 1.7 strokes long, crankpin and journals from the stroke, the piston's heights and pin from
    the bore; then the counterweights and the cylinder are sized so nothing hits anything as it runs,
    or the request is refused with the number to change."""
    def note(p, key, value, why):
        p[key] = value
        assumed.append(f"{p['name']}.{key} = {value:g} ({why})")

    def linked(kind, a):
        return next((by[l["b"]] for l in links if l["kind"] == kind and l["a"] == a), None)

    for rod in [p for p in parts if p["type"] == "connecting_rod"]:
        if linked("pin", rod["name"]) is None:
            raise MechanismError(f"connecting_rod {rod['name']} has no piston: give a piston on_rod: {rod['name']}")
    for c in [p for p in parts if p["type"] == "crankshaft"]:
        rods = [by[l["a"]] for l in links if l["kind"] == "crank" and l["b"] == c["name"]]
        pistons = [linked("pin", r["name"]) for r in rods]
        bores = [q["bore"] for q in pistons if q["bore"]]
        if c["stroke"] is None:
            if not bores:
                raise MechanismError(f"crankshaft {c['name']} has no stroke (and no piston bore to take one from)")
            note(c, "stroke", bores[0], "the piston's bore: a square engine")
        S = c["stroke"]
        for q in pistons:
            if q["bore"] is None:
                note(q, "bore", S, "the crank's stroke: a square engine")
        # throws: each rod on its own, in the order listed unless a rod names one
        taken = [r["throw"] for r in rods if r["throw"] is not None]
        if len(set(taken)) < len(taken):
            raise MechanismError(f"crankshaft {c['name']}: two connecting rods name the same throw")
        free = (i for i in range(12) if i not in taken)
        for r in rods:
            if r["throw"] is None:
                r["throw"] = next(free)
                if len(rods) > 1:
                    assumed.append(f"{r['name']}.throw = {r['throw']}")
        count = max([r["throw"] + 1 for r in rods] + [1])
        if c["throw_angles"] is None:
            c["throw_angles"] = [float(a) for a in FIRING.get(count, [360.0 * i / count for i in range(count)])]
            if count > 1:
                assumed.append(f"{c['name']}.throw_angles = {c['throw_angles']} (the usual inline firing order)")
        elif len(c["throw_angles"]) < count:
            raise MechanismError(f"crankshaft {c['name']}: throw_angles has {len(c['throw_angles'])} angles, the rods "
                                 f"need {count} throws")
        if c["crankpin_diameter"] is None:
            note(c, "crankpin_diameter", round(0.55 * S, 2), "0.55 x stroke")
        if c["journal_diameter"] is None:
            note(c, "journal_diameter", round(0.65 * S, 2), "0.65 x stroke")
        B = max([q["bore"] for q in pistons] + [S])
        r = S / 2
        c["pin_width"] = round(0.32 * B, 3)
        c["web_thickness"] = round(0.17 * B, 3)
        c["journal_width"] = round(0.3 * B, 3)
        cyl_d = max([(1.58 if (linked("liner", q["name"]) or {}).get("style") == "block" else 1.32) * q["bore"]
                     for q in pistons] + [0.0])
        c["spacing"] = round(max(c["pin_width"] + 2 * c["web_thickness"] + c["journal_width"], cyl_d + 2), 3)
        c["web_radius"] = round(c["crankpin_diameter"] / 2 + 0.08 * S, 3)
        if c["crankpin_diameter"] / 2 + c["journal_diameter"] / 2 <= r:
            raise MechanismError(f"crankshaft {c['name']}: the crankpin and the journals do not overlap (crankpin_"
                                 f"diameter + journal_diameter must be over the stroke, {S:g} mm)")
        cw = 0.85 * S
        for rod, q in zip(rods, pistons):
            if rod["length"] is None:
                note(rod, "length", round(1.7 * S, 2), "1.7 x stroke")
            if q["compression_height"] is None:
                note(q, "compression_height", round(0.36 * q["bore"], 2), "0.36 x bore")
            if q["pin_diameter"] is None:
                note(q, "pin_diameter", round(0.25 * q["bore"], 2), "0.25 x bore")
            if q["skirt_length"] is None:
                note(q, "skirt_length", round(0.33 * q["bore"], 2), "0.33 x bore")
            if q["pin_diameter"] > 0.4 * q["bore"]:
                raise MechanismError(f"piston {q['name']}: pin_diameter must be at most 0.4 x the bore")
            if rod["length"] < 2.5 * r:
                raise MechanismError(f"connecting_rod {rod['name']}: length must be at least 2.5 x the crank radius "
                                     f"({2.5 * r:g} mm) or the rod hits the cylinder")
            cw = min(cw, rod["length"] - r - q["skirt_length"] - 2)  # the skirt clears the counterweight at the bottom
        c["counterweight_radius"] = round(cw, 3)
        hub = c["journal_diameter"] / 2 + 0.08 * S
        if c["counterweights"] and cw < hub + 0.05 * S:
            raise MechanismError(f"crankshaft {c['name']}: the rods are too short for counterweights on a {S:g} mm "
                                 "stroke: make the connecting rods longer or set counterweights false")
        sweep = crank_sweep(c)
        for rod, q in zip(rods, pistons):
            gap = round(1.25 * q["pin_diameter"], 3)
            q["boss_gap"] = gap
            rod["big_end_width"] = round(c["pin_width"] - 2 * ENGINE_GAP, 3)
            rod["small_end_width"] = round(gap - 2 * ENGINE_GAP, 3)
            rod["big_end_bore"] = c["crankpin_diameter"]
            rod["small_end_bore"] = q["pin_diameter"]
            low_ring = piston_grooves(q["bore"], q["compression_height"])[-1][1]
            bottom = sweep + 2.0  # the cylinder's foot clears everything the crank sweeps
            if rod["length"] - r + low_ring < bottom + 1.0:
                need = bottom + 1.0 + r - low_ring
                raise MechanismError(f"connecting_rod {rod['name']}: {rod['length']:g} mm is too short for a {S:g} mm "
                                     f"stroke, the rings would leave the cylinder at the bottom: make it at least "
                                     f"{math.ceil(need)} mm")
            cyl = linked("liner", q["name"])
            if cyl:
                if cyl["wall"] is None:
                    note(cyl, "wall", round(0.08 * q["bore"], 2), "0.08 x bore")
                cyl["bore"] = q["bore"]
                cyl["bottom"] = round(bottom, 3)  # above the crank's axis
                cyl["length"] = round(rod["length"] + r + q["compression_height"] + 0.5 - bottom, 3)


def _shaft_parts(shaft, links, by):
    return [by[l["a"]] for l in links if l["kind"] == "shaft" and l["b"] == shaft]


def _fill_centre_housings(parts, links, by, assumed):
    """A centre housing's wall and body from the bearings it carries and the compressor wheel: a real
    one is about 0.7 of the compressor wheel across."""
    for p in [q for q in parts if q["type"] == "centre_housing"]:
        on = _shaft_parts(p["houses"], links, by)
        wheels = [g for g in on if g["type"] in WHEELS]
        if {g["type"] for g in wheels} != set(WHEELS):
            raise MechanismError(f"centre_housing {p['name']} sits between a compressor wheel and a turbine wheel: "
                                 f"shaft {p['houses']} needs one of each on it")
        bearings = [g for g in on if g["type"] == "bearing"]
        seat = max([g["outer_diameter"] for g in bearings] + [1.6 * by[p["houses"]]["diameter"]])
        if p["wall"] is None:
            p["wall"] = round(max(1.5, 0.12 * seat), 2)
            assumed.append(f"{p['name']}.wall = {p['wall']:g} (0.12 x the bearings' outer diameter)")
        comp = next(g for g in wheels if g["type"] == "compressor_wheel")
        least = seat + 0.4 + 2 * p["wall"]
        if p["outer_diameter"] is None:
            p["outer_diameter"] = round(max(least, 0.7 * comp["diameter"]), 2)
            why = "0.7 x the compressor wheel" if 0.7 * comp["diameter"] >= least else "the bearings and the wall"
            assumed.append(f"{p['name']}.outer_diameter = {p['outer_diameter']:g} ({why})")
        if p["outer_diameter"] < least - 1e-9:
            raise MechanismError(f"centre_housing {p['name']}: outer_diameter must be at least {least:g} mm (the "
                                 "bearings and the wall)")
        smallest = min(g["diameter"] for g in wheels)
        if p["outer_diameter"] > 1.2 * smallest:
            raise MechanismError(f"centre_housing {p['name']}: outer_diameter must be at most {1.2 * smallest:g} mm "
                                 "(it fits inside the volutes' scrolls)")


def _check_mesh(a, b):
    kinds = {a["type"], b["type"]}
    pair = f"gears {a['name']} and {b['name']} cannot mesh"
    if "shaft" in kinds:
        raise MechanismError(f"{pair}: a shaft has no teeth (use on_shaft)")
    if abs(a["module"] - b["module"]) > 1e-9:
        raise MechanismError(f"{pair}: different module ({a['module']:g} and {b['module']:g} mm)")
    if abs(a["pressure_angle"] - b["pressure_angle"]) > 1e-9:
        raise MechanismError(f"{pair}: different pressure angle ({a['pressure_angle']:g} and {b['pressure_angle']:g})")
    if "bevel_gear" in kinds and kinds != {"bevel_gear"}:
        raise MechanismError(f"{pair}: a bevel gear meshes only with another bevel gear")
    if "rack" in kinds and (kinds - {"rack"}) - {"spur_gear"}:
        raise MechanismError(f"{pair}: a rack meshes only with a spur gear")
    if kinds == {"rack"}:
        raise MechanismError(f"{pair}: two racks")
    if "ring_gear" in kinds:
        ring, inner = (a, b) if a["type"] == "ring_gear" else (b, a)
        if inner["type"] == "ring_gear":
            raise MechanismError(f"{pair}: two ring gears")
        if inner["type"] != "spur_gear":
            raise MechanismError(f"{pair}: only a spur gear meshes inside a ring gear")
        if ring["teeth"] - inner["teeth"] < MIN_TEETH_DIFFERENCE:
            raise MechanismError(f"{pair}: the ring gear needs at least {MIN_TEETH_DIFFERENCE} more teeth than the "
                                 f"gear inside it ({ring['teeth']} and {inner['teeth']})")
    helical = [p for p in (a, b) if p["type"] == "helical_gear"]
    if len(helical) == 1:
        raise MechanismError(f"{pair}: a helical gear meshes only with another helical gear")
    if len(helical) == 2:
        if abs(a["helix_angle"] - b["helix_angle"]) > 1e-9:
            raise MechanismError(f"{pair}: different helix angles ({a['helix_angle']:g} and {b['helix_angle']:g})")
        if a["herringbone"] != b["herringbone"]:
            raise MechanismError(f"{pair}: one is herringbone and the other is not")
        if a.get("hand") and b.get("hand") and a["hand"] == b["hand"]:
            raise MechanismError(f"{pair}: meshing helical gears need opposite hands (both are {a['hand']})")


def _check_no_loop(parts, links):
    """Gears meshing round in a closed loop lock solid; a mechanism must be a tree."""
    parent = {p["name"]: p["name"] for p in parts}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for link in links:
        ra, rb = find(link["a"]), find(link["b"])
        if ra == rb:
            raise MechanismError(f"{link['a']} and {link['b']} close a loop of connected parts; a loop of meshing "
                                 "gears cannot turn, so leave one link out")
        parent[ra] = rb


def _default_bore(p):
    if p["type"] in WHEELS:
        return float(max(2, round(0.1 * p["diameter"])))
    if p["type"] == "bearing":
        return 9e9  # a bearing has no bore of its own to offer: it takes the shaft's
    pitch_d = p["module"] * p["teeth"] / math.cos(math.radians(p.get("helix_angle") or 0))
    return float(max(round(p["module"] * 1.5), round(pitch_d * 0.25 / 2) * 2, 1))


def _fill_bores_and_shafts(parts, links, assumed):
    by = {p["name"]: p for p in parts}
    holed = ("spur_gear", "helical_gear", "bevel_gear")
    # a shaft with no diameter takes a bore one of its parts was given, else the smallest default
    # bore of its parts (so it fits every one of them); a part on a shaft with no bore takes the shaft's
    for s in [p for p in parts if p["type"] == "shaft" and p["diameter"] is None]:
        on = [by[l["a"]] for l in links if l["kind"] == "shaft" and l["b"] == s["name"]]
        if not on or all(g["type"] == "bearing" and g["bore"] is None for g in on):
            raise MechanismError(f"shaft {s['name']} has no diameter and no gear or wheel on it to take one from")
        given = [g for g in on if g["bore"] is not None]
        s["diameter"] = given[0]["bore"] if given else min(_default_bore(g) for g in on)
        assumed.append(f"{s['name']}.diameter = {s['diameter']:g}" + (f" (the bore of {given[0]['name']})" if given
                                                                     else " (fits every part on it)"))
    for p in parts:
        if p["type"] in WHEELS + ("bearing",) and p["bore"] is None:
            shaft = by.get(p["on_shaft"]) if p["on_shaft"] else None
            if shaft:
                p["bore"] = shaft["diameter"]
                assumed.append(f"{p['name']}.bore = {p['bore']:g} (the shaft's diameter)")
            elif p["type"] == "bearing":
                raise MechanismError(f"bearing {p['name']} has no bore and sits on no shaft")
            else:
                p["bore"] = _default_bore(p)
                assumed.append(f"{p['name']}.bore = {p['bore']:g}")
        if p["type"] in WHEELS and p["bore"] > 0.35 * p["diameter"]:
            raise MechanismError(f"wheel {p['name']}: bore {p['bore']:g} mm is too big for a {p['diameter']:g} mm wheel")
        if p["type"] == "bearing":
            if p["outer_diameter"] is None:
                p["outer_diameter"] = round(2.6 * p["bore"], 2)
                assumed.append(f"{p['name']}.outer_diameter = {p['outer_diameter']:g}")
            if p["width"] is None:
                p["width"] = round(0.3 * p["outer_diameter"], 2)
                assumed.append(f"{p['name']}.width = {p['width']:g}")
            if p["outer_diameter"] < 1.5 * p["bore"]:
                raise MechanismError(f"bearing {p['name']}: outer_diameter must be at least 1.5 x the bore")
    for p in parts:
        if p["type"] in holed:
            m = p["module"]
            pitch_d = m * p["teeth"] / math.cos(math.radians(p.get("helix_angle") or 0))
            root_d = pitch_d - 2 * 1.25 * m
            if p["bore"] is None:
                shaft = by.get(p["on_shaft"]) if p["on_shaft"] else None
                if shaft and shaft["diameter"]:
                    p["bore"] = shaft["diameter"]
                    assumed.append(f"{p['name']}.bore = {p['bore']:g} (the shaft's diameter)")
                else:
                    p["bore"] = _default_bore(p)
                    assumed.append(f"{p['name']}.bore = {p['bore']:g}")
            limit = root_d - 2.4 * m
            if p["hub_diameter"]:
                if p["hub_diameter"] >= root_d - 2 * m:
                    raise MechanismError(f"gear {p['name']}: hub_diameter {p['hub_diameter']:g} mm must be under "
                                         f"{root_d - 2 * m:.1f} mm (inside the tooth roots)")
                limit = p["hub_diameter"] - 2 * m
                width = p["width"] or 0
                if not p["hub_length"]:
                    p["hub_length"] = round(1.6 * width if p["type"] != "bevel_gear" else 0.8 * width, 2)
                    assumed.append(f"{p['name']}.hub_length = {p['hub_length']:g}")
                elif p["type"] != "bevel_gear" and p["hub_length"] < width:
                    raise MechanismError(f"gear {p['name']}: hub_length must be at least the width ({width:g} mm)")
            if p["bore"] >= limit:
                raise MechanismError(f"gear {p['name']}: bore {p['bore']:g} mm is too big; it must be under "
                                     f"{limit:.1f} mm to leave a rim under the teeth")
            if p.get("web") in ("holes", "spokes") and root_d < 12 * m:
                raise MechanismError(f"gear {p['name']}: too small for a web with {p['web']}; use web solid")
    for s in [p for p in parts if p["type"] == "shaft"]:
        on = [by[l["a"]] for l in links if l["kind"] == "shaft" and l["b"] == s["name"]]
        for g in on:
            if abs(g["bore"] - s["diameter"]) > 1e-6:
                raise MechanismError(f"gear {g['name']} has a {g['bore']:g} mm bore but shaft {s['name']} is "
                                     f"{s['diameter']:g} mm; they must be the same")
        if s["chamfer"] is None:
            s["chamfer"] = round(0.1 * s["diameter"], 3)
            assumed.append(f"{s['name']}.chamfer = {s['chamfer']:g}")


def _fill_pair_numbers(parts, links, by, assumed):
    """Profile shifts, helical hands and bevel numbers that depend on the mate."""
    meshes = [l for l in links if l["kind"] == "mesh"]
    mates = {p["name"]: [] for p in parts}
    for l in meshes:
        mates[l["a"]].append(l["b"])
        mates[l["b"]].append(l["a"])
    for p in parts:
        if p["type"] in EXTERNAL and p["shift"] is None:
            ext = [by[n] for n in mates[p["name"]] if by[n]["type"] in EXTERNAL]
            if len(ext) == 1 and len(mates[ext[0]["name"]]) == 1 and ext[0]["shift"] is None:
                x, xm = auto_shift(p["teeth"], ext[0]["teeth"], p.get("helix_angle") or 0)
                p["shift"], ext[0]["shift"] = x, xm
                for g in (p, ext[0]):
                    if g["shift"]:
                        assumed.append(f"{g['name']}.shift = {g['shift']:g} (so the small gear is not undercut)")
            else:
                x, _ = auto_shift(p["teeth"], None, p.get("helix_angle") or 0)
                inner = any(by[n]["type"] == "ring_gear" for n in mates[p["name"]])
                p["shift"] = 0.0 if inner or ext else x
                if p["shift"]:
                    assumed.append(f"{p['name']}.shift = {p['shift']:g} (so it is not undercut)")
        if p["type"] == "helical_gear" and not p["hand"]:
            mate = next((by[n] for n in mates[p["name"]] if by[n]["type"] == "helical_gear" and by[n]["hand"]), None)
            p["hand"] = ("left" if mate["hand"] == "right" else "right") if mate else "right"
            for n in mates[p["name"]]:
                if by[n]["type"] == "helical_gear" and not by[n]["hand"]:
                    by[n]["hand"] = "left" if p["hand"] == "right" else "right"
            assumed.append(f"{p['name']}.hand = {p['hand']}")
        if p["type"] == "bevel_gear":
            mate = next((by[n] for n in mates[p["name"]]), None)
            p["mate_teeth"] = mate["teeth"] if mate else p["teeth"]
            if p["width"] is None:
                p["width"] = round(bevel_numbers(p["module"], p["teeth"], p["mate_teeth"])["face"], 2)
                assumed.append(f"{p['name']}.width = {p['width']:g} (a third of the cone distance)")
        if p["type"] == "ring_gear" and p["rim_diameter"] is None:
            p["rim_diameter"] = round(p["module"] * p["teeth"] + 2 * 1.25 * p["module"] + 6 * p["module"], 2)
            assumed.append(f"{p['name']}.rim_diameter = {p['rim_diameter']:g}")
    for l in meshes:
        a, b = by[l["a"]], by[l["b"]]
        if "bevel_gear" == a["type"] and len(mates[a["name"]]) > 1:
            raise MechanismError(f"bevel gear {a['name']} meshes with more than one gear; this tool makes bevel pairs")


def colours(sheet):
    """Each part's colour for the explainer style: its own, else the next of the eight in the palette."""
    out, i = {}, 0
    for p in sheet["parts"]:
        if p.get("colour"):
            out[p["name"]] = p["colour"]
        else:
            out[p["name"]] = PALETTE[i % len(PALETTE)]
            i += 1
    return out


def key(sheet):
    """The numbers that make a mechanism what it is, as a short hash: the same numbers asked for
    again are the same model."""
    keep = {k: sheet[k] for k in ("parts", "links", "driver", "rpm", "direction")}
    keep["parts"] = [{k: v for k, v in p.items() if k not in ("colour", "shortening")} for p in keep["parts"]]
    text = json.dumps(keep, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:20]


def bearing_numbers(bore, outer, balls=None):
    """A ball bearing's pitch radius, ball radius, ball count and cage speed (turns per turn of the
    inner race), in the units given."""
    ri, ro = bore / 2, outer / 2
    pitch = (ri + ro) / 2
    ball = 0.62 * (ro - ri) / 2
    count = int(balls) if balls else max(6, int(2 * math.pi * pitch / (ball * 2 * 1.45)))
    return {"pitch": pitch, "ball": ball, "count": count, "cage_ratio": 0.5 * (1 - ball / pitch)}


# ISO metric coarse threads: pitch, hex across flats (ISO 4032 / 4014), mm by nominal diameter
METRIC = {3: (0.5, 5.5), 4: (0.7, 7), 5: (0.8, 8), 6: (1.0, 10), 8: (1.25, 13), 10: (1.5, 16), 12: (1.75, 18),
          14: (2.0, 21), 16: (2.0, 24), 20: (2.5, 30), 24: (3.0, 36), 30: (3.5, 46), 36: (4.0, 55)}


def thread_radius(u, diameter, pitch, internal=False):
    """The ISO metric thread's radius at phase u (0..1 along one pitch; the root's middle at 0, the
    crest's at 0.5), in the units given. External (a bolt): crest flat p/8 at the major diameter,
    root at d3 = d - 1.2269p. Internal (a nut): the same flanks a little outward (clearance), its crest
    flat at the minor diameter D1 = d - 1.0825p and its root past the major diameter."""
    u = u % 1.0
    if u > 0.5:
        u = 1.0 - u
    r_crest = diameter / 2
    r_root = diameter / 2 - 0.6134 * pitch
    if u <= 1 / 8:
        r = r_root
    elif u >= 7 / 16:
        r = r_crest
    else:  # the 60 degree flank: tan 30 relates the rise in radius to the run along the axis
        r = r_root + (r_crest - r_root) * (u - 1 / 8) / (7 / 16 - 1 / 8)
    if not internal:
        return r
    c = 0.05 * pitch
    return min(max(r + c, diameter / 2 - 0.5413 * pitch), diameter / 2 + 0.07 * pitch)


# classical V-belt sections, mm: top width, height, groove spacing e, edge f, groove above and below
# the datum line
V_SECTIONS = {"Z": (10, 6, 12, 8, 2.0, 7.0), "A": (13, 8, 15, 10, 2.75, 8.7), "B": (17, 11, 19, 12.5, 3.5, 10.8)}


def cam_motion(angle, rise=(0.0, 120.0), dwell_top=60.0, fall=120.0, lift=1.0):
    """A cam follower's lift at the cam's angle (degrees): a cycloidal rise over rise[1] degrees from
    rise[0], a dwell at full lift, a cycloidal fall, then rest. Returns (lift, d lift / d radian)."""
    a = (angle - rise[0]) % 360.0
    r, d, f = rise[1], dwell_top, fall

    def cyc(x, span):  # 0..1 over the span, with its slope per radian
        t = x / span
        return t - math.sin(2 * math.pi * t) / (2 * math.pi), (1 - math.cos(2 * math.pi * t)) / math.radians(span)
    if a < r:
        s, ds = cyc(a, r)
        return lift * s, lift * ds
    if a < r + d:
        return lift, 0.0
    if a < r + d + f:
        s, ds = cyc(a - r - d, f)
        return lift * (1 - s), -lift * ds
    return 0.0, 0.0


def belt_path(r1, r2, centre, step):
    """An open belt round two circles (r1 at the origin, r2 at (centre, 0)): points along it about
    `step` apart, running clockwise (over the top from the first to the second), and its length."""
    beta = math.asin(max(-0.99, min(0.99, (r1 - r2) / centre)))
    psi = math.pi / 2 + beta  # the spans' outward normal, the same angle on both circles
    pts = []

    def arc(cx, r, a0, a1):
        n = max(2, int(abs(a1 - a0) * r / step))
        return [(cx + r * math.cos(a0 + (a1 - a0) * i / n), r * math.sin(a0 + (a1 - a0) * i / n)) for i in range(n)]

    def span(p0, p1):
        n = max(1, int(math.dist(p0, p1) / step))
        return [(p0[0] + (p1[0] - p0[0]) * i / n, p0[1] + (p1[1] - p0[1]) * i / n) for i in range(n)]

    t1 = (r1 * math.cos(psi), r1 * math.sin(psi))
    t2 = (centre + r2 * math.cos(psi), r2 * math.sin(psi))
    b2 = (centre + r2 * math.cos(psi), -r2 * math.sin(psi))
    b1 = (r1 * math.cos(psi), -r1 * math.sin(psi))
    pts += span(t1, t2) + arc(centre, r2, psi, -psi) + span(b2, b1) + arc(0.0, r1, 2 * math.pi - psi, psi)
    length = 2 * math.dist(t1, t2) + r2 * 2 * psi + r1 * (2 * math.pi - 2 * psi)
    return pts, length


def sprocket_radius(pitch, teeth):
    """A sprocket's pitch circle radius: its rollers' centres, a chord of one pitch apart."""
    return pitch / (2 * math.sin(math.pi / teeth))


def chain_layout(pitch, z1, z2, centre):
    """Where a roller chain's rollers sit round two sprockets (z1 at the origin, z2 at (C, 0)): every
    roller on a sprocket in a tooth gap (one pitch apart round the pitch circle), the spans straight
    and a whole number of pitches long, an even number of links (inner and outer links alternate).
    The centre distance C closest to the one asked for that allows this. Returns {"centre", "slots":
    [(x, y, sprocket index or None)], "phase": [first gap angle of each sprocket], "links"}."""
    r1, r2 = sprocket_radius(pitch, z1), sprocket_radius(pitch, z2)
    d1, d2 = 2 * math.pi / z1, 2 * math.pi / z2
    best = None
    tries = [(s, e1, e2) for s in (0, -1, 1, -2, 2) for e1 in (0, 1) for e2 in (0, 1)]
    for n_shift, e1, e2 in tries:  # e1, e2: the seat half a tooth further round (the other parity)
        c, n = centre, None
        for _ in range(6):
            psi = math.pi / 2 + math.asin(max(-0.99, min(0.99, (r1 - r2) / c)))
            m1 = math.floor((math.pi - psi) / (d1 / 2) + 1e-9) - e1
            a1 = math.pi - m1 * d1 / 2  # the top seat on sprocket 1, on the wrapped side of the span
            m2 = math.floor(psi / (d2 / 2) + 1e-9) - e2
            a2 = m2 * d2 / 2
            dy = r2 * math.sin(a2) - r1 * math.sin(a1)
            if n is None:  # the span's whole pitches: chosen once, from the distance asked for
                n = max(1, round(math.hypot(c + r2 * math.cos(a2) - r1 * math.cos(a1), dy) / pitch) + n_shift)
            c_new = r1 * math.cos(a1) - r2 * math.cos(a2) + math.sqrt(max((n * pitch) ** 2 - dy ** 2, 0.0))
            if abs(c_new - c) < 1e-9:
                break
            c = c_new
        k2 = round(2 * a2 / d2)
        k1 = round((2 * math.pi - 2 * a1) / d1)
        links = 2 * n + k1 + k2
        if links % 2 == 0 and c > r1 + r2:
            cand = (abs(c - centre), c, a1, a2, n, k1, k2)
            if best is None or cand[0] < best[0]:
                best = cand
    if best is None:
        raise MechanismError("these sprockets cannot be joined by a chain at that centre distance")
    _, c, a1, a2, n, k1, k2 = best
    v1 = (r1 * math.cos(a1), r1 * math.sin(a1))
    v2 = (c + r2 * math.cos(a2), r2 * math.sin(a2))
    slots = [(v1[0] + (v2[0] - v1[0]) * i / n, v1[1] + (v2[1] - v1[1]) * i / n, 0 if i == 0 else None)
             for i in range(n)]
    slots += [(c + r2 * math.cos(a2 - i * d2), r2 * math.sin(a2 - i * d2), 1) for i in range(k2)]
    w2, w1 = (c + r2 * math.cos(-a2), -r2 * math.sin(a2)), (r1 * math.cos(a1), -r1 * math.sin(a1))
    slots += [(w2[0] + (w1[0] - w2[0]) * i / n, w2[1] + (w1[1] - w2[1]) * i / n, 1 if i == 0 else None)
              for i in range(n)]
    slots += [(r1 * math.cos(-a1 - i * d1), r1 * math.sin(-a1 - i * d1), 0) for i in range(k1)]
    centres = [(0.0, 0.0), (c, 0.0)]
    slots = [(x, y, s, centres[s] if s is not None else None) for x, y, s in slots]
    return {"centre": c, "slots": slots, "links": len(slots), "phase": [a1 % d1, a2 % d2],
            "radius": [r1, r2]}


GROOVES = (0.015, 0.018, 0.035)  # ring groove widths over the bore: two compression rings and an oil ring


def piston_grooves(bore, compression_height, widths=None):
    """Where a piston's ring grooves are: (top, bottom) height of each above the gudgeon pin's
    centre, in the units given; the top land is 0.065 of the bore, the lands between 0.032."""
    out, z = [], compression_height - 0.065 * bore
    for w in (widths or [g * bore for g in GROOVES]):
        out.append((z, z - w))
        z -= w + 0.032 * bore
    return out


def wheel_clearance(diameter_mm):
    """The gap a volute leaves round its wheel's blade tips, mm (kits.volute's default)."""
    return max(0.3, 0.008 * diameter_mm)


def describe(sheet):
    """A few words for the model's title: "spur gear pair 12/24"."""
    parts = sheet["parts"]
    kinds_present = {p["type"] for p in parts}
    if {"compressor_wheel", "turbine_wheel"} <= kinds_present:
        return "turbocharger" + (" with housings" if "volute" in kinds_present else "")
    if sheet.get("planetary"):
        ps = sheet["planetary"][0]
        return f"planetary gear set {ps['zs']}/{ps['zp']}/{ps['zr']}"
    named = [("worm", "worm drive"), ("chain", "chain drive"), ("belt", "belt drive"), ("valve", "cam and valve"),
             ("follower", "cam and follower"), ("bolt", "bolt and nut" if "nut" in kinds_present else "bolt"),
             ("spring", "spring")]
    for kind, words in named:
        if kind in kinds_present and len(parts) > 1 or (kind in kinds_present and kind in ("spring", "bolt")):
            if kind == "worm" and "worm_wheel" in kinds_present:
                w = next(p for p in parts if p["type"] == "worm_wheel")
                s = next(p for p in parts if p["type"] == "worm")
                return f"worm drive {s['starts']}:{w['teeth']}"
            if kind == "spring" and len(parts) == 1:
                return f"{parts[0]['kind']} spring"
            return words
    if "crankshaft" in kinds_present:
        n = sum(p["type"] == "piston" for p in parts)
        if not n:
            return "crankshaft"
        return ("single-cylinder" if n == 1 else f"{n}-cylinder") + " piston and crank"
    if kinds_present & set(WHEELS) and len(parts) == 1:
        return parts[0]["type"].replace("_", " ")
    gears = [p for p in parts if p["type"] in GEARS]
    kinds = sorted({p["type"].replace("_", " ") for p in gears})
    if len(gears) == 2 and len(kinds) == 1:
        return f"{kinds[0]} pair {gears[0]['teeth']}/{gears[1]['teeth']}"
    if len(gears) == 1:
        return f"{kinds[0]} {gears[0]['teeth']} teeth"
    if gears:
        return f"gear train: {', '.join(kinds)} ({len(gears)} gears)"
    return f"{parts[0]['type'].replace('_', ' ')}"


# --- where every part goes ---------------------------------------------------------------------

MM = 0.001


def _v(*a):
    return [float(x) for x in a]


def _add(a, b):
    return [a[i] + b[i] for i in range(3)]


def _sub(a, b):
    return [a[i] - b[i] for i in range(3)]


def _mul(a, s):
    return [x * s for x in a]


def _dot(a, b):
    return sum(a[i] * b[i] for i in range(3))


def _cross(a, b):
    return [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]]


def _unit(a):
    n = math.sqrt(_dot(a, a))
    return [x / n for x in a] if n > 1e-12 else [0.0, 0.0, 0.0]


def _perp(a, axis):
    return _sub(a, _mul(axis, _dot(a, axis)))


# rotations (3x3, columns are the part's local X, Y, Z in the world): a gear's own axis is its local Z
FRONT = [[1.0, 0.0, 0.0], [0.0, 0.0, -1.0], [0.0, 1.0, 0.0]]  # local Z -> world -Y (facing the camera)
BEVEL_FRONT = [[1.0, 0.0, 0.0], [0.0, 0.0, 1.0], [0.0, -1.0, 0.0]]  # local Z -> world +Y (apex behind)
IDENTITY = [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]]


def _col(rot, i):
    return [rot[0][i], rot[1][i], rot[2][i]]


def _rot_from_axis(z):
    """A rotation whose local Z is z, local X as close to world +Z (or +X) as it can be."""
    z = _unit(z)
    ref = [0.0, 0.0, 1.0] if abs(z[2]) < 0.9 else [1.0, 0.0, 0.0]
    x = _unit(_perp(ref, z))
    y = _cross(z, x)
    return [[x[0], y[0], z[0]], [x[1], y[1], z[1]], [x[2], y[2], z[2]]]


def _local_angle(rot, d):
    return math.atan2(_dot(_col(rot, 1), d), _dot(_col(rot, 0), d))


def gear_info(p):
    """The numbers of one gear the layout, the motion and the checks use (metres)."""
    t = p["type"]
    if t in WHEELS:
        return {"pitch": p["diameter"] * MM / 2, "tip": p["diameter"] * MM / 2, "module": None, "teeth": None}
    if t == "bearing":
        return {"pitch": None, "tip": p["outer_diameter"] * MM / 2, "module": None, "teeth": None}
    if t in STILL or t in ENGINE[1:]:
        return {"pitch": None, "tip": None, "module": None, "teeth": None}
    if t == "crankshaft":
        return {"pitch": p["stroke"] / 2 * MM, "tip": crank_sweep(p) * MM, "module": None, "teeth": None}
    if t in STAGE4:
        r = stage4_radius(p)
        return {"pitch": r, "tip": r, "module": None, "teeth": p.get("teeth")}
    m = p["module"] * MM
    if t == "rack":
        return {"pitch": None, "tip": None, "module": m, "teeth": p["teeth"]}
    if t == "bevel_gear":
        b = bevel_numbers(m, p["teeth"], p["mate_teeth"], p["width"] * MM)
        return {"pitch": b["pitch"], "tip": b["pitch"] + m, "module": m, "teeth": p["teeth"], "bevel": b}
    c = circles(m, p["teeth"], p["pressure_angle"], p.get("helix_angle") or 0, p.get("shift") or 0,
                t == "ring_gear", p.get("shortening") or 0)
    tip = p["rim_diameter"] * MM / 2 if t == "ring_gear" else c["tip"]
    return {"pitch": c["pitch"], "tip": tip, "module": m, "teeth": p["teeth"], "circles": c}


def stage4_radius(p):
    """How far a stage 4 part reaches from its own axis, metres (0 for what rides on another part)."""
    t = p["type"]
    if t == "pulley":
        r = p["datum_diameter"] / 2 + V_SECTIONS[p["section"]][4]
    elif t == "sprocket":
        r = sprocket_radius(p["pitch"], p["teeth"]) + 0.4 * p["pitch"]
    elif t == "cam":
        r = p["base_diameter"] / 2 + p["lift"]
    elif t == "worm":
        r = p["pitch_diameter"] / 2 + p["module"]
    elif t == "worm_wheel":
        r = p["module"] * (p["teeth"] + 2) / 2
    elif t in ("bolt", "nut"):
        r = (p.get("across_flats") or 1.6 * p.get("diameter", 10)) / math.sqrt(3)
    elif t == "spring" and not p.get("on_valve"):
        r = (p["mean_diameter"] + p["wire_diameter"]) / 2
    else:
        return 0.0
    return r * MM


def crank_sweep(c):
    """How far a crankshaft reaches from its axis as it turns, mm (webs round the pins, counterweights)."""
    return max(c["counterweight_radius"] if c["counterweights"] else 0.0, c["stroke"] / 2 + c["web_radius"],
               c["journal_diameter"] / 2 + 0.08 * c["stroke"])


def _extent(p, info):
    """Half the part's size along its axis, metres (the rest of the layout only needs a rough one)."""
    if p["type"] == "crankshaft":
        return (len(p["throw_angles"]) * p["spacing"] / 2 + 1.5 * p["journal_width"]) * MM
    if p["type"] in ENGINE:
        return 0.0
    if p["type"] == "pulley":
        e, f = V_SECTIONS[p["section"]][2:4]
        return ((p["grooves"] - 1) * e + 2 * f) / 2 * MM
    if p["type"] == "sprocket":
        return (0.29 * p["pitch"] + p["hub"][1]) * MM
    if p["type"] == "worm":
        return (p["length"] or 0) / 2 * MM
    if p["type"] in STAGE4:
        return (p.get("width") or 0) / 2 * MM
    if p["type"] == "shaft":
        return (p["length"] or 0) * MM / 2
    if p["type"] in WHEELS:
        return p["length"] * MM / 2
    w = (p.get("width") or 0) * MM
    return max(w, (p.get("hub_length") or 0) * MM) / 2


def layout(sheet):
    """Where every part goes: for each part its centre (metres), rotation (3x3, columns its local
    axes), phase (radians about its own axis) and numbers; the pairs the mesh check measures; and
    the loop of turns. Gears face the camera (their axes along Y) unless a bevel pair turns one
    sideways; each new gear goes to the right of the one it meshes with unless `at` says otherwise
    or that place is taken."""
    by = {p["name"]: p for p in sheet["parts"]}
    links = sheet["links"]
    adj = {n: [] for n in by}
    for l in links:
        adj[l["a"]].append((l["b"], l))
        adj[l["b"]].append((l["a"], l))
    # helical pairs: their shortening; external pairs: their working centre distance
    for l in links:
        if l["kind"] != "mesh":
            continue
        a, b = by[l["a"]], by[l["b"]]
        if a["type"] in EXTERNAL and b["type"] in EXTERNAL:
            geo = pair_geometry(a["module"], a["teeth"], b["teeth"], a["pressure_angle"], a.get("helix_angle") or 0,
                                a["shift"] or 0, b["shift"] or 0)
            for g in (a, b):
                g["shortening"] = max(g.get("shortening") or 0.0, geo["shortening"])
    place = {}
    order = []
    sets_of = {n: ps["name"] for ps in sheet.get("planetary") or []
               for n in (ps["sun"], ps["ring"], ps["carrier"], *ps["planets"])}

    def put(name, centre, rot, phase=0.0):
        place[name] = {"centre": centre, "rotation": rot, "phase": phase}
        order.append(name)

    def taken(name, centre, rot, partner):
        """Whether a gear at this place would run into a part already placed (its partner aside)."""
        p = by[name]
        r = _radius(p)
        axis = _col(rot, 2)
        for other, pl in place.items():
            o = by[other]
            if other == partner or o["type"] == "shaft" or (sets_of.get(name) and sets_of.get(name) == sets_of.get(other)):
                continue  # the members of one planetary set are meant to be in each other's way
            d = _sub(centre, pl["centre"])
            if abs(_dot(d, axis)) > _extent(p, None) + _extent(o, None) + 0.0005:
                continue
            across = math.sqrt(_dot(_perp(d, axis), _perp(d, axis)))
            ro = _radius(o)
            if o["type"] == "ring_gear" and across + r <= gear_info(o)["circles"]["tip"] * 0.98:
                continue  # wholly inside a ring gear
            if p["type"] == "ring_gear" and across + ro <= gear_info(p)["circles"]["tip"] * 0.98:
                continue
            if across < (r + ro) * 0.98:
                return True
        return False

    # a planetary set is laid out from its sun, a nut and washer from their bolt
    suns = [ps["sun"] for ps in sheet.get("planetary") or []] + [p["name"] for p in sheet["parts"] if p["type"] == "bolt"]
    roots = suns + [sheet["driver"]] + [p["name"] for p in sheet["parts"] if p["name"] != sheet["driver"]]
    for root in roots:
        if root in place or by[root]["type"] == "centre_housing":  # placed last, between its shaft's wheels
            continue
        p = by[root]
        if place:  # a part not connected to the first group goes beside it, to the right
            r = _radius(p)
            centre = _v(_rough_span(place, by)[1] + 0.3 * r + r + 0.005, 0, 0)
        else:
            centre = _v(0, 0, 0)
        rot = BEVEL_FRONT if p["type"] == "bevel_gear" else IDENTITY if p["type"] in ("rack", "bolt", "spring") \
            else _rot_from_axis([1.0, 0.0, 0.0]) if p["type"] == "worm" else FRONT
        put(root, centre, rot, math.radians(p.get("phase") or 0.0))
        queue = [root]
        while queue:
            cur = queue.pop(0)
            for other, l in adj[cur]:
                if other in place or l["kind"] == "case":
                    continue
                if l["kind"] == "shaft":
                    _place_on_shaft(cur, other, by, place, put, links)
                elif l["kind"] == "housing":  # a volute stands exactly where its wheel does
                    put(other, list(place[cur]["centre"]), place[cur]["rotation"], 0.0)
                elif l["kind"] in ENGINE_LINKS:
                    _place_engine(l, by, place, put, links)
                elif l["kind"] in STAGE4_LINKS:
                    _place_stage4(l, cur, other, by, place, put)
                else:
                    _place_mesh(cur, other, by, place, put, taken)
                queue.append(other)
    _size_shafts(by, place, links)
    for l in links:
        if l["kind"] == "case":
            _place_centre_housing(by[l["a"]], by[l["b"]], by, place, put, links)
    pairs = []
    for l in links:
        a, b = by[l["a"]], by[l["b"]]
        if l["kind"] == "case":
            continue  # it stands still round the shaft and its bearings: the clash check covers it
        if l["kind"] in ENGINE_LINKS:
            pairs.append(_engine_pair(l, by, place))
            continue
        if l["kind"] in STAGE4_LINKS:
            pair = _stage4_pair(l, by, place, pairs)
            if pair:
                pairs.append(pair)
            continue
        if l["kind"] == "shaft":
            pairs.append({"a": l["a"], "b": l["b"], "kind": "shaft"})
            continue
        if l["kind"] == "housing":  # the wheel must clear its volute all the way round
            pairs.append({"a": b["name"], "b": a["name"], "kind": "housing",
                          "clearance_m": wheel_clearance(b["diameter"]) * MM})
            continue
        pairs.append(_pair_numbers(a, b, place))
    for ps in sheet.get("planetary") or []:  # planets ride round: their mesh is checked, the set's ratio as a whole
        members = {ps["sun"], ps["ring"], *ps["planets"]}
        for pair in pairs:
            if pair["kind"] == "mesh" and {pair["a"], pair["b"]} <= members:
                pair["kind"] = "planet"
        rates = planetary_rates(ps)
        out = next(k for k in ("carrier", "ring", "sun") if k not in (ps["fixed"], ps["input"]))
        ratio = rates[out]
        pairs.append({"a": ps[ps["input"]], "b": ps[out], "kind": "train", "ratio": float(abs(ratio)),
                      "same_direction": ratio > 0})
    return {"place": place, "order": order, "pairs": pairs}


def _radius(p):
    """How far a part reaches from its axis (a rack: half its length), metres."""
    if p["type"] == "shaft":
        return (p["diameter"] or 10) * MM / 2
    if p["type"] in STILL or p["type"] in ENGINE[1:]:
        return 0.0  # placed round its wheel, shaft or crank, never in anyone's way
    if p["type"] in STAGE4:
        return stage4_radius(p)
    info = gear_info(p)
    return info["tip"] or (p["teeth"] + 1) * math.pi * info["module"] / 2


def _rough_span(place, by):
    xs = []
    for n, pl in place.items():
        r = _radius(by[n])
        xs += [pl["centre"][0] - r, pl["centre"][0] + r]
    return min(xs), max(xs)


def _place_on_shaft(cur, other, by, place, put, links):
    """A gear and its shaft share one axis. The first gear on a shaft sits in its middle; each
    further gear on it sits behind the one before (further from the camera), a millimetre apart."""
    c = by[cur]
    pl = place[cur]
    if by[other]["type"] == "shaft":  # the shaft follows the gear: same centre and axis
        put(other, list(pl["centre"]), _rot_from_axis(_col(pl["rotation"], 2)) if c["type"] == "bevel_gear"
            else pl["rotation"], 0.0)
        return
    # a further part on a shaft already placed: behind the parts already on it (a turbo's wheels get
    # room for their housings' back plates beside them)
    axis = _col(pl["rotation"], 2)
    on = [l["a"] for l in links if l["kind"] == "shaft" and l["b"] == cur and l["a"] in place]
    g = by[other]
    deepest, last = 0.0, None
    for n in on:
        d = _dot(_sub(place[n]["centre"], pl["centre"]), axis)
        end = d - _extent(by[n], None)
        if end < deepest or last is None:
            deepest, last = min(deepest, end), n
    wheels = [by[n]["diameter"] for n in (other, last) if n and by[n]["type"] in WHEELS]
    gap = 0.3 * max(wheels) * MM if wheels else 0.001  # room for both housings' scrolls
    offset = deepest - gap - _extent(g, None)
    if g["type"] == "bevel_gear":
        rot = BEVEL_FRONT
    elif g["type"] == "turbine_wheel":
        rot = _rot_from_axis(_mul(axis, -1))  # its nose faces away from the compressor
    else:
        rot = pl["rotation"]
    put(other, _add(pl["centre"], _mul(axis, offset)), rot, 0.0)


def _cols(x, y, z):
    """A rotation (rows) from its three columns: the part's local X, Y and Z in the world."""
    return [[x[0], y[0], z[0]], [x[1], y[1], z[1]], [x[2], y[2], z[2]]]


def _apply(rot, v):
    return [sum(rot[i][j] * v[j] for j in range(3)) for i in range(3)]


def throw_pin(c, throw):
    """A throw's crankpin centre in the crankshaft's own frame, metres: angle 0 straight up (+Y)."""
    angles = c["throw_angles"] or [0.0]
    n = len(angles)
    a = math.radians(angles[throw])
    r = c["stroke"] / 2 * MM
    return [-r * math.sin(a), r * math.cos(a), (throw - (n - 1) / 2) * c["spacing"] * MM]


def crank_slider(pin, centre, axis_point, up, length):
    """Where a piston's pin is for a crankpin at `pin` (world), the cylinder's axis through
    axis_point along up: the rod of `length` reaches it straight up the bore."""
    d = _sub(pin, axis_point)
    along = _dot(d, up)
    off = _sub(d, _mul(up, along))
    height = along + math.sqrt(max(length ** 2 - _dot(off, off), 0.0))
    return _add(axis_point, _mul(up, height))


def _place_engine(l, by, place, put, links):
    """A rod on its crankpin, its piston straight up the cylinder's axis above the throw (the crank's
    local Y is up, the gudgeon pin parallel to the crank), the rings with the piston, the cylinder
    standing on its foot above everything the crank sweeps."""
    a_name, b_name = l["a"], l["b"]
    if l["kind"] == "crank":
        rod, c = by[a_name], by[b_name]
        pl = place[c["name"]]
        rot = pl["rotation"]
        axis, up = _col(rot, 2), _col(rot, 1)
        local = throw_pin(c, rod["throw"])
        pin = _add(pl["centre"], _apply(rot, local))
        axis_point = _add(pl["centre"], _mul(axis, local[2]))
        q = crank_slider(pin, pl["centre"], axis_point, up, rod["length"] * MM)
        y = _unit(_sub(q, pin))
        put(rod["name"], pin, _cols(_cross(y, axis), y, axis), 0.0)
        place[rod["name"]]["engine"] = {"pin_local": local, "axis_point": axis_point, "up": up, "piston_at": q,
                                        "axis": axis, "stroke_m": c["stroke"] * MM}
    elif l["kind"] == "pin":
        e = place[a_name]["engine"]
        put(b_name, list(e["piston_at"]), _cols(e["axis"], _cross(e["up"], e["axis"]), e["up"]), 0.0)
        place[b_name]["engine"] = e
    elif l["kind"] == "ring":
        put(b_name, list(place[a_name]["centre"]), place[a_name]["rotation"], 0.0)
    else:  # the cylinder round its piston
        e = place[a_name]["engine"]
        put(b_name, _add(e["axis_point"], _mul(e["up"], by[b_name]["bottom"] * MM)), place[a_name]["rotation"], 0.0)


STAGE4_LINKS = ("bolt", "belt", "chain", "cam", "spring", "worm", "carrier")
UP = [0.0, 0.0, 1.0]


def _upright(z, x=(1.0, 0.0, 0.0)):
    """A rotation whose local Z is z and local X is x (perpendicular to it)."""
    return _cols(list(x), _cross(z, list(x)), z)


def valve_spring_place(valve, spring):
    """Where a valve's spring stands in the valve's own frame, mm: its top just under the bucket, its
    foot (the seat in the head) its fitted length further down the stem."""
    top = 0.35 * valve["bucket_diameter"] + 0.5 if valve.get("bucket_diameter") else 0.385 * valve["head_diameter"] + 0.5
    return top, top + spring["free_length"]


def _place_stage4(l, cur, other, by, place, put):
    k = l["kind"]
    if k == "bolt":  # the nut screwed on, seated; the washer between it and the work
        bolt, part = by[l["a"]], by[l["b"]]
        pl = place[bolt["name"]]
        axis = _col(pl["rotation"], 2)
        if part["type"] == "nut":
            z = bolt["nut_at"]
            put(part["name"], _add(pl["centre"], _mul(axis, z * MM)), pl["rotation"],
                2 * math.pi * (z / bolt["pitch"]) % (2 * math.pi))  # its thread in the bolt's
        else:
            z = (bolt["nut_at"] + 0.84 * bolt["diameter"]) if bolt["nut_at"] is not None else -0.2 * bolt["diameter"]
            put(part["name"], _add(pl["centre"], _mul(axis, z * MM)), pl["rotation"], 0.0)
    elif k in ("belt", "chain"):
        drive = by[l["a"]]
        a, b = drive["on"]
        ref = a if a in place else b
        rot = place[ref]["rotation"]
        x = _col(rot, 0)
        c = drive["centre_distance"] * MM
        ca = place[a]["centre"] if a in place else _sub(place[b]["centre"], _mul(x, c))
        for name, centre in ((a, ca), (b, _add(ca, _mul(x, c)))):
            if name not in place:
                put(name, centre, rot, 0.0)
        if k == "chain":  # each sprocket turned so a tooth gap waits where its chain's roller sits
            lay = chain_layout(drive["pitch"], by[a]["teeth"], by[b]["teeth"], drive["centre_distance"])
            place[a]["phase"], place[b]["phase"] = lay["phase"]
        if drive["name"] not in place:
            put(drive["name"], list(ca), rot, 0.0)
    elif k == "cam":  # a follower above its cam, a valve below (an overhead cam opens it downward)
        cam, f = by[l["a"]], by[l["b"]]
        pl = place[cam["name"]]
        d = UP if f["type"] == "follower" else _mul(UP, -1)
        x = _col(pl["rotation"], 2)  # the roller's axle (the follower's local X) parallel to the cam's shaft
        put(f["name"], _add(pl["centre"], _mul(d, f["table"][0] * MM)), _upright(d, x), 0.0)
        if f["type"] == "valve":
            pl["phase"] = math.pi  # the rise starts at the cam's point facing its follower: here, below
    elif k == "spring":
        valve, spring = by[l["a"]], by[l["b"]]
        pv = place[valve["name"]]
        d = _col(pv["rotation"], 2)
        top, foot = valve_spring_place(valve, spring)
        put(spring["name"], _add(pv["centre"], _mul(d, foot * MM)), _upright(_mul(d, -1), _col(pv["rotation"], 0)), 0.0)
    elif k == "worm":
        worm, wheel = by[l["a"]], by[l["b"]]
        pl = place[worm["name"]]
        a = (worm["pitch_diameter"] + worm["module"] * wheel["teeth"]) / 2 * MM
        put(wheel["name"], _add(pl["centre"], _mul(UP, a)), FRONT, -math.pi / 2 + math.pi / wheel["teeth"])
    elif k == "carrier":
        put(other, list(place[cur]["centre"]), place[cur]["rotation"], 0.0)


def _stage4_pair(l, by, place, pairs):
    k = l["kind"]
    if k in ("belt", "chain"):
        drive = by[l["a"]]
        if any(p.get("drive") == drive["name"] for p in pairs):
            return None
        a, b = (by[n] for n in drive["on"])
        size = (lambda p: p["datum_diameter"]) if k == "belt" else (lambda p: p["teeth"])
        return {"a": a["name"], "b": b["name"], "kind": "drive", "drive": drive["name"], "ratio": size(a) / size(b),
                "same_direction": True}
    if k == "cam":
        return {"a": l["a"], "b": l["b"], "kind": "cam", "module_m": 0.002}
    if k == "worm":
        worm, wheel = by[l["a"]], by[l["b"]]
        a = (worm["pitch_diameter"] + worm["module"] * wheel["teeth"]) / 2 * MM
        return {"a": worm["name"], "b": wheel["name"], "kind": "mesh", "module_m": worm["module"] * MM, "centre_m": a,
                "ratio": worm["starts"] / wheel["teeth"], "same_direction": worm["hand"] == "left"}
    return None


def _engine_pair(l, by, place):
    """What the mesh check measures for an engine link (bpy_lib.measure)."""
    a, b = by[l["a"]], by[l["b"]]
    if l["kind"] == "crank":
        return {"a": a["name"], "b": b["name"], "kind": "crank", "pin_local_m": place[a["name"]]["engine"]["pin_local"],
                "length_m": a["length"] * MM}
    if l["kind"] == "pin":
        return {"a": a["name"], "b": b["name"], "kind": "pin", "length_m": a["length"] * MM}
    if l["kind"] == "ring":
        return {"a": a["name"], "b": b["name"], "kind": "ring"}
    return {"a": a["name"], "b": b["name"], "kind": "liner", "crown_m": a["compression_height"] * MM,
            "rings_low_m": piston_grooves(a["bore"], a["compression_height"])[-1][1] * MM, "top_m": b["length"] * MM,
            "bottom_m": 0.0, "stroke_m": place[a["name"]]["engine"]["stroke_m"]}


HOUSING_GAP = 0.2  # mm between a centre housing's end faces and the volutes' back plates


def _place_centre_housing(p, shaft, by, place, put, links):
    """A centre housing fills the room between the two wheels on its shaft: from the compressor
    volute's back plate to the turbine volute's (or, with no volute, to just behind each wheel),
    round the bearings. Its own axis (local Z) points to the compressor; local X is up, where the
    oil comes in. The numbers kits.centre_housing is built from are kept on its place."""
    sp = place[shaft["name"]]
    axis = _col(sp["rotation"], 2)
    on = _shaft_parts(shaft["name"], links, by)
    volutes = {q["houses"]: q for q in by.values() if q["type"] == "volute"}

    def along(name):
        return _dot(place[name]["centre"], axis)

    comp = next(g for g in on if g["type"] == "compressor_wheel")
    turb = next(g for g in on if g["type"] == "turbine_wheel")
    u = 1.0 if along(turb["name"]) > along(comp["name"]) else -1.0  # from the compressor to the turbine

    def inner_face(w, side):
        v = volutes.get(w["name"])
        plate = (wheel_clearance(w["diameter"]) + (v["wall"] or max(1.0, 0.035 * w["diameter"]))) if v \
            else 0.02 * w["diameter"]
        return along(w["name"]) + side * (w["length"] / 2 + plate + HOUSING_GAP) * MM

    front, back = inner_face(comp, u), inner_face(turb, -u)
    length = (back - front) * u
    bearings = [g for g in on if g["type"] == "bearing"]
    room = []  # from each end face to the nearest face of each bearing, metres
    for g in bearings:
        s, half = along(g["name"]), g["width"] / 2 * MM
        room += [(s - front) * u - half, (back - s) * u - half]
    if length <= 0 or (room and min(room) < 0.0015):
        raise MechanismError(f"centre_housing {p['name']}: the bearings on {shaft['name']} must sit between its "
                             "compressor wheel and its turbine wheel (list them between the wheels)")
    end_room = min(room or [length / 3])
    seat = max([g["outer_diameter"] for g in bearings] + [1.6 * shaft["diameter"]])
    end_wall = max(1.0, min(0.06 * comp["diameter"], end_room / MM - 0.5))
    numbers = {"length": round(length / MM, 3), "shaft_diameter": shaft["diameter"],
               "bore_diameter": round(seat + 0.4, 3), "outer_diameter": p["outer_diameter"],
               "front_flange_diameter": round(max(p["outer_diameter"], 1.1 * comp["diameter"]), 3),
               "back_flange_diameter": round(max(p["outer_diameter"], 1.1 * turb["diameter"]), 3),
               "end_wall": round(end_wall, 3), "oil_ports": p["oil_ports"]}
    rot = _rot_from_axis(_mul(axis, -u))
    centre = _add(sp["centre"], _mul(axis, (front + back) / 2 - _dot(sp["centre"], axis)))
    put(p["name"], centre, rot, 0.0)
    place[p["name"]]["housing"] = numbers


def _place_mesh(cur, other, by, place, put, taken):
    a, b = by[cur], by[other]
    pa = place[cur]
    if "rack" in (a["type"], b["type"]):
        _place_rack(cur, other, by, place, put)
        return
    if a["type"] == "bevel_gear":
        _place_bevel(cur, other, by, place, put)
        return
    axis = _col(pa["rotation"], 2)
    internal = "ring_gear" in (a["type"], b["type"])
    if internal:
        ring, inner = (a, b) if a["type"] == "ring_gear" else (b, a)
        dist = gear_info(inner)["circles"]["transverse_module"] * (ring["teeth"] - inner["teeth"]) / 2
    else:
        geo = pair_geometry(a["module"] * MM, a["teeth"], b["teeth"], a["pressure_angle"], a.get("helix_angle") or 0,
                            a["shift"] or 0, b["shift"] or 0)
        dist = geo["centre"]
    wanted = b.get("at") if b.get("at") is not None else 0.0
    tries = [wanted] + [wanted + d for d in (180, 60, -60, 120, -120, 30, -30, 90, -90, 150, -150)]
    # in the plane of the gears: angle 0 is the world's +X side, 90 the top (+Z) when they face the camera
    right = _unit(_perp([1.0, 0.0, 0.0], axis)) if abs(axis[0]) < 0.9 else _unit(_perp([0.0, 1.0, 0.0], axis))
    up = _cross(right, axis) if _dot(_cross(right, axis), [0, 0, 1]) >= 0 else _mul(_cross(right, axis), -1)
    rot = pa["rotation"]
    for ang in tries:
        d = _add(_mul(right, math.cos(math.radians(ang))), _mul(up, math.sin(math.radians(ang))))
        if internal and a["type"] != "ring_gear":
            centre = _sub(pa["centre"], _mul(d, dist))  # the ring round the inner gear
        else:
            centre = _add(pa["centre"], _mul(d, dist))  # beside it, or inside the ring towards d
        if not taken(other, centre, rot, cur):
            break
    contact = _contact(a, pa["centre"], b, centre, axis)
    phase = _mate_phase(a, pa, b, centre, rot, contact)
    put(other, centre, rot, phase)


def _contact(a, ca, b, cb, axis):
    """The pitch point of a pair: on the line of centres at gear a's pitch radius (outward from a
    ring's centre when one of them is a ring gear)."""
    ia = gear_info(a)
    if b["type"] == "ring_gear":
        # a inside ring b: the contact is on the far side of a from b's centre
        return _add(ca, _mul(_unit(_perp(_sub(ca, cb), axis)), ia["pitch"]))
    if a["type"] == "ring_gear":
        ib = gear_info(b)
        return _add(cb, _mul(_unit(_perp(_sub(cb, ca), axis)), ib["pitch"]))
    return _add(ca, _mul(_unit(_perp(_sub(cb, ca), axis)), ia["pitch"]))


def turn_sign(axis_a, ca, axis_b, cb, contact):
    """+1 when two meshing gears turn the same way about their own axes, -1 when opposite."""
    va = _cross(axis_a, _sub(contact, ca))
    vb = _cross(axis_b, _sub(contact, cb))
    return 1 if _dot(va, vb) > 0 else -1


def _mate_phase(a, pa, b, cb, rot_b, contact):
    """The angle gear b starts at so it meshes with gear a as a already stands: in the state where
    one has a tooth at the contact (the external gear) and the other a gap, then turned on with a."""
    axis_a, axis_b = _col(pa["rotation"], 2), _col(rot_b, 2)
    da = _unit(_perp(_sub(contact, pa["centre"]), axis_a))
    db = _unit(_perp(_sub(contact, cb), axis_b))
    theta_a, theta_b = _local_angle(pa["rotation"], da), _local_angle(rot_b, db)
    pitch_a, pitch_b = 2 * math.pi / a["teeth"], 2 * math.pi / b["teeth"]
    # the ring gear (or, between two external gears, the new one) has the gap at the contact
    a_gap = a["type"] == "ring_gear"
    start_a = theta_a - (pitch_a / 2 if a_gap else 0.0)
    start_b = theta_b - (0.0 if a_gap else pitch_b / 2)
    sign = turn_sign(axis_a, pa["centre"], axis_b, cb, contact)
    turned = pa["phase"] - start_a
    return start_b + sign * turned * a["teeth"] / b["teeth"]


def _place_bevel(cur, other, by, place, put):
    """Two bevel gears at 90 degrees: their pitch cones share one apex. The new gear stands on the
    world's +X side of the first (or, when the first already looks along X, below it)."""
    a, b = by[cur], by[other]
    pa = place[cur]
    ia, ib = gear_info(a), gear_info(b)
    ua = _col(pa["rotation"], 2)  # towards the apex
    L = ia["bevel"]["cone_distance"]
    apex = _add(pa["centre"], _mul(ua, L * math.cos(math.radians(ia["bevel"]["cone_deg"]))))
    d = _unit(_perp([1.0, 0.0, 0.0], ua)) if abs(ua[0]) < 0.9 else _unit(_perp([0.0, 0.0, -1.0], ua))
    ub = _mul(d, -1)
    cb = _add(apex, _mul(d, L * math.cos(math.radians(ib["bevel"]["cone_deg"]))))
    rot = _rot_from_axis(ub)
    contact = _add(pa["centre"], _mul(d, ia["pitch"]))
    phase = _mate_phase(a, pa, b, cb, rot, contact)
    put(other, cb, rot, phase)


def _place_rack(cur, other, by, place, put):
    """A rack lies under the gear that drives it, teeth up, its pitch line on the gear's pitch
    circle (moved out by the gear's profile shift); a gap of the rack under a tooth of the gear."""
    a, b = by[cur], by[other]
    pa = place[cur]
    if a["type"] == "rack":  # a gear on a rack placed first: the gear above its middle gap
        rack, gear, pr = a, b, pa
        ig = gear_info(gear)
        lift = ig["pitch"] + (gear.get("shift") or 0) * ig["module"]
        centre = _add(pr["centre"], [_gap_x(rack), 0.0, lift])
        put(other, centre, FRONT, -math.pi / 2)  # a tooth pointing straight down into the gap
        return
    gear, rack = a, b
    ig = gear_info(gear)
    lift = ig["pitch"] + (gear.get("shift") or 0) * ig["module"]
    axis = _col(pa["rotation"], 2)
    # the gear has turned this far from having a tooth pointing straight down; its bottom moved
    # along the rack by the pitch radius times that, and the rack's gap must be there
    turned = pa["phase"] - (-math.pi / 2)
    slide = ig["pitch"] * turned * turn_sign_rack(axis)
    p = math.pi * rack["module"] * MM
    shift = slide - _gap_x(rack)
    shift -= p * round(shift / p)  # another gap a whole pitch along: the rack stays centred under the gear
    centre = _add(pa["centre"], [shift, 0.0, -lift])
    put(other, centre, IDENTITY, 0.0)


def turn_sign_rack(axis):
    """Which way along +X the bottom of a gear moves when it turns positively about its axis."""
    v = _cross(axis, [0.0, 0.0, -1.0])
    return 1 if v[0] > 0 else -1


def _gap_x(rack):
    """Where the gap nearest the rack's middle is, along its length."""
    p = math.pi * rack["module"] * MM
    n = rack["teeth"]
    return 0.0 if n % 2 == 0 else p / 2


def _size_shafts(by, place, links):
    """A shaft runs through every gear on it, with some to spare at each end."""
    for s in [p for p in by.values() if p["type"] == "shaft"]:
        if s["name"] not in place:
            continue
        on = [l["a"] for l in links if l["kind"] == "shaft" and l["b"] == s["name"]]
        pl = place[s["name"]]
        axis = _col(pl["rotation"], 2)
        lo, hi = 0.0, 0.0
        for n in on:
            d = _dot(_sub(place[n]["centre"], pl["centre"]), axis)
            g = by[n]
            if g["type"] == "bevel_gear":
                info = gear_info(g)
                lo = min(lo, d - (g.get("hub_length") or 0) * MM - info["module"] * 2)
                hi = max(hi, d + info["bevel"]["face"])
            else:
                e = _extent(g, gear_info(g))
                lo, hi = min(lo, d - e), max(hi, d + e)
        spare = max(10.0, 1.5 * s["diameter"]) * MM
        lo, hi = lo - spare, hi + spare
        # a length given is kept, centred on the parts it carries (not on where the first one stood)
        pl["length_mm"] = s["length"] if s["length"] else round((hi - lo) / MM, 2)
        pl["centre"] = _add(pl["centre"], _mul(axis, (lo + hi) / 2))


def _pair_numbers(a, b, place):
    """What the mesh check measures for one meshing pair."""
    pa, pb = place[a["name"]], place[b["name"]]
    if "rack" in (a["type"], b["type"]):
        gear, rack = (a, b) if b["type"] == "rack" else (b, a)
        ig = gear_info(gear)
        return {"a": gear["name"], "b": rack["name"], "kind": "rack", "module_m": ig["module"],
                "centre_m": ig["pitch"] + (gear.get("shift") or 0) * ig["module"],
                "metres_per_radian": ig["pitch"]}
    axis_a, axis_b = _col(pa["rotation"], 2), _col(pb["rotation"], 2)
    contact = _contact(a, pa["centre"], b, pb["centre"], axis_a)
    sign = turn_sign(axis_a, pa["centre"], axis_b, pb["centre"], contact)
    if a["type"] == "bevel_gear":
        centre = None
    else:
        d = _perp(_sub(pb["centre"], pa["centre"]), axis_a)
        centre = math.sqrt(_dot(d, d))
    return {"a": a["name"], "b": b["name"], "kind": "mesh", "module_m": a["module"] * MM, "centre_m": centre,
            "ratio": a["teeth"] / b["teeth"], "same_direction": sign > 0}


# --- the loop of turns -------------------------------------------------------------------

def speeds(sheet):
    """Each part's turns per turn of the driver (signed: + the driver's way), and each turning
    part's picture period in its own turns: a plain gear looks the same again after one tooth, a
    gear with a keyway or holes only after a whole turn (or after a share of holes and teeth)."""
    by = {p["name"]: p for p in sheet["parts"]}
    lay = layout(sheet)
    rates = {sheet["driver"]: Fraction(1)}
    todo = [sheet["driver"]]
    racks = {}
    while todo:
        cur = todo.pop()
        for pair in lay["pairs"]:
            if cur not in (pair["a"], pair["b"]):
                continue
            other = pair["b"] if pair["a"] == cur else pair["a"]
            if other in rates or other in racks or pair["kind"] in ("housing", "cam", "planet", "train") + ENGINE_LINKS:
                continue
            if pair["kind"] in ("drive",) or (pair["kind"] == "mesh" and "teeth" not in by[pair["a"]]):
                ratio = Fraction(pair["ratio"]).limit_denominator(100000)  # a belt, chain or worm drive
                step = ratio if pair["a"] == cur else 1 / ratio
                rates[other] = rates[cur] * step * (1 if pair["same_direction"] else -1)
            elif pair["kind"] == "shaft":
                rates[other] = rates[cur] * Fraction(_cage(by[other])).limit_denominator(1000) \
                    / Fraction(_cage(by[cur])).limit_denominator(1000)
            elif pair["kind"] == "rack":
                racks[other] = (cur, pair["metres_per_radian"])
                continue
            else:
                ratio = Fraction(by[pair["a"]]["teeth"], by[pair["b"]]["teeth"])
                step = ratio if pair["a"] == cur else 1 / ratio
                rates[other] = rates[cur] * step * (1 if pair["same_direction"] else -1)
            todo.append(other)
    for ps in sheet.get("planetary") or []:  # Willis: each member from the input, the planets their own
        w = planetary_rates(ps)
        base = rates.get(ps[ps["input"]], Fraction(1))
        for key in ("sun", "ring", "carrier"):
            rates[ps[key]] = base * w[key]
        for pn in ps["planets"]:
            rates[pn] = base * w["planet"]
    periods = {}
    for name in rates:
        periods[name] = period(by[name])
    for ps in sheet.get("planetary") or []:  # the carrier must come back to its start: planets swap places
        periods[ps["carrier"]] = Fraction(1)
    return {"rates": rates, "periods": periods, "racks": racks}


def _cage(p):
    """How fast a part turns for each turn of the shaft it sits on: a bearing at its cage's speed (its
    balls run round, its races look the same at any angle), everything else 1."""
    if p["type"] == "bearing":  # a simple fraction close to it: the loop stays a few turns long
        return float(Fraction(bearing_numbers(p["bore"], p["outer_diameter"], p.get("balls"))["cage_ratio"])
                     .limit_denominator(3))
    return 1.0


def period(p):
    """A part's picture period in its own turns (Fraction)."""
    t = p["type"]
    if t in ("crankshaft", "cam", "spring", "carrier"):  # once a turn (a carrier: see speeds)
        return Fraction(1)
    if t in ("bolt", "nut"):
        return Fraction(1, 6)
    if t == "pulley":
        return Fraction(1, 72)
    if t == "worm":
        return Fraction(1, p["starts"])
    if t == "shaft":
        return Fraction(1) if p.get("keyway") else Fraction(1, 8)
    if t in WHEELS:
        return Fraction(1, p["blades"])
    if t == "bearing":
        return Fraction(1, bearing_numbers(p["bore"], p["outer_diameter"], p.get("balls"))["count"])
    if p.get("keyway"):
        return Fraction(1)
    teeth = p.get("teeth") or 1
    marks = p.get("holes") or p.get("spokes") or 0
    return Fraction(1, math.gcd(teeth, marks) if marks else teeth)


def loop_turns(sheet):
    """The fewest driver turns after which every turning part looks exactly as at the start."""
    s = speeds(sheet)
    turns = Fraction(0)
    for name, rate in s["rates"].items():
        if rate == 0:
            continue
        need = s["periods"][name] / abs(rate)  # driver turns for this part to repeat
        turns = need if turns == 0 else _lcm(turns, need)
    return turns or Fraction(1)


def _lcm(a, b):
    num = a.numerator * b.numerator // math.gcd(a.numerator, b.numerator)
    return Fraction(num, math.gcd(a.denominator, b.denominator))


# --- the scripts Blender runs ------------------------------------------------------------

def _m(v):
    return round(float(v) * MM, 7)


def part_code(p, lay=None, by=None):
    """The part's build script: one call to its kit tool with its numbers in metres."""
    t = p["type"]
    n = p["name"]
    if t == "shaft":
        length = p["length"] or ((lay or {}).get("place", {}).get(n, {}).get("length_mm")) or 4 * p["diameter"]
        args = (f"diameter={_m(p['diameter'])}, length={_m(length)}, keyway={bool(p['keyway'])}, "
                f"chamfer={_m(p['chamfer'])}")
        call = f"k.shaft({args}, name={n!r})"
    elif t == "compressor_wheel":
        call = (f"k.compressor_wheel(diameter={_m(p['diameter'])}, length={_m(p['length'])}, blades={p['blades']}, "
                f"splitters={bool(p['splitters'])}, inducer_ratio={round(p['inducer_diameter'] / p['diameter'], 5)}, "
                f"bore={_m(p['bore'])}, backsweep={p['backsweep']}, name={n!r})")
    elif t == "turbine_wheel":
        call = (f"k.turbine_wheel(diameter={_m(p['diameter'])}, length={_m(p['length'])}, blades={p['blades']}, "
                f"exducer_ratio={round(p['exducer_diameter'] / p['diameter'], 5)}, bore={_m(p['bore'])}, name={n!r})")
    elif t == "volute":
        w = (by or {}).get(p["houses"]) or {}
        turbine = w.get("type") == "turbine_wheel"
        inlet = w.get("exducer_diameter" if turbine else "inducer_diameter") or 0.7 * w.get("diameter", 50)
        call = (f"k.volute(wheel_diameter={_m(w.get('diameter', 50))}, wheel_length={_m(w.get('length', 20))}, "
                f"inlet_diameter={_m(inlet)}, kind={'turbine' if turbine else 'compressor'!r}, "
                f"wall={_m(p['wall']) if p.get('wall') else None}, flanges={bool(p['flanges'])}, name={n!r})")
    elif t in STAGE4:
        call = _stage4_call(p, by or {})
    elif t == "crankshaft":
        call = (f"k.crankshaft(stroke={_m(p['stroke'])}, crankpin_diameter={_m(p['crankpin_diameter'])}, "
                f"journal_diameter={_m(p['journal_diameter'])}, pin_width={_m(p['pin_width'])}, "
                f"web_thickness={_m(p['web_thickness'])}, journal_width={_m(p['journal_width'])}, "
                f"throw_angles={p['throw_angles']}, spacing={_m(p['spacing'])}, "
                f"counterweights={bool(p['counterweights'])}, counterweight_radius={_m(p['counterweight_radius'])}, "
                f"web_radius={_m(p['web_radius'])}, name={n!r})")
    elif t == "connecting_rod":
        call = (f"k.connecting_rod(length={_m(p['length'])}, big_end_bore={_m(p['big_end_bore'] + 0.04)}, "
                f"small_end_bore={_m(p['small_end_bore'] + 0.03)}, big_end_width={_m(p['big_end_width'])}, "
                f"small_end_width={_m(p['small_end_width'])}, name={n!r})")
    elif t == "piston":
        call = (f"k.piston(bore={_m(p['bore'])}, compression_height={_m(p['compression_height'])}, "
                f"pin_diameter={_m(p['pin_diameter'])}, skirt_length={_m(p['skirt_length'])}, "
                f"boss_gap={_m(p['boss_gap'])}, crown={p['crown']!r}, name={n!r})")
    elif t == "piston_rings":
        q = (by or {}).get(p["on_piston"]) or {}
        call = (f"k.piston_rings(bore={_m(q.get('bore', 80))}, compression_height="
                f"{_m(q.get('compression_height', 29))}, name={n!r})")
    elif t == "cylinder":
        call = (f"k.cylinder(bore={_m(p['bore'])}, length={_m(p['length'])}, wall={_m(p['wall'])}, "
                f"style={p['style']!r}, name={n!r})")
    elif t == "centre_housing":
        hs = (lay or {}).get("place", {}).get(n, {}).get("housing") or {}
        args = ", ".join(f"{k}={_m(v)}" for k, v in hs.items() if k != "oil_ports")
        call = f"k.centre_housing({args}, oil_ports={bool(p['oil_ports'])}, name={n!r})"
    elif t == "bearing":
        call = (f"k.bearing(bore={_m(p['bore'])}, outer_diameter={_m(p['outer_diameter'])}, width={_m(p['width'])}, "
                f"balls={p.get('balls')}, name={n!r})")
    elif t == "rack":
        call = (f"k.rack(module={_m(p['module'])}, teeth={p['teeth']}, width={_m(p['width'])}, "
                f"height={_m(p['height'])}, pressure_angle={p['pressure_angle']}, backlash={_m(p['backlash'])}, "
                f"name={n!r})")
    elif t == "ring_gear":
        call = (f"k.ring_gear(module={_m(p['module'])}, teeth={p['teeth']}, width={_m(p['width'])}, "
                f"rim_diameter={_m(p['rim_diameter'])}, pressure_angle={p['pressure_angle']}, "
                f"backlash={_m(p['backlash'])}, chamfer={_m(p['chamfer'])}, name={n!r})")
    elif t == "bevel_gear":
        hub = f"({_m(p['hub_diameter'])}, {_m(p['hub_length'])})" if p["hub_diameter"] else "None"
        call = (f"k.bevel_gear(module={_m(p['module'])}, teeth={p['teeth']}, mate_teeth={p['mate_teeth']}, "
                f"width={_m(p['width'])}, bore={_m(p['bore'])}, pressure_angle={p['pressure_angle']}, hub={hub}, "
                f"keyway={bool(p['keyway'])}, backlash={_m(p['backlash'])}, name={n!r})")
    else:
        hub = f"({_m(p['hub_diameter'])}, {_m(p['hub_length'])})" if p["hub_diameter"] else "None"
        common = (f"module={_m(p['module'])}, teeth={p['teeth']}, width={_m(p['width'])}, bore={_m(p['bore'])}, "
                  f"pressure_angle={p['pressure_angle']}, shift={p['shift'] or 0}, "
                  f"shortening={round(p.get('shortening') or 0, 5)}, backlash={_m(p['backlash'])}, "
                  f"chamfer={_m(p['chamfer'])}, hub={hub}, holes={p['holes']}, spokes={p['spokes']}, "
                  f"keyway={bool(p['keyway'])}")
        if t == "helical_gear":
            call = (f"k.helical_gear({common}, helix_angle={p['helix_angle']}, hand={p['hand']!r}, "
                    f"herringbone={bool(p['herringbone'])}, name={n!r})")
        else:
            call = f"k.spur_gear({common}, name={n!r})"
    return (f'"""{n}: a {t.replace("_", " ")} built by the part tools from its numbers."""\n'
            "from bpy_lib import kits as k\n\n\n"
            f"def build():\n    return {call}\n")


def _stage4_call(p, by):
    """The kit call that builds a stage 4 part from its numbers (metres)."""
    t, n = p["type"], p["name"]
    if t == "bolt":
        return (f"k.bolt({_m(p['diameter'])}, {_m(p['length'])}, pitch={_m(p['pitch'])}, "
                f"across_flats={_m(p['across_flats'])}, thread_length={_m(p['thread_length'])}, name={n!r})")
    if t in ("nut", "washer"):
        b = by[p["on_bolt"]]
        if t == "nut":
            return (f"k.nut({_m(b['diameter'])}, pitch={_m(b['pitch'])}, across_flats={_m(b['across_flats'])}, "
                    f"name={n!r})")
        return f"k.washer({_m(b['diameter'])}, name={n!r})"
    if t == "spring":
        return (f"k.spring({_m(p['wire_diameter'])}, {_m(p['mean_diameter'])}, {_m(p['free_length'])}, "
                f"{p['coils']}, kind={p['kind']!r}, name={n!r})")
    if t == "pulley":
        return (f"k.pulley({_m(p['datum_diameter'])}, grooves={p['grooves']}, section={p['section']!r}, "
                f"bore={_m(p['bore'])}, name={n!r})")
    if t == "belt":
        a, b = (by[x] for x in p["on"])
        return (f"k.belt({_m(a['datum_diameter'] / 2)}, {_m(b['datum_diameter'] / 2)}, {_m(p['centre_distance'])}, "
                f"section={p['section']!r}, grooves={p['grooves']}, name={n!r})")
    if t == "sprocket":
        return (f"k.sprocket({_m(p['pitch'])}, {p['teeth']}, bore={_m(p['bore'])}, "
                f"hub=({_m(p['hub'][0])}, {_m(p['hub'][1])}), name={n!r})")
    if t == "chain":
        a, b = (by[x] for x in p["on"])
        return (f"k.chain({_m(p['pitch'])}, [(x * 0.001, y * 0.001, s, (c[0] * 0.001, c[1] * 0.001) if c else None) "
                f"for x, y, s, c in k._mech().chain_layout({p['pitch']}, {a['teeth']}, {b['teeth']}, "
                f"{p['centre_distance']})['slots']], name={n!r})")
    if t == "cam":
        f = next((q for q in by.values() if q.get("on_cam") == n), None)
        table = f["table"] if f else cam_table(p, "roller", 0.0)
        kind = f["contact_kind"] if f else "roller"
        roller = (f.get("roller_diameter") or 0.0) / 2 if f and kind == "roller" else 0.0
        return (f"k.cam({[_m(r) for r in table]}, kind={kind!r}, roller={_m(roller)}, width={_m(p['width'])}, "
                f"bore={_m(p['bore'])}, name={n!r})")
    if t == "follower":
        cam = by[p["on_cam"]]
        if p["kind"] == "roller":
            return (f"k.follower('roller', roller={_m(p['roller_diameter'] / 2)}, stem={_m(0.2 * cam['base_diameter'])}, "
                    f"length={_m(1.6 * cam['base_diameter'])}, width={_m(0.8 * cam['width'])}, name={n!r})")
        return (f"k.follower('flat', stem={_m(0.2 * cam['base_diameter'])}, length={_m(1.6 * cam['base_diameter'])}, "
                f"face={_m(p['face_diameter'])}, name={n!r})")
    if t == "valve":
        return (f"k.valve({_m(p['head_diameter'])}, {_m(p['stem_diameter'])}, {_m(p['length'])}, "
                f"bucket={_m(p['bucket_diameter'])}, name={n!r})")
    if t == "worm":
        return (f"k.worm({_m(p['module'])}, {p['starts']}, {_m(p['pitch_diameter'])}, {_m(p['length'])}, "
                f"hand={p['hand']!r}, name={n!r})")
    if t == "worm_wheel":
        mn = p["module"] * math.cos(math.radians(p["lead_angle"]))
        return (f"k.helical_gear({_m(mn)}, {p['teeth']}, {_m(p['width'])}, bore={_m(p['bore'])}, "
                f"helix_angle={p['lead_angle']}, hand={p['hand']!r}, name={n!r})")
    if t == "carrier":
        return (f"k.carrier({_m(p['radius'])}, {p['planets']}, {_m(p['pin'])}, {_m(p['width'])}, name={n!r})")
    raise MechanismError(f"no tool builds a {t}")


def assembly_code(sheet, lay):
    places = {n: {"centre": [round(v, 7) for v in pl["centre"]],
                  "rotation": [[round(v, 7) for v in row] for row in pl["rotation"]],
                  "phase": round(pl["phase"], 7)} for n, pl in lay["place"].items()}
    return ('"""The mechanism placed from its sheet: every centre, axis and starting angle worked out by\n'
            'the part tools so meshing teeth sit in each other\'s gaps; then the whole set is put on the floor."""\n'
            "import bpy\nfrom mathutils import Matrix\n\n"
            f"PLACES = {json.dumps(places, indent=1)}\n\n\n"
            "def assemble(parts):\n"
            "    for name, p in PLACES.items():\n"
            "        obj = parts[name]\n"
            "        obj.matrix_world = (Matrix.Translation(p['centre']) @ Matrix(p['rotation']).to_4x4()\n"
            "                            @ Matrix.Rotation(p['phase'], 4, 'Z'))\n"
            "    bpy.context.view_layer.update()\n"
            "    # the lowest real point (a turned part's bounding box reaches lower than its teeth)\n"
            "    low = min((o.matrix_world @ v.co).z for o in parts.values() for v in o.data.vertices)\n"
            "    for obj in parts.values():\n"
            "        obj.location.z -= low\n")


METALS = {"spur_gear": (0.62, 0.63, 0.65), "helical_gear": (0.66, 0.6, 0.48), "ring_gear": (0.5, 0.52, 0.55),
          "bevel_gear": (0.7, 0.62, 0.42), "rack": (0.55, 0.56, 0.58), "shaft": (0.78, 0.78, 0.79),
          "compressor_wheel": (0.8, 0.81, 0.83), "turbine_wheel": (0.42, 0.4, 0.38), "volute": (0.52, 0.53, 0.55),
          "bearing": (0.72, 0.73, 0.75), "centre_housing": (0.46, 0.47, 0.49), "crankshaft": (0.6, 0.6, 0.62),
          "connecting_rod": (0.55, 0.56, 0.58), "piston": (0.78, 0.78, 0.8), "piston_rings": (0.35, 0.35, 0.37),
          "cylinder": (0.5, 0.5, 0.52), "bolt": (0.55, 0.56, 0.58), "nut": (0.6, 0.61, 0.63),
          "washer": (0.7, 0.71, 0.73), "spring": (0.35, 0.36, 0.4), "pulley": (0.4, 0.42, 0.45),
          "belt": (0.08, 0.08, 0.09), "sprocket": (0.5, 0.5, 0.52), "chain": (0.45, 0.45, 0.47),
          "cam": (0.62, 0.62, 0.64), "follower": (0.7, 0.7, 0.72), "valve": (0.75, 0.74, 0.7),
          "worm": (0.62, 0.63, 0.65), "worm_wheel": (0.7, 0.55, 0.3), "carrier": (0.5, 0.52, 0.55)}


def materials_code(sheet):
    lines = ['"""Machined steel and brass for the realistic look; the explainer style recolours every part."""',
             "from bpy_lib import materials as m", "", "", "def apply(parts):"]
    for p in sheet["parts"]:
        c = METALS.get(p["type"], (0.6, 0.6, 0.62))
        n = p["name"]
        if p["type"] == "shaft":
            lines.append(f"    m.assign(parts[{n!r}][0], m.polished_metal(color={c}, roughness=0.18, "
                         f"name={n + '_steel'!r}))")
            continue
        lines += [f"    face = m.brushed_metal(color={c}, roughness=0.32, direction='Z', name={n + '_teeth'!r})",
                  f"    body = m.brushed_metal(color={tuple(round(v * 0.9, 3) for v in c)}, roughness=0.45, "
                  f"name={n + '_body'!r})",
                  f"    m.assign_slot(parts[{n!r}][0], 'tooth_face', face)",
                  f"    m.assign_slot(parts[{n!r}][0], 'hub', body)"]
        if p["type"] not in ("rack", "ring_gear", "volute"):
            lines.append(f"    m.assign_slot(parts[{n!r}][0], 'bore', m.polished_metal(color={c}, roughness=0.2, "
                         f"name={n + '_bore'!r}))")
    return "\n".join(lines) + "\n"


def motion_code(sheet, lay):
    by = {p["name"]: p for p in sheet["parts"]}
    reverse = sheet["direction"] == "clockwise"  # a positive turn is counterclockwise seen from the front
    lines = ['"""The mechanism turning: the driver at its speed, every other part from it through the meshes."""',
             "from bpy_lib import motion as m", "", "", "def animate(parts):"]
    own = _own_motion(sheet, lay, by, reverse)  # a nut, a spring or a planetary set moves itself
    if own:
        return "\n".join(lines + own) + "\n"
    lines.append(f"    m.spin(parts[{sheet['driver']!r}][0], rpm={sheet['rpm']}, reverse={reverse})")
    done, todo = {sheet["driver"]}, [sheet["driver"]]
    while todo:
        cur = todo.pop(0)
        for pair in lay["pairs"]:
            if cur not in (pair["a"], pair["b"]):
                continue
            other = pair["b"] if pair["a"] == cur else pair["a"]
            if other in done:
                continue
            done.add(other)
            todo.append(other)
            if pair["kind"] in ("housing", "cam", "train", "planet") + ENGINE_LINKS:
                continue  # a volute stands still round its wheel; the crank and the cam drive theirs below
            if pair["kind"] == "drive" or (pair["kind"] == "mesh" and by[cur]["type"] == "worm"):
                ratio = pair["ratio"] if pair["a"] == cur else 1 / pair["ratio"]
                lines.append(f"    m.mesh_drive(parts[{cur!r}][0], parts[{other!r}][0], ratio={round(ratio, 9)}, "
                             f"same_direction={pair['same_direction']})")
            elif pair["kind"] == "shaft":
                ratio = _cage(by[other]) / _cage(by[cur])
                lines.append(f"    m.mesh_drive(parts[{cur!r}][0], parts[{other!r}][0], ratio={round(ratio, 9)}, "
                             "same_direction=True)")
            else:
                ratio = by[cur]["teeth"] / by[other]["teeth"] if by[other]["type"] != "rack" else None
                lines.append(f"    m.mesh_drive(parts[{cur!r}][0], parts[{other!r}][0]"
                             + (f", ratio={round(ratio, 9)}" if ratio else "") + ")")
    for pair in [q for q in lay["pairs"] if q["kind"] == "crank"]:  # each rod and piston from its crankpin
        rod = pair["a"]
        piston = next(q["b"] for q in lay["pairs"] if q["kind"] == "pin" and q["a"] == rod)
        rings = [q["b"] for q in lay["pairs"] if q["kind"] == "ring" and q["a"] == piston]
        followers = "[" + ", ".join(f"parts[{r!r}][0]" for r in rings) + "]"
        pin = [round(v, 7) for v in pair["pin_local_m"]]
        lines.append(f"    m.crank_slider(parts[{pair['b']!r}][0], parts[{rod!r}][0], parts[{piston!r}][0], pin={pin}, "
                     f"length={round(pair['length_m'], 7)}, followers={followers})")
    for p in sheet["parts"]:
        if p["type"] == "chain":  # its links run on from the first sprocket's turning
            first = by[p["on"][0]]
            lines.append(f"    m.chain_drive(parts[{p['name']!r}][0], parts[{first['name']!r}][0], teeth={first['teeth']})")
        if p["type"] in ("follower", "valve"):
            spring = next((q for q in sheet["parts"] if q.get("on_valve") == p["name"]), None)
            table = [round(r * MM, 7) for r in p["table"]]
            extra = (f", spring=parts[{spring['name']!r}][0], spring_length={_m(spring['free_length'])}"
                     if spring else "")
            lines.append(f"    m.cam_drive(parts[{p['on_cam']!r}][0], parts[{p['name']!r}][0], table={table}{extra})")
    return "\n".join(lines) + "\n"


def _own_motion(sheet, lay, by, reverse):
    """The motion of a mechanism driven by a part that does not simply turn: a nut screwing off and
    on, a spring squeezed and let go, or a planetary set (every member at once)."""
    d = by[sheet["driver"]]
    rpm = sheet["rpm"]
    if d["type"] == "nut":
        b = by[d["on_bolt"]]
        return [f"    m.screw(parts[{d['name']!r}][0], pitch={_m(b['pitch'])}, travel={_m(4 * b['pitch'])}, rpm={rpm})"]
    if d["type"] == "spring":
        return [f"    m.squash(parts[{d['name']!r}][0], share={d['squeeze']}, rpm={rpm}, "
                f"stretch={d['kind'] == 'extension'})"]
    for ps in sheet.get("planetary") or []:
        if sheet["driver"] == ps[ps["input"]]:
            w = planetary_rates(ps)
            turns = loop_turns(sheet)
            planets = "[" + ", ".join(f"parts[{n!r}][0]" for n in ps["planets"]) + "]"
            return [f"    m.planetary(parts[{ps['sun']!r}][0], parts[{ps['ring']!r}][0], parts[{ps['carrier']!r}][0], "
                    f"{planets}, rates={{'sun': {float(w['sun'])}, 'ring': {float(w['ring'])}, "
                    f"'carrier': {float(w['carrier'])}, 'planet': {float(w['planet'])}}}, rpm={rpm}, "
                    f"reverse={reverse}, turns={float(turns)})"]
    return None


def scripts(sheet):
    """{relative path: code} for a version folder: one part script per part, the assembly, the
    materials and the motion; plus the layout they were written from."""
    lay = layout(sheet)
    by = {p["name"]: p for p in sheet["parts"]}
    files = {f"parts/{p['name']}.py": part_code(p, lay, by) for p in sheet["parts"]}
    files["assembly.py"] = assembly_code(sheet, lay)
    files["materials.py"] = materials_code(sheet)
    if len(sheet["parts"]) > 1 or sheet["parts"][0]["type"] != "rack":
        files["motion.py"] = motion_code(sheet, lay)
    return files, lay


def plan_entries(sheet):
    """The parts of the version's plan (plan.json), one per part, with the numbers they were made from."""
    out = []
    for p in sheet["parts"]:
        info = {k: v for k, v in p.items() if k not in ("meshes_with", "on_shaft", "at")}
        out.append({"name": p["name"], "tool": p["type"], "description": describe({"parts": [p]}),
                    "numbers": info})
    return out


def housings(sheet):
    """The parts a cutaway slices open: the housings (volutes, the centre housing)."""
    return [p["name"] for p in sheet["parts"] if p["type"] in STILL]


def exploded_offsets(sheet, lay=None):
    """How far each part slides out in the exploded view (metres, a vector per part): along the main
    shaft, in assembly order from the middle outwards, a housing further out than the wheel it
    houses, so every part stands clear of its neighbours."""
    lay = lay or layout(sheet)
    by = {p["name"]: p for p in sheet["parts"]}
    crank = next((p for p in sheet["parts"] if p["type"] == "crankshaft"), None)
    if crank:  # an engine comes apart upwards, as in a workshop manual: rod, piston, rings, cylinder
        return _engine_exploded(sheet, lay, by, crank)
    if any(p["type"] in STAGE4 for p in sheet["parts"]):
        return _stage4_exploded(sheet, lay, by)
    shaft = next((p["name"] for p in sheet["parts"] if p["type"] == "shaft"), sheet["driver"])
    axis = _col(lay["place"][shaft]["rotation"], 2)
    housed = {p["houses"]: p["name"] for p in sheet["parts"] if p.get("houses")}
    middle = sum(_dot(pl["centre"], axis) for pl in lay["place"].values()) / len(lay["place"])
    rows = []
    lifted = [p["name"] for p in sheet["parts"] if p["type"] == "centre_housing"]
    for name, pl in lay["place"].items():
        if name in lifted:
            continue
        s = _dot(pl["centre"], axis) - middle
        if by[name]["type"] == "volute":
            s += math.copysign(1e-6, s or 1.0)  # just outside its wheel, so it slides off it
        rows.append((s, name))
    rows.sort()
    size = max([2 * _radius(p) for p in sheet["parts"]] + [_extent(p, None) * 2 for p in sheet["parts"]] + [0.01])
    step = 0.45 * size
    centre_rank = (len(rows) - 1) / 2
    out = {}
    for rank, (s, name) in enumerate(rows):
        if by[name]["type"] == "shaft":
            out[name] = [0.0, 0.0, 0.0]
            continue
        out[name] = [round(v, 6) for v in _mul(axis, (rank - centre_rank) * step)]
    for name in lifted:  # the centre housing rises off the bearings it carries, so they show
        out[name] = [0.0, 0.0, round(0.75 * size, 6)]
    del housed
    return out


def _stage4_exploded(sheet, lay, by):
    """Fasteners slide off down the bolt, belts and chains off their wheels towards the viewer,
    followers and valves away from their cams, a worm wheel up off its worm, a planetary set apart
    along its axis (carrier back, planets and ring forward)."""
    pl = lay["place"]
    size = max([2 * _radius(p) for p in sheet["parts"]] + [0.02])
    step = 0.35 * size
    out = {p["name"]: [0.0, 0.0, 0.0] for p in sheet["parts"]}

    def along(name, axis_of, k):
        out[name] = [round(v, 6) for v in _mul(_col(pl[axis_of]["rotation"], 2), k * step)]

    for p in sheet["parts"]:
        t = p["type"]
        if t in ("nut", "washer"):  # off past the bolt's tip, the nut furthest
            bolt = by[p["on_bolt"]]
            d, L, pitch = bolt["diameter"], bolt["length"], bolt["pitch"]
            mm = 5 * pitch + 0.84 * d + (0.6 * L if t == "nut" else 0.2 * d + 0.25 * L)
            out[p["name"]] = [round(v, 6) for v in _mul(_col(pl[bolt["name"]]["rotation"], 2), -mm * MM)]
        elif t in ("belt", "chain"):
            along(p["name"], p["on"][0], 1.2)
        elif t in ("follower", "valve"):
            along(p["name"], p["name"], 1.0)
        elif t == "spring" and p.get("on_valve"):
            out[p["name"]] = [round(v, 6) for v in _mul(_col(pl[p["on_valve"]]["rotation"], 2), 0.5 * step)]
        elif t == "worm_wheel":
            out[p["name"]] = [0.0, 0.0, round(step, 6)]
    for ps in sheet.get("planetary") or []:
        along(ps["carrier"], ps["sun"], -1.2)
        for n in ps["planets"]:
            along(n, ps["sun"], 1.2)
        along(ps["ring"], ps["sun"], 2.4)
    return out


def _engine_exploded(sheet, lay, by, crank):
    """Each part of an engine lifted just clear of the one below it: the rod off its crankpin, the
    piston off the rod's small end, the rings off the piston's crown, the cylinder over the rings."""
    gap = 0.12 * 2 * crank_sweep(crank) * MM
    out = {p["name"]: [0.0, 0.0, 0.0] for p in sheet["parts"]}
    pairs = lay["pairs"]
    for cp in [q for q in pairs if q["kind"] == "crank"]:
        rod = by[cp["a"]]
        piston = by[next(q["b"] for q in pairs if q["kind"] == "pin" and q["a"] == rod["name"])]
        rod_up = crank["stroke"] / 2 * MM + rod["big_end_bore"] * 0.7 * MM + gap  # big end over the pin
        small_end = rod["small_end_bore"] * 0.75 * MM
        piston_up = rod_up + (piston["skirt_length"] * MM + small_end) + gap
        grooves = piston_grooves(piston["bore"], piston["compression_height"])
        rings_up = piston_up + (piston["compression_height"] - grooves[-1][1]) * MM + gap
        out[rod["name"]][2], out[piston["name"]][2] = round(rod_up, 6), round(piston_up, 6)
        for q in pairs:
            if q["kind"] == "ring" and q["a"] == piston["name"]:
                out[q["b"]][2] = round(rings_up, 6)
            if q["kind"] == "liner" and q["a"] == piston["name"]:
                foot = _dot(_sub(lay["place"][q["b"]]["centre"], lay["place"][piston["name"]]["centre"]), [0, 0, 1])
                top_ring = grooves[0][0] * MM
                out[q["b"]][2] = round(rings_up + top_ring - foot + gap, 6)
    return out


def check_pairs(lay):
    """The pairs the Blender mesh check measures (bpy_lib.measure.mesh_report)."""
    return [dict(p) for p in lay["pairs"]]


def size_mm(sheet):
    """Each gear's main circles in mm, for the sheet the agents and the Village read."""
    out = {}
    for p in sheet["parts"]:
        if p["type"] in ("spur_gear", "helical_gear", "ring_gear"):
            c = circles(p["module"], p["teeth"], p["pressure_angle"], p.get("helix_angle") or 0, p.get("shift") or 0,
                        p["type"] == "ring_gear", p.get("shortening") or 0)
            out[p["name"]] = {"pitch_diameter": round(2 * c["pitch"], 3), "tip_diameter": round(2 * c["tip"], 3),
                              "root_diameter": round(2 * c["root"], 3), "base_diameter": round(2 * c["base"], 3)}
        elif p["type"] == "bevel_gear":
            b = bevel_numbers(p["module"], p["teeth"], p["mate_teeth"], p["width"])
            out[p["name"]] = {"pitch_diameter": round(2 * b["pitch"], 3), "cone_angle_deg": round(b["cone_deg"], 3),
                              "cone_distance": round(b["cone_distance"], 3)}
    return out
