"""The base cars the generator starts from: the downloaded public-domain cars in assets_cc0/, each
read once in Blender and written into assets_cc0/catalog.json with its type, era, size, triangle
count, wheel positions and paint colour, plus one overview picture of them all to check by eye.

    python -m modelgen.tools.car_library            # read every car, write the catalogue and overview
    python -m modelgen.tools.car_library --quick    # the catalogue only, no pictures

Libraries (all CC0): assets_cc0/grab3d/*.glb (Grab3D), assets_cc0/quaternius/*.glb (Quaternius Cars
Bundle). Kenney's toy-sized kit is not used.
"""

import json
import subprocess
import sys
import tempfile
from pathlib import Path

from .. import config

ASSETS = config.ROOT / "assets_cc0"
CATALOG = ASSETS / "catalog.json"
THUMBS = ASSETS / "thumbs"
OVERVIEW = ASSETS / "overview.jpg"
LIBRARIES = ("grab3d", "quaternius")
# the first rule whose word is in a file's name gives the type
TYPE_RULES = (("station-wagon", "wagon"), ("pickup", "pickup"), ("police", "police"), ("taxi", "taxi"),
              ("off-roader", "offroad"), ("suv", "suv"), ("minivan", "minivan"), ("microbus", "van"),
              ("van", "van"), ("hatchback", "hatchback"), ("compact", "compact"), ("sports", "sports"),
              ("race", "race"), ("rally", "rally"), ("muscle", "muscle"), ("coupe", "coupe"),
              ("convertible", "convertible"), ("roadster", "convertible"), ("sedan", "sedan"),
              ("limousine", "limousine"), ("electric", "electric"), ("lowrider", "lowrider"),
              ("microcar", "microcar"), ("dune-buggy", "buggy"), ("tuk-tuk", "tuktuk"), ("classic", "classic"),
              ("atomic", "classic"), ("car", "sedan"))
ERAS = ("1950s", "1960s", "1970s", "1980s", "1990s")
JOB = '''import sys
sys.path.insert(0, {package!r})
from bpy_lib.shells import car_remix
{call}
'''


def classify(stem):
    """(type, era) from a base car's file name."""
    name = stem.lower().replace("_", "-")
    era = next((e for e in ERAS if name.startswith(e)), "classic" if "classic" in name or "atomic" in name
               else "modern")
    kind = next((t for word, t in TYPE_RULES if word in name), "car")
    return kind, era


def files():
    """Every base car file, by library."""
    return [p for lib in LIBRARIES for p in sorted((ASSETS / lib).glob("*.glb"))]


def run_blender(call, timeout=3600):
    """Run one car_remix call in headless Blender; returns (lines of stdout, return code)."""
    exe = config.blender_path()
    if not exe:
        raise RuntimeError("Blender was not found")
    folder = Path(tempfile.mkdtemp(prefix="mg_car_remix_"))
    script = folder / "job.py"
    script.write_text(JOB.format(package=str(config.PACKAGE), call=call), encoding="utf-8")
    flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
    done = subprocess.run([exe, "-b", "--python-exit-code", "1", "--python", str(script)], capture_output=True,
                          text=True, encoding="utf-8", errors="replace", timeout=timeout, creationflags=flags)
    return done.stdout.splitlines() + done.stderr.splitlines(), done.returncode


def tagged(lines, tag):
    """The JSON after each line starting with tag."""
    return [json.loads(line[len(tag):]) for line in lines if line.startswith(tag)]


def build(pictures=True):
    """Read every base car and write the catalogue; returns its entries."""
    paths = files()
    if not paths:
        raise RuntimeError(f"no base cars in {ASSETS}")
    if pictures:
        THUMBS.mkdir(parents=True, exist_ok=True)
    call = f"car_remix.catalogue({[str(p) for p in paths]!r}, {str(THUMBS) if pictures else None!r})"
    lines, code = run_blender(call)
    found = {Path(b["file"]).resolve(): b for b in tagged(lines, "MG_BASE ")}
    if code != 0 or len(found) != len(paths):
        raise RuntimeError("Blender did not read every car:\n" + "\n".join(lines[-30:]))
    entries = []
    for path in paths:
        info = dict(found[path.resolve()])
        info.pop("file")
        kind, era = classify(path.stem)
        entries.append({"name": f"{path.parent.name}/{path.stem}", "file": path.relative_to(ASSETS).as_posix(),
                        "type": kind, "era": era, **info})
    CATALOG.write_text(json.dumps(entries, indent=1), encoding="utf-8")
    if pictures:
        overview(entries)
    return entries


def load():
    """The catalogue's usable entries (those Blender could read)."""
    if not CATALOG.is_file():
        raise RuntimeError(f"{CATALOG} is missing: run python -m modelgen.tools.car_library")
    return [e for e in json.loads(CATALOG.read_text(encoding="utf-8")) if "error" not in e]


def overview(entries, path=OVERVIEW, columns=6):
    """One picture of every base car with its name, type and era."""
    from PIL import Image, ImageDraw, ImageFont
    w, h, label = 420, 280, 34
    rows = -(-len(entries) // columns)
    sheet = Image.new("RGB", (columns * w, rows * (h + label)), "white")
    draw = ImageDraw.Draw(sheet)
    try:
        font = ImageFont.truetype("arial.ttf", 17)
    except OSError:
        font = ImageFont.load_default()
    for i, e in enumerate(entries):
        x, y = (i % columns) * w, (i // columns) * (h + label)
        thumb = THUMBS / (Path(e["file"]).stem + ".png")
        if thumb.is_file():
            sheet.paste(Image.open(thumb).convert("RGB").resize((w, h)), (x, y))
        text = e["name"] + (f"  [{e['type']}, {e['era']}]" if "error" not in e else "  FAILED")
        draw.text((x + 6, y + h + 6), text, fill="black", font=font)
    sheet.save(path, quality=88)
    return path


def main(args):
    entries = build(pictures="--quick" not in args)
    bad = [e for e in entries if "error" in e]
    print(f"{'car':38}{'type':13}{'era':9}{'L':>6}{'WB':>6}{'W':>6}{'H':>6}{'wheel':>7}{'tris':>7}")
    for e in entries:
        if "error" in e:
            print(f"{e['name']:38}FAILED: {e['error']}")
            continue
        print(f"{e['name']:38}{e['type']:13}{e['era']:9}{e['length']:6.2f}{e['wheelbase']:6.2f}{e['width']:6.2f}"
              f"{e['height']:6.2f}{2 * e['wheel_radius']:7.2f}{e['triangles']:7d}")
    print(f"{len(entries) - len(bad)} cars catalogued, {len(bad)} failed: {CATALOG}")


if __name__ == "__main__":
    main(sys.argv[1:])
