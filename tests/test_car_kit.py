import pytest

from modelgen.tools import blueprint as bp
from modelgen.tools import car_kit as ck
from modelgen.tools import car_space as cs

SEDANS = cs.group("sedan")


@pytest.fixture(scope="module")
def marks():
    return {n: ck.face_marks(n) for n in SEDANS}


def test_every_real_sedan_has_its_front_rear_and_side_marks():
    assert len(SEDANS) >= 12
    for n in SEDANS:
        views = bp.manifest()[n]["views"]
        for view, parts in (("front", ck.END_PARTS["front"]), ("rear", ck.END_PARTS["rear"]), ("side", ck.SIDE_PARTS)):
            kit = views[view]["kit"]
            assert set(parts) <= set(kit), (n, view)
        for view in ("front", "rear"):
            assert "trim_y" in views[view]["kit"], (n, view)
        # every car has the main lamps, grille, intake and mirror; plate, fog and trim may be absent
        for view, part in (("front", "headlamp"), ("front", "grille"), ("front", "intake"), ("rear", "tail"),
                           ("side", "headlamp"), ("side", "tail"), ("side", "mirror")):
            assert views[view]["kit"][part], (n, view, part)


def test_every_box_lies_inside_its_view_of_the_car(marks):
    for n, m in marks.items():
        for view in ("front", "rear"):
            for part, b in m[view].items():
                if part == "trim_z" or not b:
                    continue
                assert -1.0 <= b["x_lo"] < b["x_hi"] <= 1.0, (n, view, part)
                assert 0.0 <= b["z_lo"] < b["z_hi"] <= 0.85, (n, view, part)
        for part, b in m["side"].items():
            assert -0.01 <= b["y_lo"] < b["y_hi"] <= 1.01, (n, part)
            assert 0.0 <= b["z_lo"] < b["z_hi"] <= 0.85, (n, part)


def test_the_parts_sit_in_a_sane_order(marks):
    for n, m in marks.items():
        f, r, s = m["front"], m["rear"], m["side"]
        # the headlamp above the lower intake; the tail lamp's top above the rear plate's bottom (on some
        # cars, the Fusion, the plate sits high on the boot lid, beside the lamps rather than under them)
        assert (f["headlamp"]["z_lo"] + f["headlamp"]["z_hi"]) > (f["intake"]["z_lo"] + f["intake"]["z_hi"]), n
        if r["plate"]:
            assert r["tail"]["z_hi"] > r["plate"]["z_lo"], n
        # a paired lamp lies in its own half of the car
        for view, part in ck.PAIRED:
            b = m[view][part]
            if b:
                assert b["x_lo"] > 0, (n, view, part)
        # from the side: the headlamp at the nose, the tail lamp at the tail, the mirror between
        assert s["headlamp"]["y_hi"] < s["mirror"]["y_lo"] < s["mirror"]["y_hi"] < s["tail"]["y_lo"], n


@pytest.fixture(scope="module")
def kits():
    out = []
    for seed in range(1, 21):
        v = cs.sample_numbers("sedan", seed)
        out.append((v, ck.sample_kit(v, seed)))
    return out


def test_a_seed_always_gives_the_same_kit(kits):
    v, kit = kits[0]
    assert ck.sample_kit(v, 1) == kit


def test_only_the_n0_families_are_used(kits):
    for _, kit in kits:
        for part, family in kit["choices"].items():
            assert family in ck.FAMILIES[part], (part, family)
            assert family not in ("trapezoid", "twin round")


def test_every_size_and_place_is_inside_the_real_ranges(kits):
    R = ck.ranges("sedan")
    for v, kit in kits:
        for end in ("front", "rear"):
            for name, e in kit[end].items():
                if name == "trim_z":
                    if e is not None:
                        lo, hi = R[end]["trim_z"]["trim_z"]
                        assert lo <= e <= hi, (v["name"], end)
                    continue
                for k, x in e["box"].items():
                    if k in R[end][name]:
                        lo, hi = R[end][name][k]
                        assert lo - 1e-3 <= x <= hi + 1e-3, (v["name"], end, name, k)
        for name, e in kit["side"].items():
            for k in ("y_lo",):
                lo, hi = R["side"][name][k]
                assert lo - 1e-3 <= e["box"][k] <= hi + 1e-3, (v["name"], name, k)


