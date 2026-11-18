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
}
GEARS = ("spur_gear", "helical_gear", "ring_gear", "bevel_gear", "rack")
EXTERNAL = ("spur_gear", "helical_gear")
WHEELS = ("compressor_wheel", "turbine_wheel")
ON_SHAFT = ("spur_gear", "helical_gear", "bevel_gear", "compressor_wheel", "turbine_wheel", "bearing")
LINK_FIELDS["houses"] = {"type": "string", "about": "a volute: the wheel it houses"}
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
    unknown = set(raw) - set(fields) - set(LINK_FIELDS) - {"name", "type", "driver"}
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


def clean(raw):
    """A mechanism request checked and completed: (sheet, assumed). Raises MechanismError with the
    field named when it cannot be built."""
    if not isinstance(raw, dict):
        raise MechanismError("mechanism must be an object with a list of parts")
    items = raw.get("parts")
    if not isinstance(items, list) or not items:
        raise MechanismError("mechanism.parts must be a list of at least one part")
    if len(items) > 24:
        raise MechanismError("mechanism.parts: at most 24 parts in one mechanism")
    assumed = []
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
        if p.get("houses"):
            w = by.get(p["houses"])
            if w is None or w["type"] not in WHEELS:
                raise MechanismError(f"volute {p['name']} houses {p['houses']}, which is not a compressor or turbine "
                                     "wheel of this mechanism")
            if any(l["kind"] == "housing" and l["b"] == w["name"] for l in links):
                raise MechanismError(f"wheel {w['name']} has two volutes")
            links.append({"kind": "housing", "a": p["name"], "b": w["name"]})
        if p["type"] in WHEELS + ("bearing",) and p["meshes_with"]:
            raise MechanismError(f"a {p['type']} ({p['name']}) has no teeth to mesh with")
    for link in [l for l in links if l["kind"] == "mesh"]:
        _check_mesh(by[link["a"]], by[link["b"]])
    _check_no_loop(parts, links)
    _fill_bores_and_shafts(parts, links, assumed)
    _fill_pair_numbers(parts, links, by, assumed)
    driver = raw.get("driver") or next((p["name"] for p in parts if p.get("driver")), None)
    if driver in (None, ""):
        driver = next((p["name"] for kinds in (GEARS[:4], ("shaft",), WHEELS, ("bearing",))
                       for p in parts if p["type"] in kinds), parts[0]["name"])
        assumed.append(f"driver = {driver}")
    if driver not in by:
        raise MechanismError(f"driver {driver} is not one of the parts")
    if by[driver]["type"] in ("rack", "volute"):
        raise MechanismError(f"driver {driver} is a {by[driver]['type']} and cannot drive; name a gear, wheel or shaft")
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
    sheet["colours"] = colours(sheet)
    lay = layout(sheet)  # also proves the parts can be placed
    for p in parts:
        if p["type"] == "shaft" and p["length"] is None:
            assumed.append(f"{p['name']}.length = {lay['place'][p['name']]['length_mm']:g} (the gears on it plus "
                           "some each side)")
    return sheet, assumed


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


def wheel_clearance(diameter_mm):
    """The gap a volute leaves round its wheel's blade tips, mm (kits.volute's default)."""
    return max(0.3, 0.008 * diameter_mm)


def describe(sheet):
    """A few words for the model's title: "spur gear pair 12/24"."""
    parts = sheet["parts"]
    kinds_present = {p["type"] for p in parts}
    if {"compressor_wheel", "turbine_wheel"} <= kinds_present:
        return "turbocharger" + (" with housings" if "volute" in kinds_present else "")
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
    if t == "volute":
        return {"pitch": None, "tip": None, "module": None, "teeth": None}
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


