"""The catalogue of free ready-made models: reading Poly Haven's list, searching it, fetching one
model's files and making an asset from it. No test touches the network: the one function that asks
Poly Haven for anything is replaced by a fake."""

import json

import pytest

from modelgen.tools import asset_catalog as ac
from modelgen.tools import car_library

MODELS = {
    "WoodenChair_01": {"name": "Wooden Chair 01", "categories": ["furniture", "seating"],
                       "tags": ["chair", "wood", "old"], "category": "Furniture/Seating/Chairs",
                       "polycount": 5000, "dimensions": [450.0, 500.0, 900.0], "download_count": 10,
                       "description": "An old wooden chair."},
    "Rockingchair_01": {"name": "Rockingchair 01", "categories": ["furniture", "seating"], "tags": ["chair"],
                        "category": "Furniture/Seating/Chairs", "polycount": 9000, "dimensions": [600, 900, 1000]},
    "bench_vice_01": {"name": "Bench Vice 01", "categories": ["tools"], "tags": ["vice", "workshop"],
                      "category": "Tools/Workshop/Vices", "polycount": 20000, "dimensions": [200, 300, 250]},
    "painted_wooden_bench": {"name": "Painted Wooden Bench", "categories": ["furniture", "seating"],
                             "tags": ["bench", "wood"], "category": "Furniture/Seating/Benches",
                             "polycount": 3000, "dimensions": [1500, 500, 800]},
    "coast_rocks_01": {"name": "Coast Rocks 01", "categories": ["nature", "rocks"], "tags": ["rock", "stone"],
                       "category": "Nature/Rocks", "polycount": 80000, "dimensions": [2000, 1500, 900]},
    "street_lamp_01": {"name": "Street Lamp 01", "categories": ["lighting"], "tags": ["lamp", "street"],
                       "category": "Lighting/Outdoor/Street Lamps", "polycount": 7000, "dimensions": [400, 900, 4200]},
}
FILES = {"gltf": {"1k": {"gltf": {
    "url": "https://dl.polyhaven.org/file/ph-assets/Models/gltf/1k/WoodenChair_01/WoodenChair_01_1k.gltf", "size": 9,
    "include": {"WoodenChair_01.bin": {"url": "https://dl.polyhaven.org/file/x/WoodenChair_01.bin", "size": 3},
                "textures/WoodenChair_01_diff_1k.jpg": {"url": "https://dl.polyhaven.org/file/x/diff.jpg",
                                                         "size": 4}}}}}}
REAL_HTTP = ac._http


@pytest.fixture
def catalogue(tmp_path, monkeypatch):
    """A catalogue in a temporary folder, read from a fake Poly Haven. Returns the list of addresses asked for."""
    asked = []

    def fake(url, timeout=60):
        asked.append(url)
        if url.endswith("/assets?t=models"):
            return json.dumps(MODELS).encode()
        if "/files/" in url:
            return json.dumps(FILES).encode()
        return {"gltf": b"gltf-file", "bin": b"bin", "jpg": b"jpeg"}[url.rsplit(".", 1)[1]]

    monkeypatch.setattr(ac, "OBJECTS", tmp_path / "objects")
    monkeypatch.setattr(ac, "CATALOG", tmp_path / "objects" / "catalog.json")
    monkeypatch.setattr(ac, "_http", fake)
    ac.build()
    return asked


def top(text):
    hits = ac.search(text)
    return hits[0]["entry"]["id"] if hits else None


def test_build_reads_the_list_once_and_keeps_sizes_in_metres(catalogue):
    entries = ac.load()
    assert catalogue == ["https://api.polyhaven.com/assets?t=models"]
    chair = ac.entry_by_id("polyhaven/WoodenChair_01")
    assert len(entries) == len(MODELS) and chair["style"] == "realistic" and chair["library"] == "polyhaven"
    assert chair["size"] == {"width": 0.45, "depth": 0.5, "height": 0.9} and chair["triangles"] == 5000


def test_search_finds_the_thing_asked_for(catalogue):
    assert top("wooden chair") == "polyhaven/WoodenChair_01"
    assert top("a red chair with arms") == "polyhaven/WoodenChair_01"          # colour words are not object words
    assert top("park benches") == "polyhaven/painted_wooden_bench"              # a bench, not the bench vice
    assert top("street lamp") == "polyhaven/street_lamp_01"
    assert top("a big boulder") == "polyhaven/coast_rocks_01"                   # boulder and stone mean rock
    assert top("Build a small wooden chair for a game, about 90 cm tall.") == "polyhaven/WoodenChair_01"


def test_search_finds_nothing_for_what_is_not_there(catalogue):
    assert ac.search("dragon") == []
    assert ac.search("spaceship") == []
    assert ac.search("rock band poster") == []        # 'rock' here is not the thing asked for
    assert ac.search("red sports car") == []          # cars belong to the car generator
    assert ac.search("") == []


def test_local_packs_are_indexed_by_file_name(catalogue):
    pack = ac.OBJECTS / "quaternius" / "nature"
    pack.mkdir(parents=True)
    (pack / "PineTree_3.glb").write_bytes(b"x")
    entries = ac.build(refresh=False)
    tree = ac.entry_by_id("quaternius/nature/PineTree_3", entries)
    assert tree["style"] == "lowpoly" and tree["library"] == "quaternius" and "pine" in tree["tags"]
    assert len(entries) == len(MODELS) + 1 and len(catalogue) == 1   # no second request to the API
    assert top("pine tree") == "quaternius/nature/PineTree_3"
    assert ac.fetch(tree) == pack / "PineTree_3.glb"


