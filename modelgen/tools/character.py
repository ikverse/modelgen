"""Characters (people) built from a character sheet, with no build team.

A character sheet says who the person is: age in years, sex, height, build, ancestry, skin tone,
style, and for later stages their hair, clothes and face. `clean` checks a sheet and fills in what
it leaves out (every filled-in value is listed in `assumed`); `body` turns it into the body model's
sliders (bpy_lib.character_body); `steps` gives the Blender job that builds, rigs, dresses,
animates, measures and exports the character in one run.

Everything here is plain Python: the host checks a sheet without Blender, and the same numbers
reach Blender in the job.
"""

import functools
import hashlib
import json

from .. import config

SEXES = {"female": 1.0, "woman": 1.0, "girl": 1.0, "f": 1.0, "male": 0.0, "man": 0.0, "boy": 0.0, "m": 0.0,
         "neutral": 0.5, "androgynous": 0.5}
# build -> (muscle, weight) sliders, 0..1 with 0.5 average
BUILDS = {"skinny": (0.15, 0.0), "slim": (0.3, 0.15), "average": (0.5, 0.5), "athletic": (0.9, 0.4),
          "muscular": (1.0, 0.65), "stocky": (0.7, 0.8), "chubby": (0.3, 0.85), "heavy": (0.4, 1.0)}
STYLES = ("realistic", "cartoon", "shonen_anime", "gritty_anime")
# what the clothes and hair tools make (bpy_lib.character_parts GARMENTS and HAIR_STYLES; a test keeps
# the two lists the same)
GARMENT_TYPES = ("tshirt", "tank_top", "shirt", "blouse", "sweater", "hoodie", "cardigan", "turtleneck", "jacket",
                 "leather_jacket", "coat", "pants", "jeans", "chinos", "shorts", "leggings", "skirt", "dress", "onesie",
                 "sneakers", "shoes", "flats", "boots")
GARMENT_KIND = {**{t: "top" for t in ("tshirt", "tank_top", "shirt", "blouse", "sweater", "hoodie", "cardigan",
                                      "turtleneck", "jacket", "leather_jacket", "coat")},
                **{t: "bottom" for t in ("pants", "jeans", "chinos", "shorts", "leggings", "skirt")},
                "dress": "dress", "onesie": "onesie", **{t: "shoes" for t in ("sneakers", "shoes", "flats", "boots")}}
OUTER = ("cardigan", "jacket", "leather_jacket", "coat")  # tops worn over another top
HAIR_STYLES = ("bald", "buzz", "short", "crew", "undercut", "medium", "bob", "long", "ponytail", "bun", "spiky",
               "curly", "afro")
SKIRT_LENGTHS = ("mini", "knee", "midi", "long")
ACCESSORIES = ("glasses",)
# the face and body sliders the agents are shown (the body model has about 190; these shape a face)
FACE_SLIDERS = ("head-scale-horiz-decr-incr", "head-scale-vert-decr-incr", "head-fat-decr-incr",
                "head-age-decr-incr", "eye-scale-decr-incr", "eye-trans-down-up", "eye-corner1-down-up",
                "eye-epicanthus-in-out", "eyebrows-trans-down-up", "eyebrows-angle-down-up",
                "nose-scale-horiz-decr-incr", "nose-scale-vert-decr-incr", "nose-hump-decr-incr",
                "nose-point-width-decr-incr", "mouth-scale-horiz-decr-incr", "mouth-lowerlip-volume-decr-incr",
                "mouth-upperlip-height-decr-incr", "mouth-angles-down-up", "chin-width-decr-incr",
                "chin-prominent-decr-incr", "chin-height-decr-incr", "cheek-bones-decr-incr",
                "ear-scale-decr-incr", "neck-scale-horiz-decr-incr", "torso-vshape-decr-incr")
MAX_GARMENTS = 6
# typical skin tones (sRGB) by the largest ancestry share, light to deep within each
SKIN = {"african": "#6b4430", "asian": "#d9ae8c", "caucasian": "#e3b9a0", "mixed": "#b9876a"}
EYES = {"african": "#3b2416", "asian": "#3b2416", "caucasian": "#5a6e8a", "mixed": "#4a3322"}
AGE_RANGE = (0.0, 95.0)
HEIGHT_RANGE = (40.0, 230.0)
ANCESTRY = ("african", "asian", "caucasian")