def _extent(p, info):
    """Half the part's size along its axis, metres (the rest of the layout only needs a rough one)."""
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
            if other == partner or o["type"] == "shaft":
                continue
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

    roots = [sheet["driver"]] + [p["name"] for p in sheet["parts"] if p["name"] != sheet["driver"]]
    for root in roots:
        if root in place:
            continue
        p = by[root]
        if place:  # a part not connected to the first group goes beside it, to the right
            r = _radius(p)
            centre = _v(_rough_span(place, by)[1] + 0.3 * r + r + 0.005, 0, 0)
        else:
            centre = _v(0, 0, 0)
        rot = BEVEL_FRONT if p["type"] == "bevel_gear" else IDENTITY if p["type"] == "rack" else FRONT
        put(root, centre, rot, 0.0)
        queue = [root]
        while queue:
            cur = queue.pop(0)
            for other, l in adj[cur]:
                if other in place:
                    continue
                if l["kind"] == "shaft":
                    _place_on_shaft(cur, other, by, place, put, links)
                elif l["kind"] == "housing":  # a volute stands exactly where its wheel does
                    put(other, list(place[cur]["centre"]), place[cur]["rotation"], 0.0)
                else:
                    _place_mesh(cur, other, by, place, put, taken)
                queue.append(other)
    _size_shafts(by, place, links)
    pairs = []
    for l in links:
        a, b = by[l["a"]], by[l["b"]]
        if l["kind"] == "shaft":
            pairs.append({"a": l["a"], "b": l["b"], "kind": "shaft"})
            continue
        if l["kind"] == "housing":  # the wheel must clear its volute all the way round
            pairs.append({"a": b["name"], "b": a["name"], "kind": "housing",
                          "clearance_m": wheel_clearance(b["diameter"]) * MM})
            continue
        pairs.append(_pair_numbers(a, b, place))
    return {"place": place, "order": order, "pairs": pairs}


def _radius(p):
    """How far a part reaches from its axis (a rack: half its length), metres."""
    if p["type"] == "shaft":
        return (p["diameter"] or 10) * MM / 2
    if p["type"] == "volute":
        return 0.0  # placed round its wheel, never in anyone's way
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
        if s["length"]:
            pl["length_mm"] = s["length"]
            continue
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
        pl["length_mm"] = round((hi - lo) / MM, 2)
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
            if other in rates or other in racks or pair["kind"] == "housing":
                continue
            if pair["kind"] == "shaft":
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
    periods = {}
    for name in rates:
        periods[name] = period(by[name])
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
          "bearing": (0.72, 0.73, 0.75)}


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
             "from bpy_lib import motion as m", "", "", "def animate(parts):",
             f"    m.spin(parts[{sheet['driver']!r}][0], rpm={sheet['rpm']}, reverse={reverse})"]
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
            if pair["kind"] == "housing":
                continue  # a volute stands still round its wheel
            if pair["kind"] == "shaft":
                ratio = _cage(by[other]) / _cage(by[cur])
                lines.append(f"    m.mesh_drive(parts[{cur!r}][0], parts[{other!r}][0], ratio={round(ratio, 9)}, "
                             "same_direction=True)")
            else:
                ratio = by[cur]["teeth"] / by[other]["teeth"] if by[other]["type"] != "rack" else None
                lines.append(f"    m.mesh_drive(parts[{cur!r}][0], parts[{other!r}][0]"
                             + (f", ratio={round(ratio, 9)}" if ratio else "") + ")")
    return "\n".join(lines) + "\n"


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
    """The parts a cutaway slices open: the housings (volutes)."""
    return [p["name"] for p in sheet["parts"] if p["type"] == "volute"]


def exploded_offsets(sheet, lay=None):
    """How far each part slides out in the exploded view (metres, a vector per part): along the main
    shaft, in assembly order from the middle outwards, a housing further out than the wheel it
    houses, so every part stands clear of its neighbours."""
    lay = lay or layout(sheet)
    by = {p["name"]: p for p in sheet["parts"]}
    shaft = next((p["name"] for p in sheet["parts"] if p["type"] == "shaft"), sheet["driver"])
    axis = _col(lay["place"][shaft]["rotation"], 2)
    housed = {p["houses"]: p["name"] for p in sheet["parts"] if p.get("houses")}
    middle = sum(_dot(pl["centre"], axis) for pl in lay["place"].values()) / len(lay["place"])
    rows = []
    for name, pl in lay["place"].items():
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
    del housed
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
