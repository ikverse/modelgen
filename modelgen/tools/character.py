"""Characters (people) built from a character sheet, with no build team.

A character sheet says who the person is: age in years, sex, height, build, ancestry, skin tone,
style, and for later stages their hair, clothes and face. `clean` checks a sheet and fills in what
it leaves out (every filled-in value is listed in `assumed`); `body` turns it into the body model's
sliders (bpy_lib.character_body); `steps` gives the Blender job that builds, rigs, dresses,
animates, measures and exports the character in one run.

Everything here is plain Python: the host checks a sheet without Blender, and the same numbers
reach Blender in the job.
"""

import hashlib
import json

from .. import config

SEXES = {"female": 1.0, "woman": 1.0, "girl": 1.0, "f": 1.0, "male": 0.0, "man": 0.0, "boy": 0.0, "m": 0.0,
         "neutral": 0.5, "androgynous": 0.5}
# build -> (muscle, weight) sliders, 0..1 with 0.5 average
BUILDS = {"skinny": (0.15, 0.0), "slim": (0.3, 0.15), "average": (0.5, 0.5), "athletic": (0.9, 0.4),
          "muscular": (1.0, 0.65), "stocky": (0.7, 0.8), "chubby": (0.3, 0.85), "heavy": (0.4, 1.0)}
STYLES = ("realistic",)  # stage 2 adds the stylised looks
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
    out["look"] = str(raw.get("look") or "").strip()[:600]  # hair, clothes, face in words: used from stage 2
    if out["age"] < 3 and out["build"] in ("athletic", "muscular"):
        raise CharacterError(f"character.build {out['build']!r} does not fit a {out['age']:g}-year-old: "
                             "use slim, average or chubby")
    return out, assumed


def body(sheet):
    """The body model's sliders for a cleaned sheet (bpy_lib.character_body.coefficients)."""
    return {"age": sheet["age"], "female": sheet["female"], "muscle": sheet["muscle"], "weight": sheet["weight"],
            "proportions": sheet["proportions"], "height_cm": sheet["height_cm"], **sheet["ancestry"]}


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
        "style": {"choices": list(STYLES), "default": "realistic"},
        "look": {"type": "text", "about": "hair, clothes and face in words"},
    }


def data_root():
    return str(config.ROOT / str(config.get("characters", "data", "assets_cc0/anny_body")))


def cache_dir():
    return str(config.data_dir() / "body_cache")


def available():
    """Whether the body data is on this PC."""
    return (config.ROOT / str(config.get("characters", "data", "assets_cc0/anny_body")) / "3dobjs" / "base.obj").is_file()


def build_step(sheet, clips=None):
    """The Blender step that builds, rigs, colours and animates the character of a cleaned sheet."""
    return {"kind": "character", "body": body(sheet), "sheet": sheet, "root": data_root(), "cache": cache_dir(),
            "clips": list(clips or ["rig_test"]), "label": f"build {who(sheet)}"}


def report_step():
    return {"kind": "rig_report", "label": "check the skeleton and the skinning"}
