"""The car sheet: the numbers a car body is built from, its default details, and the plan made from it.

Plain Python with no Blender import, so the pipeline uses it to plan and check a sheet, and
bpy_lib.shells.car uses it inside Blender to build the body.

Conventions: metres, Z up, the car stands on Z = 0, its front faces -Y, it is centred on X = 0 and
Y = 0 (the nose is at Y = -length / 2). A sheet is a dict; anything it leaves out comes from the
preset of its `style`, scaled to its length, width and height, so a sheet can be as short as
{"style": "coupe", "length": 4.6}. `features` adds to, replaces (same name) or removes
({"name": ..., "remove": true}) the default details.
"""

import math
import pprint

STYLES = ("coupe", "sedan", "hatch", "suv", "van")

# The rear bumper of each style: (how far out, its top above the underside, how soft its top edge, how
# far along the sides), in metres.
REAR_BUMPERS = {"sport": (0.065, 0.34, 0.08, 0.45), "classic": (0.05, 0.38, 0.10, 0.40),
                "compact": (0.04, 0.36, 0.09, 0.30), "step": (0.08, 0.45, 0.04, 0.35), "none": (0.0, 0.0, 0.0, 0.0)}
REAR_BUMPER_OF = {"coupe": "sport", "sedan": "classic", "hatch": "compact", "suv": "step", "van": "step"}

# Side-profile and plan numbers of a typical car of each style. Y values are positions along the
# car (front is negative), Z values heights above the floor.
PRESETS = {
    "coupe": dict(length=4.60, width=1.78, height=1.36, wheelbase=2.70, front_overhang=0.90,
                  wheel_diameter=0.66, wheel_width=0.245, track=1.48, ride_height=0.14,
                  nose_z=0.70, cowl_z=0.92, windshield_base=-1.00, roof_front=-0.15, roof_rear=0.90,
                  deck_front=1.71, deck_z=1.00, tail_z=0.97, belt_z=0.92, max_width_z=0.55,
                  roof_width=1.20, window_split=0.62, look="angular", flare_style="boxed", fender_rise=0.03,
                  nose_point=0.16),
    "sedan": dict(length=4.75, width=1.80, height=1.44, wheelbase=2.80, front_overhang=0.92,
                  wheel_diameter=0.66, wheel_width=0.225, track=1.54, ride_height=0.15,
                  nose_z=0.72, cowl_z=0.98, windshield_base=-0.95, roof_front=-0.10, roof_rear=0.90,
                  deck_front=1.78, deck_z=1.02, tail_z=0.98, belt_z=0.97, max_width_z=0.58,
                  roof_width=1.24, window_split=0.50),
    "hatch": dict(length=4.20, width=1.76, height=1.47, wheelbase=2.60, front_overhang=0.85,
                  wheel_diameter=0.62, wheel_width=0.205, track=1.52, ride_height=0.14,
                  nose_z=0.74, cowl_z=1.00, windshield_base=-0.84, roof_front=-0.05, roof_rear=1.40,
                  deck_front=1.93, deck_z=1.06, tail_z=1.05, belt_z=1.00, max_width_z=0.60,
                  roof_width=1.24, window_split=0.52),
    "suv": dict(length=4.70, width=1.90, height=1.75, wheelbase=2.85, front_overhang=0.90,
                wheel_diameter=0.76, wheel_width=0.255, track=1.62, ride_height=0.25,
                nose_z=0.98, cowl_z=1.20, windshield_base=-0.89, roof_front=-0.05, roof_rear=1.85,
                deck_front=2.15, deck_z=1.25, tail_z=1.22, belt_z=1.15, max_width_z=0.80,
                roof_width=1.42, window_split=0.48),
    "van": dict(length=4.90, width=1.95, height=1.95, wheelbase=3.00, front_overhang=0.85,
                wheel_diameter=0.68, wheel_width=0.215, track=1.66, ride_height=0.20,
                nose_z=1.00, cowl_z=1.25, windshield_base=-1.55, roof_front=-1.00, roof_rear=2.25,
                deck_front=2.40, deck_z=1.80, tail_z=1.75, belt_z=1.20, max_width_z=0.80,
                roof_width=1.70, window_split=0.30),
}

# Shape numbers every style shares (metres unless named a fraction).
COMMON = dict(
    tuck=0.05,            # how far the lower side tucks in from the widest point
    shoulder=0.03,        # how far the beltline shoulder sits in from the widest point
    nose_taper=0.90,      # plan-view width at the very nose, as a fraction of the full width
    tail_taper=0.94,      # the same at the tail
    taper_length=0.75,    # how far back from each end the taper runs
    fender_rise=0.015,    # the fender tops (the fender peak line) sit this much above the hood
    fender_valley=0.10,   # the hood meets the fender in a valley line this far inside the fender peak
    hood_dip=0.0,         # the hood centre sits this much below its sides (negative: a raised centre)
    hood_curve=1.3,       # 1 is a straight hood and deck; higher rounds them over at the nose and tail
    roof_crown=0.035,     # the roof centre sits this much above its side edges
    flare=0.04,           # how far the fenders stand out around the wheels
    flare_style="soft",   # "soft": a swell that fades into the side; "boxed": a blister, a raised flare whose
                          # edge rolls off quickly but smoothly
    flare_spread=0.16,    # soft flares: how far the bulge fades out beyond the wheel opening
    flare_width=0.07,     # boxed flares: how far the blister's full height reaches past the wheel opening
    flare_reach=1.35,     # how far the front fender swell runs forward into the bumper (1: round)
    hip_length=0.30,      # how far the rear haunch swells forward of the rear wheel opening, into the door
    front_fender_rise=0.02,  # the fender line arches up this much over the front wheel
    rear_fender_rise=0.025,  # the shoulder (hip line) rises this much over the rear wheel
    arch_cladding=False,  # black plastic trim round the wheel openings (SUVs, crossovers)
    vents=None,           # air vents, any of "gill", "side_intake", "hood_vents", "fender_trim" (empty: gills
                          # for a coupe, a fender trim strip for an SUV, none for the others)
    wheel_gap=0.03,       # space between the tyre and the wheel opening
    well_clearance=0.03,  # space between the tyre's inner face and the wheel-well wall
    # side view of the ends, a sideways "<" as on real cars: the most forward point at nose_peak_z, the hood's
    # leading edge nose_rollover behind it, the bumper's bottom nose_tuck behind it (empty: by style)
    nose_peak_z=None,
    nose_rollover=None,
    nose_tuck=None,
    tail_peak_z=None,     # the same at the back: the rearmost point's height,
    tail_rollover=None,   # how far the deck's trailing edge sits ahead of it,
    tail_tuck=None,       # and how far the rear bumper's bottom sits ahead of it
    rollover_taper=0.6,   # above the tip the rollover shrinks toward the sides by up to this fraction, so the
                          # fenders run forward and only the hood's (or deck's) centre rolls far back
    diffuser_height=None,  # how tall the diffuser's black panel is (empty: up to the plate or just under it)
    nose_rake=0.05,       # the top of the front face sits this far behind its bottom
    tail_rake=0.04,       # the same at the back
    nose_corner=0.10,     # plan view: the front corners are cut back this far behind the nose centre
    tail_corner=0.08,     # the same at the rear corners
    nose_point=None,      # plan view: how far the nose centre leads its corners (empty: nose_corner); a wedge
                          # or a pointed supercar nose 0.2-0.35, a blunt van nose 0.05
    nose_round=2.0,       # plan view shape of the nose, 1 to 6: 1 a V point, 2 a round curve, 4 or more a flat
                          # front with rounded corners
    tail_point=None,      # the same at the tail (empty: tail_corner)
    tail_round=2.0,
    nose_edge_radius=0.04,  # side view: the rounding where the nose face meets the hood (0: a crisp edge)
    tail_edge_radius=0.05,  # the same where the deck or tailgate meets the tail face
    doors=None,           # 2 or 4 side doors (empty: 2 for a coupe, 4 for the other styles); rear doors get
                          # their own shut lines and handles
    dlo_style=None,       # side window shape: "coupe" (a teardrop sweeping up to a point at the back), "sedan"
                          # (a kink up at the rear corner), "hatch" or "suv" (the rear quarter kicked up),
                          # "van" (long and straight); empty: the car's style
    belt_rise=None,       # how much the windows' lower edge rises from front to back (empty: by dlo_style)
    dlo_corner=0.12,      # how round the window opening's corners are (0.05 crisp to 0.2 very round)
    dlo_front="point",    # the side windows' front end: "point" (the glass runs forward to a point at the mirror,
                          # down the windshield pillar) or "upright" (it stops short with an upright edge)
    dlo_rear="",          # their rear end: "point" (the glass runs back to a point along the rear pillar) or
                          # "upright" (empty: upright for a van, point for the others)
    lamp_shape=None,      # projector headlight shape: "swept", "angular", "slim", "round", "rect", "oval" (one
                          # big upright oval), "blade" (a thin sliver pointed at its inner end) or "split" (an
                          # LED slit up high, the main lamp down in the bumper corner) (empty: swept for a coupe
                          # or sedan, angular for a hatch, slim for an SUV, rect for a van)
    lamp_span=0.5,        # headlight width as a fraction of the half-width; the outer end stays put
    lamp_height=0.095,    # headlight height in metres
    lamp_drop=0.09,       # how far the headlight's centre sits below nose_z
    lamp_bar=False,       # a thin light strip across the nose joining the two headlights
    round_count=2,        # lamp_shape "round": 1 or 2 lamps a side
    grille="",            # upper grille: "slot" (a bar between the lamps), "small" (a short slot), "wide" (a big
                          # trapezoid between the lamps), "tall" (an upright grille between the lamps) or "none"
                          # (empty: wide for a sedan, tall for an SUV, slot for the others)
    grille_pattern="horizontal",  # bars across the upper grille: "horizontal", "vertical", "mesh" (crossed) or "none"
    intake_pattern="mesh",  # bars across the lower front intake: "horizontal", "vertical", "mesh" or "none"
    rear_pattern="vertical",  # bars across the rear diffuser opening ("none": the diffuser keeps its three fins)
    intake="",            # lower intake: "full" (a wide mouth with corner intakes), "center" (a narrower central
                          # mouth), "split" (two big side intakes and a small centre one) or "low" (a slim slot
                          # low down) (empty: full for a coupe, center for a sedan or hatch, low for an SUV or van)
    tail_lamp_style=None,  # tail lamps: "wrap", "bar", "slim", "round_twin", "vertical", "split" (cut in two at
                          # the boot lid), "tri" (three vertical bars a side) or "square" (small square lamps low
                          # in the bumper corners) (empty: vertical for an SUV or van, wrap for the others)
    tail_lamp_height=None,  # how tall the vertical tail lamps are, or how thick the bar is (empty: 0.55 / 0.085)
    plate_rear="",        # rear number plate: "bumper" or "panel" (on the tailgate or boot face, between the
                          # lamps) (empty: panel for a hatch, SUV or van, bumper for the others)
    diffuser=False,       # a black lower rear panel with vertical fins
    raised_bumpers=False,  # front bumper and lip raised out of the body (off: flush, shown by their shut
                          # lines; raised ones stick out as fins on a nose that slopes back)
    rear_bumper_style="",  # the rear bumper, swelling out of the tail as one smooth surface with it: "sport" (low
                          # and tight), "classic" (medium, a rounder edge), "compact" (small), "step" (tall, a
                          # flat step on top) or "none" (empty: sport for a coupe, classic for a sedan, compact
                          # for a hatch, step for an SUV or van)
    rear_bumper_out=None,  # how far it stands out of the tail (empty: the style's)
    rear_bumper_top=None,  # its top edge's height above the car's underside (empty: the style's)
    rear_bumper_edge=None,  # how far down its top edge rolls from the body into the bumper: bigger is softer
    rear_bumper_wrap=None,  # how far it runs forward along the sides before it fades out
    door_length=None,     # the front door's length; the side glass splits where it ends (empty: at window_split)
    dlo_end=None,         # where the side glass ends, toward the back (empty: at deck_front); a mid-engine car
                          # ends it behind the door, the long engine cover behind it painted
    rear_glass_length=None,  # how far the rear window runs back from roof_rear (empty: down to deck_front)
    lamp_style="projector",  # headlights: "projector" (housing, two lenses, light strip, cut along the nose
                          # surface), "popup" (closed lids on the hood, a thin lamp strip in the bumper; 80s-90s
                          # wedges), "popup_open" (the same lids raised: a wedge pod on each hood corner, its
                          # upright front face a lit lens, its body-colour lid sloping back into the hood) or
                          # "flush" (one lens cut straight back from the front)
    bumper=0.035,         # how far the front and rear bumpers stand out of the body
    front_lip=0.025,      # how far the front lip stands out beyond the bumper
    skirt=0.025,          # how far the side skirts stand out of the lower side
    look="smooth",        # "smooth": soft rounded panels; "angular": flat panels with crisp character lines
    a_pillar=0.07,        # width of the pillar beside the windshield
    c_pillar=0.10,        # width of the pillar beside the rear window
    window_margin=0.025,  # side glass stops this far below the roof edge
    detail=None,          # smoothing levels of the skin; empty: 2 for the smooth look, 1 for the angular one
)

Y_KEYS = ("front_overhang", "windshield_base", "roof_front", "roof_rear", "deck_front", "wheelbase")
Z_KEYS = ("nose_z", "cowl_z", "deck_z", "tail_z", "belt_z", "max_width_z")
X_KEYS = ("track", "roof_width")

MATERIAL_SLOTS = ("paint", "glass", "trim", "lamp_front", "lamp_rear", "lamp_amber", "lamp_led", "lamp_led_rear",
                  "tyre", "rim", "chrome", "accent")


def _lerp(a, b, t):
    return a + (b - a) * t


def _clamp(v, lo=0.0, hi=1.0):
    return max(lo, min(hi, v))


def _smooth(t):
    t = _clamp(t)
    return t * t * (3 - 2 * t)