class CharacterError(ValueError):
    pass


def _num(raw, key, low, high, where="character"):
    value = raw.get(key)
    try:
        value = float(value)
    except (TypeError, ValueError):
        raise CharacterError(f"{where}.{key} must be a number from {low} to {high}, not {value!r}") from None
    if not low <= value <= high:
        raise CharacterError(f"{where}.{key} must be from {low} to {high}, not {value:g}")
    return value


def _hex(value, where):
    text = str(value or "").strip().lower()
    if len(text) == 7 and text[0] == "#" and all(c in "0123456789abcdef" for c in text[1:]):
        return text
    raise CharacterError(f"{where} must be a colour like \"#c8a088\", not {value!r}")


def _sex(raw, assumed):
    if "female" in raw and raw["female"] is not None:
        return _num(raw, "female", 0.0, 1.0)
    sex = str(raw.get("sex") or "").strip().lower()
    if not sex:
        assumed.append("sex: neutral (not given)")
        return 0.5
    if sex not in SEXES:
        raise CharacterError(f"character.sex must be female, male or neutral, not {sex!r}")
    return SEXES[sex]


def typical_height_cm(age, female):
    """The median height (cm) for an age in years and a sex (0 male .. 1 female); the same table
    the body model uses (bpy_lib.character_body.HEIGHTS)."""
    table = [(0, 50, 49), (1, 76, 74), (2, 87, 86), (4, 103, 102), (6, 116, 115), (8, 128, 127), (10, 138, 138),
             (12, 149, 151), (14, 164, 160), (16, 174, 163), (18, 176, 164), (30, 177, 164), (50, 176, 163),
             (70, 173, 160), (90, 169, 156)]
    age = min(max(float(age), 0.0), 90.0)
    for (a0, m0, f0), (a1, m1, f1) in zip(table, table[1:]):
        if age <= a1:
            t = (age - a0) / (a1 - a0)
            male, fem = m0 + t * (m1 - m0), f0 + t * (f1 - f0)
            return male + float(female) * (fem - male)
    return table[-1][1] + float(female) * (table[-1][2] - table[-1][1])


def _ancestry(raw, assumed):
    given = raw.get("ancestry")
    if not given:
        assumed.append("ancestry: an even mix (not given)")
        return {k: 0.333 for k in ANCESTRY}
    if isinstance(given, str):
        key = given.strip().lower()
        if key not in ANCESTRY:
            raise CharacterError(f"character.ancestry must be one of {', '.join(ANCESTRY)} or shares of them, "
                                 f"not {given!r}")
        return {k: (1.0 if k == key else 0.0) for k in ANCESTRY}
    if not isinstance(given, dict):
        raise CharacterError("character.ancestry must be a name or {african, asian, caucasian} shares")
    shares = {}
    for k in ANCESTRY:
        v = given.get(k, 0)
        try:
            shares[k] = max(0.0, float(v))
        except (TypeError, ValueError):
            raise CharacterError(f"character.ancestry.{k} must be a number") from None
    unknown = set(given) - set(ANCESTRY)
    if unknown:
        raise CharacterError(f"character.ancestry has unknown keys {sorted(unknown)}: use {', '.join(ANCESTRY)}")
    total = sum(shares.values())
    if total <= 0:
        raise CharacterError("character.ancestry shares add up to nothing")
    return {k: round(v / total, 3) for k, v in shares.items()}


def _main_ancestry(shares):
    best = max(shares, key=shares.get)
    return best if shares[best] >= 0.6 else "mixed"


@functools.lru_cache(maxsize=1)
def face_sliders():
    """The local sliders the body model has (nose, eyes, mouth, chin, head shape, torso...), named as
    bpy_lib.character_body.local_targets names them; each runs from -1 to 1."""
    path = config.ROOT / str(config.get("characters", "data", "assets_cc0/anny_body")) / "targets" / "target.json"
    if not path.is_file():
        return ()
    out = []
    for key, group in json.loads(path.read_text(encoding="utf-8")).items():
        if key == "genitals":
            continue
        for cat in group.get("categories", []):
            opp = cat.get("opposites") or {}
            if opp.get("negative-unsided") and opp.get("positive-unsided"):
                out.append(cat["name"])
            elif opp.get("negative-left") and opp.get("positive-left"):
                out.append(cat["name"])  # sided: one value moves both sides
    return tuple(sorted(out))


