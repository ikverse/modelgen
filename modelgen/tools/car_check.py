"""Build one example car from its reference, render it as a clay model and score it against the
reference: python -m modelgen.tools.car_check <type> [<type> ...]

Outside Blender this starts a headless Blender on this same file, which builds the body
(bpy_lib.shells.car_v2) and wheels, writes their triangles and renders the clay views; then it scores
the triangles against the reference's blueprint views (modelgen.tools.car_score) and puts the renders,
the overlays and the scores into one report picture. Everything goes to
library/references/checks/<type>/.
"""

import json
import subprocess
import sys
from pathlib import Path

CLAY_VIEWS = ["side", "front_flat", "rear_flat", "top_flat", "front_left", "rear_right"]
PAINT_VIEWS = ["front_left", "rear_right", "side", "front_flat", "rear_flat"]
PAINT = (0.06, 0.2, 0.52)  # a deep blue
FLAT = {"side": "side", "front_flat": "front", "rear_flat": "rear"}  # flat render -> blueprint view


def _render_with_frames(rig, stage, out, views, prefix, frames):
    """rig.render_views, recording for each flat view how the picture maps metres to pixels: for
    (u, v) as the blueprint views take them, pixel = (su * u + ou, sv * v + ov)."""
    import bpy
    from bpy_extras.object_utils import world_to_camera_view
    from mathutils import Vector

    center, radius = stage  # the studio set once (rig.stage), shared by every set of renders
    cam = rig.camera()
    scene = bpy.context.scene
    w, h = scene.render.resolution_x, scene.render.resolution_y
    try:
        for name in views:
            az, el = rig.VIEWS[name]
            cam.data.type = "ORTHO" if name in rig.ORTHO else "PERSP"
            cam.data.ortho_scale = radius * 2.1
            rig.aim(cam, center, radius, az, el)
            rig._render(str(out / f"{prefix}_{name}.png"))
            if name in FLAT:
                bpy.context.view_layer.update()
                pts = {"side": [(0, -2, 0), (0, 2, 1)], "front": [(-1, 0, 0), (1, 0, 1)],
                       "rear": [(-1, 0, 0), (1, 0, 1)]}[FLAT[name]]
                (a, b) = [world_to_camera_view(scene, cam, Vector(p)) for p in pts]
                iu = 1 if FLAT[name] == "side" else 0  # u is y on the side view, x on the end views
                su = (b.x - a.x) * w / (pts[1][iu] - pts[0][iu])
                sv = -(b.y - a.y) * h / (pts[1][2] - pts[0][2])
                frames[FLAT[name]] = {"su": su, "ou": a.x * w - su * pts[0][iu],
                                      "sv": sv, "ov": (1 - a.y) * h - sv * pts[0][2], "size": [w, h]}
    finally:
        cam.data.type = "PERSP"


def _inside_blender(name, out_dir):
    """Runs in Blender: build, tag, finish, dump the triangles and render clay and painted views."""
    from bpy_lib import helpers as h
    from bpy_lib import materials as m
    from bpy_lib import rig
    import bpy
    from bpy_lib.shells import car_body, car_details, car_front, car_v2
    from bpy_lib.shells.car import SHEET_KEY

    h.reset_scene()
    body = car_v2.body(name)
    print("=== SKIPPED", body.get("mg_skipped_details") or "none", flush=True)
    wheels = car_v2.wheels(name)
    mirrors = car_details.mirrors(car_body.body(name))
    body[h.PART_KEY] = "body"
    body[SHEET_KEY] = json.dumps({"reference": name})  # marks it a car, for the car lights
    for w in wheels:
        w[h.PART_KEY] = "wheel"
    for mi in mirrors:
        mi[h.PART_KEY] = "side_mirror"
    # the lamps' clear covers (made as the lamps were cut) and the grille, projectors and bars
    front = [o for o in bpy.data.objects if "mg_lens" in o] + car_front.parts(name, body)
    for o in front:
        o[h.PART_KEY] = "front_detail"
    print("=== FRONT PARTS", ", ".join(sorted(o.name for o in front)), flush=True)
    parts = {"body": [body], "wheel": wheels, "side_mirror": mirrors, "front_detail": front}
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    n_all = car_v2.dump_triangles([body] + wheels, str(out / "car.tris"))
    n_body = car_v2.dump_triangles([body], str(out / "body.tris"))
    print("=== TRIANGLES", n_all, n_body, flush=True)
    rig.setup_render(640, 32, "CYCLES", "cpu")  # the CPU: the GPU may be busy with other programs
    frames = {}
    stage = rig.stage()
    clay = m.car_finish(parts, paint=(0.56, 0.57, 0.59), style="realistic")
    clay_glass = next(n for n in clay["glass"].node_tree.nodes if n.type == "BSDF_PRINCIPLED")
    m._set(clay_glass, "transmission", 0.0)  # a design clay's glass: opaque dark grey, no empty cabin behind it
    m._set(clay_glass, "base_color", (0.1, 0.1, 0.11))
    m._set(clay_glass, "roughness", 0.15)
    _render_with_frames(rig, stage, out, CLAY_VIEWS, "clay", {})
    finish = m.car_finish(parts, paint=PAINT, style="realistic")
    glass = finish["glass"]  # dark glossy glass you cannot see through: there is no cabin behind it yet
    bsdf = next(n for n in glass.node_tree.nodes if n.type == "BSDF_PRINCIPLED")
    m._set(bsdf, "transmission", 0.0)
    m._set(bsdf, "base_color", (0.012, 0.014, 0.018))
    m._set(bsdf, "roughness", 0.04)
    _render_with_frames(rig, stage, out, PAINT_VIEWS, "paint", frames)
    (out / "frames.json").write_text(json.dumps(frames), encoding="utf-8")
    zebra = _zebra_material()
    for i in range(len(body.data.materials)):  # the whole body under the stripes, glass and lamps too
        body.data.materials[i] = zebra
    _render_with_frames(rig, stage, out, ["front_left", "rear_right", "side"], "zebra", {})
    print("=== RENDERED", flush=True)