def resolve(sheet):
    """The full sheet: the style preset scaled to the sheet's size, the shared defaults, the sheet's own
    numbers on top, and the derived positions (axles, ends). Raises ValueError for an unknown style."""
    sheet = dict(sheet or {})
    style = sheet.get("style", "coupe")
    if style not in PRESETS:
        raise ValueError(f"unknown car style {style!r}: use one of {', '.join(STYLES)}")
    base = dict(PRESETS[style])
    ky = float(sheet.get("length", base["length"])) / base["length"]
    kz = float(sheet.get("height", base["height"])) / base["height"]
    kx = float(sheet.get("width", base["width"])) / base["width"]
    for k in Y_KEYS:
        base[k] *= ky
    for k in Z_KEYS:
        base[k] *= kz
    for k in X_KEYS:
        base[k] *= kx
    s = {**COMMON, **base, "style": style}
    s.update({k: v for k, v in sheet.items() if k not in ("features",)})
    for k, v in list(s.items()):
        if isinstance(v, int) and not isinstance(v, bool) and k != "detail":
            s[k] = float(v)
    s["y_front"] = -s["length"] / 2
    s["y_rear"] = s["length"] / 2
    s["axle_front"] = s["y_front"] + s["front_overhang"]
    s["axle_rear"] = s["axle_front"] + s["wheelbase"]
    s["wheel_radius"] = s["wheel_diameter"] / 2
    s["arch_radius"] = s["wheel_radius"] + s["wheel_gap"]
    s["half_width"] = s["width"] / 2 - s["flare"]  # the body side away from the wheels
    if s["dlo_style"] is None:
        s["dlo_style"] = style
    if not s["dlo_rear"]:
        s["dlo_rear"] = "upright" if style == "van" else "point"
    if s["belt_rise"] is None:
        s["belt_rise"] = {"coupe": 0.06, "sedan": 0.035, "hatch": 0.05, "suv": 0.04, "van": 0.0}.get(s["dlo_style"], 0.04)
    if s["lamp_shape"] is None:
        s["lamp_shape"] = {"coupe": "swept", "sedan": "swept", "hatch": "angular", "suv": "slim", "van": "rect"}[style]
    if s["tail_lamp_style"] is None:
        s["tail_lamp_style"] = "vertical" if style in ("suv", "van") else "wrap"
    # the ends' side-view shape, from measured real cars of each kind: (peak height, rollover, tuck)
    nose = {"hatch": (0.45, 0.15, 0.15), "sedan": (0.45, 0.15, 0.18), "coupe": (0.40, 0.25, 0.15),
            "suv": (0.60, 0.20, 0.15), "van": (0.55, 0.20, 0.18)}[style]
    for key, value in zip(("nose_peak_z", "nose_rollover", "nose_tuck"), nose):
        if s[key] is None:
            s[key] = value
    if s["tail_peak_z"] is None:
        s["tail_peak_z"] = round(s["ride_height"] + 0.55 * (s["tail_z"] - s["ride_height"]), 4)
    if s["tail_rollover"] is None:
        s["tail_rollover"] = 0.10
    if s["tail_tuck"] is None:
        s["tail_tuck"] = 0.08 if style in ("suv", "van") else 0.12
    if not s["grille"]:
        s["grille"] = {"sedan": "wide", "suv": "tall"}.get(style, "slot")
    if not s["intake"]:
        s["intake"] = {"coupe": "full", "sedan": "center", "hatch": "center"}.get(style, "low")
    if not s["plate_rear"]:
        s["plate_rear"] = "panel" if style in ("hatch", "suv", "van") else "bumper"
    if s["vents"] is None:
        s["vents"] = {"coupe": ["gill"], "suv": ["fender_trim"]}.get(style, [])
    if s["doors"] is None:
        s["doors"] = 2 if style == "coupe" else 4
    if s["nose_point"] is None:
        s["nose_point"] = s["nose_corner"]
    if s["tail_point"] is None:
        s["tail_point"] = s["tail_corner"]
    if s["detail"] is None:
        s["detail"] = 1 if s["look"] == "angular" else 2
    s["boxed_width"] = s["flare_width"] if s["flare_style"] == "boxed" else 0.0
    if int(s["doors"]) == 4 and "window_split" not in sheet:
        # four doors: the B-pillar (where the side glass splits) sits where the front door ends, a
        # little past the middle of the run from behind the front wheel to over the rear wheel
        door_front = s["axle_front"] + s["arch_radius"] + s["boxed_width"] + 0.08
        b_pillar = door_front + 0.55 * (s["axle_rear"] - 0.05 - door_front)
        span = max(1e-6, s["roof_rear"] - s["roof_front"])
        s["window_split"] = round(_clamp((b_pillar - s["roof_front"]) / span, 0.15, 0.85), 3)
    s["well_x"] = s["track"] / 2 - s["wheel_width"] / 2 - s["well_clearance"]
    s["hub_reach"] = round(s["track"] / 2 - s["well_x"] + 0.005, 4)  # wheel centre to the well wall, embedded
    s.setdefault("mirror_y", s["windshield_base"] + 0.14)
    s.setdefault("mirror_z", s["belt_z"] - 0.03)
    s.setdefault("wing_y", s["y_rear"] - 0.22)
    s.setdefault("wing_spacing", min(1.0, s["width"] * 0.55))
    if not s["rear_bumper_style"]:
        s["rear_bumper_style"] = REAR_BUMPER_OF[style]
    # the tips clear of the bumper's corner vents, where it has them
    s.setdefault("exhaust_x", s["half_width"] * (0.36 if s["rear_bumper_style"] in ("sport", "classic") else 0.55))
    preset = REAR_BUMPERS.get(s["rear_bumper_style"], REAR_BUMPERS["none"])
    given_top = s["rear_bumper_top"] is not None
    for key, value in zip(("rear_bumper_out", "rear_bumper_top", "rear_bumper_edge", "rear_bumper_wrap"), preset):
        if s[key] is None:
            s[key] = 0.09 if key == "rear_bumper_out" and style == "van" and value else value
    if s["rear_bumper_style"] != "none":
        if not given_top:  # under the tail lamps of a low tail
            s["rear_bumper_top"] = round(min(s["rear_bumper_top"], max(0.22, s["tail_z"] - s["ride_height"] - 0.22)), 4)
        # it fades out behind the rear wheel opening
        room = s["y_rear"] - (s["axle_rear"] + s["arch_radius"] + s["boxed_width"] + 0.06)
        s["rear_bumper_wrap"] = round(max(0.1, min(s["rear_bumper_wrap"], room)), 4)
        s.setdefault("exhaust_z", round(s["ride_height"] + max(0.10, 0.38 * s["rear_bumper_top"]), 4))
    s.setdefault("exhaust_z", s["ride_height"] + 0.12)
    s["features_in"] = list(sheet.get("features") or [])
    return s


def check(sheet):
    """Problems with a sheet's numbers, as plain sentences (empty when it can be built)."""
    try:
        s = resolve(sheet)
    except (ValueError, TypeError) as exc:
        return [str(exc)]
    out = []
    order = [("the nose", s["y_front"]), ("windshield_base", s["windshield_base"]), ("roof_front", s["roof_front"]),
             ("roof_rear", s["roof_rear"]), ("deck_front", s["deck_front"]), ("the tail", s["y_rear"])]
    for (na, a), (nb, b) in zip(order, order[1:]):
        if not a < b:
            out.append(f"{na} (y {a:.3f}) must lie in front of {nb} (y {b:.3f}); the front is -Y")
    r = s["arch_radius"]
    if s["axle_front"] - r < s["y_front"] + 0.1 or s["axle_rear"] + r > s["y_rear"] - 0.1:
        out.append("a wheel opening runs past the end of the body: shorten the wheelbase or lengthen the overhangs")
    if s["track"] / 2 + s["wheel_width"] / 2 > s["width"] / 2 - 0.005:
        out.append("the tyres stick out past the body: the track plus a tyre width must stay inside the width")
    if s["well_x"] < 0.15:
        out.append("the wheels are too wide for the track: the wheel wells would meet in the middle")
    for name in ("nose_z", "cowl_z", "deck_z", "tail_z", "belt_z", "max_width_z"):
        if not s["ride_height"] < s[name] < s["height"]:
            out.append(f"{name} ({s[name]:.3f}) must be between ride_height and height")
    if not s["max_width_z"] < s["belt_z"]:
        out.append("max_width_z must be below belt_z")
    if s["wheel_diameter"] + s["wheel_gap"] > s["belt_z"]:
        out.append("the wheel openings reach the beltline: smaller wheels or a higher belt_z")
    if not 0.3 < s["roof_width"] < s["width"]:
        out.append("roof_width must be narrower than the body")
    if s["style"] not in STYLES:
        out.append(f"style must be one of {', '.join(STYLES)}")
    if s["look"] not in ("smooth", "angular"):
        out.append("look must be smooth or angular")
    if s["flare_style"] not in ("soft", "boxed"):
        out.append("flare_style must be soft or boxed")
    if s["detail"] not in (0, 1, 2, 3):
        out.append("detail must be 0, 1, 2 or 3 smoothing levels")
    for name in ("nose_corner", "tail_corner"):
        if not 0 <= s[name] <= 0.3:
            out.append(f"{name} must be between 0 and 0.3 m")
    for name in ("nose_point", "tail_point"):
        if not 0 <= s[name] <= 0.4:
            out.append(f"{name} must be between 0 and 0.4 m")
    for name in ("nose_round", "tail_round"):
        if not 1 <= s[name] <= 6:
            out.append(f"{name} must be between 1 and 6")
    for name, end_z in (("nose_edge_radius", s["nose_z"]), ("tail_edge_radius", s["tail_z"])):
        if not 0 <= s[name] <= 0.15:
            out.append(f"{name} must be between 0 and 0.15 m")
        elif end_z - s[name] < s["ride_height"] + 0.15:
            out.append(f"{name} is too big for a face that low: keep it under {end_z - s['ride_height'] - 0.15:.2f}")
    for name in ("front_fender_rise", "rear_fender_rise"):
        if not 0 <= s[name] <= 0.1:
            out.append(f"{name} must be between 0 and 0.1 m")
    if not 0 <= s["hip_length"] <= 0.8:
        out.append("hip_length must be between 0 and 0.8 m")
    if not 1 <= s["flare_reach"] <= 2.5:
        out.append("flare_reach must be between 1 and 2.5")
    for name in ("bumper", "front_lip", "skirt", "flare", "flare_width"):
        if not 0 <= s[name] <= 0.15:
            out.append(f"{name} must be between 0 and 0.15 m")
    for f in s["features_in"]:
        if not isinstance(f, dict) or not f.get("name"):
            out.append(f"every feature needs a name: {f!r}")
        elif not f.get("remove") and f.get("kind", "recess") not in ("recess", "raise", "groove"):
            out.append(f"feature {f['name']}: kind must be recess, raise or groove")
        elif not f.get("remove") and f.get("view") not in (None, "side", "front", "rear", "top", "aim"):
            out.append(f"feature {f['name']}: view must be side, front, rear, top or aim")
        elif not f.get("remove") and f.get("view") == "aim" and not (
                len(f.get("aim") or []) == 3 and len(f.get("at") or []) == 3):
            out.append(f"feature {f['name']}: view aim needs `aim` (the direction the cut travels) and `at` "
                       "(the point its outline is centred on), each [x, y, z]")
        elif not f.get("remove") and f.get("bars") not in (None, *GRILLE_PATTERNS):
            out.append(f"feature {f['name']}: bars must be one of {', '.join(GRILLE_PATTERNS)}")
        elif not f.get("remove") and f.get("bars") not in (None, "none") and (
                f.get("kind", "recess") != "recess" or f.get("view") not in ("front", "rear")):
            out.append(f"feature {f['name']}: bars go only on a recess cut from the front or the rear")
    unknown = [v for v in s["vents"] or [] if v not in VENTS]
    if unknown:
        out.append(f"unknown vents {unknown}: use any of {', '.join(VENTS)}")
    if s["doors"] not in (2, 4):
        out.append("doors must be 2 or 4")
    if s["dlo_style"] not in STYLES:
        out.append(f"dlo_style must be one of {', '.join(STYLES)}")
    if not 0 <= s["belt_rise"] <= 0.15:
        out.append("belt_rise must be between 0 and 0.15 m")
    if not 0.03 <= s["dlo_corner"] <= 0.25:
        out.append("dlo_corner must be between 0.03 and 0.25")
    for key in ("dlo_front", "dlo_rear"):
        if s[key] not in ("point", "upright"):
            out.append(f"{key} must be point or upright")
    if s["lamp_shape"] not in LAMP_SHAPES:
        out.append(f"lamp_shape must be one of {', '.join(LAMP_SHAPES)}")
    if s["tail_lamp_style"] not in TAIL_LAMP_STYLES:
        out.append(f"tail_lamp_style must be one of {', '.join(TAIL_LAMP_STYLES)}")
    if s["lamp_style"] not in LAMP_STYLES:
        out.append(f"lamp_style must be one of {', '.join(LAMP_STYLES)}")
    if s["grille"] not in GRILLES:
        out.append(f"grille must be one of {', '.join(GRILLES)}")
    if s["intake"] not in INTAKES:
        out.append(f"intake must be one of {', '.join(INTAKES)}")
    for key in ("grille_pattern", "intake_pattern", "rear_pattern"):
        if s[key] not in GRILLE_PATTERNS:
            out.append(f"{key} must be one of {', '.join(GRILLE_PATTERNS)}")
    if s["plate_rear"] not in ("bumper", "panel"):
        out.append("plate_rear must be bumper or panel")
    if s["rear_bumper_style"] not in REAR_BUMPERS:
        out.append(f"rear_bumper_style must be one of {', '.join(REAR_BUMPERS)}")
    elif s["rear_bumper_style"] != "none":
        if not 0.02 <= s["rear_bumper_out"] <= 0.15:
            out.append("rear_bumper_out must be between 0.02 and 0.15 m")
        if not 0.2 <= s["rear_bumper_top"] <= 0.7:
            out.append("rear_bumper_top must be between 0.2 and 0.7 m above the underside")
        if not 0.03 <= s["rear_bumper_edge"] <= 0.2 or s["rear_bumper_edge"] > s["rear_bumper_top"] - 0.08:
            out.append("rear_bumper_edge must be between 0.03 and 0.2 m and leave 0.08 m of bumper under it")
    if not 0.25 <= s["lamp_span"] <= 0.7:
        out.append("lamp_span must be between 0.25 and 0.7")
    if not 0.04 <= s["lamp_height"] <= 0.25:
        out.append("lamp_height must be between 0.04 and 0.25 m")
    if not 0.03 <= s["lamp_drop"] <= 0.3:
        out.append("lamp_drop must be between 0.03 and 0.3 m")
    if s["round_count"] not in (1, 2):
        out.append("round_count must be 1 or 2")
    if s["tail_lamp_height"] is not None and not 0.03 <= s["tail_lamp_height"] <= 0.7:
        out.append("tail_lamp_height must be between 0.03 and 0.7 m")
    if s["door_length"] is not None and not 0.7 <= s["door_length"] <= 1.6:
        out.append("door_length must be between 0.7 and 1.6 m")
    if s["dlo_end"] is not None and not s["roof_front"] < s["dlo_end"] < s["y_rear"] - 0.3:
        out.append("dlo_end must lie behind roof_front and at least 0.3 m ahead of the tail")
    if s["rear_glass_length"] is not None and not 0.15 <= s["rear_glass_length"] <= 3.0:
        out.append("rear_glass_length must be between 0.15 and 3 m")
    for end in ("nose", "tail"):
        top = s[f"{end}_z"]
        if not s["ride_height"] + 0.1 <= s[f"{end}_peak_z"] <= top - 0.05:
            out.append(f"{end}_peak_z must be at least 0.1 m above ride_height and 0.05 m below {end}_z")
        if not 0 <= s[f"{end}_rollover"] <= 0.5:
            out.append(f"{end}_rollover must be between 0 and 0.5 m")
        if not 0 <= s[f"{end}_tuck"] <= 0.3:
            out.append(f"{end}_tuck must be between 0 and 0.3 m")
    if not 0 <= s["rollover_taper"] <= 0.95:
        out.append("rollover_taper must be between 0 and 0.95")
    if s["diffuser_height"] is not None and not 0.05 <= s["diffuser_height"] <= 0.6:
        out.append("diffuser_height must be between 0.05 and 0.6 m")
    return out


def rear_bumper(s):
    """The rear bumper the body is built with: {"out", "top" (its top edge's height above the floor),
    "edge", "wrap"}, or None for rear_bumper_style "none"."""
    if s["rear_bumper_style"] == "none":
        return None
    return dict(out=s["rear_bumper_out"], top=round(s["ride_height"] + s["rear_bumper_top"], 4),
                edge=s["rear_bumper_edge"], wrap=s["rear_bumper_wrap"])


def face_setback_at(s, end, x, z, widest):
    """face_setback at a point x out from the centre line: above the tip the rollover shrinks toward
    the sides by up to rollover_taper, so the fenders run forward while the hood's centre rolls back."""
    t = face_setback(s, end, z)
    if z > s[f"{end}_peak_z"]:
        t *= 1 - s["rollover_taper"] * (min(1.0, abs(x) / max(1e-6, widest))) ** 2
    return t


def face_setback(s, end, z):
    """How far the end face at height z sits back from the car's very end (the nose's or tail's most
    forward point): 0 at the peak, the tuck at the bottom, the rollover at the top, eased in between."""
    zb, peak, top = s["ride_height"], s[f"{end}_peak_z"], s[f"{end}_z"]
    if z <= peak:
        return s[f"{end}_tuck"] * _smooth((peak - z) / max(1e-6, peak - zb))
    u = _clamp((z - peak) / max(1e-6, top - peak))
    return s[f"{end}_rollover"] * u ** 1.6


