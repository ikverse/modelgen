"""A car from a request in words: "red 90s SUV", "small blue hatchback", "sports car".

The request names a type, perhaps an era and a colour. A base car of that type is picked from the
catalogue (modelgen.tools.car_library), then made into a new car: its proportions moved within safe
limits, its paint changed, and often another car's wheels put on (bpy_lib.shells.car_remix). The
seed decides every choice, so the same request with a new seed is a different car, and the same
seed is the same car again.

    python -m modelgen.tools.car_request "red 90s suv"
    python -m modelgen.tools.car_request "sports car" --count 4
    python -m modelgen.tools.car_request "blue sedan" --seed 7

Each car is written to library/generated/remix/ as a .glb, a picture and a .json saying how it was
made. Without --seed, the first seed not yet used for that request is taken.
"""

import json
import random
import re
import sys
from pathlib import Path

from .. import config
from ..bpy_lib.shells import car_remix
from . import car_library

OUT = config.ROOT / "library" / "generated" / "remix"
# words in a request -> the base types they may be built from, the first being the best match
TYPE_WORDS = {
    "station wagon": ("wagon",), "wagon": ("wagon",), "estate": ("wagon",),
    "pickup truck": ("pickup",), "pickup": ("pickup",), "truck": ("pickup",),
    "police car": ("police",), "police": ("police",), "cop car": ("police",),
    "taxi": ("taxi",), "cab": ("taxi",),
    "off roader": ("offroad", "suv"), "offroader": ("offroad", "suv"), "off road": ("offroad", "suv"),
    "offroad": ("offroad", "suv"), "4x4": ("offroad", "suv"), "jeep": ("offroad", "suv"),
    "suv": ("suv", "offroad"), "crossover": ("suv",),
    "minivan": ("minivan",), "mpv": ("minivan",), "people carrier": ("minivan",),
    "microbus": ("van",), "bus": ("van",), "delivery van": ("van",), "van": ("van", "minivan"),
    "hatchback": ("hatchback", "compact"), "hatch": ("hatchback", "compact"),
    "compact car": ("compact", "hatchback"), "compact": ("compact", "hatchback"),
    "city car": ("compact", "microcar"), "small car": ("compact", "hatchback"),
    "hypercar": ("sports",), "supercar": ("sports",), "sports car": ("sports",), "sportscar": ("sports",),
    "sport car": ("sports",), "sports": ("sports",),
    "race car": ("race",), "racing car": ("race",), "formula": ("race",), "rally car": ("rally",),
    "rally": ("rally",), "muscle car": ("muscle",), "muscle": ("muscle",),
    "coupe": ("coupe", "sports"), "convertible": ("convertible",), "cabrio": ("convertible",),
    "roadster": ("convertible",), "sedan": ("sedan", "electric"), "saloon": ("sedan", "electric"),
    "family car": ("sedan", "wagon", "hatchback"), "limousine": ("limousine",), "limo": ("limousine",),
    "electric car": ("electric",), "electric": ("electric",), "ev": ("electric",),
    "lowrider": ("lowrider",), "dune buggy": ("buggy",), "buggy": ("buggy",), "microcar": ("microcar",),
    "bubble car": ("microcar",), "tuk tuk": ("tuktuk",), "tuktuk": ("tuktuk",), "rickshaw": ("tuktuk",),
    "classic car": ("classic",), "vintage car": ("classic",),
}
HYPER_WORDS = ("hypercar", "supercar")   # no such base car: a recent sports car is made lower, wider and longer
HYPER_ERAS = ("modern", "1990s", "1980s")
STYLE_WORDS = {"low poly": "quaternius", "lowpoly": "quaternius", "flat": "quaternius",
               "angular": "quaternius"}  # the angular, flat-shaded cars, only when asked for
