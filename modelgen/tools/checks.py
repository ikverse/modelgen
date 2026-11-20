"""Geometric checks: the measurements from Blender (bpy_lib\\measure.py) judged against the brief.

The four hard gates are the rubric's: watertight, no floating pieces, correct size, on the floor.
Overlaps between parts that are not declared attached are reported as warnings.
"""

from .. import config, families


def _dims(spec):
    d = (spec or {}).get("dimensions") or {}
    try:
        return float(d["width"]), float(d["depth"]), float(d["height"])
    except (KeyError, TypeError, ValueError):
        return None


def _within(actual, expected, tol):
    return expected > 0 and abs(actual - expected) <= tol * expected


def size_check(size, expected, tol):
    """Height must match Z; width and depth may be either way round (a model turned 90 degrees)."""
    if expected is None:
        return True, "the spec gives no dimensions"
    w, d, h = expected
    x, y, z = size
    straight = _within(x, w, tol) and _within(y, d, tol)
    turned = _within(x, d, tol) and _within(y, w, tol)
    ok = _within(z, h, tol) and (straight or turned)
    detail = (f"model {x:.3f} x {y:.3f} x {z:.3f} m, spec {w:.3f} x {d:.3f} x {h:.3f} m "
              f"(width x depth x height, tolerance {tol:.0%})")
    return ok, detail


def evaluate(measure, spec, tolerance=None):
    """Judge one version. Returns {"passed", "gates": {id: {"pass", "detail"}}, "warnings": [...]}."""
    tol = tolerance if tolerance is not None else float(config.get("model", "size_tolerance", 0.05))
    floor_tol = float(config.get("model", "floor_tolerance_m", 0.002))
    gates, warnings = {}, []
    if not measure or measure.get("empty") or not measure.get("bbox"):
        reason = "there is no geometry to check"
        gates = {g: {"pass": False, "detail": reason} for g in ("watertight", "no_floaters", "correct_size", "on_floor")}
        return {"passed": False, "gates": gates, "warnings": [reason]}

    # a ready-made model placed as a part is a scan or an artist's shell, not a closed solid
    open_parts = [p for p in measure["parts"] if p.get("non_manifold_edges", 0) > 0 and not p.get("asset")]
    gates["watertight"] = {
        "pass": not open_parts,
        "detail": "every part is a closed solid" if not open_parts else
        "open edges in: " + ", ".join(f"{p['object']} ({p['non_manifold_edges']} edges)" for p in open_parts),
    }
    # a ready-made model is often many loose shells (each plank, each leaf): they are not floaters
    ready = {p["part"] for p in measure["parts"] if p.get("asset")}
    floaters = [f for f in measure.get("floaters") or [] if f.get("part") not in ready]
    gates["no_floaters"] = {
        "pass": not floaters,
        "detail": "every piece is connected to the floor through touching parts" if not floaters else
        "floating: " + ", ".join(f"{f['object']} piece {f['island']} at {f['center']}" for f in floaters[:10]),
    }
    ok, detail = size_check(measure["bbox"]["size"], _dims(spec), tol)
    gates["correct_size"] = {"pass": ok, "detail": detail}
    min_z = float(measure.get("min_z", 0.0))
    gates["on_floor"] = {
        "pass": abs(min_z) <= floor_tol,
        "detail": f"lowest point at Z = {min_z:.4f} m" + ("" if abs(min_z) <= floor_tol else
                                                           " (it must be 0: move the model so it stands on the floor)"),
    }
    for o in measure.get("overlaps") or []:
        if not o.get("attached"):
            warnings.append(f"{o['a']} and {o['b']} pass through each other without being declared attached "
                            f"({o['pairs']} intersecting faces)")
    found = {"passed": all(g["pass"] for g in gates.values()), "gates": gates, "warnings": warnings}
    found.update(triangle_report(measure, spec))
    return found