def face_reach(s, end):
    """How far in from the end the face shape reshapes the sections: far enough for a gentle curve."""
    return max(0.45, 1.6 * max(s[f"{end}_rollover"], s[f"{end}_tuck"]))


def door_start(s):
    """Where the front door begins: 8 cm behind the front wheel opening."""
    return s["axle_front"] + s["arch_radius"] + s["boxed_width"] + 0.08


def glass_split(s):
    """Where the side glass splits (the B-pillar): at the door's end when door_length is set, otherwise
    window_split of the way along the roof."""
    if s["door_length"] is not None:
        return door_start(s) + s["door_length"]
    return _lerp(s["roof_front"], s["roof_rear"], s["window_split"])


# Usual proportions by style (rough ranges of real cars): the hood (nose to the windshield's foot)
# and the cabin (windshield foot to rear-glass foot) as shares of the length, and the windshield's
# angle from horizontal in degrees.
USUAL = {
    "coupe": dict(hood=(0.26, 0.40), cabin=(0.50, 0.63), windshield=(20, 30)),
    "sedan": dict(hood=(0.28, 0.32), cabin=(0.52, 0.60), windshield=(24, 32)),
    "hatch": dict(hood=(0.25, 0.31), cabin=(0.58, 0.72), windshield=(26, 34)),
    "suv": dict(hood=(0.28, 0.35), cabin=(0.58, 0.74), windshield=(28, 38)),
    "van": dict(hood=(0.18, 0.26), cabin=(0.68, 0.86), windshield=(30, 48)),
}


def proportion_notes(s):
    """Plain sentences for each proportion of a resolved sheet outside the usual range of its style:
    an upright windshield makes a short cabin and a long hood and boot."""
    usual = USUAL[s["style"]]
    hood = (s["windshield_base"] - s["y_front"]) / s["length"]
    cabin = (s["deck_front"] - s["windshield_base"]) / s["length"]
    run = max(1e-6, s["roof_front"] - s["windshield_base"])
    angle = math.degrees(math.atan2(s["height"] - s["cowl_z"], run))
    out = []
    for name, value, (lo, hi), shown in (("hood", hood, usual["hood"], f"{100 * hood:.0f}% of the length"),
                                         ("cabin", cabin, usual["cabin"], f"{100 * cabin:.0f}% of the length"),
                                         ("windshield", angle, usual["windshield"], f"{angle:.0f} degrees")):
        if value < lo or value > hi:
            unit = "%" if name != "windshield" else " degrees"
            scale = 100 if name != "windshield" else 1
            out.append(f"The {name} is {shown}; a {s['style']} usually has {lo * scale:.0f}-{hi * scale:.0f}{unit}"
                       + (" (move windshield_base forward to lay the windshield down and shorten the hood)"
                          if name in ("hood", "windshield") and value > hi else "") + ".")
    return out


# --- the side profile and the cross-sections --------------------------------------------------

def _knots(s):
    return [(s["y_front"], s["nose_z"]), (s["windshield_base"], s["cowl_z"]), (s["roof_front"], s["height"]),
            (s["roof_rear"], s["height"]), (s["deck_front"], s["deck_z"]), (s["y_rear"], s["tail_z"])]


def _profile(s, y):
    knots = _knots(s)
    if y <= knots[0][0]:
        return knots[0][1]
    for i, ((ya, za), (yb, zb)) in enumerate(zip(knots, knots[1:])):
        if y <= yb:
            t = (y - ya) / (yb - ya) if yb > ya else 1.0
            if i == 0:
                t = 1 - (1 - t) ** s["hood_curve"]
            elif i == len(knots) - 2:
                t = t ** s["hood_curve"]
            return _lerp(za, zb, t)
    return knots[-1][1]


def _edge_drop(radius, d):
    """How far a quarter-circle edge of `radius` falls below the top line, d from the end."""
    if radius <= 0 or d >= radius:
        return 0.0
    k = 1 - max(0.0, d) / radius
    return radius * (1 - math.sqrt(max(0.0, 1 - k * k)))


def z_top(s, y):
    """Height of the centre line (hood, roof, deck) at y. The hood rises fast from the nose and
    flattens toward the windshield; the deck stays flat and drops toward the tail. The top rounds
    down into the nose and tail faces by nose_edge_radius and tail_edge_radius."""
    z = _profile(s, y)
    return (z - _edge_drop(s["nose_edge_radius"], y - s["y_front"])
            - _edge_drop(s["tail_edge_radius"], s["y_rear"] - y))


def cabin(s, y):
    """0 where the top is hood or deck, 1 on the roof; in between on the windshield and rear glass."""
    return _clamp((z_top(s, y) - s["belt_z"]) / max(1e-6, s["height"] - s["belt_z"]))


def taper(s, y):
    """Plan-view width at y as a fraction of the full width (narrower at the nose and tail)."""
    t_front = 1 - (y - s["y_front"]) / s["taper_length"]
    t_rear = 1 - (s["y_rear"] - y) / s["taper_length"]
    f = 1.0
    if t_front > 0:
        f -= (1 - s["nose_taper"]) * _smooth(t_front)
    if t_rear > 0:
        f -= (1 - s["tail_taper"]) * _smooth(t_rear)
    return f


def edge(s, y):
    """(half-width, height) of the top edge at y: the fender line on the hood, the roof edge on the cabin,
    the A- and C-pillars in between."""
    c = cabin(s, y)
    hw = s["half_width"] * taper(s, y)
    hood_x = hw - s["shoulder"] - 0.05
    x = _lerp(hood_x, s["roof_width"] / 2 * taper(s, y), _smooth(c))
    z = z_top(s, y) + s["fender_rise"] * (1 - c) - s["roof_crown"] * c
    return x, z


def fender_rise(s, y):
    """How much the shoulder and fender line rise at y: an arch over each wheel, the rear one (the
    hips) reaching forward by hip_length."""
    r = s["arch_radius"]
    front = s["front_fender_rise"] * _smooth(1 - abs(y - s["axle_front"]) / (1.6 * r))
    dy = y - s["axle_rear"]
    reach = 1.6 * r + (s["hip_length"] if dy < 0 else 0.0)
    rear = s["rear_fender_rise"] * _smooth(1 - abs(dy) / reach)
    return front + rear


def half_section(s, y):
    """The right half of the cross-section at y, bottom centre to top centre: ten (x, z) points, named
    in SECTION_POINTS. On the hood the top runs fender peak, fender valley, hood side, hood centre; on
    the roof the same points make the roof's crown."""
    zb = s["ride_height"]
    hw = s["half_width"] * taper(s, y)
    ex, ez = edge(s, y)
    zc = z_top(s, y)
    c = _smooth(cabin(s, y))
    belt = min(s["belt_z"], ez - 0.03)
    k = (s["max_width_z"] - zb) / max(1e-6, s["belt_z"] - zb)
    zmax = zb + (belt - zb) * k
    low = hw - s["tuck"]
    dip = s["hood_dip"] * (1 - c)
    valley = (_lerp(max(0.3 * ex, ex - s["fender_valley"]), 0.75 * ex, c), _lerp(zc, ez + (zc - ez) * 0.55, c))
    side = (0.45 * ex, _lerp(zc - dip * 0.3, ez + (zc - ez) * 0.9, c))
    near = (0.18 * ex, _lerp(zc - dip * 0.85, zc - 0.002, c))
    rise = fender_rise(s, y)
    belt += rise
    ez_side = ez + rise * (1 - c)  # the fender peak arches over the front wheel; the roof edge stays put
    return [(0.0, zb), (0.55 * low, zb), (low, zb + 0.035), (hw, zmax), (hw - s["shoulder"], belt), (ex, ez_side),
            valley, side, near, (0.0, zc - dip)]


SECTION_POINTS = ("bottom_centre", "floor_edge", "rocker", "widest", "shoulder", "fender_peak", "fender_valley",
                  "hood_side", "near_centre", "centre")
ROCKER_INDEX, WIDEST_INDEX, BELT_INDEX, EDGE_INDEX, VALLEY_INDEX = 2, 3, 4, 5, 6


def creases(s):
    """How crisp each line along the car is (point index to crease 0..1): the angular look creases
    every character line; the smooth look only the shoulder and the fender peak."""
    if s["look"] == "angular":
        return {ROCKER_INDEX: 0.6, WIDEST_INDEX: 0.3, BELT_INDEX: 0.9, EDGE_INDEX: 0.8, VALLEY_INDEX: 0.6}
    return {BELT_INDEX: 0.75, EDGE_INDEX: 0.55}


def ring_creases(s):
    """Crisp lines across the car (ring index to crease) where the glass meets the hood, roof and deck:
    the angular look only."""
    if s["look"] != "angular":
        return {}
    ys = stations(s)
    out = {}
    for key in ("windshield_base", "roof_front", "roof_rear", "deck_front"):
        i = min(range(len(ys)), key=lambda j: abs(ys[j] - s[key]))
        if abs(ys[i] - s[key]) < 0.02:
            out[i] = 0.7
    return out


def end_crease(s):
    """How crisp the edge round the nose and tail faces is: softer the rounder their top edges are."""
    base = 0.6 if s["look"] == "angular" else 0.8
    radius = (s["nose_edge_radius"] + s["tail_edge_radius"]) / 2
    return round(base * (1 - 0.7 * min(1.0, radius / 0.06)), 3)


def stations(s):
    """The Y positions of the cross-sections: the key places of the profile, filled in so no gap is
    longer than 0.22 m."""
    r = s["arch_radius"]
    keys = [s["y_front"], s["y_front"] + 0.03, s["y_front"] + 0.12, s["y_front"] + 0.3,
            s["axle_front"] - r - 0.05, s["axle_front"], s["axle_front"] + r + 0.05,
            s["windshield_base"], (s["windshield_base"] + s["roof_front"]) / 2, s["roof_front"],
            (s["roof_front"] + s["roof_rear"]) / 2, s["roof_rear"], (s["roof_rear"] + s["deck_front"]) / 2,
            s["deck_front"], s["axle_rear"] - r - 0.05, s["axle_rear"], s["axle_rear"] + r + 0.05,
            s["y_rear"] - 0.3, s["y_rear"] - 0.12, s["y_rear"] - 0.03, s["y_rear"]]
    # a big rounded end edge gets one more section inside its curve (behind the raked end rings)
    if s["nose_edge_radius"] >= 0.05:
        keys.append(s["y_front"] + max(0.065, 0.6 * s["nose_edge_radius"]))
    if s["tail_edge_radius"] >= 0.05:
        keys.append(s["y_rear"] - max(0.065, 0.6 * s["tail_edge_radius"]))
    keys = sorted(y for y in keys if s["y_front"] <= y <= s["y_rear"])
    out = [keys[0]]
    for y in keys[1:]:
        if y - out[-1] < 0.025:
            if y == s["y_rear"]:
                out[-1] = y
            continue
        prev, gap = out[-1], y - out[-1]
        n = int(math.ceil(gap / 0.22))
        out += [prev + gap * i / n for i in range(1, n)]
        out.append(y)
    return out


def _end_shapes(s, centre=False):
    """The ends' side-view shape: every point of the end section gets the setback of its own height on
    the end face (face_setback, plus the face's rake); every section within reach of the end is squeezed
    toward the middle by it, so a point never passes the same point on the next section.
    [(end y, direction, setback per section point, reach)] for the nose and the tail."""
    ends = []
    for end, at, direction in (("nose", s["y_front"], 1.0), ("tail", s["y_rear"], -1.0)):
        face = half_section(s, at)
        zb, zt = face[0][1], face[-1][1]
        widest = max(x for x, _ in face) or 1.0
        # (centre: the points as if on the centre line, where the rollover is whole)
        back = [face_setback_at(s, end, 0.0 if centre else x, z, widest)
                + s[f"{end}_rake"] * (z - zb) / max(1e-6, zt - zb) for x, z in face]
        full = [face_setback(s, end, z) + s[f"{end}_rake"] * (z - zb) / max(1e-6, zt - zb) for _, z in face]
        ends.append((at, direction, back, max(face_reach(s, end), 1.6 * max(full))))  # one reach for both
    return ends


def _shaped_y(ends, y, j):
    """y of point j of the section at y with the ends' side-view shape applied."""
    for at, direction, back, reach in ends:
        d = (y - at) * direction
        if 0 <= d < reach:
            t = back[j]
            return at + direction * (t + d * (1 - t / reach))
    return y


def end_centre_lines(s):
    """The nose's and tail's centre lines for the skin's end caps: the inner points (bottom to top) up
    the middle of each end face, shaped in side view as the sections are but without the plan-view corner
    cut-back, which belongs to the corners only; so the middle of the nose leads at nose_peak_z, the
    bumper's bottom tucked and the hood's edge rolled back behind it."""
    ends = _end_shapes(s, centre=True)
    out = []
    for y in (stations(s)[0], stations(s)[-1]):
        half = half_section(s, y)
        out.append([(0.0, round(_shaped_y(ends, y, j), 5), z) for j, (_, z) in enumerate(half)][1:-1])
    return out


def rings(s):
    """Closed rings of (x, y, z) points, front to back, all with the same count: the cage of the skin.
    Each ring starts at the bottom centre and runs up the right (+X) side and down the left."""
    ys = stations(s)
    ends = _end_shapes(s)
    out = []
    for i, y in enumerate(ys):
        half = half_section(s, y)
        widest = max(x for x, _ in half) or 1.0
        corner, power = 0.0, 2.0
        if s["nose_point"] > 0 and y - s["y_front"] < 2.5 * s["nose_point"]:
            corner = s["nose_point"] * (1 - (y - s["y_front"]) / (2.5 * s["nose_point"]))
            power = s["nose_round"]
        elif s["tail_point"] > 0 and s["y_rear"] - y < 2.5 * s["tail_point"]:
            corner = -s["tail_point"] * (1 - (s["y_rear"] - y) / (2.5 * s["tail_point"]))
            power = s["tail_round"]

        def py(j, x):  # the ends' side-view shape, and their corners cut back in plan view
            return _shaped_y(ends, y, j) + corner * (abs(x) / widest) ** power

        right = [(x, py(j, x), z) for j, (x, z) in enumerate(half)]
        left = [(-x, py(j, x), z) for j, (x, z) in reversed(list(enumerate(half))[1:-1])]
        out.append(right + left)
    return out


# --- default details ------------------------------------------------------------------------

def _rect(cu, cv, w, h, r=0.0, n=4):
    """A rectangle centred on (cu, cv), counter-clockwise, with corners rounded by r."""
    r = max(0.0, min(r, w / 2 - 1e-4, h / 2 - 1e-4))
    if r <= 0:
        return [(cu + w / 2, cv + h / 2), (cu - w / 2, cv + h / 2), (cu - w / 2, cv - h / 2), (cu + w / 2, cv - h / 2)]
    hu, hv = w / 2 - r, h / 2 - r
    pts = []
    for ou, ov, start in ((hu, hv, 0), (-hu, hv, 90), (-hu, -hv, 180), (hu, -hv, 270)):
        for i in range(n + 1):
            a = math.radians(start + 90 * i / n)
            pts.append((cu + ou + r * math.cos(a), cv + ov + r * math.sin(a)))
    return pts


def _circle(cu, cv, r, n=28):
    return [(cu + r * math.cos(2 * math.pi * i / n), cv + r * math.sin(2 * math.pi * i / n)) for i in range(n)]


def _dedupe(points, eps=1e-5):
    """A closed outline without repeated points (they make zero-length cutter edges), its last point
    dropped too when it repeats the first."""
    out = []
    for p in points:
        if not out or math.dist(out[-1], p) > eps:
            out.append(p)
    while len(out) > 2 and math.dist(out[0], out[-1]) <= eps:
        out.pop()
    return out


