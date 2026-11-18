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


def mesh_gates(report, pairs=None):
    """The mesh check judged (bpy_lib.measure.mesh_report): meshing teeth neither cut into each other
    nor float apart at any of the moments, centre distances and speed ratios are the sheet's, and
    nothing else passes through anything while it moves. Returns {gate: {"pass", "detail",
    "route"}}; route is who fixes a failure: "assembly" (spacing, starting angle) or "motion"
    (ratio, direction)."""
    rows = [r for r in (report or {}).get("pairs", []) if r.get("kind") in ("mesh", "rack")]
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
        if r["kind"] == "mesh" and r.get("ratio_expected"):
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