def _grey_for(age):
    return "#d8d6d2" if age >= 72 else "#b9b6b0" if age >= 60 else None


def default_look(sheet):
    """A plain look for the age and sex when the sheet gives none: what most people wear."""
    age, sex = sheet["age"], sheet["sex"]
    hair_colour = _grey_for(age) or "#3b2a1e"
    if age < 2:
        return {"hair": {"style": "buzz", "colour": "#6b4a2b"}, "outfit": [{"type": "onesie", "colour": "#a9cbe6"}]}
    hair = {"style": "ponytail" if sex == "female" and age < 60 else "bob" if sex == "female" else "short",
            "colour": hair_colour}
    if sex == "female":
        outfit = [{"type": "blouse", "colour": "#e9e4dc"}, {"type": "jeans", "colour": "#3d5a80"},
                  {"type": "flats", "colour": "#3a2a22"}]
    else:
        outfit = [{"type": "tshirt", "colour": "#e8e8e8"}, {"type": "jeans", "colour": "#3d5a80"},
                  {"type": "sneakers", "colour": "#f2f2f2"}]
    return {"hair": hair, "outfit": outfit}


def clean_look(raw, sheet, assumed):
    """The structured look (hair, outfit, accessories, brows, face) of a sheet, checked; what was
    filled in goes to assumed. Raises CharacterError naming the field."""
    out = {}
    given = {k: raw.get(k) for k in ("hair", "outfit", "accessories", "brows", "face") if raw.get(k) is not None}
    plain = default_look(sheet)
    if "outfit" not in given:
        assumed.append("outfit: " + ", ".join(g["type"].replace("_", " ") for g in plain["outfit"]))
    if "hair" not in given:
        assumed.append(f"hair: {plain['hair']['style']}")
    outfit = given.get("outfit", plain["outfit"])
    if not isinstance(outfit, list) or len(outfit) > MAX_GARMENTS:
        raise CharacterError(f"character.outfit must be a list of at most {MAX_GARMENTS} garments")
    clean_outfit, kinds = [], {}
    for i, g in enumerate(outfit):
        where = f"character.outfit[{i}]"
        if not isinstance(g, dict) or g.get("type") not in GARMENT_TYPES:
            raise CharacterError(f"{where}.type must be one of {', '.join(GARMENT_TYPES)}, not "
                                 f"{(g or {}).get('type') if isinstance(g, dict) else g!r}")
        item = {"type": g["type"], "colour": _hex(g.get("colour") or "#777777", f"{where}.colour")}
        if g["type"] in ("skirt", "dress"):
            length = g.get("length") or "knee"
            if length not in SKIRT_LENGTHS:
                raise CharacterError(f"{where}.length must be one of {', '.join(SKIRT_LENGTHS)}")
            item["length"] = length
        kind = GARMENT_KIND[g["type"]]
        slot = "outer" if g["type"] in OUTER else kind
        if slot in kinds:
            raise CharacterError(f"{where}: two {slot} garments ({kinds[slot]} and {g['type']}); one {slot} at most")
        kinds[slot] = g["type"]
        clean_outfit.append(item)
    if "onesie" in kinds.values() and len(kinds) > 2:
        raise CharacterError("character.outfit: a onesie is worn alone (shoes at most)")
    if "dress" in kinds and ("top" in kinds or "bottom" in kinds):
        raise CharacterError("character.outfit: a dress takes no other top or bottom (an outer layer and shoes are fine)")
    order = {"top": 0, "dress": 0, "onesie": 0, "bottom": 1, "shoes": 2, "outer": 3}
    clean_outfit.sort(key=lambda g: order["outer" if g["type"] in OUTER else GARMENT_KIND[g["type"]]])
    out["outfit"] = clean_outfit
    hair = given.get("hair", plain["hair"])
    if not isinstance(hair, dict) or hair.get("style") not in HAIR_STYLES:
        raise CharacterError(f"character.hair.style must be one of {', '.join(HAIR_STYLES)}")
    out["hair"] = {"style": hair["style"],
                   "colour": _hex(hair.get("colour") or _grey_for(sheet["age"]) or "#3b2a1e", "character.hair.colour")}
    if hair.get("length") is not None and hair["style"] == "long":
        out["hair"]["length"] = _num(hair, "length", 0.0, 0.4, "character.hair")
    brows = given.get("brows") or {}
    if not isinstance(brows, dict):
        raise CharacterError("character.brows must be {colour, thickness, arch}")
    age_thick = 0.05 if sheet["age"] < 2 else 0.3 if sheet["age"] < 12 else 0.5
    out["brows"] = {"colour": _hex(brows.get("colour") or (_lighter(out["hair"]["colour"]) if sheet["age"] < 2
                                                             else out["hair"]["colour"]), "character.brows.colour"),
                    "thickness": _num(brows, "thickness", 0, 1, "character.brows") if brows.get("thickness") is not None
                    else age_thick,
                    "arch": _num(brows, "arch", 0, 1, "character.brows") if brows.get("arch") is not None else 0.5}
    accessories = given.get("accessories") or []
    if not isinstance(accessories, list):
        raise CharacterError("character.accessories must be a list")
    out["accessories"] = []
    for i, a in enumerate(accessories):
        if not isinstance(a, dict) or a.get("type") not in ACCESSORIES:
            raise CharacterError(f"character.accessories[{i}].type must be one of {', '.join(ACCESSORIES)}")
        item = {"type": a["type"], "shape": a.get("shape") or "round", "frame": a.get("frame") or "thin",
                "colour": _hex(a.get("colour") or "#1b1b1b", f"character.accessories[{i}].colour")}
        if item["shape"] not in ("round", "square") or item["frame"] not in ("thin", "thick"):
            raise CharacterError(f"character.accessories[{i}]: shape round or square, frame thin or thick")
        out["accessories"].append(item)
    face = given.get("face") or {}
    if not isinstance(face, dict):
        raise CharacterError("character.face must be {slider: -1..1}")
    names = set(face_sliders())
    out["face"] = {}
    for k, v in face.items():
        if names and k not in names:
            close = [n for n in names if n.split("-")[0] == str(k).split("-")[0]][:6]
            raise CharacterError(f"character.face has no slider {k!r}" + (f"; for example {', '.join(close)}"
                                                                         if close else ""))
        out["face"][k] = round(_num(face, k, -1.0, 1.0, "character.face"), 3)
    return out