def _crosses(points):
    """True when the closed outline crosses itself."""
    n = len(points)

    def side(p, q, r):
        return (q[0] - p[0]) * (r[1] - p[1]) - (q[1] - p[1]) * (r[0] - p[0])

    for i in range(n):
        a, b = points[i], points[(i + 1) % n]
        for j in range(i + 2, n):
            if i == 0 and j == n - 1:
                continue
            c, d = points[j], points[(j + 1) % n]
            if side(a, b, c) * side(a, b, d) < 0 and side(c, d, a) * side(c, d, b) < 0:
                return True
    return False


def outline_points(outline):
    """A feature outline as a list of (u, v): a point list, or {"rect": [cu, cv, w, h, r]},
    {"circle": [cu, cv, r]} or {"poly": [[u, v], ...]}."""
    if isinstance(outline, dict):
        if "rect" in outline:
            return _dedupe(_rect(*[float(v) for v in outline["rect"]]))
        if "circle" in outline:
            return _dedupe(_circle(*[float(v) for v in outline["circle"]]))
        if "poly" in outline:
            return _dedupe([(float(u), float(v)) for u, v in outline["poly"]])
        raise ValueError(f"unknown outline {outline!r}: use rect, circle, poly or a point list")
    return _dedupe([(float(u), float(v)) for u, v in outline])


def nudged(feature, du, dv):
    """The detail moved by (du, dv) in its view's plane: its outline, or a groove's path."""
    if not (du or dv):
        return feature
    f = dict(feature)
    if "path" in f:
        f["path"] = [(float(u) + du, float(v) + dv) for u, v in f["path"]]
    outline = f.get("outline")
    if isinstance(outline, dict) and ("rect" in outline or "circle" in outline):
        key = "rect" if "rect" in outline else "circle"
        values = [float(x) for x in outline[key]]
        f["outline"] = {key: [values[0] + du, values[1] + dv] + values[2:]}
    elif outline is not None:
        f["outline"] = [(u + du, v + dv) for u, v in outline_points(outline)]
    return f


GLASS_BOW = 0.04  # how far the middle of the windshield's and rear glass's edges bows out from their corners


def _glass_top_straight(s, y_a, y_b, n=7):
    """The plain straight-edged glass outline, for when the curved one will not cut cleanly."""
    ys = [_lerp(y_a, y_b, i / (n - 1)) for i in range(n)]
    right = [(round(edge(s, y)[0] - s["a_pillar"], 5), round(y, 5)) for y in ys]
    return right + [(-x, y) for x, y in reversed(right)]


def _glass_top(s, y_a, y_b, n=7, bow=0.0):
    """Top-view outline of the windshield or rear glass between y_a and y_b, inside the pillars. bow:
    how far the middle of its front and back edges lies ahead (negative: behind) of their corners, as
    the curved cowl and roof edges of a real car run; the corners are rounded."""
    ys = [_lerp(y_a, y_b, i / (n - 1)) for i in range(n)]
    right = [(edge(s, y)[0] - s["a_pillar"], y) for y in ys]
    across = 6

    def edge_line(y, x_side, reverse):
        pts = [(x_side * (1 - 2 * k / across), y - bow * (1 - (1 - 2 * k / across) ** 2)) for k in range(1, across)]
        return pts[::-1] if reverse else pts

    outline = right + edge_line(right[-1][1], right[-1][0], False) + [(-x, y) for x, y in reversed(right)] \
        + edge_line(right[0][1], right[0][0], True)
    return _rounded(outline, cut=0.08, rounds=2)


def _rounded(points, cut=0.14, rounds=2):
    """A closed outline with its corners rounded off (corner cutting): the rounder the longer the
    edges meeting at a corner."""
    pts = list(points)
    for _ in range(rounds):
        out = []
        for i, (a, b) in enumerate(zip(pts, pts[1:] + pts[:1])):
            out.append((_lerp(a[0], b[0], cut), _lerp(a[1], b[1], cut)))
            out.append((_lerp(a[0], b[0], 1 - cut), _lerp(a[1], b[1], 1 - cut)))
        pts = out
    return [(round(u, 5), round(v, 5)) for u, v in pts]


DLO_MARGIN = 0.025   # the black band of the window opening outside the glass
GLASS_FRAME = 0.02   # and the rubber seal step round the glass inside it
B_PILLAR = 0.07      # the black pillar between the front and rear side glass


def side_openings(s):
    """The side windows in the side view, (y, z): the whole window opening (black trim, round its
    glass and across the B-pillar) and the glass pieces set into it (front door glass, then rear
    door or quarter glass). ([], []) when the cabin is too low for side glass."""
    # a tall tail (a van, an estate) puts deck_front right at the back: the glass stops before the
    # rear corner, which the pillar wraps round
    ya, yb = s["windshield_base"], min(s["deck_front"], s["y_rear"] - s["tail_corner"] - s["c_pillar"] - 0.02)
    if s["dlo_end"] is not None:
        yb = min(yb, s["dlo_end"])

    def top(y):
        return min(edge(s, y - s["a_pillar"] * 1.6)[1], edge(s, y + s["c_pillar"] * 1.6)[1]) - s["window_margin"]

    def bottom(y):
        """The window's lower edge: rising toward the back by belt_rise, then shaped by dlo_style."""
        t = _clamp((y - ya) / max(1e-6, yb - ya))
        z = s["belt_z"] + 0.005 + s["belt_rise"] * t ** 1.5
        style = s["dlo_style"]
        if style == "coupe":  # the rear end sweeps up to a point under the roof: a teardrop
            z += max(0.0, top(y) - z - 0.03) * _smooth((t - 0.62) / 0.38) ** 2
        elif style == "sedan":  # a kink up at the rear corner
            z += 0.06 * _smooth((t - 0.86) / 0.12)
        elif style in ("hatch", "suv"):  # the rear quarter window kicked up
            z += 0.05 * _smooth((t - 0.74) / 0.06)
        return z

    # an end that comes to a point runs on until the opening is only DLO_TIP tall: forward down the
    # windshield pillar to the belt line at the mirror, back along the rear pillar; an upright end
    # stops where the glass would be less than 12 cm tall, with a short upright edge
    front_point, rear_point = s["dlo_front"] == "point", s["dlo_rear"] == "point"
    y_lo = ya - 0.25 if front_point else ya
    y_hi = yb + 0.05 if rear_point else yb
    middle = (ya + yb) / 2
    ys = [_lerp(y_lo, y_hi, i / 160) for i in range(161)]
    # where the roof does not come down to the belt line by itself (an upright tail, a tall cab), the
    # opening is drawn in to its point over the last stretch: the lower edge sweeps up to the roof at
    # the back, the upper edge comes down to the belt line at the front
    reach = 0.35

    def lower(y):
        z = bottom(y)
        if rear_point and y > y_hi - reach:
            z += max(0.0, top(y) - z - DLO_TIP * 0.8) * _smooth((y - (y_hi - reach)) / reach) ** 2
        return z

    def upper(y):
        z = top(y)
        if front_point and y < y_lo + reach:
            z -= max(0.0, z - bottom(y) - DLO_TIP * 0.8) * _smooth((y_lo + reach - y) / reach) ** 2
        return z

    def pointed(y):
        return front_point if y < middle else rear_point

    inside = [y for y in ys if upper(y) > lower(y) + (DLO_TIP if pointed(y) else 0.12)]
    if len(inside) < 3:
        return [], []
    cut = s["dlo_corner"]
    crisp = front_point or rear_point  # a point stays a point: soft corners would blunt it

    def outline(run, m, soft):
        under = [(y, lower(y) + m) for y in reversed(run)][::20 if crisp else 8]
        if under[-1][0] != run[0]:
            under.append((run[0], lower(run[0]) + m))
        return _rounded([(y, upper(y) - m) for y in run] + under, cut=min(soft, 0.05) if crisp else soft,
                        rounds=1 if crisp else 2)

    dlo = outline(inside, 0.0, cut)
    m = DLO_MARGIN
    split = glass_split(s)
    glass_ys = [y for y in inside if upper(y) - m > lower(y) + m + (DLO_TIP - 0.005 if pointed(y) else 0.08)]
    pieces = [[y for y in glass_ys if y < split - B_PILLAR / 2 and (front_point or y > inside[0] + m * 0.6)],
              [y for y in glass_ys if y > split + B_PILLAR / 2 and (rear_point or y < inside[-1] - m * 0.6)]]
    glass = [outline(piece, m, max(0.06, cut * 0.8)) for piece in pieces if len(piece) >= 3]
    return dlo, glass


DLO_TIP = 0.03  # how tall a side window still is at an end that comes to a point


def _side_glass(s):
    """Side-view outlines (y, z) of the door glass and the quarter glass."""
    return side_openings(s)[1]


def _box(u0, u1, v0, v1, r=0.0):
    """A rectangle from (u0, v0) to (u1, v1) with corners rounded by r, as a point list."""
    return _rect((u0 + u1) / 2, (v0 + v1) / 2, u1 - u0, v1 - v0, r)


def _arch_band(cy, cz, r_in, r_out, a0=-12.0, a1=192.0, n=24):
    """Side-view outline (y, z) of a band round a wheel opening: the arc from a0 to a1 degrees
    (0 toward the back, 90 up, 180 toward the front) between radii r_in and r_out."""
    outer = [(cy + r_out * math.cos(math.radians(a0 + (a1 - a0) * i / n)),
              cz + r_out * math.sin(math.radians(a0 + (a1 - a0) * i / n))) for i in range(n + 1)]
    inner = [(cy + r_in * math.cos(math.radians(a1 - (a1 - a0) * i / n)),
              cz + r_in * math.sin(math.radians(a1 - (a1 - a0) * i / n))) for i in range(n + 1)]
    return outer + inner


def _raised(s):
    """The shapes that stand out of the skin: the front bumper and lip, side skirts, boxed flares, a hood
    bulge. (The rear bumper is no raised box: it swells out of the skin itself, see rear_bumper.)"""
    hw = s["half_width"]
    zb, yf, yr = s["ride_height"], s["y_front"], s["y_rear"]
    r, band = s["arch_radius"], s["boxed_width"]
    nose = min(yf + 0.45, s["axle_front"] - r - band - 0.04)
    front_top = s["nose_z"] - 0.17
    under = zb - 0.12  # bumpers, lip and skirts reach under the car, so no outline edge skims the underside
    raise_ = dict(kind="raise", material="paint")
    # the bumpers stay on the end faces: drawn inside the body's width and stopped a little behind the
    # end's own curve, they never run back along the sides, where a cut nearly along the surface would
    # stand out as a fin
    bumper_front = min(nose, yf + s["nose_point"] + 0.28)
    out = [
        dict(name="front_bumper", view="front", until=bumper_front, height=s["bumper"],
             outline={"poly": _box(-hw * 0.97, hw * 0.97, under, front_top, 0.03)}, **raise_),
        dict(name="front_lip", view="front", until=bumper_front, height=s["bumper"] + s["front_lip"],
             outline={"poly": _box(-hw * 0.88, hw * 0.88, under, zb + 0.05, 0.02)}, **raise_),
    ] if s["raised_bumpers"] else []
    out += [
        dict(name="side_skirts", view="side", until=hw - s["tuck"] - 0.06, height=s["skirt"],
             outline={"poly": _box(s["axle_front"] + r + band + 0.03, s["axle_rear"] - r - band - 0.03, under,
                                   zb + 0.10, 0.02)}, **raise_),
    ]
    if s["arch_cladding"]:  # black plastic trim standing a little proud round each wheel opening
        for name, axle in (("front_arch_trim", s["axle_front"]), ("rear_arch_trim", s["axle_rear"])):
            out.append(dict(name=name, view="side", until=hw * 0.6, height=0.006, kind="raise", material="trim",
                            outline=_arch_band(axle, s["wheel_radius"], r - 0.03, r + 0.055, a0=-8.0, a1=188.0)))
    if s["style"] == "coupe":
        y0, y1 = yf + 0.45, s["windshield_base"] - 0.12
        out.append(dict(name="hood_bulge", view="top", until=s["nose_z"] - 0.12, height=0.018,
                        outline={"rect": [0.0, (y0 + y1) / 2, hw * 0.55, y1 - y0, 0.06]}, **raise_))
    return out


def end_protrusion(features_list):
    """How far raised details stand out past the nose and the tail: (front, rear) in metres."""
    front = max([float(f.get("height", 0)) for f in features_list if f.get("kind") == "raise"
                 and f.get("view") == "front"] or [0.0])
    rear = max([float(f.get("height", 0)) for f in features_list if f.get("kind") == "raise"
                and f.get("view") == "rear"] or [0.0])
    return front, rear


def nose_aim(s, x, z, on_hood=0.3):
    """Where a lamp at (x, z) of the nose faces: (aim, at), the direction a cut into the nose surface
    travels there (the surface's inward normal, leaning `on_hood` of the way toward the hood's) and
    a point on or just in front of that surface. From the sheet's own maths, so it follows the plan
    shape (nose_point, nose_round), the rake and the hood's slope. The maths is an estimate of the
    built skin (it can sit centimetres off and lean many degrees wrong), so a detail placed with it is
    marked `on_nose`, and the build (bpy_lib.shells.car) measures the real surface there instead."""
    zb, yf = s["ride_height"], s["y_front"]
    widest = max(1e-6, s["half_width"] * taper(s, yf))
    t = min(1.0, abs(x) / widest)
    p = s["nose_round"]
    offset = s["nose_point"] * t ** p
    slope_plan = s["nose_point"] * p * t ** (p - 1) / widest if t > 0 else 0.0  # dy/dx of the nose in plan
    h = max(0.1, s["nose_z"] - zb)
    # the face's own side-view shape: how far back it sits at z, and how it leans there
    reach = _end_shapes(s)[0][3]
    face_widest = max(px for px, _ in half_section(s, yf)) or 1.0
    back = face_setback_at(s, "nose", x, z, face_widest) * (1 - offset / reach)
    lean = (face_setback_at(s, "nose", x, z + 0.01, face_widest)
            - face_setback_at(s, "nose", x, z - 0.01, face_widest)) / 0.02
    front = (math.copysign(slope_plan, x), -1.0, s["nose_rake"] / h + lean)
    y_hood = yf + offset + 0.15
    rise = (z_top(s, y_hood + 0.05) - z_top(s, y_hood - 0.05)) / 0.1
    hood = (0.0, -rise, 1.0)

    def unit(v):
        n = math.sqrt(sum(c * c for c in v)) or 1.0
        return tuple(c / n for c in v)

    front, hood = unit(front), unit(hood)
    normal = unit(tuple((1 - on_hood) * a + on_hood * b for a, b in zip(front, hood)))
    at_y = yf + offset + back + s["nose_rake"] * (z - zb) / h
    return [round(-c, 4) for c in normal], [round(x, 4), round(at_y, 4), round(z, 4)]


LAMP_STYLES = ("projector", "popup", "popup_open", "flush")
LAMP_SHAPES = ("swept", "angular", "slim", "round", "rect", "oval", "blade", "split")
GRILLES = ("slot", "small", "wide", "tall", "none")
INTAKES = ("full", "center", "split", "low")
GRILLE_PATTERNS = ("horizontal", "vertical", "mesh", "none")


def lamp_box(s):
    """(centre x, width, height) of a projector headlight: lamp_span wide, its outer end at 87% of the
    half-width whatever its width."""
    hw = s["half_width"]
    w = hw * s["lamp_span"]
    return hw * 0.87 - w / 2, w, s["lamp_height"]


POPUP_LID_LENGTH = 0.30   # front to back, the hinge at the back