def triangle_report(measure, spec):
    """How many triangles the model has, and for a game asset its family's limit: {"triangles",
    "triangle_budget", "over_budget"}. The game export is reduced to the limit by itself; a model far
    over it loses detail there, so the critic is told."""
    triangles = sum(int(p.get("triangles") or 0) for p in (measure or {}).get("parts", []))
    out = {"triangles": triangles}
    if str((spec or {}).get("use") or "").lower() == "game":
        limit = families.budget(families.of(spec))
        out["triangle_budget"] = limit
        out["over_budget"] = triangles > limit
    return out


MESH_DEPTH = 0.03       # meshing teeth may cut into each other this share of a module (polygon facets)
MESH_GAP = 0.15         # and come no further apart than this share of a module at their closest
CENTRE_TOL = 0.002      # the centre distance may be off by this share
RATIO_TOL = 0.01        # the speed ratio by this share
CLASH_DEPTH = 0.0002    # any other two parts may pass this far into each other (0.2 mm), no further
MOUNT_TOL = 0.00005      # a part on a shaft or pin may sit this far off its axis (0.05 mm), no further
MOUNT_TILT_DEG = 0.1     # nor turn about an axis tilted further than this from it
CAM_TOL = 0.00025      # a follower may sit this far into or off its cam (the profile is a polygon): 0.25 mm
PIN_TOL = 0.00015      # a rod's ends may stray this far off their pins between keyed frames (0.15 mm)