def test_every_outline_is_inside_its_car_and_apart_from_the_others(kits):
    for v, kit in kits:
        for end in ("front", "rear"):
            elems = []
            for name, e in kit[end].items():
                if name == "trim_z":
                    continue
                for poly in e["polys"]:
                    for x, z in poly:
                        assert abs(x) <= ck._end_half_width(v, end, z) - ck.MARGIN + 1e-3, (v["name"], end, name)
                if name in ("headlamp", "tail", "fog"):
                    n = len(e["polys"]) // 2
                    elems += [(name + "_r", e["polys"][:n]), (name + "_l", e["polys"][n:])]
                else:
                    elems.append((name, e["polys"]))
            for i, (na, pa) in enumerate(elems):
                for nb, pb in elems[i + 1:]:
                    if {na, nb} == {"tail_r", "tail_l"}:
                        continue  # a tail bar's two halves meet at the centre line
                    d = min(ck.poly_distance(a, b) for a in pa for b in pb)
                    assert d >= ck.GAP - 1e-3, (v["name"], end, na, nb, d)


def test_paired_elements_are_mirror_images(kits):
    for v, kit in kits:
        for end, name in (("front", "headlamp"), ("front", "fog"), ("rear", "tail")):
            e = kit[end].get(name)
            if not e:
                continue
            n = len(e["polys"]) // 2
            for right, left in zip(e["polys"][:n], e["polys"][n:]):
                assert sorted((round(-x, 4), z) for x, z in left) == sorted((x, z) for x, z in right)


def test_fog_lamps_and_lower_trim_come_on_some_cars_only(kits):
    fogs = sum("fog" in kit["choices"] for _, kit in kits)
    trims = sum(kit["front"]["trim_z"] is not None or kit["rear"]["trim_z"] is not None for _, kit in kits)
    assert 0 < fogs < len(kits) and 0 < trims < len(kits)


def test_the_ranges_cover_every_real_sedan(marks):
    ranges = ck.face_ranges("sedan")
    for view in ("front", "rear", "side"):
        for part, r in ranges[view].items():
            if part == "trim_z":
                continue
            assert r["of"] == len(SEDANS)
            for n, m in marks.items():
                b = m[view][part]
                if not b:
                    continue
                for k, v in b.items():
                    lo, hi = r[k]
                    assert lo <= v <= hi, (n, view, part, k)
    assert ranges["front"]["headlamp"]["count"] == len(SEDANS)


def test_every_generated_car_keeps_its_kit_in_the_kits_folder():
    folder = cs.GENERATED / "sedan"
    cars = sorted(folder.glob("sedan_[0-9][0-9][0-9].json"))
    assert len(cars) == 20
    for car in cars:
        assert ck.kit_path(car).is_file(), car.name
    assert not list(folder.glob("sedan_*_kit.json"))  # none left beside the cars


@pytest.mark.blender
def test_a_generated_sedan_builds_with_its_kit_on_a_closed_body(tmp_path):
    ref = cs.GENERATED / "sedan" / "sedan_001.json"
    glb = tmp_path / "sedan_001.glb"
    result = cs.build_glb(ref, glb)
    assert result.ok, result.traceback or result.stdout[-1500:]
    rep = cs.kit_report(result)
    assert rep and not rep["skipped"], rep
    lo, hi = rep["gap_mm"]
    assert 1.0 <= lo and hi <= 5.0, rep          # every kit point 1-5 mm outside the body
    assert cs.glb_closed(glb)[0]                 # the body still one closed solid in the file
    tris = cs.glb_triangles(glb)
    assert sum(v["triangles"] for v in tris.values()) < 20000
    kit = [v for k, v in tris.items() if k.startswith("kit")]
    mirrors = [v for k, v in tris.items() if k.startswith("mirrors")]
    assert len(kit) == 1 and len(mirrors) == 1
    assert {"lamp_front", "lamp_rear", "gloss_black", "plate"} <= set(kit[0]["materials"])
    assert {"paint", "glass"} <= set(mirrors[0]["materials"])


def test_every_two_generated_faces_differ():
    import json
    folder = cs.GENERATED / "sedan"
    cars = []
    for path in sorted(folder.glob("sedan_[0-9][0-9][0-9].json")):
        v = json.loads(path.read_text(encoding="utf-8"))["generated"]["numbers"]
        cars.append((v, json.loads(ck.kit_path(path).read_text(encoding="utf-8"))))
    assert len(cars) == 20
    for i, (va, ka) in enumerate(cars):
        for vb, kb in cars[i + 1:]:
            assert ck.face_distance(ka, va["proportions"], kb, vb["proportions"]) >= ck.MIN_FACE, (va["name"], vb["name"])
            assert not ck.same_design(ka, kb), (va["name"], vb["name"])