def popup_lid(s):
    """(inner x, outer x, front y, length) of the right pop-up lamp lid on the hood; the left mirrors it."""
    hw = s["half_width"]
    return hw * 0.4, hw * 0.8, s["y_front"] + s["nose_point"] * 0.6 + 0.10, POPUP_LID_LENGTH


def popup_pods(s):
    """The raised pop-up lamps of lamp_style "popup_open", for the assembly: one dict per side with the
    pod's centre x, front y, width, length and the height of its upright front face. Each pod stands on
    its lid: seen from the side a wedge, the front face upright (the lens), the lid sloping back down into
    the hood at the hinge. Empty for any other lamp_style."""
    if s["lamp_style"] != "popup_open":
        return []
    x0, x1, y0, length = popup_lid(s)
    width = x1 - x0 - 0.01
    face = round(min(0.15, max(0.10, width * 0.55)), 3)
    return [dict(x=side * (x0 + x1) / 2, y=y0 + 0.005, width=round(width, 4), length=length - 0.01, face=face)
            for side in (1, -1)]


def _headlights(s, nose, lamp_z):
    """The headlights of the sheet's lamp_style: "projector" (a dark housing with a light bezel, two
    round projector lenses in chrome rings and a light strip, cut along the nose's own surface, placed
    by lamp_span, lamp_height and lamp_drop), "popup" (closed pop-up lids on the hood with a thin lamp
    strip in the bumper), "popup_open" (the same, the lids raised as pods: see popup_pods) or "flush" (one lens cut
    straight back from the front). lamp_bar adds a light
    strip across the nose joining the two."""
    out = _headlight_lamps(s, nose, lamp_z)
    if s["lamp_bar"] and s["lamp_style"] == "projector":
        cx, w, h = lamp_box(s)
        inner = cx - w / 2 - 0.01
        z = lamp_z - 0.045  # under the grille slot, along the lamps' lower edges
        out.append(dict(name="headlight_bar", kind="groove", view="front", until=nose, material="lamp_led",
                        width=0.008, optional=True, path=[(round(-inner, 4), round(z, 4)), (round(inner, 4), round(z, 4))]))
    return out


def _headlight_lamps(s, nose, lamp_z):
    hw = s["half_width"]
    style = s["lamp_style"]
    if style == "flush":
        return [dict(name="headlights", kind="recess", view="front", mirror_x=True, until=nose,
                     material="lamp_front", depth=0.012, frame=0.012, frame_material="trim", frame_depth=0.005,
                     outline={"poly": [(hw * 0.34, lamp_z - 0.055), (hw * 0.88, lamp_z - 0.035),
                                       (hw * 0.9, lamp_z + 0.05), (hw * 0.36, lamp_z + 0.06)]})]
    if style in ("popup", "popup_open"):
        x0, x1, y0, length = popup_lid(s)
        lid = [(x0, y0), (x1, y0 + 0.02), (x1, y0 + length), (x0, y0 + length), (x0, y0)]
        strip_z = s["nose_z"] - 0.17 - 0.06  # inside the front bumper, under its top edge
        return [dict(name="headlight_lids", kind="groove", view="top", mirror_x=True, until=0.0, width=0.005,
                     material="trim", path=_soft(lid), optional=True),
                dict(name="headlights", kind="recess", view="front", mirror_x=True, until=nose,
                     material="lamp_front", depth=0.008 + s["bumper"], frame=0.008, frame_material="trim",
                     frame_depth=0.004 + s["bumper"],
                     outline={"rect": [hw * 0.6, strip_z, hw * 0.42, 0.04, 0.01]})]
    # projector: everything cut along the nose surface at the lamp, in the sheet's lamp_shape
    cx, w, h = lamp_box(s)
    aim, at = nose_aim(s, cx, lamp_z)
    on = dict(view="aim", aim=aim, at=at, until=0.25, mirror_x=True, on_nose=True)
    shape = s["lamp_shape"]
    lens_r = h * 0.27
    lenses = [(-w * 0.22, 0.008), (w * 0.2, 0.012)]
    if shape == "swept":  # long and narrow, the outer end sweeping up and back
        housing = _rounded([(-w / 2, -h * 0.3), (w * 0.3, -h * 0.5), (w / 2 + 0.03, h * 0.05), (w / 2, h * 0.55),
                            (-w * 0.1, h * 0.4), (-w / 2, h * 0.2)], cut=0.18, rounds=2)
        drl = [(-w * 0.42, h * 0.22), (w * 0.05, h * 0.33), (w * 0.42, h * 0.38)]
        lens_r = h * 0.22
        lenses = [(-w * 0.22, -0.002), (w * 0.15, -0.006)]
    elif shape == "angular":  # a sharp inner end and a straight, slanted top
        housing = _rounded([(-w / 2, h * 0.1), (-w * 0.25, -h / 2), (w / 2, -h * 0.45), (w / 2, h * 0.5),
                            (-w * 0.3, h * 0.42)], cut=0.06, rounds=1)
        drl = [(-w * 0.3, h * 0.28), (w * 0.42, h * 0.32)]
    elif shape == "slim":  # a thin LED slit over a separate main lamp
        housing = _rounded([(-w / 2, h * 0.2), (w / 2, h * 0.25), (w / 2, h * 0.5), (-w / 2, h * 0.42)],
                           cut=0.2, rounds=2)
        drl = None
        lenses = []
    elif shape == "round":  # twin round lamps, or one big one
        housing = None
        drl = None
    elif shape == "oval":  # one big upright oval with a single lens
        housing = [(w / 2 * math.cos(2 * math.pi * k / 28), h / 2 * math.sin(2 * math.pi * k / 28)) for k in range(28)]
        drl = [(w / 2 * 0.72 * math.cos(math.radians(a)), h / 2 * 0.72 * math.sin(math.radians(a)))
               for a in range(30, 151, 15)]
        lenses = [(0.0, -h * 0.05)]
        lens_r = min(h, w) * 0.26
    elif shape == "blade":  # a thin sliver, pointed at its inner end, rising to the outer end
        housing = _rounded([(-w / 2, h * 0.15), (w / 2 - 0.03, -h * 0.5), (w / 2, -h * 0.2), (w / 2, h * 0.5),
                            (-w * 0.1, h * 0.35)], cut=0.12, rounds=2)
        drl = [(-w * 0.4, h * 0.2), (w * 0.4, h * 0.38)]
        lenses = [(w * 0.28, -h * 0.02)]
        lens_r = h * 0.24
    elif shape == "split":  # the LED slit up here; the main lamp goes down into the bumper corner below
        housing = _rounded([(-w / 2, -h * 0.15), (w / 2, -h * 0.05), (w / 2, h * 0.2), (-w / 2, h * 0.1)],
                           cut=0.2, rounds=2)
        drl = None
        lenses = []
    else:  # rect: a rounded rectangle
        housing = _rounded([(-w / 2, -h / 2), (w / 2, -h / 2), (w / 2, h / 2), (-w / 2, h / 2)], cut=0.08, rounds=2)
        drl = [(-w * 0.42, h * 0.32), (w * 0.42, h * 0.32)]
    out = []
    if housing is not None:
        out.append(dict(name="headlights", kind="recess", material="trim", depth=0.03, frame=0.01,
                        frame_material="lamp_front", frame_depth=0.006, outline={"poly": housing}, **on))
    if shape == "split":  # the slit glows; the main lamp is a separate housing low in the bumper corner
        out[-1].update(material="lamp_led", frame_material="trim")
        main_z = s["nose_z"] - 0.17 - 0.08  # inside the front bumper, under its top edge
        mw, mh = w * 0.62, 0.075
        maim, mat = nose_aim(s, cx + w * 0.12, main_z, on_hood=0.0)
        low = dict(view="aim", aim=maim, at=mat, until=0.25, mirror_x=True, on_nose=True)
        out.append(dict(name="headlight_main", kind="recess", material="trim", depth=0.03 + s["bumper"], frame=0.008,
                        frame_material="chrome", frame_depth=0.006, optional=True,
                        outline={"rect": [0.0, 0.0, mw, mh, 0.02]}, **low))
        for i, u in enumerate((-mw * 0.22, mw * 0.22)):
            out.append(dict(name=f"headlight_projector_{'inner' if i == 0 else 'outer'}", kind="recess",
                            material="lamp_front", depth=0.04 + s["bumper"], frame=0.006, frame_material="chrome",
                            frame_depth=0.034, optional=True, outline={"circle": [u, 0.0, mh * 0.3]}, **low))
    if shape == "slim":  # the slit itself glows; the main lamp sits under it
        out[-1].update(material="lamp_led", frame_material="trim")
        out.append(dict(name="headlight_main", kind="recess", material="trim", depth=0.03, frame=0.008,
                        frame_material="chrome", frame_depth=0.006, optional=True,
                        outline={"poly": _rounded([(-w * 0.2, -h * 0.75), (w * 0.35, -h * 0.75), (w * 0.35, -h * 0.05),
                                                   (-w * 0.2, -h * 0.05)], cut=0.15, rounds=2)}, **on))
        lenses = [(w * 0.08, -h * 0.4)]
        lens_r = h * 0.22
    if shape == "round":
        single = int(s["round_count"]) == 1
        centres = ((0.0, 0.0),) if single else ((-w * 0.25, 0.0), (w * 0.25, 0.0))
        for i, (u, v) in enumerate(centres):
            rad = min(h * 0.5, w * 0.45) if single else min(h * 0.62, w * 0.24)
            out.append(dict(name=f"headlight_{'inner' if i == 0 else 'outer'}", kind="recess", material="trim",
                            depth=0.03, frame=0.01, frame_material="chrome", frame_depth=0.006,
                            outline={"circle": [u, v, rad]}, **on))
            out.append(dict(name=f"headlight_ring_{i + 1}", kind="groove", material="lamp_led", width=0.008,
                            optional=True, path=[(u + rad * 0.72 * math.cos(2 * math.pi * k / 24),
                                                  v + rad * 0.72 * math.sin(2 * math.pi * k / 24)) for k in range(25)],
                            **on))
        lenses = list(centres)
        lens_r = min(h * 0.24, w * 0.2) if single else min(h * 0.3, w * 0.12)
    for (u, v), name in zip(lenses, ("headlight_projector_inner", "headlight_projector_outer")):
        out.append(dict(name=name, kind="recess", material="lamp_front", depth=0.04, frame=0.008,
                        frame_material="chrome", frame_depth=0.034, outline={"circle": [u, v, lens_r]}, **on))
    if drl:  # the daytime running light: a glowing LED line along the lamp's upper edge
        out.append(dict(name="headlight_strip", kind="groove", material="lamp_led", width=0.008, optional=True,
                        path=drl, **on))
    return out


SHUT_LINE = 0.005  # the width of every panel gap


def _soft(path, cut=0.2, rounds=2):
    """An open path with its corners rounded off (its ends kept), so the tube a groove sweeps along
    it never folds over itself at a sharp corner."""
    pts = [tuple(p) for p in path]
    closed = len(pts) > 3 and math.dist(pts[0], pts[-1]) < 1e-6
    if closed:
        loop = _rounded(pts[:-1], cut=cut, rounds=rounds)
        return loop + [loop[0]]
    for _ in range(rounds):
        out = [pts[0]]
        for a, b in zip(pts, pts[1:]):
            out.append((_lerp(a[0], b[0], cut), _lerp(a[1], b[1], cut)))
            out.append((_lerp(a[0], b[0], 1 - cut), _lerp(a[1], b[1], 1 - cut)))
        out.append(pts[-1])
        pts = out
    return [(round(u, 5), round(v, 5)) for u, v in pts]


def _window_frame(s, y0, y1):
    """The top of the door's window frame between y0 and y1 in the side view: the window opening's
    upper edge lifted a little, but kept under the roof's edge (a line over the roof's rounded edge
    is cut by rays that only graze it), front to back."""
    dlo, _ = side_openings(s)
    if not dlo:
        return []
    lift = min(0.03, 0.6 * s["window_margin"])
    # the opening's upper edge: for each stretch of y, its highest point (its lower edge can rise too)
    lo, hi = max(y0, min(y for y, _ in dlo)), min(y1, max(y for y, _ in dlo))
    pts = []
    for i in range(25):
        y = _lerp(lo, hi, i / 24)
        near = [z for yy, z in dlo if abs(yy - y) < 0.03]
        if near and max(near) > s["belt_z"] + 0.04:
            pts.append((round(y, 4), round(max(near) + lift, 4)))
    return pts


def _doors(s):
    """The doors' shut lines and handles. The front door's line runs from the sill up its front edge
    behind the front wheel, round its window frame over the glass, and down the B-pillar; a
    four-door car's rear door continues from the B-pillar over its own glass and down in front of
    the rear wheel."""
    zb, r, band = s["ride_height"], s["arch_radius"], s["boxed_width"]
    door_front = door_start(s)
    split = glass_split(s)
    door_rear = split if s["door_length"] is not None else max(door_front + 0.7, split)
    sill = zb + 0.16  # above the side skirt
    belt = s["belt_z"] - 0.012
    dlo, _ = side_openings(s)
    line = dict(kind="groove", view="side", until=0.0, width=SHUT_LINE, material="trim", optional=True)
    frame = _window_frame(s, -10.0, door_rear)
    path = [(door_front + 0.04, sill), (door_front, sill + 0.04), (door_front, belt)]
    if frame:
        if frame[0][0] - 0.03 > door_front + 0.02:  # the door's front edge runs along the belt to the A-pillar
            path.append((frame[0][0] - 0.03, belt + 0.01))
        path += frame
        if abs(frame[-1][0] - door_rear) > 1e-4:  # the frame may end at the B-pillar already
            path.append((door_rear, frame[-1][1]))
    path += [(door_rear, sill + 0.04), (door_rear - 0.04, sill), (door_front + 0.04, sill)]
    out = [dict(name="door_line", path=_soft(path), **line),
           dict(name="door_handle", kind="recess", view="side", until=0.0, material="trim", depth=0.008, optional=True,
                outline={"rect": [door_rear - 0.16, s["belt_z"] - 0.07, 0.13, 0.025, 0.01]})]
    if int(s["doors"]) == 4:
        # the rear door reaches over the front half of the rear wheel and is cut round its opening
        R = r + band + 0.02
        rear_end = s["axle_rear"] - 0.05
        arch_front = s["axle_rear"] - R
        if arch_front > door_rear + 0.3:
            top = _window_frame(s, door_rear + 0.01, rear_end)
            rear = ([(door_rear + 0.005, top[0][1])] + top) if top else [(door_rear + 0.005, belt)]
            if top and abs(top[-1][0] - rear_end) > 1e-4:
                rear.append((rear_end, rear[-1][1]))
            rear.append((rear_end, belt))
            start = math.degrees(math.acos(max(-1.0, min(1.0, (rear_end - s["axle_rear"]) / R))))
            for i in range(9):  # round the wheel opening, from above it to its front
                a = math.radians(_lerp(start, 180.0, i / 8))
                rear.append((round(s["axle_rear"] + R * math.cos(a), 4), round(s["wheel_radius"] + R * math.sin(a), 4)))
            rear += [(arch_front, sill + 0.04), (arch_front - 0.04, sill), (door_rear + 0.045, sill)]
            out += [dict(name="rear_door_line", path=_soft(rear), **line),
                    dict(name="rear_door_handle", kind="recess", view="side", until=0.0, material="trim", optional=True,
                         depth=0.008, outline={"rect": [rear_end - 0.16, s["belt_z"] - 0.07, 0.13, 0.025, 0.01]})]
    return out