def _zebra_material():
    """The car designers' check of a surface: light and dark stripes reflected in it (bright where the
    view, mirrored in the surface, points into a band of the sky). Smooth surfaces bend the stripes
    smoothly; a ripple shows as a wobble or a break in them."""
    import bpy

    mat = bpy.data.materials.new("zebra")
    mat.use_nodes = True
    nt = mat.node_tree
    nt.nodes.clear()
    coords = nt.nodes.new("ShaderNodeTexCoord")
    split = nt.nodes.new("ShaderNodeSeparateXYZ")
    nt.links.new(coords.outputs["Reflection"], split.inputs[0])
    scale = nt.nodes.new("ShaderNodeMath")
    scale.operation = "MULTIPLY"
    scale.inputs[1].default_value = 24.0  # about a dozen stripes from horizon to zenith
    nt.links.new(split.outputs["Z"], scale.inputs[0])
    wave = nt.nodes.new("ShaderNodeMath")
    wave.operation = "SINE"
    nt.links.new(scale.outputs[0], wave.inputs[0])
    band = nt.nodes.new("ShaderNodeMath")
    band.operation = "GREATER_THAN"
    band.inputs[1].default_value = 0.0
    nt.links.new(wave.outputs[0], band.inputs[0])
    glow = nt.nodes.new("ShaderNodeEmission")
    nt.links.new(band.outputs[0], glow.inputs["Strength"])
    out = nt.nodes.new("ShaderNodeOutputMaterial")
    nt.links.new(glow.outputs[0], out.inputs["Surface"])
    return mat


def run(name, out_dir=None):
    """Build, render and score one car type; returns the scores and writes the report."""
    from .. import config
    from . import blueprint, car_score

    out = Path(out_dir or blueprint.REFS / "checks" / name)
    out.mkdir(parents=True, exist_ok=True)
    if blueprint.manifest()[name].get("details"):
        blueprint.write_details(name)  # the named details' outlines, for the build in Blender
    exe = config.blender_path()
    if not exe:
        raise RuntimeError("Blender is not installed")
    cmd = [exe, "-b", "--factory-startup", "-noaudio", "--python-exit-code", "1", "--python", __file__, "--",
           "inside", name, str(out)]
    proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    (out / "blender.log").write_text(proc.stdout + "\n" + proc.stderr, encoding="utf-8")
    if proc.returncode != 0 or "=== RENDERED" not in proc.stdout:
        raise RuntimeError(f"the Blender build failed; see {out / 'blender.log'}")
    ref = json.loads((blueprint.REFS / f"{name}.json").read_text(encoding="utf-8"))
    by_view = {}
    if ref["views"]["side"].get("wheels_from") == "arches":  # its side view shows empty wheel openings
        by_view["side"] = car_score.load_triangles(out / "body.tris")
    scores = car_score.score(name, out / "car.tris", out, by_view)
    skipped = next((ln.split("=== SKIPPED", 1)[1].strip() for ln in proc.stdout.splitlines() if "=== SKIPPED" in ln), "")
    scores["skipped_details"] = skipped
    from ..bpy_lib.shells import car_body
    b = car_body.body(name)
    middle = (b.y_front + 0.5, b.y_rear - 0.5)
    scores["bumps_mm"] = {"body_middle": round(1000 * car_body.Body.bumps(b.rings(), middle), 1),
                          "glasshouse": round(1000 * car_body.Body.bumps(b.glass_rings()), 1)}
    (out / "scores.json").write_text(json.dumps(scores, indent=1), encoding="utf-8")
    frames = json.loads((out / "frames.json").read_text(encoding="utf-8"))
    for view, frame in frames.items():
        if view in blueprint.manifest()[name]["views"]:
            detail_overlay(name, view, out / f"paint_{ {'side': 'side', 'front': 'front_flat', 'rear': 'rear_flat'}[view] }.png",
                           frame, out / f"details_{view}.png")
    report(name, out, scores)
    return scores