def mesh_gates(report, pairs=None):
    """The mesh check judged (bpy_lib.measure.mesh_report): meshing teeth neither cut into each other
    nor float apart at any of the moments, centre distances and speed ratios are the sheet's, and
    nothing else passes through anything while it moves. Returns {gate: {"pass", "detail",
    "route"}}; route is who fixes a failure: "assembly" (spacing, starting angle) or "motion"
    (ratio, direction)."""
    rows = [r for r in (report or {}).get("pairs", []) if r.get("kind") in ("mesh", "rack", "planet", "drive", "train")]
    gates = {}
    missing = [f"{r['a']}/{r['b']}" for r in rows if not r.get("found", True)]
    depth_bad, gap_bad, centre_bad, ratio_bad = [], [], [], []
    for r in rows:
        if not r.get("found", True):
            continue
        m = r.get("module_m") or 0.002
        pair = f"{r['a']} and {r['b']}"
        if r.get("max_depth_m", 0) > MESH_DEPTH * m + 2e-5:
            depth_bad.append(f"{pair} cut {r['max_depth_m'] * 1000:.3f} mm into each other (moment "
                             f"{r.get('worst_moment')})")
        if r.get("max_gap_m", 0) > MESH_GAP * m:
            gap_bad.append(f"{pair} are {r['max_gap_m'] * 1000:.3f} mm apart at their closest (moment "
                           f"{r.get('worst_moment')}): the teeth do not engage")
        want = r.get("centre_expected_m")
        if want and abs(r.get("centre_m", 0) - want) > max(CENTRE_TOL * want, 0.02 * m):
            centre_bad.append(f"{pair}: centre distance {r['centre_m'] * 1000:.3f} mm, the sheet says "
                              f"{want * 1000:.3f} mm")
        if r["kind"] in ("mesh", "drive", "train") and r.get("ratio_expected"):
            want_ratio = r["ratio_expected"] * (1 if r.get("same_direction_expected") else -1)
            got = r.get("ratio")
            if got is None or abs(got - want_ratio) > RATIO_TOL * abs(want_ratio):
                ratio_bad.append(f"{pair}: {r['b']} turns {got if got is not None else 0:+.4f} times per turn of "
                                 f"{r['a']}, the sheet says {want_ratio:+.4f} (+ the same way, - the other way)")
        if r["kind"] == "rack" and r.get("metres_per_radian_expected"):
            want_rate = r["metres_per_radian_expected"]
            got = abs(r.get("ratio") or 0)
            if abs(got - want_rate) > 0.02 * want_rate:
                ratio_bad.append(f"{pair}: the rack slides {got * 1000:.3f} mm per radian of {r['a']}, it should "
                                 f"slide {want_rate * 1000:.3f}")
    if missing:
        gates["mesh_parts_found"] = {"pass": False, "detail": "parts of a meshing pair are missing: " +
                                     ", ".join(missing), "route": "assembly"}
    gates["teeth_clear"] = {"pass": not depth_bad, "route": "assembly",
                            "detail": "; ".join(depth_bad) or "meshing teeth never cut into each other"}
    gates["teeth_engaged"] = {"pass": not gap_bad, "route": "assembly",
                              "detail": "; ".join(gap_bad) or "meshing teeth stay in mesh all through the loop"}
    gates["centre_distance"] = {"pass": not centre_bad, "route": "assembly",
                                "detail": "; ".join(centre_bad) or "every centre distance is the sheet's"}
    gates["speed_ratio"] = {"pass": not ratio_bad, "route": "motion",
                            "detail": "; ".join(ratio_bad) or "every part turns at the sheet's ratio and way round"}
    housed = [r for r in (report or {}).get("pairs", []) if r.get("kind") == "housing" and r.get("found", True)]
    if housed:
        bad = []
        for r in housed:
            if r.get("max_depth_m", 0) > CLASH_DEPTH:
                bad.append(f"the blades of {r['a']} pass {r['max_depth_m'] * 1000:.2f} mm into {r['b']} (moment "
                           f"{r.get('worst_moment')})")
        gates["blades_clear"] = {"pass": not bad, "route": "assembly",
                                 "detail": "; ".join(bad) or "every wheel's blades clear its housing all the way round"}
    rows_all = [r for r in (report or {}).get("pairs", []) if r.get("found", True)]
    mounted = [r for r in rows_all if r.get("kind") in ("shaft", "mount", "pin_mount") and "max_offset_m" in r]
    if mounted:
        bad = []
        for r in mounted:
            on = {"shaft": f"the axis of its shaft {r['b']}", "pin_mount": f"its pin on {r['b']}",
                  "mount": f"the axis of {r['b']}"}[r["kind"]]
            if r["max_offset_m"] > MOUNT_TOL:
                bad.append(f"{r['a']} sits {r['max_offset_m'] * 1000:.2f} mm off {on}")
            if r.get("beyond_end_m", 0) > MOUNT_TOL:
                bad.append(f"{r['a']} stands {r['beyond_end_m'] * 1000:.2f} mm past the end of {r['b']}")
            if r.get("max_tilt_deg", 0) > MOUNT_TILT_DEG:
                bad.append(f"{r['a']} is tilted {r['max_tilt_deg']:.2f} degrees from {on}")
        gates["parts_on_their_mounts"] = {
            "pass": not bad, "route": "assembly",
            "detail": "; ".join(bad) or "every gear, wheel and planet sits on its shaft or pin all through the loop"}
    cams = [r for r in rows_all if r.get("kind") == "cam"]
    if cams:
        bad = []
        for r in cams:
            if r.get("max_depth_m", 0) > CAM_TOL:
                bad.append(f"{r['b']} digs {r['max_depth_m'] * 1000:.2f} mm into {r['a']} (moment {r.get('worst_moment')})")
            if r.get("max_gap_m", 0) > CAM_TOL:
                bad.append(f"{r['b']} lifts {r['max_gap_m'] * 1000:.2f} mm off {r['a']} (moment {r.get('worst_moment')})")
        gates["follower_contact"] = {"pass": not bad, "route": "motion",
                                     "detail": "; ".join(bad) or "every follower rides on its cam all the way round"}
    pinned = [r for r in rows_all if r.get("kind") in ("crank", "pin")]
    if pinned:
        bad = [f"{r['a']} comes {r['max_offset_m'] * 1000:.2f} mm off "
               f"{'its crankpin on ' if r['kind'] == 'crank' else 'the gudgeon pin of '}{r['b']}"
               for r in pinned if r.get("max_offset_m", 0) > PIN_TOL]
        gates["pins_in_bores"] = {"pass": not bad, "route": "motion",
                                  "detail": "; ".join(bad) or "every rod stays on its crankpin and its piston's pin"}
    liners = [r for r in rows_all if r.get("kind") == "liner"]
    if liners:
        bad = []
        for r in liners:
            if r.get("max_off_axis_m", 0) > PIN_TOL:
                bad.append(f"{r['a']} strays {r['max_off_axis_m'] * 1000:.2f} mm off the axis of {r['b']}")
            if r.get("top_room_m", 0) < -1e-5:
                bad.append(f"{r['a']}'s crown rises {-r['top_room_m'] * 1000:.2f} mm out of the top of {r['b']}")
            if r.get("bottom_room_m", 0) < -1e-5:
                bad.append(f"{r['a']}'s rings drop {-r['bottom_room_m'] * 1000:.2f} mm out of the foot of {r['b']}")
            want = r.get("stroke_expected_m")
            if want and abs(r.get("stroke_m", 0) - want) > 0.005 * want:
                bad.append(f"{r['a']} travels {r['stroke_m'] * 1000:.2f} mm, the stroke is {want * 1000:.2f} mm")
        gates["piston_in_liner"] = {"pass": not bad, "route": "assembly",
                                    "detail": "; ".join(bad) or "every piston runs on its cylinder's axis, the full "
                                                                "stroke, its crown and rings inside all the way"}
    clashes = [c for c in (report or {}).get("clashes", []) if c.get("max_depth_m", 0) > CLASH_DEPTH]
    gates["no_clashes"] = {"pass": not clashes, "route": "assembly",
                           "detail": "; ".join(f"{c['a']} and {c['b']} pass {c['max_depth_m'] * 1000:.2f} mm into each "
                                               f"other (moment {c['moment']})" for c in clashes)
                           or "no other parts pass through each other while moving"}
    return gates