DEFAULT_LIBRARY = "grab3d"               # otherwise every car is of the one soft, rounded style
WHITEWALL_ERAS = ("1950s", "1960s", "classic")
RIMS = {"silver": (0.78, 0.80, 0.82), "dark": (0.17, 0.17, 0.19)}
MAX_TRIANGLES = 22000                    # a heavier car's body is thinned towards this ...
KEEP_HEAVY = ("grab3d/police-car", "grab3d/taxi")   # ... except these: thinning bends the lettering on their doors
ORDINARY = ("sedan", "hatchback", "suv", "wagon", "coupe", "compact", "sports", "minivan", "pickup")
OLD_ERAS = ("1950s", "1960s", "1970s", "classic")
ERA_WORDS = {"classic": OLD_ERAS, "vintage": OLD_ERAS, "retro": OLD_ERAS + ("1980s",), "old": OLD_ERAS,
             "modern": ("modern",), "new": ("modern",)}
COLOURS = {"red": (0.62, 0.07, 0.06), "dark red": (0.36, 0.05, 0.06), "blue": (0.10, 0.24, 0.52),
           "dark blue": (0.06, 0.11, 0.26), "light blue": (0.45, 0.66, 0.84), "green": (0.11, 0.36, 0.20),
           "dark green": (0.07, 0.20, 0.13), "black": (0.05, 0.05, 0.06), "white": (0.92, 0.92, 0.90),
           "silver": (0.70, 0.71, 0.73), "grey": (0.36, 0.37, 0.39), "gray": (0.36, 0.37, 0.39),
           "yellow": (0.90, 0.72, 0.10), "orange": (0.88, 0.40, 0.08), "purple": (0.32, 0.14, 0.44),
           "pink": (0.88, 0.50, 0.62), "brown": (0.30, 0.18, 0.11), "beige": (0.78, 0.71, 0.58),
           "teal": (0.08, 0.40, 0.42), "gold": (0.72, 0.58, 0.26)}
PALETTE = ("red", "dark red", "blue", "dark blue", "green", "dark green", "black", "white", "silver", "grey",
           "yellow", "orange", "beige", "teal")
LIVERY = ("police", "taxi")          # their paint is their job: only repainted when a colour is asked for
ODD_WHEELS = ("buggy", "race", "tuktuk", "microcar", "limousine")
WHEEL_FIT = 0.12                     # a donor's wheel within this fraction of the car's wheel size
SWAP_CHANCE = 0.6


def _words(text):
    return " " + re.sub(r"[^a-z0-9]+", " ", text.lower()).strip() + " "


def _longest(text, table):
    """The longest key of a table found as whole words in the text, or None."""
    for key in sorted(table, key=len, reverse=True):
        if f" {key} " in text:
            return key
    return None


def parse(request):
    """What a request asks for: {"types", "eras", "colour", "colour_name", "library", "profile",
    "note"}; types, eras, colour, profile and note are None when the request does not say."""
    text = _words(request)
    style = _longest(text, STYLE_WORDS)
    if style:
        text = text.replace(f" {style} ", " ")
    word = _longest(text, TYPE_WORDS)
    eras = None
    m = re.search(r" (?:19)?([5-9]0) ?s ", text)
    if m:
        eras = (f"19{m.group(1)}s",)
    else:
        era_word = _longest(text, ERA_WORDS)
        if era_word and not (word and era_word in word):
            eras = ERA_WORDS[era_word]
    hyper = word in HYPER_WORDS
    if hyper:
        eras = HYPER_ERAS
    colour = _longest(text, COLOURS)
    note = (f"there is no {word} among the base cars; a recent sports car made lower, wider and longer is used"
            if hyper else None)
    return {"types": TYPE_WORDS[word] if word else None, "eras": eras,
            "colour": COLOURS[colour] if colour else None, "colour_name": colour,
            "library": STYLE_WORDS[style] if style else DEFAULT_LIBRARY, "styled": bool(style),
            "profile": "hyper" if hyper else None, "note": note}


def revised(want, changes):
    """What is asked for after a change in words: what the change names (a type, an era, a colour, a
    style) replaces the old, the rest stays. Returns (the new want, whether the change named anything)."""
    new = parse(changes)
    out = dict(want)
    if new["types"]:
        out.update(types=new["types"], profile=new["profile"], note=new["note"])
        if new["profile"]:
            out["eras"] = new["eras"]
    if new["eras"] and not new["profile"]:
        out["eras"] = new["eras"]
    if new["colour"]:
        out.update(colour=new["colour"], colour_name=new["colour_name"])
    if new["styled"]:
        out.update(library=new["library"], styled=True)
    return out, bool(new["types"] or new["eras"] or new["colour"] or new["styled"])