def detail_overlay(name, view, render_path, frame, out_path):
    """The blueprint's own lines of one view laid over the painted flat render of the built car, at the
    same scale and place: every window, lamp and grille should sit on the drawing's."""
    from PIL import Image, ImageOps

    from . import blueprint

    entry = blueprint.manifest()[name]
    spec = entry["views"][view]
    to_metres, _, _ = blueprint._view_frame(name, view)
    x0, y0, x1, y1 = spec["box"]
    # to_metres is linear in each axis: u from the page's X, v from its Y
    (ua, va), (ub, vb) = to_metres(x0, y0), to_metres(x0 + 100, y0 + 100)
    au, bu = (ub - ua) / 100, ua - (ub - ua) / 100 * x0
    av, bv = (vb - va) / 100, va - (vb - va) / 100 * y0
    render = Image.open(render_path).convert("RGB")
    w, h = render.size
    # each render pixel (px, py) -> metres -> page pixel: an affine map for Image.transform
    su, ou, sv, ov = frame["su"], frame["ou"], frame["sv"], frame["ov"]
    # u = (px - ou) / su ; X = (u - bu) / au  =>  X = px / (su au) - (ou / su + bu) / au
    cx, cc = 1 / (su * au), -(ou / su + bu) / au
    cy, cr = 1 / (sv * av), -(ov / sv + bv) / av
    with Image.open(blueprint.REFS / entry["file"]) as im:
        page = im.convert("L")
    lines = page.transform((w, h), Image.AFFINE, (cx, 0, cc, 0, cy, cr), resample=Image.BILINEAR, fillcolor=255)
    ink = ImageOps.invert(lines).point(lambda v: 255 if v > 90 else 0)
    picture = render.copy()
    picture.paste(Image.new("RGB", (w, h), (255, 200, 0)), (0, 0), ink)
    picture.save(out_path)
    return out_path


def report(name, out, scores):
    """One picture: the painted and clay renders, the detail overlays and the outline overlays, the
    scores written on."""
    from PIL import Image, ImageDraw, ImageFont

    renders = [out / f"paint_{v}.png" for v in ("front_left", "rear_right") if (out / f"paint_{v}.png").exists()]
    renders += [out / f"details_{v}.png" for v in ("side", "front", "rear") if (out / f"details_{v}.png").exists()]
    renders += [out / f"clay_{v}.png" for v in ("front_left",) if (out / f"clay_{v}.png").exists()]
    renders += [out / f"zebra_{v}.png" for v in ("front_left", "side") if (out / f"zebra_{v}.png").exists()]
    overlays = [out / f"{v}_overlay.png" for v in ("side", "top", "front", "rear") if (out / f"{v}_overlay.png").exists()]
    tile = 420
    cols = 3
    imgs = []
    for p in renders + overlays:
        im = Image.open(p).convert("RGB")
        im.thumbnail((tile, tile))
        imgs.append(im)
    rows = (len(imgs) + cols - 1) // cols
    sheet = Image.new("RGB", (cols * tile, rows * tile + 70), "white")
    for i, im in enumerate(imgs):
        sheet.paste(im, ((i % cols) * tile + (tile - im.width) // 2, 70 + (i // cols) * tile + (tile - im.height) // 2))
    try:
        font = ImageFont.truetype("arial.ttf", 17)
    except OSError:
        font = ImageFont.load_default()
    line = "   ".join(f"{v}: IoU {s['iou']:.3f}, edges {s['edge_mean_cm']} cm mean / {s['edge_p95_cm']} cm p95"
                       for v, s in scores.items() if isinstance(s, dict) and "iou" in s)
    if scores.get("bumps_mm"):
        line += f"   bumps: body {scores['bumps_mm']['body_middle']} mm, glasshouse {scores['bumps_mm']['glasshouse']} mm"
    if scores.get("skipped_details") not in (None, "", "none"):
        line += f"   left out: {scores['skipped_details']}"
    d = ImageDraw.Draw(sheet)
    d.text((8, 8), f"{name}: built from its reference", fill=(0, 0, 0), font=font)
    d.text((8, 36), line, fill=(0, 0, 0), font=font)
    sheet.save(out / "report.png")
    return out / "report.png"


if __name__ == "__main__":
    args = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else sys.argv[1:]
    if args and args[0] == "inside":  # in Blender
        sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # the modelgen folder: bpy_lib
        _inside_blender(args[1], args[2])
    else:
        sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
        for n in args:
            print(n, json.dumps(run(n)))