def parts_checks(measure, mesh, floor_tolerance=None):
    """The checks a mechanism built by the part tools is held to in place of the critic: every part a
    closed solid, standing on the floor, and the mesh check. (Gears on shafts are not asked to rest
    on anything: a mechanism is shown as it runs.)"""
    tol = floor_tolerance if floor_tolerance is not None else float(config.get("model", "floor_tolerance_m", 0.002))
    gates = {}
    parts = (measure or {}).get("parts") or []
    open_parts = [p for p in parts if p.get("non_manifold_edges", 0) > 0]
    detail = ("there is no geometry" if not parts else "every part is a closed solid" if not open_parts else
              "open edges in: " + ", ".join(f"{p['object']} ({p['non_manifold_edges']})" for p in open_parts))
    gates["watertight"] = {"pass": bool(parts) and not open_parts, "detail": detail}
    min_z = float((measure or {}).get("min_z", 0.0))
    gates["on_floor"] = {"pass": abs(min_z) <= tol, "detail": f"lowest point at Z = {min_z:.4f} m"}
    for name, g in mesh_gates(mesh).items():
        gates[name] = {"pass": g["pass"], "detail": g["detail"]}
    out = {"passed": all(g["pass"] for g in gates.values()), "gates": gates, "warnings": []}
    out["triangles"] = sum(int(p.get("triangles") or 0) for p in parts)
    return out


MAX_STRETCH = 4.0  # an edge in a test pose more than 4x longer or shorter than at rest: a vertex on the wrong bone
MIN_BONES = 50  # the game skeleton has 53: spine, neck, head, arms, legs, all five fingers