def _lighter(hex_colour, amount=0.45):
    c = [int(hex_colour[i:i + 2], 16) for i in (1, 3, 5)]
    return "#" + "".join(f"{round(x + (255 - x) * amount):02x}" for x in c)


def clean(raw):
    """Check a character sheet and fill in what it leaves out. Returns (sheet, assumed): assumed
    lists each value chosen by default, in words. Raises CharacterError naming the field."""
    if not isinstance(raw, dict):
        raise CharacterError("the character must be an object: {\"age\": 30, \"sex\": \"female\", ...}")
    assumed = []
    out = {}
    out["name"] = str(raw.get("name") or "").strip()[:60] or "character"
    if raw.get("age") is None:
        assumed.append("age: 30 (not given)")
        out["age"] = 30.0
    else:
        out["age"] = _num(raw, "age", *AGE_RANGE)
    out["female"] = _sex(raw, assumed)
    out["sex"] = "female" if out["female"] > 0.75 else "male" if out["female"] < 0.25 else "neutral"
    if raw.get("height_cm") is not None:
        out["height_cm"] = _num(raw, "height_cm", *HEIGHT_RANGE)
    else:
        out["height_cm"] = round(typical_height_cm(out["age"], out["female"]), 1)
        assumed.append(f"height: {out['height_cm']:g} cm, the median for that age and sex")
    build = str(raw.get("build") or "").strip().lower()
    if build and build not in BUILDS:
        raise CharacterError(f"character.build must be one of {', '.join(BUILDS)}, not {build!r}")
    if not build and raw.get("muscle") is None and raw.get("weight") is None:
        assumed.append("build: average")
    out["build"] = build or "average"
    muscle, weight = BUILDS[out["build"]]
    out["muscle"] = _num(raw, "muscle", 0.0, 1.0) if raw.get("muscle") is not None else muscle
    out["weight"] = _num(raw, "weight", 0.0, 1.0) if raw.get("weight") is not None else weight
    out["proportions"] = _num(raw, "proportions", 0.0, 1.0) if raw.get("proportions") is not None else 0.5
    out["ancestry"] = _ancestry(raw, assumed)
    main = _main_ancestry(out["ancestry"])
    if raw.get("skin"):
        out["skin"] = _hex(raw["skin"], "character.skin")
    else:
        out["skin"] = SKIN[main]
        assumed.append(f"skin tone {out['skin']}")
    out["eyes"] = _hex(raw["eyes"], "character.eyes") if raw.get("eyes") else EYES[main]
    style = str(raw.get("style") or "realistic").strip().lower()
    if style not in STYLES:
        raise CharacterError(f"character.style must be one of {', '.join(STYLES)}, not {style!r}")
    out["style"] = style
    out["look"] = str(raw.get("look") or "").strip()[:600]  # hair, clothes, face in words
    if out["age"] < 3 and out["build"] in ("athletic", "muscular"):
        raise CharacterError(f"character.build {out['build']!r} does not fit a {out['age']:g}-year-old: "
                             "use slim, average or chubby")
    out.update(clean_look(raw, out, assumed))
    from . import clips as clip_lib
    try:
        out["clips"] = clip_lib.clean(raw.get("clips"))
    except clip_lib.ClipError as exc:
        raise CharacterError(str(exc)) from None
    if raw.get("clips") in (None, "", "default") and out["clips"]:
        assumed.append(f"clips: the everyday set ({len(out['clips'])})")
    return out, assumed


