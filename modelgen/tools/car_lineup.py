"""The lineup: a type's real cars and its generated ones as game meshes, side by side at one scale.

Each real car's measured numbers (modelgen.tools.car_space) are written as a reference to
library/generated/<type>/real/<name>.json and built with the same game builder as the generated cars
(bpy_lib.shells.car_game), to <name>.glb; so real and generated cars are seen in the same style. Every
car is rendered twice by Blender (CPU, 640 px, 32 samples): a flat side view and a three-quarter view,
both from the same fixed camera for every car, so a long car looks long. The renders are joined into
library/generated/<type>/lineup.png: the real cars in the top row, each with its blueprint's side view
under it (to show how far the builder strays from the drawing), then the generated ones, each with its
two biggest blend parents.

    python -m modelgen.tools.car_lineup sedan
"""

import json
import sys
import tempfile
from pathlib import Path

from PIL import Image, ImageDraw, ImageOps

from . import blender, blueprint
from . import car_space as cs

RENDER = {"engine": "CYCLES", "device": "CPU", "size": 640, "samples": 32}
SIDE_SCALE = 5.8      # metres across the flat side view
CENTRE = (0.0, 0.0, 0.72)
RADIUS = 2.9          # the three-quarter camera frames a sphere this big round CENTRE, for every car
TILE = 300            # tile width in the lineup (px)
COLUMNS = 12

RENDER_SCRIPT = '''

import math

from mathutils import Vector

from bpy_lib import rig


def assemble(parts):
    """Render the car from the same two fixed cameras as every other car in the lineup."""
    rig.setup_render({size}, {samples}, "CYCLES", "CPU")
    rig.stage()
    cam = rig.camera()
    centre = Vector({centre!r})
    cam.data.type = "ORTHO"
    cam.data.ortho_scale = {side_scale}
    rig.aim(cam, centre, {radius}, 90, 0)
    rig._render({side!r})
    cam.data.type = "PERSP"
    rig.aim(cam, centre, {radius}, -35, 18)
    rig._render({three!r})
    if {back!r}:
        rig.aim(cam, centre, {radius}, 145, 18)
        rig._render({back!r})
'''


def real_refs(type_):
    """Each real car of the type written as a reference made from its measured numbers:
    library/generated/<type>/real/<name>.json; returns their paths."""
    out = cs.GENERATED / type_ / "real"
    out.mkdir(parents=True, exist_ok=True)
    paths = []
    for v in cs.real(type_):
        ref = cs.to_reference(v)
        ref["car"] = v["car"]
        path = out / f"{v['name']}.json"
        path.write_text(json.dumps(ref, indent=1), encoding="utf-8")
        paths.append(path)
    return paths


def render(ref_path, out_dir):
    """Build the car from its reference in Blender (with its kit and mirrors, when it has a kit) and
    render its side and three-quarter views, and with a kit a rear three-quarter view too; returns
    (side, three-quarter, rear three-quarter or None) paths, or raises with Blender's traceback."""
    out_dir.mkdir(parents=True, exist_ok=True)
    has_kit = (ref_path.parent / "kits" / ref_path.name).is_file()
    side = out_dir / f"{ref_path.stem}_side.png"
    three = out_dir / f"{ref_path.stem}_34.png"
    back = out_dir / f"{ref_path.stem}_back34.png" if has_kit else None
    folder = Path(tempfile.mkdtemp(prefix="mg_lineup_"))
    script = folder / "lineup_job.py"
    script.write_text(cs.WRAPPER.format(ref=str(ref_path)) + RENDER_SCRIPT.format(
        size=RENDER["size"], samples=RENDER["samples"], centre=CENTRE, side_scale=SIDE_SCALE, radius=RADIUS,
        side=str(side), three=str(three), back=str(back) if back else ""), encoding="utf-8")
    parts = list(cs.GAME_PARTS) + (["kit", "mirrors"] if has_kit else [])
    steps = [blender.part_step(p, script, func=f"build_{p}") for p in parts]
    steps += [blender.materials_step(script), blender.assembly_step(script)]
    result = blender.run_job(steps, timeout=900, render=RENDER)
    if not result.ok or not side.is_file() or not three.is_file() or (back and not back.is_file()):
        raise RuntimeError(f"{ref_path.stem}: {result.failed_step}\n{result.traceback or result.stdout[-1500:]}")
    return side, three, back


def _side_crop(img):
    """The band of the flat side view the car stands in (the ground to 2 m up)."""
    k = img.width / SIDE_SCALE                 # pixels per metre
    mid = img.height / 2
    top = mid - (2.0 - CENTRE[2]) * k
    bottom = mid + (CENTRE[2] + 0.12) * k
    return img.crop((0, max(0, int(top)), img.width, min(img.height, int(bottom))))