def candidates(want, catalogue):
    """The base cars a request may be built from, best matches only: of the request's style when
    that style has the type at all, the first asked type that has any car, in the asked era when it
    has one there."""
    styled = [e for e in catalogue if e["library"] == want.get("library", DEFAULT_LIBRARY)]
    if want.get("library", DEFAULT_LIBRARY) != DEFAULT_LIBRARY:
        pool = _typed(want, styled)
        if pool:
            return pool
        styled = [e for e in catalogue if e["library"] == DEFAULT_LIBRARY]
    return _typed(want, styled)


def _typed(want, catalogue):
    if want["types"]:
        pool = []
        for kind in want["types"]:
            pool = [e for e in catalogue if e["type"] == kind]
            if pool:
                break
    else:
        pool = [e for e in catalogue if e["type"] in ORDINARY]
    if want["eras"]:
        in_era = [e for e in pool if e["era"] in want["eras"]]
        pool = in_era or pool
    return pool


def donors(base, catalogue):
    """The base cars whose wheels fit this one. The heavy cars' wheels are heavy too, and are not lent:
    a light body would have to be thinned to pay for them."""
    r = base["wheel_radius"]
    return [e for e in catalogue
            if e["name"] != base["name"] and e["triangles"] <= MAX_TRIANGLES and e["library"] == base["library"] and e["wheels"] == base["wheels"] == 4
            and e["type"] not in ODD_WHEELS and abs(e["wheel_radius"] - r) <= WHEEL_FIT * r]


def plan(request, seed, catalogue=None, want=None):
    """Every choice for one car: the base, the shape, the paint and the wheels. want: what is asked
    for, when it is no longer just the request's words (see revised); the seed's choices stay the
    same wherever the want leaves them free, so a change of colour repaints the same car."""
    catalogue = car_library.load() if catalogue is None else catalogue
    want = parse(request) if want is None else want
    pool = candidates(want, catalogue)
    if not pool:
        raise ValueError(f"no base car fits {request!r}")
    rng = random.Random(f"{_words(request).strip()}|{seed}")
    # with no era asked for, a recent car is three times as likely as one from before the 1980s
    weights = [1.0 if want["eras"] or e["era"] not in OLD_ERAS else 1.0 / 3 for e in pool]
    base = rng.choices(pool, weights=weights)[0]
    profile = want["profile"]
    limits = car_remix.PROFILES[profile]
    shape = {k: rng.triangular(lo, hi, min(hi, max(lo, car_remix.NEUTRAL[k]))) for k, (lo, hi) in limits.items()}
    shape = {k: round(v, 4) for k, v in car_remix.clamp_shape(shape, profile).items()}
    own = rng.choice(PALETTE)                # drawn whether used or not: the later choices do not shift
    shade = rng.uniform(0.92, 1.08)          # a little lighter or darker, never another tint
    if want["colour"]:
        name, paint = want["colour_name"], want["colour"]
    elif base["type"] in LIVERY:
        name, paint = None, None
    else:
        name, paint = own, COLOURS[own]
    if paint:
        paint = tuple(round(min(1.0, v * shade), 3) for v in paint)
    fits = donors(base, catalogue) if base["type"] not in ODD_WHEELS else []
    wheels = rng.choice(fits)["name"] if fits and rng.random() < SWAP_CHANCE else None
    rim = rng.choice(sorted(RIMS))
    finish = {"rim": RIMS[rim], "rim_name": rim, "whitewalls": base["era"] in WHITEWALL_ERAS} if wheels else None
    return {"request": request, "seed": seed, "base": base["name"], "type": base["type"], "era": base["era"],
            "shape": shape, "profile": profile, "paint": paint, "paint_name": name, "wheels": wheels,
            "finish": finish, "max_triangles": None if base["name"] in KEEP_HEAVY else MAX_TRIANGLES,
            "note": want["note"], "want": want}


