"""The side-profile fit without Blender: a side view drawn from a known sheet is fitted back to close
numbers, a picture with no clean background is reported as not confident, and the car architect's
tool returns the overlay picture."""

import random

from PIL import Image, ImageDraw

from modelgen.bpy_lib.shells import car_sheet
from modelgen.tools import agent_tools as at
from modelgen.tools import silhouette

TRUTH = {"style": "coupe", "height": 1.25, "wheelbase": 2.55, "roof_front": -0.05, "deck_z": 0.95, "nose_z": 0.62}


def side_view(path, sheet, nose_right=False, ppm=140, background=(235, 236, 240)):
    """A flat side view of the sheet, dark on a plain background, with a margin all round."""
    s = car_sheet.resolve(sheet)
    w, h = int(s["length"] * ppm) + 80, int(s["height"] * ppm) + 120
    img = Image.new("RGB", (w, h), background)
    draw = ImageDraw.Draw(img)
    for poly in silhouette.outline(s):
        xs = [(y - s["y_front"]) * ppm for y, _ in poly]
        xs = [w - 40 - x for x in xs] if nose_right else [40 + x for x in xs]
        draw.polygon([(x, h - 60 - z * ppm) for x, (_, z) in zip(xs, poly)], fill=(30, 50, 120))
    img.save(path)
    return path


def test_a_side_view_of_a_known_sheet_fits_back_to_its_numbers(tmp_path):
    path = side_view(tmp_path / "side.png", TRUTH, nose_right=True)
    result = silhouette.fit(path, {"style": "coupe"})
    assert result["ok"] and result["confident"], result["warnings"]
    assert result["flipped"]
    assert result["overlap_after"] > 0.97 > result["overlap_before"]
    fitted, truth = car_sheet.resolve(result["sheet"]), car_sheet.resolve(TRUTH)
    for key in ("height", "wheelbase", "roof_front", "deck_z", "nose_z"):
        assert abs(fitted[key] - truth[key]) < 0.04, (key, fitted[key], truth[key])
    assert not car_sheet.check(result["sheet"])


def test_a_busy_background_is_not_confident(tmp_path):
    rnd = random.Random(1)
    img = Image.new("RGB", (600, 300))
    img.putdata([(rnd.randrange(256), rnd.randrange(256), rnd.randrange(256)) for _ in range(600 * 300)])
    img.save(tmp_path / "noise.png")
    result = silhouette.fit(tmp_path / "noise.png", {"style": "coupe"})
    assert not result.get("confident")
    assert result["warnings"]


def test_the_architect_tool_fits_a_reference_and_shows_the_overlay(tmp_path):
    path = side_view(tmp_path / "side.png", TRUTH)
    assert at.fit_tools([]) == []
    tool = at.fit_tools([path])[0]
    assert tool.name == "fit_side_profile"
    result = tool.fn({"reference": 1, "sheet": {"style": "coupe"}})
    assert result["ok"] and not result["flipped"]
    assert Image.open(result["_images"][0]).size[0] > 100
    assert not any(k.startswith("_") and k != "_images" for k in result)
    assert tool.fn({"reference": 2, "sheet": {}})["ok"] is False
    names = [t.name for t in at.shell_architect_tools({}, references=[path])]
    assert "fit_side_profile" in names and "submit_plan" in names