def character_checks(made, rig, sheet, floor_tolerance=None):
    """The checks a character built by the character path is held to in place of the critic: the
    height the sheet asks for, standing on the floor, a full game skeleton, every vertex skinned
    with at most four bones, and the skin following the bones in test poses."""
    tol = floor_tolerance if floor_tolerance is not None else float(config.get("model", "floor_tolerance_m", 0.002))
    made, rig = made or {}, rig or {}
    gates = {}
    want = float(sheet.get("height_cm") or 0) / 100
    got = float(made.get("height") or 0)
    gates["correct_size"] = {"pass": want > 0 and abs(got - want) <= 0.02 * want,
                             "detail": f"{got * 100:.1f} cm tall, the sheet asks for {want * 100:.1f} cm"}
    ground = float(made.get("ground") or 0)
    gates["on_floor"] = {"pass": abs(ground) <= tol, "detail": f"lowest point at Z = {ground:.4f} m"}
    bones = int(rig.get("bones") or 0)
    gates["skeleton"] = {"pass": bool(rig.get("rig")) and bones >= MIN_BONES,
                         "detail": f"{bones} bones" if rig.get("rig") else "no skeleton"}
    loose = {k: v for k, v in (rig.get("unweighted") or {}).items() if v}
    most = int(rig.get("max_influences") or 0)
    gates["skinned"] = {"pass": bool(rig.get("skinned")) and not loose and 0 < most <= 4,
                        "detail": ("every vertex follows the skeleton, at most %d bones each" % most) if not loose
                        else "vertices with no bone: " + ", ".join(f"{k} ({v})" for k, v in loose.items())}
    stretch = rig.get("stretch") or {}
    worst = max(stretch.values()) if stretch else None
    bad = [k for k, v in stretch.items() if v > MAX_STRETCH]
    gates["skin_follows"] = {"pass": worst is not None and not bad,
                             "detail": "no test poses" if worst is None else
                             (f"in the test poses edges stretch at most {worst:.2f}x" if not bad else
                              "the skin tears in: " + ", ".join(f"{k} ({stretch[k]:.1f}x)" for k in bad))}
    poke = {k: v for k, v in (rig.get("poke") or {}).items() if v.get("checked")}
    if poke:
        bad = {k: v["share"] for k, v in poke.items() if v["share"] > MAX_POKE}
        worst = max(v["share"] for v in poke.values())
        gates["clothes_cover"] = {"pass": not bad, "detail": (
            f"at most {worst * 100:.1f}% of the skin under the clothes shows through, at rest and in the test poses"
            if not bad else "skin shows through the clothes in: " +
            ", ".join(f"{k} ({v * 100:.0f}%)" for k, v in bad.items()))}
    out = {"passed": all(g["pass"] for g in gates.values()), "gates": gates, "warnings": []}
    out["triangles"] = sum(int(p.get("faces") or 0) * 2 for p in (made.get("parts") or {}).values())
    return out


MAX_POKE = 0.05  # share of the skin next to clothes that may show through them in a pose


def part_report(measure, expected=None, tol=None):
    """What a builder is told about the part it just built."""
    tol = tol if tol is not None else float(config.get("model", "part_size_tolerance", 0.03))
    if not measure or not measure.get("parts"):
        return {"error": "the part produced no geometry"}
    part = measure["parts"][0]
    report = {
        "size_m": {"x": part["size"][0], "y": part["size"][1], "z": part["size"][2]},
        "faces": part["faces"],
        "triangles": part.get("triangles"),
        "separate_pieces": part["islands"],
        "non_manifold_edges": part["non_manifold_edges"],
    }
    if expected:
        try:
            w, d, h = float(expected["width"]), float(expected["depth"]), float(expected["height"])
            ok, detail = size_check(part["size"], (w, d, h), tol)
            report["size_vs_plan"] = ("matches: " if ok else "DOES NOT MATCH: ") + detail
        except (KeyError, TypeError, ValueError):
            pass
    return report
