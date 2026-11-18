"""The render sets ModelGen makes, and the contact sheets the agents look at.

Final renders of a version: an eight-angle turntable, a wireframe pair and close-ups of the most
detailed parts, all under the same fixed three-point rig (modelgen\\bpy_lib\\rig.py); a car also
gets eight car views (flat side, front, rear and top, and four low three-quarter views). Each set is
also tiled into one JPEG contact sheet so the critic sees a whole set in a single picture, and
reference images paired with views are set beside them.
"""

import math
from pathlib import Path

from PIL import Image, ImageDraw

from .. import config
from . import blender

PREVIEW_VIEWS = ["front", "right", "top", "three_quarter"]
MATERIAL_VIEWS = ["three_quarter", "back_three_quarter"]
# A car: flat views to compare silhouettes, and low three-quarter views like a car photo
CAR_PREVIEW_VIEWS = ["side", "front_flat", "rear_flat", "top_flat", "front_left", "rear_right"]
CAR_VIEWS = ["front_left", "front_right", "rear_left", "rear_right", "side", "front_flat", "rear_flat", "top_flat"]


def preview_step(out_dir, views=None, prefix="preview"):
    """Quick views for a builder, the assembler or the materials agent."""
    return {"kind": "render", "set": "views", "key": prefix, "views": views or PREVIEW_VIEWS, "prefix": prefix,
            "dir": str(out_dir), "size": int(config.get("render", "preview_size", 384)),
            "samples": int(config.get("render", "preview_samples", 24)), "label": f"render {prefix}"}


def context_step(out_dir, part, views=None):
    """A builder's part where it sits in the whole model: two close-ups, then whole views with the
    part painted orange (bpy_lib.rig.render_in_context)."""
    return {"kind": "render", "set": "context", "key": "context", "part": part,
            "views": list(views or ["three_quarter", "back_three_quarter"]), "dir": str(out_dir),
            "size": int(config.get("render", "preview_size", 384)),
            "samples": int(config.get("render", "preview_samples", 24)), "label": f"render '{part}' in the model"}


CONTEXT_LABELS = ["in model: close-up", "in model: close-up, other side"]


FRAME_COUNT = 6


def frames_step(out_dir, count=FRAME_COUNT):
    """Frames spread across one loop of the motion, close up on the biggest spinning wheel from the
    side, then the smoke and steam markers as balls; at preview size. The animator's check, and one
    more sheet for the critic."""
    return {"kind": "render", "set": "frames", "key": "frames", "count": count, "dir": str(out_dir),
            "size": int(config.get("render", "preview_size", 384)),
            "samples": int(config.get("render", "preview_samples", 24)), "label": "render motion frames"}


def marker_label(kind):
    return f"{kind} marker: {'white' if kind == 'steam' else 'grey'} ball"


def frame_labels(paths):
    """A label for each picture of the frame strip: the frame's place in the loop, or which marker
    a close-up shows (files markers_<n>_<kind>.png)."""
    frames = [p for p in paths if not Path(p).stem.startswith("markers")]
    labels, n = [], 0
    for p in paths:
        stem = Path(p).stem
        if stem.startswith("markers"):
            labels.append(marker_label(stem.split("_")[-1]))
        else:
            n += 1
            labels.append(f"frame {n} of {len(frames)}")
    return labels


def final_steps(out_dir, car=False, motion=False, views=(), studio=None):
    """views: a family's flat views, rendered as one more set; studio: "vehicle" for the car studio."""
    size = int(config.get("render", "size", 768))
    samples = int(config.get("render", "samples", 96))
    common = {"kind": "render", "dir": str(out_dir), "size": size, "samples": samples, "studio": studio}
    steps = [
        {**common, "set": "turntable", "count": int(config.get("render", "turntable_angles", 8)),
         "label": "render turntable"},
        {**common, "set": "wireframe", "label": "render wireframe"},
        {**common, "set": "closeups", "count": int(config.get("render", "closeups", 3)), "label": "render close-ups"},
    ]
    if car:
        steps.append({**common, "set": "views", "key": "car_views", "views": CAR_VIEWS, "prefix": "car",
                      "label": "render car views"})
    if views and not car:
        steps.append({**common, "set": "views", "key": "family_views", "views": list(views), "prefix": "flat",
                      "label": "render flat views"})
    if motion:
        steps.append({**frames_step(out_dir), "studio": studio})
    return steps