def _panel_lines(s):
    """The other shut lines of a real car: the hood, the boot lid or tailgate, the fuel flap, and the
    joints where the bumpers meet the fenders. Each is left out if it cannot be cut cleanly."""
    zb, yf, yr = s["ride_height"], s["y_front"], s["y_rear"]
    hw, r, band = s["half_width"], s["arch_radius"], s["boxed_width"]
    line = dict(kind="groove", width=SHUT_LINE, material="trim", until=0.0, optional=True)

    def valley_x(y):
        return max(0.2, edge(s, y)[0] - s["fender_valley"] * 0.6)

    cowl = s["windshield_base"] - 0.05
    if s["lamp_style"] in ("popup", "popup_open"):
        front = yf + s["nose_point"] * 0.6 + 0.10 + 0.33  # behind the pop-up lids
    else:
        front = yf + s["nose_point"] + 0.12
    out = []
    if cowl - front > 0.3:
        ys = [_lerp(front, cowl, i / 6) for i in range(7)]
        right = [(round(valley_x(y), 4), round(y, 4)) for y in ys]
        hood = right + [(-x, y) for x, y in reversed(right)] + [right[0]]  # a closed loop round the hood
        out.append(dict(name="hood_line", view="top", path=_soft(hood), **line))
    if s["style"] in ("coupe", "sedan") and s["deck_z"] < s["height"] - 0.2:
        y0, y1 = s["deck_front"] + 0.06, yr - s["tail_point"] - s["tail_edge_radius"] - 0.03
        if y1 - y0 > 0.25:
            x = round(max(0.25, min(edge(s, y0)[0], edge(s, y1)[0]) - 0.11), 4)
            boot = _rounded([(x, y0), (x, y1), (-x, y1), (-x, y0)], cut=0.08)
            out.append(dict(name="boot_line", view="top", path=boot + [boot[0]], **line))
    else:  # a tailgate: up the tail between the lamps and over the rear glass
        z0 = zb + 0.40 + 0.03  # above the rear bumper
        z1 = s["height"] - 0.05
        out.append(dict(name="tailgate_line", view="rear", between_lamps=True,
                        path=_soft([(hw * 0.6, z0), (hw * 0.6, z1), (-hw * 0.6, z1), (-hw * 0.6, z0), (hw * 0.6, z0)],
                                   cut=0.08),
                        **line))
    # the fuel flap, on the right rear quarter above the wheel
    fz = max(s["belt_z"] - 0.10, s["wheel_radius"] + r + band + 0.09)
    if fz + 0.06 < s["belt_z"] - 0.02:
        fy = s["axle_rear"] + 0.17  # behind the rear door, which can reach over the front of the wheel
        flap = _rounded([(fy - 0.085, fz - 0.055), (fy + 0.085, fz - 0.055), (fy + 0.085, fz + 0.055),
                         (fy - 0.085, fz + 0.055)], cut=0.2)
        out.append(dict(name="fuel_flap", view="side", both_sides=False, path=flap + [flap[0]], **line))
    # bumper joints: from the top of each wheel opening to the lamps
    R = r + band + 0.025
    a, b = math.radians(150), math.radians(30)
    out.append(dict(name="front_bumper_joint", view="side",
                    path=[(round(s["axle_front"] + R * math.cos(a), 4), round(s["wheel_radius"] + R * math.sin(a), 4)),
                          (round(yf + s["nose_point"] + 0.07, 4), round(s["nose_z"] - 0.13, 4))], **line))
    out.append(dict(name="rear_bumper_joint", view="side",
                    path=[(round(s["axle_rear"] + R * math.cos(b), 4), round(s["wheel_radius"] + R * math.sin(b), 4)),
                          (round(yr - s["tail_point"] - 0.07, 4),
                           round((rear_bumper(s) or {}).get("top", zb + 0.42), 4))], **line))
    return out


TAIL_LAMP_STYLES = ("wrap", "bar", "slim", "round_twin", "vertical", "split", "tri", "square")
LENS = 0.008       # how far a tail lamp's lens stands out of the paint
SURROUND = 0.004   # and its dark surround, 12 mm wider all round


def _band(points, thickness):
    """A closed outline `thickness` wide round an open curve of (u, v) points (a thin lamp strip)."""
    top, bottom = [], []
    for i, (u, v) in enumerate(points):
        a = points[max(0, i - 1)]
        b = points[min(len(points) - 1, i + 1)]
        du, dv = b[0] - a[0], b[1] - a[1]
        n = math.hypot(du, dv) or 1.0
        nu, nv = -dv / n * thickness / 2, du / n * thickness / 2
        top.append((u + nu, v + nv))
        bottom.append((u - nu, v - nv))
    return _rounded(top + bottom[::-1], cut=0.2, rounds=1)


def _inset_poly(points, distance):
    """A closed outline moved inward by `distance` (plain Python), or None when it is too thin or the
    moved outline would cross itself."""
    pts = []
    for p in _dedupe(list(points)):  # points closer than the inset would fold the moved outline over
        if not pts or math.dist(pts[-1], p) >= distance:
            pts.append(p)
    if len(pts) > 3 and math.dist(pts[0], pts[-1]) < distance:
        pts.pop()

    def area(q):
        return sum(q[i][0] * q[(i + 1) % len(q)][1] - q[(i + 1) % len(q)][0] * q[i][1] for i in range(len(q)))

    if area(pts) < 0:
        pts = pts[::-1]
    out = []
    n = len(pts)
    for i in range(n):
        p0, p1, p2 = pts[i - 1], pts[i], pts[(i + 1) % n]
        d0 = (p1[0] - p0[0], p1[1] - p0[1])
        d1 = (p2[0] - p1[0], p2[1] - p1[1])
        l0, l1 = math.hypot(*d0), math.hypot(*d1)
        if l0 < 1e-9 or l1 < 1e-9:
            continue
        n0 = (-d0[1] / l0, d0[0] / l0)
        n1 = (-d1[1] / l1, d1[0] / l1)
        b = (n0[0] + n1[0], n0[1] + n1[1])
        bl = math.hypot(*b) or 1.0
        b = (b[0] / bl, b[1] / bl)
        k = distance / max(0.35, b[0] * n1[0] + b[1] * n1[1])
        out.append((round(p1[0] + b[0] * k, 5), round(p1[1] + b[1] * k, 5)))
    return out if len(out) >= 3 and area(out) > 0 and not _crosses(out) else None


def _tail_lamps(s, tail):
    """The tail lamps of the sheet's tail_lamp_style, standing proud of the paint: each a lens raised
    LENS out of the body on a dark surround, with glowing LED lines (slot lamp_led_rear) cut into it.
    "wrap" (curved lamps sweeping round the rear corner onto the side), "bar" (a full-width light
    bar), "slim" (thin curved LED strips), "round_twin" (two round lamps a side with LED rings) or
    "vertical" (tall lamps up the rear corners). Each piece is left out if it cannot be cut cleanly."""
    hw, zb, yr = s["half_width"], s["ride_height"], s["y_rear"]
    style = s["tail_lamp_style"]
    top = s["tail_z"] - s["tail_edge_radius"] - 0.03       # under the rounded top edge
    low = zb + 0.40 + 0.04                                 # over the rear bumper
    zc = max(low + 0.07, min(top - 0.07, s["tail_z"] - 0.2))
    # cut from just ahead of the tail's point: a lamp wider than the tapering tail would otherwise lay a
    # grazing layer along the side
    rear = dict(view="rear", until=max(tail, yr - s["tail_point"] - 0.12), optional=True)
    out = []

    def lamp(name, outline, mirror=True, led=None):
        """A raised lens on its surround, with an LED line (a path) cut into it."""
        poly = outline_points(outline)
        out.append(dict(name=f"{name}_surround", kind="raise", material="trim", height=SURROUND,
                        outline={"poly": poly}, mirror_x=mirror, **rear))
        lens = _inset_poly(poly, 0.012) or poly
        out.append(dict(name=name, kind="raise", material="lamp_rear", height=LENS, outline={"poly": lens},
                        mirror_x=mirror, **rear))
        if led:
            out.append(dict(name=f"{name}_led", kind="groove", material="lamp_led_rear", width=0.014, path=led,
                            mirror_x=mirror, **rear))

    if style == "wrap":
        x0, x1 = hw * 0.42, hw * 0.96
        shape = _rounded([(x0, zc - 0.015), (x1 - 0.05, zc - 0.05), (x1, zc - 0.02), (x1, zc + 0.06),
                          (x0 + 0.08, zc + 0.035), (x0, zc + 0.02)], cut=0.18, rounds=2)
        lamp("tail_lamps", shape, led=[(x0 + 0.03, zc + 0.005), (x1 - 0.06, zc - 0.012), (x1 - 0.02, zc + 0.03)])
        # its end on the side, round the corner
        y1 = yr - 0.012
        y0 = y1 - s["tail_point"] - 0.16
        out.append(dict(name="tail_lamps_side", kind="raise", material="lamp_rear", height=LENS * 0.9, view="side",
                        until=hw * 0.7, optional=True,
                        outline={"poly": _rounded([(y0, zc - 0.005), (y1, zc - 0.03), (y1, zc + 0.06),
                                                   (y0 + 0.02, zc + 0.045)], cut=0.2, rounds=2)}))
    elif style == "split":  # the outer part on the body, the inner part on the boot lid, split at its shut line
        y0, y1 = s["deck_front"] + 0.06, yr - s["tail_point"] - s["tail_edge_radius"] - 0.03
        bx = max(0.25, min(edge(s, y0)[0], edge(s, y1)[0]) - 0.11)  # the boot line's x
        if not hw * 0.45 < bx < hw * 0.85:
            bx = hw * 0.68
        xo0, xo1 = bx + 0.025, hw * 0.96
        outer = _rounded([(xo0, zc - 0.035), (xo1 - 0.05, zc - 0.05), (xo1, zc - 0.02), (xo1, zc + 0.06),
                          (xo0, zc + 0.045)], cut=0.18, rounds=2)
        lamp("tail_lamps", outer, led=[(xo0 + 0.025, zc + 0.01), (xo1 - 0.06, zc - 0.005), (xo1 - 0.02, zc + 0.03)])
        xi0, xi1 = max(hw * 0.28, bx - 0.26), bx - 0.025
        inner = _rounded([(xi0, zc - 0.025), (xi1, zc - 0.035), (xi1, zc + 0.045), (xi0, zc + 0.035)],
                         cut=0.18, rounds=2)
        lamp("tail_lamps_inner", inner, led=[(xi0 + 0.025, zc + 0.008), (xi1 - 0.02, zc + 0.008)])
        y1s = yr - 0.012
        y0s = y1s - s["tail_point"] - 0.16
        out.append(dict(name="tail_lamps_side", kind="raise", material="lamp_rear", height=LENS * 0.9, view="side",
                        until=hw * 0.7, optional=True,
                        outline={"poly": _rounded([(y0s, zc - 0.005), (y1s, zc - 0.03), (y1s, zc + 0.06),
                                                   (y0s + 0.02, zc + 0.045)], cut=0.2, rounds=2)}))
    elif style == "tri":  # three vertical bars a side on one dark surround
        x0, x1 = hw * 0.48, hw * 0.9
        half = 0.07
        surround = _rounded([(x0, zc - half), (x1, zc - half), (x1, zc + half), (x0, zc + half)], cut=0.1, rounds=2)
        out.append(dict(name="tail_lamps_surround", kind="raise", material="trim", height=SURROUND,
                        outline={"poly": surround}, mirror_x=True, **rear))
        bw = (x1 - x0 - 0.04) / 3 - 0.02
        for i, name in enumerate(("tail_lamps", "tail_lamps_2", "tail_lamps_3")):
            cx = x0 + 0.02 + bw / 2 + i * (bw + 0.02) + 0.01
            out.append(dict(name=name, kind="raise", material="lamp_rear", height=LENS, mirror_x=True,
                            outline={"poly": _rect(cx, zc, bw, 2 * half - 0.03, 0.01)}, **rear))
            out.append(dict(name=f"{name}_led", kind="groove", material="lamp_led_rear", width=0.012, mirror_x=True,
                            path=[(cx, zc - half + 0.035), (cx, zc + half - 0.035)], **rear))
    elif style == "square":  # small square lamps low on the rear corners, each with an LED ring
        side = 0.11
        cx = hw * 0.97 - side / 2 - 0.04
        z = zb + 0.40 - side / 2 - 0.05
        ring = [(cx + side * 0.3 * math.cos(2 * math.pi * k / 24), z + side * 0.3 * math.sin(2 * math.pi * k / 24))
                for k in range(25)]
        lamp("tail_lamps", {"rect": [cx, z, side, side, 0.015]}, led=ring)
    elif style == "bar":
        t = (s["tail_lamp_height"] or 0.085) / 2
        bar = _rounded([(-hw * 0.95, zc - t), (hw * 0.95, zc - t), (hw * 0.95, zc + t),
                        (-hw * 0.95, zc + t)], cut=0.06, rounds=2)
        lamp("tail_lamps", bar, mirror=False, led=[(-hw * 0.9, zc + 0.005), (hw * 0.9, zc + 0.005)])
    elif style == "slim":
        curve = [(hw * (0.25 + 0.7 * i / 10), zc + 0.03 * math.sin(math.pi * i / 10) - 0.02 * i / 10)
                 for i in range(11)]
        out.append(dict(name="tail_lamps_surround", kind="raise", material="trim", height=SURROUND,
                        outline={"poly": _band(curve, 0.05)}, mirror_x=True, **rear))
        out.append(dict(name="tail_lamps", kind="raise", material="lamp_led_rear", height=LENS,
                        outline={"poly": _band(curve, 0.034)}, mirror_x=True, **rear))
    elif style == "round_twin":
        for name, cx, rad in (("tail_lamps", hw * 0.76, 0.095), ("tail_lamps_inner", hw * 0.52, 0.085)):
            ring = [(cx + rad * 0.62 * math.cos(2 * math.pi * k / 24), zc + rad * 0.62 * math.sin(2 * math.pi * k / 24))
                    for k in range(25)]
            lamp(name, {"circle": [cx, zc, rad]}, led=ring)
    else:  # vertical
        z0 = low + 0.02
        z1 = min(top, z0 + (s["tail_lamp_height"] or 0.55))
        x0, x1 = hw * 0.8, hw * 0.97
        shape = _rounded([(x0, z0), (x1, z0), (x1, z1), (x0 + 0.02, z1 - 0.03)], cut=0.15, rounds=2)
        lamp("tail_lamps", shape, led=[((x0 + x1) / 2, z0 + 0.04), ((x0 + x1) / 2 + 0.01, z1 - 0.06)])
    return out