STYLES_FILE = config.PACKAGE / "character_styles.yaml"


@functools.lru_cache(maxsize=1)
def _styles():
    import yaml
    return yaml.safe_load(STYLES_FILE.read_text(encoding="utf-8")) or {}


def style(name):
    """A drawing style's profile (character_styles.yaml): proportions, face, shading, outline,
    saturation, hair volume."""
    return _styles().get(name) or _styles()["realistic"]


def body(sheet):
    """The body model's sliders for a cleaned sheet (bpy_lib.character_body.coefficients), with the
    style's proportions and face sliders added to the sheet's own."""
    prof = style(sheet.get("style", "realistic"))
    local = dict(prof.get("face") or {})
    for k, v in (sheet.get("face") or {}).items():
        local[k] = max(-1.0, min(1.0, local.get(k, 0.0) + v))
    return {"age": sheet["age"], "female": sheet["female"], "muscle": sheet["muscle"], "weight": sheet["weight"],
            "proportions": sheet["proportions"], "height_cm": sheet["height_cm"], **sheet["ancestry"],
            "local": local, "style_proportions": dict(prof.get("proportions") or {})}


def _saturate(hex_colour, amount):
    import colorsys
    r, g, b = (int(hex_colour[i:i + 2], 16) / 255 for i in (1, 3, 5))
    h, lum, s = colorsys.rgb_to_hls(r, g, b)
    r, g, b = colorsys.hls_to_rgb(h, lum, max(0.0, min(1.0, s * amount)))
    return "#" + "".join(f"{round(x * 255):02x}" for x in (r, g, b))


def look(sheet):
    """What Blender dresses the character in: the sheet's look with the style's colours and hair
    volume, and how it is shaded."""
    prof = style(sheet.get("style", "realistic"))
    sat = float(prof.get("saturation", 1.0))
    paint = (lambda c: _saturate(c, sat)) if sat != 1.0 else (lambda c: c)
    return {"outfit": [{**g, "colour": paint(g["colour"])} for g in sheet.get("outfit") or []],
            "hair": {**sheet["hair"], "colour": paint(sheet["hair"]["colour"]),
                     "volume": float(prof.get("hair_volume", 1.0))} if sheet.get("hair") else None,
            "brows": sheet.get("brows"), "accessories": sheet.get("accessories") or [],
            "skin": paint(sheet["skin"]), "eyes": sheet["eyes"],
            "shading": prof.get("shading", "pbr"), "outline": float(prof.get("outline", 0) or 0),
            "iris_deg": float(prof.get("iris_deg", 30)), "glint": bool(prof.get("glint", False))}


def key(sheet):
    """A short id of exactly this character: the same sheet always gives the same key."""
    keep = {k: v for k, v in sheet.items() if k != "name"}
    return hashlib.sha1(json.dumps(keep, sort_keys=True).encode()).hexdigest()[:12]


def age_words(age):
    if age < 1:
        return f"{round(age * 12)}-month-old"
    return f"{age:g}-year-old"


