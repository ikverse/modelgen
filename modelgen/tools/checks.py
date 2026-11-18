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