def changed(made, changes, catalogue=None):
    """The plan for a car after a change in words. A change that names a colour, type, era or style
    keeps the car's seed, so everything it does not name stays; any other change is taken as "not
    this one" and gives the next seed's car."""
    want, named = revised(made["want"], changes)
    return plan(made["request"], made["seed"] if named else made["seed"] + 1, catalogue, want=want)


def another(made, catalogue=None):
    """The next car for the same request."""
    return plan(made["request"], made["seed"] + 1, catalogue, want=made["want"])


def describe(made):
    """One plain sentence about a planned car."""
    paint = f"{made['paint_name']} " if made["paint_name"] else ""
    base = made["base"].split("/", 1)[1].replace("-", " ").replace("_", " ")
    wheels = ", on another car's wheels" if made["wheels"] else ""
    return f"a {paint}{made['type']} made from the free base car \"{base}\" ({made['era']}){wheels}"


def slug(request):
    return re.sub(r"[^a-z0-9]+", "_", request.lower()).strip("_")[:40] or "car"


def next_seed(request):
    used = {int(p.stem.rsplit("_", 1)[1]) for p in OUT.glob(f"{slug(request)}_*.json")
            if p.stem.rsplit("_", 1)[1].isdigit()} if OUT.is_dir() else set()
    return next(n for n in range(1, 100000) if n not in used)


def build(request, seed=None, out=None, picture=True):
    """Make one car for a request; returns its record (the plan, the measurements and the files)."""
    out = OUT if out is None else out
    out.mkdir(parents=True, exist_ok=True)
    catalogue = car_library.load()
    seed = next_seed(request) if seed is None else seed
    made = plan(request, seed, catalogue)
    stem = out / f"{slug(request)}_{seed:03d}"
    record = make(made, stem.with_suffix(".glb"), picture=stem.with_suffix(".png") if picture else None,
                  catalogue=catalogue)
    stem.with_suffix(".json").write_text(json.dumps(record, indent=1), encoding="utf-8")
    return record


def make(made, glb, picture=None, blend=None, catalogue=None):
    """Build a planned car in Blender: its .glb, and when asked a picture and a .blend the project's
    renders can open. Returns the plan with the measurements and the files."""
    catalogue = car_library.load() if catalogue is None else catalogue
    by_name = {e["name"]: e for e in catalogue}
    job = {"base": str(car_library.ASSETS / by_name[made["base"]]["file"]), "shape": made["shape"],
           "profile": made["profile"], "finish": made["finish"], "max_triangles": made["max_triangles"],
           "paint": made["paint"], "glb": str(glb), "blend": str(blend) if blend else None,
           "wheels": str(car_library.ASSETS / by_name[made["wheels"]]["file"]) if made["wheels"] else None,
           "picture": str(picture) if picture else None}
    lines, code = car_library.run_blender(f"car_remix.remix({job!r})", timeout=900)
    result = car_library.tagged(lines, "MG_REMIX ")
    if code != 0 or not result or not Path(glb).is_file():
        raise RuntimeError("the car was not built:\n" + "\n".join(lines[-30:]))
    return {**made, "glb": job["glb"], "picture": job["picture"], "blend": job["blend"],
            "before": result[0]["before"], "after": result[0]["after"],
            "wheels_swapped": result[0].get("wheels_swapped", False),
            "repainted": result[0].get("repainted", False), "lightened": result[0].get("lightened", False)}


def main(args):
    if not args:
        print(__doc__)
        return
    seed = count = None
    rest = []
    it = iter(args)
    for a in it:
        if a == "--seed":
            seed = int(next(it))
        elif a == "--count":
            count = int(next(it))
        else:
            rest.append(a)
    request = " ".join(rest)
    for i in range(count or 1):
        r = build(request, seed + i if seed is not None else None)
        a = r["after"]
        if r["note"] and i == 0:
            print("note:", r["note"])
        print(f"{r['glb']}\n  from {r['base']} ({r['type']}, {r['era']}), paint {r['paint_name'] or 'kept'}, "
              f"wheels {r['wheels'] or 'kept'}\n  length {a['length']} m, wheelbase {a['wheelbase']} m, "
              f"height {a['height']} m, {a['triangles']} triangles")


if __name__ == "__main__":
    main(sys.argv[1:])