def who(sheet):
    """The person in a few words: "a 30-year-old man", "an 8-year-old girl", "a 6-month-old baby"."""
    age, sex = sheet["age"], sheet["sex"]
    if age < 2:
        noun = "baby"
    elif age < 13:
        noun = {"female": "girl", "male": "boy"}.get(sex, "child")
    elif age < 18:
        noun = {"female": "teenage girl", "male": "teenage boy"}.get(sex, "teenager")
    else:
        noun = {"female": "woman", "male": "man"}.get(sex, "person")
    words = age_words(age)
    article = "an" if words[0] in "8" or words.startswith(("11-", "18-")) else "a"
    return f"{article} {words} {noun}"


def describe(sheet):
    """One line: who, how tall, what build, what style."""
    build = "" if sheet["build"] == "average" else f", {sheet['build']}"
    return f"{who(sheet)}, {sheet['height_cm']:g} cm{build}, {sheet['style']}"


def catalogue():
    """What a character sheet takes, for the designer agent and GET /v1/characters."""
    return {
        "name": {"type": "text", "about": "a short name for the character"},
        "age": {"unit": "years", "min": AGE_RANGE[0], "max": AGE_RANGE[1], "default": 30,
                "about": "0.5 is a six-month-old baby, 8 a child, 16 a teenager, 80 an old person"},
        "sex": {"choices": ["female", "male", "neutral"], "default": "neutral"},
        "height_cm": {"unit": "cm", "min": HEIGHT_RANGE[0], "max": HEIGHT_RANGE[1],
                      "default": "the median for the age and sex"},
        "build": {"choices": list(BUILDS), "default": "average"},
        "muscle": {"min": 0, "max": 1, "default": "from build", "about": "0.5 average; overrides build"},
        "weight": {"min": 0, "max": 1, "default": "from build", "about": "0.5 average; overrides build"},
        "proportions": {"min": 0, "max": 1, "default": 0.5, "about": "0 idealised, 1 uncommon"},
        "ancestry": {"choices": list(ANCESTRY), "about": "a name, or shares {african, asian, caucasian}",
                     "default": "an even mix"},
        "skin": {"type": "colour", "default": "a typical tone for the ancestry"},
        "eyes": {"type": "colour", "default": "a typical colour for the ancestry"},
        "style": {"choices": list(STYLES), "default": "realistic",
                  "about": {k: style(k).get("about") for k in STYLES}},
        "look": {"type": "text", "about": "hair, clothes and face in words"},
        "outfit": {"about": "[{type, colour, length}]: one inner top, one outer top, one bottom (or a dress, or a "
                            "onesie alone), one pair of shoes", "types": list(GARMENT_TYPES),
                   "outer": list(OUTER), "lengths": list(SKIRT_LENGTHS), "default": "plain clothes for the age"},
        "hair": {"about": "{style, colour}", "styles": list(HAIR_STYLES)},
        "brows": {"about": "{colour, thickness 0..1, arch 0..1}", "default": "the hair's colour, thin for a child"},
        "accessories": {"about": "[{type: glasses, shape: round|square, frame: thin|thick, colour}]"},
        "face": {"about": "{slider: -1..1}", "sliders": list(FACE_SLIDERS)},
        "clips": {"about": '"default" (an everyday set), "all", or a list of clip names from the animation library',
                  "default": "the everyday set", "choices": _clip_names()},
    }


def _clip_names():
    from . import clips as clip_lib
    return clip_lib.available()


def data_root():
    return str(config.ROOT / str(config.get("characters", "data", "assets_cc0/anny_body")))


def cache_dir():
    return str(config.data_dir() / "body_cache")


def available():
    """Whether the body data is on this PC."""
    return (config.ROOT / str(config.get("characters", "data", "assets_cc0/anny_body")) / "3dobjs" / "base.obj").is_file()


def build_step(sheet, clips=None):
    """The Blender step that builds, rigs, colours and animates the character of a cleaned sheet:
    the sheet's library clips moved onto its skeleton, or the rig_test clip when there are none."""
    from . import clips as clip_lib
    library = sheet.get("clips") or []
    return {"kind": "character", "body": body(sheet), "sheet": sheet, "look": look(sheet), "root": data_root(),
            "cache": cache_dir(), "library": str(clip_lib.library_path()) if library else None,
            "library_clips": list(library),
            "clips": list(clips or ([] if library else ["rig_test"])), "label": f"build {who(sheet)}"}


def report_step():
    return {"kind": "rig_report", "label": "check the skeleton and the skinning"}