def contact_sheet(paths, out_path, columns=None, tile=384, labels=None):
    """Tile images into one JPEG. Returns the sheet's path, or None when there are no images."""
    images = [Path(p) for p in paths if Path(p).is_file()]
    if not images:
        return None
    columns = columns or min(4, len(images))
    rows = math.ceil(len(images) / columns)
    sheet = Image.new("RGB", (columns * tile, rows * tile), (40, 40, 40))
    draw = ImageDraw.Draw(sheet)
    for i, path in enumerate(images):
        with Image.open(path) as im:
            im = im.convert("RGB")
            im.thumbnail((tile, tile))
            x, y = (i % columns) * tile, (i // columns) * tile
            sheet.paste(im, (x + (tile - im.width) // 2, y + (tile - im.height) // 2))
        label = labels[i] if labels and i < len(labels) else None
        if label:
            draw.rectangle((x, y, x + 8 + 7 * len(label), y + 18), fill=(0, 0, 0))
            draw.text((x + 4, y + 3), label, fill=(235, 235, 235))
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(out_path, "JPEG", quality=88)
    return str(out_path)


def pair_sheet(pairs, out_path, tile=384):
    """Each reference image beside the render of the same view, one pair per row: pairs is a list of
    (reference_path, render_path, label). Returns the sheet's path, or None when no pair has both."""
    rows = [(Path(a), Path(b), label) for a, b, label in pairs if Path(a).is_file() and Path(b).is_file()]
    if not rows:
        return None
    paths, labels = [], []
    for ref, own, label in rows:
        paths += [ref, own]
        labels += [f"reference: {label}", f"model: {label}"]
    return contact_sheet(paths, out_path, columns=2, tile=tile, labels=labels)


def reference_pairs(reference_views, references, renders_by_view):
    """(reference, render, view) for each reference the plan pairs with a view. reference_views maps the
    reference's number ("1" is the first image) to a view name."""
    out = []
    for key, view in (reference_views or {}).items():
        try:
            ref = references[int(key) - 1]
        except (ValueError, IndexError, TypeError):
            continue
        if view in renders_by_view:
            out.append((ref, renders_by_view[view], view))
    return out


async def render_version(blend_path, out_dir, car=False, reference_views=None, references=(), motion=False,
                         views=(), studio=None):
    """Render the final sets of a saved version and build its contact sheets. A car also gets its car
    views and, with reference images paired to views, a sheet of each reference beside its view; a
    model with motion gets a strip of frames across one loop."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    result = await blender.run_job_async(final_steps(out_dir, car=car, motion=motion, views=views, studio=studio),
                                         open_blend=blend_path)
    renders = result.outputs.get("renders", {}) if result.ok else {}
    sheets = {}
    turn = renders.get("turntable", [])
    if turn:
        labels = [f"{-35 + 360 * i // len(turn)} deg" for i in range(len(turn))]
        sheets["turntable"] = contact_sheet(turn, out_dir / "sheet_turntable.jpg", columns=4, labels=labels)
        if len(turn) >= 8:
            # the same views for the critic's eyes: four to a picture, so each stays large when it is shown
            half = len(turn) // 2
            sheets["turntable_large"] = [
                contact_sheet(turn[:half], out_dir / "critic_turntable_1.jpg", columns=2, tile=640, labels=labels[:half]),
                contact_sheet(turn[half:], out_dir / "critic_turntable_2.jpg", columns=2, tile=640, labels=labels[half:])]
    if renders.get("wireframe"):
        sheets["wireframe"] = contact_sheet(renders["wireframe"], out_dir / "sheet_wireframe.jpg", columns=2, tile=512)
    if renders.get("closeups"):
        sheets["closeups"] = contact_sheet(renders["closeups"], out_dir / "sheet_closeups.jpg",
                                           columns=min(3, len(renders["closeups"])), tile=512,
                                           labels=[Path(p).stem.replace("closeup_", "") for p in renders["closeups"]])
    views = renders.get("car_views") or []
    if views:
        sheets["car_views"] = contact_sheet(views, out_dir / "sheet_car_views.jpg", columns=4, labels=CAR_VIEWS)
        by_view = dict(zip(CAR_VIEWS, views))
        pairs = reference_pairs(reference_views, list(references), by_view)
        if pairs:
            sheets["reference_pairs"] = pair_sheet(pairs, out_dir / "sheet_reference_pairs.jpg")
    flat = renders.get("family_views") or []
    if flat:
        sheets["family_views"] = contact_sheet(flat, out_dir / "sheet_family_views.jpg", columns=len(flat),
                                               labels=list(views))
    frames = renders.get("frames") or []
    if frames:
        sheets["motion"] = contact_sheet(frames, out_dir / "sheet_motion.jpg", columns=3,
                                         labels=frame_labels(frames))
    return result, renders, sheets