def test_fetch_downloads_once_into_the_models_own_folder(catalogue):
    chair = ac.entry_by_id("polyhaven/WoodenChair_01")
    path = ac.fetch(chair)
    folder = ac.OBJECTS / "polyhaven" / "WoodenChair_01"
    assert path == folder / "WoodenChair_01.gltf" and path.read_bytes() == b"gltf-file"
    assert (folder / "textures" / "WoodenChair_01_diff_1k.jpg").read_bytes() == b"jpeg"
    assert ac.downloaded_bytes(chair) == 9 + 3 + 4
    asked = len(catalogue)
    assert ac.fetch(chair) == path and len(catalogue) == asked   # already here: nothing asked again


def test_fetch_refuses_a_model_that_is_too_big(catalogue, monkeypatch):
    monkeypatch.setattr(ac, "MAX_BYTES", 10)
    with pytest.raises(RuntimeError, match="limit"):
        ac.fetch(ac.entry_by_id("polyhaven/WoodenChair_01"))


@pytest.mark.parametrize("url", ["https://example.com/x.gltf", "http://api.polyhaven.com/assets",
                                 "https://polyhaven.com.evil.org/a", "file:///C:/Windows/win.ini"])
def test_only_poly_haven_is_ever_asked(url):
    with pytest.raises(RuntimeError, match="refused"):   # refused before any connection is made
        REAL_HTTP(url)


def test_unsafe_file_names_are_refused(catalogue, monkeypatch):
    bad = json.loads(json.dumps(FILES))
    bad["gltf"]["1k"]["gltf"]["include"]["../../outside.bin"] = {"url": "https://dl.polyhaven.org/x.bin", "size": 1}
    monkeypatch.setattr(ac, "_http", lambda url, timeout=60: json.dumps(bad).encode() if "/files/" in url else b"x")
    with pytest.raises(RuntimeError, match="unsafe"):
        ac.fetch(ac.entry_by_id("polyhaven/WoodenChair_01"))
    assert not (ac.OBJECTS.parent / "outside.bin").exists()


def test_plan_takes_colour_and_size_from_the_request(catalogue):
    chair = ac.entry_by_id("polyhaven/WoodenChair_01")
    made = ac.plan("a dark blue wooden chair", chair, {"height": 120, "unit": "cm"})
    assert made["asset"] == "polyhaven/WoodenChair_01" and made["colour_name"] == "dark blue"
    assert made["colour"] == list(ac.COLOURS["dark blue"]) and made["size"] == {"height": 1.2}
    plain = ac.plan("wooden chair", chair)
    assert plain["colour"] is None and plain["size"] is None
    assert ac.plan("wooden chair, 80 cm tall", chair)["size"] == {"height": 0.8}
    assert ac.describe(made) == "Wooden Chair 01 from Poly Haven, recoloured dark blue, 1.2 m tall"


def test_changes_in_words(catalogue):
    made = ac.plan("wooden chair", ac.entry_by_id("polyhaven/WoodenChair_01"))
    red = ac.changed(made, "make it red")
    assert red["colour_name"] == "red" and red["size"] is None and made["colour"] is None
    assert ac.changed(red, "1.5 m tall please")["size"] == {"height": 1.5}
    assert ac.changed(red, "a bit bigger")["size"] == {"height": 1.125}     # from its own 0.9 m
    assert ac.changed(red, "back to the original colour")["colour"] is None
    assert ac.changed(red, "smaller")["colour_name"] == "red"               # what is not named stays


CUBE = '''import bpy
bpy.ops.wm.read_factory_settings(use_empty=True)
bpy.ops.mesh.primitive_cube_add(size=2.0, location=(5, 5, 5))
bpy.ops.mesh.primitive_cube_add(size=1.0, location=(5, 5, 6.5))
bpy.ops.export_scene.gltf(filepath={path!r}, export_format="GLB")
'''


@pytest.mark.blender
def test_make_builds_an_asset_from_a_model_file(catalogue, tmp_path):
    pack = ac.OBJECTS / "testpack"
    pack.mkdir(parents=True)
    lines, code = car_library.run_blender(CUBE.format(path=str(pack / "stone_block.glb")))
    assert code == 0, "\n".join(lines[-20:])
    ac.build(refresh=False)
    entry = ac.search("stone block")[0]["entry"]
    made = ac.plan("a green stone block, 50 cm tall", entry)
    out = tmp_path / "out"
    record = ac.make(made, out / "asset.glb", out / "model.blend")
    after = record["after"]
    assert (out / "asset.glb").stat().st_size > 500 and (out / "model.blend").is_file()
    assert after["height"] == pytest.approx(0.5, abs=1e-3) and after["ground"] == pytest.approx(0.0, abs=1e-4)
    assert after["width"] == pytest.approx(1 / 3, abs=1e-3)   # scaled evenly: 2 m wide, 3 m tall before
    assert after["triangles"] == 24 and record["tinted"] is True