def _blueprint_side(name):
    """The real car's blueprint side view, its nose turned to match the renders (front to the left)."""
    e = blueprint.manifest()[name]
    with Image.open(blueprint.REFS / e["file"]) as im:
        page = im.convert("RGB")
    spec = e["views"]["side"]
    crop = page.crop(tuple(spec["box"]))
    return ImageOps.mirror(crop) if spec.get("nose", "left") == "right" else crop


def _fit(img, width):
    return img.resize((width, max(1, round(img.height * width / img.width))), Image.LANCZOS)


def lineup(type_, reals, gens, path):
    """Join the renders into one picture (see the module docstring). reals and gens: lists of
    (numbers, side render, three-quarter render, rear three-quarter render or None)."""
    f, small = cs._font(15), cs._font(12)
    side_h = _fit(_side_crop(Image.open(reals[0][1])), TILE).height
    three_h = TILE
    bp_h = 70
    tile_h = 22 + side_h + 2 * three_h + 40
    rows = 1 + (len(gens) + COLUMNS - 1) // COLUMNS
    head = 34
    pic = Image.new("RGB", (COLUMNS * TILE, rows * (tile_h + head)), "white")
    d = ImageDraw.Draw(pic)
    d.text((8, 8), f"Real {type_}s, rebuilt with the game builder (blueprint side view under each)", fill=(0, 0, 0), font=f)
    d.text((8, tile_h + head + 8), f"Generated {type_}s", fill=(0, 0, 0), font=f)

    def tile(i, row0, v, side, three, back, real):
        r, c = divmod(i, COLUMNS)
        ox, oy = c * TILE, (row0 + r) * (tile_h + head) + head
        d.text((ox + 6, oy + 2), v["car"][:40], fill=(90, 90, 90) if real else (20, 60, 200), font=small)
        y = oy + 22
        s = _fit(_side_crop(Image.open(side).convert("RGB")), TILE)
        pic.paste(s, (ox, y))
        y += s.height
        for view in (three, back):  # the car fills the middle of the fixed frame
            if view is None:
                continue
            t = Image.open(view).convert("RGB")
            m = t.width // 8
            pic.paste(_fit(t.crop((m, m, t.width - m, t.height - m)), TILE), (ox, y))
            y += three_h
        if real:
            bp = _fit(_blueprint_side(v["name"]), TILE - 20)
            if bp.height > bp_h:
                bp = bp.resize((round(bp.width * bp_h / bp.height), bp_h), Image.LANCZOS)
            pic.paste(bp, (ox + 10, y))
            note = ""
        else:
            top2 = sorted(v["blend"].items(), key=lambda kv: -kv[1])[:2]
            note = "\n".join(f"{w:.0%} {name[:34]}" for name, w in top2)
        p = v["proportions"]
        sizes = f"L {p['length']:.2f}  WB {p['wheelbase']:.2f}  H {p['height']:.2f}  W {p['width']:.2f}"
        d.multiline_text((ox + 6, y + (bp_h if real else 0) + 2), "\n".join(filter(None, [sizes, note])),
                         fill=(0, 0, 0), font=small, spacing=1)

    for i, (v, side, three, back) in enumerate(reals):
        tile(i, 0, v, side, three, back, True)
    for i, (v, side, three, back) in enumerate(gens):
        tile(i, 1, v, side, three, back, False)
    pic.save(path)
    return path


def main(args):
    type_ = args[0] if args else "sedan"
    folder = cs.GENERATED / type_
    renders = folder / "renders"
    reals = []
    for ref_path in real_refs(type_):
        result = cs.build_glb(ref_path, ref_path.with_suffix(".glb"))
        if not result.ok:
            print(f"{ref_path.stem}: glb FAILED\n{result.traceback}")
        side, three, back = render(ref_path, renders)
        reals.append((cs.measure(ref_path.stem), side, three, back))
        print("rendered", ref_path.stem, flush=True)
    gens = []
    for ref_path in sorted(folder.glob(f"{type_}_*.json")):
        ref = json.loads(ref_path.read_text(encoding="utf-8"))
        side, three, back = render(ref_path, renders)
        gens.append((ref["generated"]["numbers"], side, three, back))
        print("rendered", ref_path.stem, flush=True)
    print("written:", lineup(type_, reals, gens, folder / "lineup.png"))


if __name__ == "__main__":
    main(sys.argv[1:])
