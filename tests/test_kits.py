"""The kits (bpy_lib\\kits.py) build one fixed model per family, and each passes the geometric checks:
closed solids, nothing floating, on the floor, the right size, and the named material slots still
there after a part is finished. These need Blender and skip with the install command when it is absent."""

from pathlib import Path

import pytest

from modelgen.tools import blender, checks

FIXTURES = Path(__file__).resolve().parent / "fixtures"

MODELS = {
    "container": {"parts": ["mug", "bowl", "vase"], "dimensions": {"width": 0.416, "depth": 0.15, "height": 0.212},
                  "slots": {"mug": {"outside", "inside"}, "bowl": {"outside", "inside"}, "vase": {"outside", "inside"}},
                  "max_faces": 9000},
    "device": {"parts": ["shell", "bezel", "screen", "dpad", "button"],
               "dimensions": {"width": 0.09, "depth": 0.032, "height": 0.15},
               "slots": {"shell": {"shell", "seam", "pocket", "slot"}, "bezel": {"trim"}, "screen": {"screen"},
                         "dpad": {"button"}, "button": {"button"}},
               "max_faces": 2500},
    "building": {"parts": ["walls", "windows", "door", "roof", "tower"],
                 "dimensions": {"width": 38.47, "depth": 16.24, "height": 48.02},
                 "slots": {"walls": {"wall", "reveal", "plinth"}, "windows": {"frame", "glass", "sill"},
                           "door": {"door", "frame", "metal"}, "roof": {"roof", "gable"},
                           "tower": {"glass", "band", "mullion"}},
                 "max_faces": 2500},
    "vehicle": {"parts": ["body", "wheel_front", "wheel_rear"], "dimensions": {"width": 2.4, "depth": 8.0, "height": 2.93},
                "slots": {"body": {"body", "glass", "trim", "door", "lamp_clear", "lamp_red", "lamp_amber"},
                          "wheel_front": {"tyre", "rim"}, "wheel_rear": {"tyre", "rim"}},
                "max_faces": 5000},
    "scene": {"parts": ["ground", "tree_round", "tree_conifer", "bench", "fence", "lamp_post"],
              "dimensions": {"width": 12.0, "depth": 10.0, "height": 5.1},
              "slots": {"tree_round": {"trunk", "leaves"}, "tree_conifer": {"trunk", "leaves"}, "bench": {"wood", "metal"},
                        "fence": {"post", "rail"}, "lamp_post": {"metal", "lamp"}},
              "max_faces": 3000},
}

def model_steps(name):
    file = FIXTURES / f"kit_{name}.py"
    steps = [blender.part_step(p, file, func=f"build_{p}") for p in MODELS[name]["parts"]]
    steps += [blender.assembly_step(file), blender.materials_step(file), blender.measure_step()]
    return steps


@pytest.mark.blender
@pytest.mark.parametrize("name", list(MODELS))
def test_kit_fixture_builds_and_passes_checks(name):
    result = blender.run_job(model_steps(name), timeout=600)
    assert result.ok, result.traceback or result.stdout[-2000:]
    measure = result.outputs["measure"]
    found = checks.evaluate(measure, {"dimensions": MODELS[name]["dimensions"]}, tolerance=0.02)
    failing = {k: g["detail"] for k, g in found["gates"].items() if not g["pass"]}
    assert not failing, failing
    assert not found["warnings"], found["warnings"]
    assert {p["part"] for p in measure["parts"]} == set(MODELS[name]["parts"])
    assert sum(p["faces"] for p in measure["parts"]) <= MODELS[name]["max_faces"]


@pytest.mark.blender
@pytest.mark.parametrize("name", list(MODELS))
def test_kit_parts_keep_their_slots(name):
    """Before any material is assigned, every finished part still carries its named slots."""
    file = FIXTURES / f"kit_{name}.py"
    steps = [blender.part_step(p, file, func=f"build_{p}") for p in MODELS[name]["parts"]]
    result = blender.run_job(steps + [blender.material_report_step()], timeout=600)
    assert result.ok, result.traceback or result.stdout[-2000:]
    by_part = {}
    for slot, entry in result.outputs["material_report"].items():
        for part in entry["parts"]:
            by_part.setdefault(part, set()).add(slot)
    for part, wanted in MODELS[name]["slots"].items():
        assert wanted <= by_part.get(part, set()), (part, by_part.get(part))


@pytest.mark.blender
def test_the_hood_is_one_closed_sloping_solid_with_its_grille_and_lamps():
    file = FIXTURES / "kit_vehicle.py"
    result = blender.run_job([blender.part_step("hood", file, func="build_hood"), blender.measure_step(),
                              blender.material_report_step()], timeout=300)
    assert result.ok, result.traceback or result.stdout[-2000:]
    part = result.outputs["measure"]["parts"][0]
    assert part["non_manifold_edges"] == 0 and part["islands"] == 1 and part["triangles"] < 900
    width, length, height = part["size"]
    assert abs(length - 1.5) < 0.06 and 2.1 <= width <= 2.2 and abs(height - 0.9) < 0.05  # floor 0.5 to top 1.4
    assert {"body", "trim", "lamp_clear"} <= set(result.outputs["material_report"])