def default_features(s):
    """The details every car of this sheet gets unless its `features` change them: the raised shapes
    first, then the glass, lamps, openings and lines cut into the skin."""
    hw = s["half_width"]
    zb, yf, yr = s["ride_height"], s["y_front"], s["y_rear"]
    bumper = s["bumper"]  # a recess inside a bumper measures its depth from the skin under the bumper
    glass = dict(kind="recess", material="glass", depth=0.014, frame=0.03, frame_material="trim", frame_depth=0.006)
    rear_end = s["deck_front"] - 0.05  # where the rear glass stops: at the deck, or rear_glass_length behind the roof
    if s["rear_glass_length"] is not None:
        rear_end = min(rear_end, s["roof_rear"] + s["rear_glass_length"])
    feats = _raised(s) + [
        dict(name="windshield", view="top", outline=_glass_top(s, s["windshield_base"] + 0.02 + GLASS_BOW,
                                                                s["roof_front"] - 0.05 + GLASS_BOW, bow=GLASS_BOW),
             fallback_outline=_glass_top_straight(s, s["windshield_base"] + 0.04, s["roof_front"] - 0.05),
             until=s["belt_z"] - 0.05, **glass),
        dict(name="rear_window", view="top", outline=_glass_top(s, s["roof_rear"] + 0.06 - GLASS_BOW,
                                                                 rear_end - GLASS_BOW, bow=-GLASS_BOW),
             fallback_outline=_glass_top_straight(s, s["roof_rear"] + 0.06, rear_end),
             until=s["belt_z"] - 0.05, **glass),
    ]
    dlo, side = side_openings(s)
    if dlo:  # the black window opening first, then the glass set deeper into it with a seal step
        feats.append(dict(name="side_window_opening", kind="recess", view="side", outline=dlo, until=0.0,
                          material="trim", depth=0.008))
    for i, poly in enumerate(side):
        feats.append(dict(name="side_glass_front" if i == 0 else "side_glass_rear", view="side", outline=poly,
                          until=0.0, kind="recess", material="glass", depth=0.02, frame=GLASS_FRAME,
                          frame_material="trim", frame_depth=0.012))
    r = s["arch_radius"]
    nose = min(yf + 0.45, s["axle_front"] - r - s["boxed_width"] - 0.04)
    tail = max(yr - 0.45, s["axle_rear"] + r + s["boxed_width"] + 0.04)
    lamp_z = s["nose_z"] - s["lamp_drop"]
    feats += (_headlights(s, nose, lamp_z) + _separated(_grille(s, nose, lamp_z), _intakes(s, nose))
              + _tail_lamps(s, tail))
    if s["plate_rear"] == "panel":  # on the tailgate or boot face, above the bumper, between the lamps
        feats.append(dict(name="plate", kind="recess", view="rear", until=tail, material="trim", depth=0.012,
                          outline={"rect": [0.0, zb + 0.40 + 0.05 + 0.065, 0.44, 0.13, 0.015]}))
    else:
        feats.append(dict(name="plate", kind="recess", view="rear", until=tail, material="trim", depth=0.012,
                          outline={"rect": [0.0, zb + 0.24, 0.44, 0.13, 0.015]}))
    # (the rear bumper is the skin itself, so what is cut into it is as deep as it says)
    feats.append(dict(name="reflectors", kind="recess", view="rear", mirror_x=True, until=tail, material="lamp_rear",
                      depth=0.006, outline={"rect": [0.36, zb + 0.24, 0.14, 0.035, 0.008]}))
    if s["diffuser"]:  # a black panel low in the rear bumper, with vertical fins
        # its foot 8 cm up: any lower and the cut grazes the floor where it curves under
        low, top = zb + 0.08, (zb + 0.26 if s["plate_rear"] == "panel" else zb + 0.165)
        if s["diffuser_height"] is not None:
            top = low + s["diffuser_height"]
        rb = rear_bumper(s)
        if rb:  # inside the bumper, under its rolled top edge
            top = max(low + 0.05, min(top, rb["top"] - rb["edge"] - 0.01))
        feats.append(dict(name="diffuser", kind="recess", view="rear", until=tail, material="trim", optional=True,
                          depth=0.04, outline={"rect": [0.0, (low + top) / 2, 0.8, top - low, 0.02]},
                          bars=s["rear_pattern"]))
        for i, x in enumerate((-0.2, 0.0, 0.2) if s["rear_pattern"] == "none" else ()):
            feats.append(dict(name=f"diffuser_fin_{i + 1}", kind="groove", view="rear", until=tail, material="chrome",
                              width=0.01, optional=True, path=[(x, low + 0.015), (x, top - 0.015)]))
    if s["rear_bumper_style"] in ("sport", "classic"):  # an air vent with bars in each corner of the bumper
        feats.append(dict(name="rear_vents", kind="recess", view="rear", mirror_x=True, until=tail, material="trim",
                          optional=True, depth=0.03, bars=s["rear_pattern"],
                          outline={"rect": [round(hw * 0.62, 4), zb + 0.125, 0.15, 0.05, 0.012]}))
    if s["deck_front"] - s["roof_rear"] < 0.3:
        # a near-upright tail (a van's): seen from above its glass is a sliver on the tail's top edge,
        # so it is cut from behind instead
        z0 = s["belt_z"] + 0.08
        z1 = s["height"] - s["tail_edge_radius"] - 0.07
        w = min(s["roof_width"] * 0.8, 2 * (hw * s["tail_taper"] - s["c_pillar"] - 0.05))
        feats = [dict(name="rear_window", view="rear", until=s["deck_front"] - 0.12,
                      outline={"rect": [0.0, round((z0 + z1) / 2, 4), round(w, 4), round(z1 - z0, 4), 0.04]}, **glass)
                 if f["name"] == "rear_window" else f for f in feats]
    feats += _doors(s) + _panel_lines(s) + _vents(s)
    return feats


OPENING_GAP = 0.03  # metres of body left between the upper grille and the lower intake


def _separated(grille, intakes):
    """The grille and the intakes, the intake mouth's top lowered where it would run into the grille:
    two openings that overlap cut one ragged hole."""
    if not grille:
        return intakes
    g_bottom = min(v for _, v in outline_points(grille[0]["outline"]))
    out = []
    for f in intakes:
        rect = (f.get("outline") or {}).get("rect")
        if f.get("name") == "intake" and f.get("view") == "front" and rect:
            cu, cv, w, hgt, r = rect
            top, bottom = cv + hgt / 2, cv - hgt / 2
            limit = g_bottom - OPENING_GAP
            if top > limit:
                top = max(bottom + 0.06, limit)
                f = dict(f, outline={"rect": [cu, round((top + bottom) / 2, 4), w, round(top - bottom, 4),
                                              round(min(r, (top - bottom) / 3), 4)]})
        out.append(f)
    return grille + out


BAR = 0.01         # a grille bar's thickness
BAR_PITCH = 0.035  # centre to centre; a mesh's bars are set wider apart each way
BAR_INSET = 0.012  # bars stop this far inside the opening's edge


def _spans(points, at, along_u):
    """Where a line across a closed outline is inside it: for along_u the line v = at, else u = at.
    Returns a sorted list of (start, end) on the line."""
    hits = []
    n = len(points)
    for i in range(n):
        (u0, v0), (u1, v1) = points[i], points[(i + 1) % n]
        a0, a1, b0, b1 = (v0, v1, u0, u1) if along_u else (u0, u1, v0, v1)
        if (a0 <= at < a1) or (a1 <= at < a0):
            hits.append(b0 + (at - a0) * (b1 - b0) / (a1 - a0))
    hits.sort()
    return [(hits[i], hits[i + 1]) for i in range(0, len(hits) - 1, 2)]


def grille_bars(s, feats=None):
    """The bars across each opening that has a pattern (the upper grille, the lower front intake, the
    rear diffuser), for the assembly to build: one dict per bar with the opening's view ("front" or
    "rear"), its `until`, the bar's rectangle (u0, u1, v0, v1) in that view's plane and how deep it is.
    Each bar stays BAR_INSET inside the opening's outline, whatever its shape."""
    feats = features(s) if feats is None else feats
    out = []
    for f in feats:
        pattern = f.get("bars") or "none"
        if pattern == "none" or f.get("view") not in ("front", "rear") or f.get("kind", "recess") != "recess":
            continue
        pts = outline_points(f["outline"])
        us, vs = [p[0] for p in pts], [p[1] for p in pts]
        depth = round(min(0.03, 0.6 * float(f.get("depth", 0.02))), 4)
        pitch = BAR_PITCH * (1.4 if pattern == "mesh" else 1.0)
        base = dict(opening=f["name"], view=f["view"], until=f.get("until", 0.0), depth=depth)
        first_bar = len(out)
        for along_u in ((True, False) if pattern == "mesh" else (pattern == "horizontal",)):
            lo, hi = (min(vs), max(vs)) if along_u else (min(us), max(us))
            lo, hi = lo + BAR_INSET, hi - BAR_INSET
            if hi - lo < BAR:
                continue
            count = int((hi - lo) // pitch) + 1
            first = (lo + hi) / 2 - (count - 1) * pitch / 2  # centred in the opening
            for k in range(count):
                at = first + k * pitch
                for a, b in _spans(pts, at, along_u):
                    a, b = a + BAR_INSET, b - BAR_INSET
                    if b - a < 0.02:
                        continue
                    if along_u:
                        out.append(dict(base, u0=round(a, 4), u1=round(b, 4), v0=round(at - BAR / 2, 4),
                                        v1=round(at + BAR / 2, 4)))
                    else:
                        out.append(dict(base, u0=round(at - BAR / 2, 4), u1=round(at + BAR / 2, 4), v0=round(a, 4),
                                        v1=round(b, 4)))
        if f.get("mirror_x"):  # the same bars in the opening's twin on the other side
            out += [dict(b, u0=-b["u1"], u1=-b["u0"]) for b in out[first_bar:]]
    return out


def _grille(s, nose, lamp_z):
    """The upper grille of the sheet's `grille`, kept between the headlights."""
    style = s["grille"]
    if style == "none":
        return []
    cx, w, h = lamp_box(s)
    gx = max(0.12, cx - w / 2 - 0.04)  # the grille's half-width at most: just inside the lamps' inner ends
    g = dict(name="grille", kind="recess", view="front", until=nose, material="trim", depth=0.025,
             bars=s["grille_pattern"])
    if style == "small":
        return [dict(outline={"rect": [0.0, lamp_z - 0.01, min(s["half_width"] * 0.36, 2 * gx), 0.045, 0.012]}, **g)]
    if style == "wide":  # a big trapezoid, narrowing toward its foot, reaching down into the bumper
        z0, z1 = lamp_z - 0.13, lamp_z + 0.04
        poly = _rounded([(gx, z1), (-gx, z1), (-gx * 0.8, z0), (gx * 0.8, z0)], cut=0.1, rounds=2)
        return [dict(outline={"poly": poly}, frame=0.012, frame_material="chrome", frame_depth=0.004, **g)]
    if style == "tall":  # an upright grille filling the space between the lamps
        z0, z1 = lamp_z - h * 0.6 - 0.03, lamp_z + h * 0.5
        return [dict(outline={"rect": [0.0, (z0 + z1) / 2, 2 * gx, z1 - z0, 0.02]}, frame=0.012,
                     frame_material="chrome", frame_depth=0.004, **g)]
    return [dict(outline={"rect": [0.0, lamp_z - 0.005, min(s["half_width"] * 0.56, 2 * gx), 0.06, 0.015]}, **g)]


def _intakes(s, nose):
    """The lower intakes of the sheet's `intake`, and the indicators beside them."""
    hw, zb, bumper = s["half_width"], s["ride_height"], s["bumper"]
    style = s["intake"]
    mouth = dict(name="intake", kind="recess", view="front", until=nose, material="trim", depth=0.05 + bumper,
                 bars=s["intake_pattern"])
    indicators = dict(name="indicators", kind="recess", mirror_x=True, material="lamp_amber", depth=0.006 + bumper,
                      **_on_nose(s, hw * 0.74, zb + 0.30, 0.12, 0.035, 0.01))  # off the corner
    if style == "center":
        return [dict(outline={"rect": [0.0, zb + 0.2, hw * 0.62, 0.15, 0.03]}, **mouth), indicators]
    if style == "low":
        return [dict(outline={"rect": [0.0, zb + 0.13, hw * 0.9, 0.08, 0.02]}, **mouth), indicators]
    if style == "split":  # two big side intakes along the nose's own normal, a small mouth between them
        return [dict(outline={"rect": [0.0, zb + 0.17, hw * 0.4, 0.09, 0.02]}, **mouth),
                dict(name="side_intakes", kind="recess", mirror_x=True, material="trim", depth=0.05 + bumper,
                     frame=0.01, frame_material="paint", frame_depth=0.006, **_on_nose_poly(s, hw * 0.66, zb + 0.2, hw * 0.30, 0.14))]
    return [dict(outline={"rect": [0.0, zb + 0.22, hw * 0.95, 0.2, 0.03]}, **mouth),
            # the corner pieces are cut along the nose's own normal there (as the headlights are), so on a
            # steeply receding pointed nose they sink in evenly instead of tearing chunks off the corner
            dict(name="corner_intakes", kind="recess", mirror_x=True, material="trim", depth=0.035 + bumper,
                 **_on_nose(s, hw * 0.79, zb + 0.17, 0.15, 0.09, 0.02)),
            indicators]


def _on_nose_poly(s, x, z, w, hgt):
    """As _on_nose, for a slanted trapezoid: its inner edge leaning out toward the top, its outer edge
    upright, its top a little narrower than its foot."""
    aim, at = nose_aim(s, x, z, on_hood=0.0)
    poly = _rounded([(-w / 2, -hgt / 2), (w / 2, -hgt / 2), (w / 2, hgt / 2), (-w / 2 + w * 0.3, hgt / 2)],
                    cut=0.12, rounds=2)
    return dict(view="aim", aim=aim, at=at, until=0.25, outline={"poly": poly}, on_nose=True)


def _on_nose(s, x, z, w, hgt, r):
    """The view, until and outline of a rect detail at (x, z) on the nose, cut along the nose's normal."""
    aim, at = nose_aim(s, x, z, on_hood=0.0)
    return dict(view="aim", aim=aim, at=at, until=0.25, outline={"rect": [0.0, 0.0, w, hgt, r]}, on_nose=True)


VENTS =("gill", "side_intake", "hood_vents", "fender_trim")


def _vents(s):
    """The air vents in the sheet's `vents` list: "gill" (three slanted slots between the front wheel
    and the door), "side_intake" (a big intake ahead of the rear wheel, as on a mid-engine car),
    "hood_vents" (twin vents on the hood) and "fender_trim" (a slim chrome-edged vent strip behind the
    front wheel). Each is left out if it cannot be cut cleanly."""
    zb, hw, r, band = s["ride_height"], s["half_width"], s["arch_radius"], s["boxed_width"]
    wanted = s["vents"]
    out = []
    vent = dict(kind="recess", material="trim", optional=True)
    behind_front = s["axle_front"] + r + band + 0.012  # the door's front line is 8 cm behind the wheel
    if "gill" in wanted:
        z0, z1 = zb + 0.26, min(zb + 0.42, s["belt_z"] - 0.12)
        for i in range(3):
            y = behind_front + i * 0.022
            out.append(dict(name=f"gill_{i + 1}", view="side", until=0.0, depth=0.014,
                            outline={"poly": [(y, z0), (y + 0.012, z0), (y - 0.018, z1), (y - 0.03, z1)]}, **vent))
    if "fender_trim" in wanted:
        z = min(s["belt_z"] - 0.14, s["wheel_radius"] + r + band + 0.12)
        out.append(dict(name="fender_trim", view="side", until=0.0, depth=0.01, frame=0.006, frame_material="chrome",
                        frame_depth=0.003, outline={"rect": [behind_front + 0.03, z, 0.055, 0.035, 0.008]}, **vent))
    if "side_intake" in wanted:
        y1 = s["axle_rear"] - r - band - 0.04
        y0 = max(y1 - 0.35, glass_split(s) + 0.12)
        z0, z1 = zb + 0.22, s["belt_z"] - 0.14
        if y1 - y0 > 0.2:
            out.append(dict(name="side_intake", view="side", until=0.0, depth=0.06, frame=0.012,
                            frame_material="trim", frame_depth=0.02,
                            outline={"poly": _rounded([(y0 + 0.12, z1), (y0, z0 + 0.1), (y1 - 0.02, z0),
                                                       (y1, z1 - 0.04)], cut=0.15, rounds=2)}, **vent))
    if "hood_vents" in wanted:
        y0, y1 = s["y_front"] + s["nose_point"] + 0.3, s["windshield_base"] - 0.1
        if y1 - y0 > 0.35:
            cy = _lerp(y0, y1, 0.55)
            out.append(dict(name="hood_vents", view="top", until=s["nose_z"] - 0.12, mirror_x=True, depth=0.025,
                            frame=0.012, frame_material="paint", frame_depth=0.004,
                            outline={"poly": _rounded([(hw * 0.33, cy - 0.12), (hw * 0.52, cy - 0.1),
                                                       (hw * 0.5, cy + 0.12), (hw * 0.34, cy + 0.12)],
                                                      cut=0.12, rounds=2)}, **vent))
    return out


def _between_lamps(s, line, feats):
    """The tailgate line moved in so it runs up between the tail lamps, never across them."""
    inner = []
    for f in feats:
        if f.get("view") == "rear" and f.get("kind", "recess") in ("recess", "raise") \
                and "lamp" in f.get("name", "") and f.get("mirror_x") and f.get("outline") is not None:
            us = [u for u, _ in outline_points(f["outline"])]
            if min(us) > 0:
                inner.append(min(us))
    x = s["half_width"] * 0.6
    if inner:
        x = min(x, min(inner) - 0.03)
    x = round(max(x, s["half_width"] * 0.3), 4)
    widest = max(abs(u) for u, _ in line["path"]) or 1.0
    path = [(round(u * x / widest, 5), v) for u, v in line["path"]]
    return {**line, "path": path}


def features(s):
    """The default details with the sheet's own `features` applied (replace by name, remove, add)."""
    out = {f["name"]: f for f in default_features(s)}
    order = list(out)
    for f in s.get("features_in") or []:
        name = f.get("name")
        if not name:
            continue
        if f.get("remove"):
            out.pop(name, None)
            continue
        merged = {**out.get(name, {}), **f}
        if name not in out:
            order.append(name)
        out[name] = merged
    result = [out[n] for n in order if n in out]
    for i, f in enumerate(result):
        if f.get("between_lamps"):
            result[i] = _between_lamps(s, f, result)
    # raised shapes first, so a lamp or an intake can be cut into a bumper
    return [f for f in result if f.get("kind") == "raise"] + [f for f in result if f.get("kind") != "raise"]


def wheel_positions(s):
    """(x, y, z) of each wheel centre: front right, front left, rear right, rear left."""
    x, z = s["track"] / 2, s["wheel_radius"]
    return [(x, s["axle_front"], z), (-x, s["axle_front"], z), (x, s["axle_rear"], z), (-x, s["axle_rear"], z)]


# --- the plan the pipeline makes from a car architect's submission ----------------------------

ACCESSORIES = {
    "wheel": dict(kw=("rim_diameter", "spokes", "style", "dish"),
                  anchor="the wheel centre (axle); axle along local X, outer face toward +X; the hub stub reaches "
                         "hub_reach inward along -X to touch the wheel-well wall"),
    "side_mirror": dict(kw=("length", "height", "depth", "stalk"),
                        anchor="the stalk foot where it meets the door; built for the right side, housing toward +X "
                               "and up; the left one is a mirror image"),
    "wing": dict(kw=("span", "chord", "height", "endplates", "uprights", "style", "angle_deg", "plate_height"),
                 anchor="the midpoint between the upright feet, feet on Z = 0; span along X, leading edge toward -Y"),
    "exhaust": dict(kw=("radius", "length", "count", "spacing"),
                    anchor="the centre of the pipe's front end; the pipe runs toward +Y"),
}


ACCESSORY_DEFAULTS = {  # the shell helpers' own defaults, for the sizes in the plan
    "side_mirror": dict(length=0.17, height=0.10, depth=0.085, stalk=0.06),
    "wing": dict(style="gt", endplates=True),
    "exhaust": dict(radius=0.045, length=0.16, count=1, spacing=0.11),
}


WING_STYLES = {  # chord, the plank's height above the feet, its tilt in degrees, the end plates' height, the dip
    "gt": (0.27, 0.24, 12.0, 0.15, 0.0),
    "street": (0.22, 0.15, 9.0, 0.09, 0.0),
    "curved": (0.27, 0.22, 12.0, 0.15, 0.05),
}
MIN_WING_LIFT = 0.12  # metres: a wing's plank stands at least this high above the deck, on its brackets
WING_SHEET = 0.008    # the thickness of the end plates and the brackets' flat bars


def wing_geometry(span=1.62, chord=None, height=None, upright_spacing=0.95, endplates=True, uprights=2,
                  angle_deg=None, style="gt", plate_height=None):
    """Every flat shape of a rear wing (bpy_lib.shell.wing builds them) and its size, in plain Python:
    {"blade": the plank's section, tilted and at its height, as (y, z) points; "plate": an end plate's
    outline or None; "brackets": [(x, [bars as (y, z) polygons])]; "foot": (y0, y1, thickness); "dip"
    and "lift" (the height added along the span of a curved plank); "sheet"; "size": (width, depth,
    height)}. The feet stand on z = 0 round the origin; the leading edge is toward -Y."""
    if style not in WING_STYLES:
        raise ValueError(f"unknown wing style {style!r}: use one of {', '.join(WING_STYLES)}")
    d_chord, d_height, d_angle, d_plate, dip = WING_STYLES[style]
    c = float(chord or d_chord)
    height = max(float(height or d_height), MIN_WING_LIFT)
    angle = math.radians(float(d_angle if angle_deg is None else angle_deg))
    plate_h = float(plate_height or d_plate)
    t, lip = 0.012, 0.014
    cos, sin, tan = math.cos(angle), math.sin(angle), math.tan(angle)

    def lift(x):  # a curved plank: low in the middle, rising to the plates
        return dip * ((min(1.0, abs(x) / (span / 2))) ** 2.2 - 0.35) if dip else 0.0

    def tilted(points, z0, a=1.0):
        ca, sa = (cos, sin) if a == 1.0 else (math.cos(angle * a), math.sin(angle * a))
        return [(round(y * ca - z * sa, 5), round(z0 + y * sa + z * ca, 5)) for y, z in points]

    # a flat plank: a softly rounded leading edge, a small upturned lip along the trailing edge
    section = [(-c / 2, 0.0), (-c / 2 + 0.012, t / 2), (c / 2 - 0.016, t / 2), (c / 2 - 0.005, t / 2 + lip),
               (c / 2, t / 2 + lip), (c / 2, -t / 2), (-c / 2 + 0.012, -t / 2)]
    blade = tilted(section, height)
    plate = None
    if endplates:  # a flat plate with cut corners, taller than the plank, most of it above
        length, ph = c * 1.2, plate_h
        plate = tilted([(-length / 2, -0.015), (-length / 2 + 0.05, 0.04), (length / 2 - 0.035, ph * 0.62),
                        (length / 2, ph * 0.62 - 0.03), (length / 2, -ph * 0.38 + 0.035),
                        (length / 2 - 0.05, -ph * 0.38), (-length / 2 + 0.07, -ph * 0.38)], height + lift(span / 2),
                       a=0.6)

    def bar(y0, z0, y1, z1, w=0.02):
        dy, dz = y1 - y0, z1 - z0
        ln = math.hypot(dy, dz)
        ny, nz = -dz / ln * w / 2, dy / ln * w / 2
        return [(round(y0 + ny, 5), round(z0 + nz, 5)), (round(y1 + ny, 5), round(z1 + nz, 5)),
                (round(y1 - ny, 5), round(z1 - nz, 5)), (round(y0 - ny, 5), round(z0 - nz, 5))]

    count = max(1, int(uprights))
    xs = [0.0] if count == 1 else [upright_spacing * (i / (count - 1) - 0.5) for i in range(count)]
    brackets = []
    for x in xs:
        top = height + lift(x)

        def under(y):  # the plank's underside there, the leg's end a little inside it
            return top + y * tan - 0.004

        brackets.append((round(x, 5), [bar(-0.085, 0.0, -0.035, under(-0.035)), bar(0.095, 0.0, 0.045, under(0.045)),
                                       bar(-0.07, top * 0.3, 0.06, top * 0.72, 0.014),
                                       bar(0.08, top * 0.3, -0.045, top * 0.72, 0.014)]))
    foot = (-0.115, 0.125, 0.008)
    points = blade + (plate or []) + [(foot[0], 0.0), (foot[1], 0.0)]
    if dip:
        points += [(y, z + lift(0.0)) for y, z in blade] + [(y, z + lift(span / 2)) for y, z in blade]
    ys, zs = [y for y, _ in points], [z for _, z in points]
    size = (round(span + (2 * WING_SHEET if plate else 0.0), 4), round(max(ys) - min(ys), 4), round(max(zs), 4))
    return dict(blade=blade, plate=plate, brackets=brackets, foot=foot, dip=dip, lift=lift, sheet=WING_SHEET,
                size=size)


def accessory_size(kind, s, options=None):
    """The size (width, depth, height) a separate piece comes out at from its shell helper."""
    o = {**ACCESSORY_DEFAULTS.get(kind, {}), **(options or {})}
    if kind == "wheel":
        w, d, h = s["wheel_width"] / 2 + s["hub_reach"], s["wheel_diameter"], s["wheel_diameter"]
    elif kind == "side_mirror":
        w, d, h = o["stalk"] + o["length"] + 0.012, o["depth"] + 0.004, o["stalk"] * 0.6 + o["height"] + 0.01
    elif kind == "wing":
        kw = {k: o[k] for k in ACCESSORIES["wing"]["kw"] if o.get(k) is not None}
        kw.setdefault("span", round(s["width"] * 0.92, 3))
        w, d, h = wing_geometry(upright_spacing=s["wing_spacing"], **kw)["size"]
    else:
        w = 2 * o["radius"] + (int(o["count"]) - 1) * o["spacing"]
        d, h = o["length"], 2 * o["radius"]
    return {"width": round(float(w), 3), "depth": round(float(d), 3), "height": round(float(h), 3)}


def _literal(value):
    return pprint.pformat(value, indent=1, width=100, sort_dicts=False)


def body_module(sheet):
    return ('"""The car body: one continuous skin built from this sheet (see bpy_lib.shells.car_sheet for every\n'
            'number and bpy_lib.shells.car for how it is built)."""\n'
            "from bpy_lib.shells import car\n\n"
            f"SHEET = {_literal(sheet)}\n\n\n"
            "def build():\n"
            "    return car.body(SHEET)\n")


def accessory_module(kind, s, options):
    kwargs = {}
    if kind == "wheel":
        kwargs = dict(diameter=s["wheel_diameter"], width=s["wheel_width"], hub_reach=s["hub_reach"])
    elif kind == "wing":
        kwargs = dict(span=round(s["width"] * 0.92, 3), upright_spacing=round(s["wing_spacing"], 3))
    allowed = ACCESSORIES[kind]["kw"]
    kwargs.update({k: v for k, v in (options or {}).items() if k in allowed})
    args = ", ".join(f"{k}={v!r}" for k, v in kwargs.items())
    return (f'"""{kind.replace("_", " ").capitalize()}: start from the shell helper and shape it to the spec."""\n'
            "from bpy_lib import shell\n\n\n"
            "def build():\n"
            f"    return shell.{'mirror_housing' if kind == 'side_mirror' else kind}({args})\n")


ASSEMBLY_MODULE = ('"""Places the wheels, mirrors, wing and exhaust on the car body from the sheet stored on it."""\n'
                   "from bpy_lib.shells import car\n\n\n"
                   "def assemble(parts):\n"
                   "    car.assemble(parts)\n")


def materials_module(paint=(0.12, 0.36, 0.78), style="stylised", accent=None):
    extra = f", accent={tuple(accent)!r}" if accent else ""
    return ('"""Car materials: every material slot of every part (paint, glass, trim, lamps, tyres, rims...)."""\n'
            "from bpy_lib import materials as m\n\n\n"
            "def apply(parts):\n"
            f"    m.car_finish(parts, paint={tuple(round(float(c), 3) for c in paint)!r}, style={style!r}{extra})\n")


def overall_size(s, accessories):
    """The bounding box of the finished car with its separate pieces: mirrors widen it, the exhaust
    lengthens it, a tall wing can raise it."""
    acc = accessories or {}
    width, depth, height = s["width"], s["length"], s["height"]
    mirror = acc.get("side_mirror")
    if mirror is not None and mirror is not False:
        reach = accessory_size("side_mirror", s, mirror if isinstance(mirror, dict) else {})["width"]
        width = max(width, 2 * (s["half_width"] - s["shoulder"] + reach))
    if acc.get("exhaust") is not None and acc.get("exhaust") is not False:
        depth += 0.11
    wing = acc.get("wing")
    if wing:
        w = wing if isinstance(wing, dict) else {}
        height = max(height, s["deck_z"] + accessory_size("wing", s, w)["height"])
    return {"width": round(width, 3), "depth": round(depth, 3), "height": round(height, 3)}


def make_plan(submission, spec=None):
    """The build plan for a car from the car architect's submission:
    {"sheet": {...}, "accessories": {"wheel": {...}, "side_mirror": {...}, "wing": {...} or null,
    "exhaust": {...} or null}, "reference_views": {"1": "front_left", ...}, "paint": [r, g, b],
    "style": "stylised" or "realistic", "notes": "..."}. Raises ValueError with the sheet's problems."""
    sheet = dict(submission.get("sheet") or {})
    problems = check(sheet)
    if problems:
        raise ValueError("; ".join(problems))
    s = resolve(sheet)
    acc = {"wheel": {}, "side_mirror": {}, "wing": None, "exhaust": {}}
    acc.update(submission.get("accessories") or {})
    parts = [{
        "name": "body", "builder": "shell",
        "description": f"The whole {s['style']} body as one continuous skin: nose, hood, fenders, cabin, roof, deck "
                       "and tail, with the glass, lamps, grille, intakes, door lines and wheel openings carved into it.",
        "method": "shell: car sheet (bpy_lib.shells.car.body)",
        "dimensions": {"width": round(s["width"], 3), "depth": round(s["length"], 3),
                       "height": round(s["height"] - s["ride_height"], 3)},
        "anchor": "world origin; the body is built in place", "location": [0, 0, 0], "rotation_deg": [0, 0, 0],
        "attaches_to": "floor (through the wheels)",
        "material": "material slots: paint, glass, trim, lamp_front, lamp_rear, lamp_amber",
        "seed": body_module(sheet),
    }]
    for kind in ("wheel", "side_mirror", "wing", "exhaust"):
        options = acc.get(kind)
        if options is None or options is False:
            continue
        options = options if isinstance(options, dict) else {}
        entry = {"name": kind, "description": f"The {kind.replace('_', ' ')}, a separate piece placed on the body.",
                 "method": f"shell.{'mirror_housing' if kind == 'side_mirror' else kind} helper",
                 "dimensions": accessory_size(kind, s, options), "anchor": ACCESSORIES[kind]["anchor"],
                 "attaches_to": "body",
                 "material": {"wheel": "slots tyre and rim", "side_mirror": "slots paint and glass",
                              "wing": "slot accent", "exhaust": "slots chrome and trim"}[kind],
                 "details": "; ".join(f"{k} {v}" for k, v in options.items()) or "helper defaults",
                 "seed": accessory_module(kind, s, options)}
        if kind == "wheel":
            entry["count"] = 4
            entry["instances"] = [{"location": [round(c, 4) for c in p], "rotation_deg": [0, 0, 0 if p[0] > 0 else 180]}
                                  for p in wheel_positions(s)]
        elif kind == "side_mirror":
            entry["count"] = 2
        parts.append(entry)
    finish = {"paint": list(submission.get("paint") or (0.12, 0.36, 0.78)),
              "style": submission.get("style") or "stylised"}
    return {
        "kind": "shell", "template": "car", "sheet": sheet, "accessories": acc,
        "dimensions": overall_size(s, acc),
        "reference_views": dict(submission.get("reference_views") or {}), "finish": finish,
        "notes": submission.get("notes", ""),
        "parts": parts,
        "assembly_notes": ("Generated from the sheet by bpy_lib.shells.car.assemble: wheels on the axles at "
                           f"x = ±{s['track'] / 2:.3f}, y = {s['axle_front']:.3f} and {s['axle_rear']:.3f}, "
                           f"z = {s['wheel_radius']:.3f}; mirrors, wing and exhaust seated on the body surface."),
        "rebuild": [],
    }
