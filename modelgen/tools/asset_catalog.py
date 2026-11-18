"""Free ready-made models for objects that are not cars: a catalogue to search, and a game asset
made from one entry (resized, tinted, exported) with no agents.

Two sources, both public domain (CC0):

- Poly Haven (https://polyhaven.com/license): realistic, textured models. The catalogue holds only
  their descriptions, read from Poly Haven's public API; a model's files (the glTF at 1k textures)
  are downloaded the first time it is used, into assets_cc0/objects/polyhaven/<id>/.
- Local packs: any .glb or .gltf under assets_cc0/objects/<library>/ (a Quaternius or Kenney pack
  put there by hand), described by its file name. Nothing is downloaded for these.

    python -m modelgen.tools.asset_catalog --build          # read the API and the packs, write the catalogue
    python -m modelgen.tools.asset_catalog --build --local  # the packs only; keep the Poly Haven entries
    python -m modelgen.tools.asset_catalog wooden chair     # search

Requests to the outside carry nothing about the person or the project: only the model's id, with
the plain client name "ModelGen/1.0".
"""

import json
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from . import agent_tools, car_library
from .car_request import COLOURS

OBJECTS = car_library.ASSETS / "objects"
CATALOG = OBJECTS / "catalog.json"
API = "https://api.polyhaven.com"
USER_AGENT = "ModelGen/1.0"
HOSTS = ("api.polyhaven.com", "dl.polyhaven.org")   # the only hosts ever asked for anything
RESOLUTION = "1k"
MAX_BYTES = 60 * 1024 * 1024                        # one model's files; a bigger set is refused
LIBRARY_NAMES = {"polyhaven": "Poly Haven", "quaternius": "Quaternius", "kenney": "Kenney"}
MODEL_SUFFIXES = (".glb", ".gltf")

STOP = set("a an the and or of for with without in on at to from by as is are be it its this that these those "
           "some any my your our me i we you please want need make build create generate model made get give "
           "show like looks look looking style styled kind type sort one single new nice good simple "
           "3d asset assets game ready low poly lowpoly high quality realistic real stylised stylized "
           "object prop props item thing piece use used using render renders rendering "
           "about around approximately roughly size sized tall high wide long deep big large small little tiny "
           "huge medium cm mm m metre metres meter meters inch inches foot feet".split())
CUTS = set("with without for in on at from by that which like as and".split())   # where an object phrase ends
SYNONYMS = {"couch": "sofa", "settee": "sofa", "stone": "rock", "boulder": "rock", "streetlight": "lamp",
            "lamppost": "lamp", "armchair": "chair", "pot": "pot", "drum": "barrel", "cask": "barrel",
            "box": "box", "trunk": "trunk", "tv": "television", "telly": "television", "bin": "bin",
            "shrub": "bush", "flower": "flower", "desk": "desk", "stool": "stool", "cupboard": "cabinet"}
COLOUR_WORDS = {w for name in COLOURS for w in name.split()} | {"light", "dark", "colour", "color", "coloured",
                                                                 "colored", "tinted", "painted"}
UNITS = {"mm": 0.001, "cm": 0.01, "m": 1.0, "in": 0.0254, "inch": 0.0254, "ft": 0.3048}
MIN_SCORE = 3.0


# --- the catalogue ------------------------------------------------------------------------

def _http(url, timeout=60):
    """The bytes at a Poly Haven address. Any other host is refused."""
    parts = urllib.parse.urlsplit(url)
    if parts.scheme != "https" or parts.hostname not in HOSTS:
        raise RuntimeError(f"refused to download from {parts.hostname or url}: not a Poly Haven address")
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as reply:
            return reply.read()
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise RuntimeError(f"could not reach Poly Haven ({exc})") from exc


def _words(text):
    """Lower-case words of a name or sentence, digits dropped, camelCase split."""
    text = re.sub(r"([a-z])([A-Z])", r"\1 \2", str(text or ""))
    return [w for w in re.sub(r"[^a-z]+", " ", text.lower()).split() if w]


def _single(word):
    """A plain singular of a word, enough to match 'chairs' with 'chair' and 'bushes' with 'bush'."""
    if len(word) > 4 and word.endswith("ies"):
        return word[:-3] + "y"
    if len(word) > 4 and word.endswith(("ches", "shes", "sses", "xes")):
        return word[:-2]
    if len(word) > 3 and word.endswith("s") and not word.endswith(("ss", "us", "is")):
        return word[:-1]
    return word


def _norm(word):
    word = _single(word)
    return SYNONYMS.get(word, word)


def polyhaven_entries():
    """Every Poly Haven model as a catalogue entry (descriptions only; one request to the API)."""
    models = json.loads(_http(f"{API}/assets?t=models").decode("utf-8"))
    out = []
    for key, m in sorted(models.items()):
        dims = m.get("dimensions") or []
        entry = {"id": f"polyhaven/{key}", "library": "polyhaven", "name": m.get("name") or key,
                 "categories": list(m.get("categories") or []), "tags": list(m.get("tags") or []),
                 "style": "realistic", "category": m.get("category") or "",
                 "description": m.get("description") or "", "downloads": int(m.get("download_count") or 0)}
        if len(dims) == 3 and all(isinstance(d, (int, float)) and d > 0 for d in dims):
            # the API gives millimetres as x, y, z of the model as it stands
            entry["size"] = {"width": round(dims[0] / 1000, 4), "depth": round(dims[1] / 1000, 4),
                             "height": round(dims[2] / 1000, 4)}
        if m.get("polycount"):
            entry["triangles"] = int(m["polycount"])  # Poly Haven's own count of faces: a guide, not exact
        out.append(entry)
    return out


def local_entries():
    """Every model file in a pack folder under assets_cc0/objects/ (not Poly Haven's download folder)."""
    out = []
    if not OBJECTS.is_dir():
        return out
    for folder in sorted(p for p in OBJECTS.iterdir() if p.is_dir() and p.name != "polyhaven"):
        for path in sorted(p for p in folder.rglob("*") if p.suffix.lower() in MODEL_SUFFIXES):
            words = _words(path.stem)
            parents = [w for part in path.relative_to(folder).parts[:-1] for w in _words(part)]
            out.append({"id": f"{folder.name}/{path.relative_to(folder).with_suffix('').as_posix()}",
                        "library": folder.name, "name": " ".join(words).title() or path.stem,
                        "categories": sorted(set(parents)), "tags": sorted(set(words)), "style": "lowpoly",
                        "file": path.relative_to(OBJECTS).as_posix()})
    return out


def build(refresh=True):
    """Write the catalogue and return its entries. refresh=False keeps the Poly Haven entries already
    in the catalogue (no request to the API) and only reads the pack folders again."""
    if refresh:
        entries = polyhaven_entries()
    else:
        entries = [e for e in load() if e.get("library") == "polyhaven"]
    entries += local_entries()
    OBJECTS.mkdir(parents=True, exist_ok=True)
    tmp = CATALOG.with_suffix(".tmp")
    tmp.write_text(json.dumps(entries, indent=1, ensure_ascii=False), encoding="utf-8")
    tmp.replace(CATALOG)
    return entries


def load():
    """The catalogue's entries, or [] when it has not been built."""
    if not CATALOG.is_file():
        return []
    return json.loads(CATALOG.read_text(encoding="utf-8"))


def entry_by_id(asset_id, entries=None):
    return next((e for e in (load() if entries is None else entries) if e["id"] == asset_id), None)


# --- search ---------------------------------------------------------------------------------

def object_words(text):
    """(head, modifiers) of the object a request names: the words of its first phrase that say what
    the thing is, the last of them being the head ("an old wooden chair with arms" -> chair, [old,
    wooden]); then the object words of the rest of the text as weaker modifiers."""
    text = str(text or "")
    match = re.search(r"[\"“]([^\"”]{3,80})[\"”]", text)   # a quoted request inside a longer one
    first = match.group(1) if match else re.split(r"[.;:!?\n(]", text.strip(), maxsplit=1)[0]
    phrase, cut = [], False
    for w in _words(first.split(",")[0]):
        if w in CUTS and phrase:
            cut = True
        if cut:
            continue
        if w not in STOP and w not in COLOUR_WORDS and len(w) > 1:
            phrase.append(_norm(w))
    if not phrase:
        return None, []
    rest = [_norm(w) for w in _words(text) if w not in STOP and w not in COLOUR_WORDS and len(w) > 2]
    extra = [w for w in dict.fromkeys(rest) if w not in phrase]
    return phrase[-1], phrase[:-1] + extra[:12]


def _fields(entry):
    name = [_norm(w) for w in _words(entry.get("name")) + _words(entry["id"].split("/", 1)[1])]
    name = list(dict.fromkeys(name))
    joined = {a + b for a, b in zip(name, name[1:])}                      # "tool box" also reads "toolbox"
    split = {p for w in name for p in _split_compound(w)}
    leaf = {_norm(w) for w in _words((entry.get("category") or "").split("/")[-1])}
    tags = {_norm(w) for t in entry.get("tags") or [] for w in _words(t)}
    cats = {_norm(w) for c in entry.get("categories") or [] for w in _words(c)}
    cats |= {_norm(w) for w in _words(entry.get("category"))}
    words = {_norm(w) for w in _words(entry.get("description"))}
    name_words = [w for w in name if w not in ("collection",)]
    return {"name": set(name) | joined | split, "head": name_words[-1] if name_words else "",
            "leaf": leaf, "tags": tags, "cats": cats, "text": words}


COMPOUND_TAILS = ("chair", "table", "box", "lamp", "light", "shelf", "case", "stand", "pot", "bench", "board")


def _split_compound(word):
    """'rockingchair' -> {'chair'}; 'bookshelf' -> {'shelf'}: the tail of a name written as one word."""
    return {tail for tail in COMPOUND_TAILS if word.endswith(tail) and len(word) > len(tail) + 2}


def search(text, limit=5, entries=None):
    """The catalogue models that fit a request, best first: [{"entry", "score", "why"}]. The request's
    head word (what the thing is) must be in the model's name, tags or category; a request for a car
    finds nothing here (the car generator makes those)."""
    if agent_tools.looks_like_car(text):
        return []
    head, modifiers = object_words(text)
    if not head:
        return []
    found = []
    for entry in (load() if entries is None else entries):
        f = _fields(entry)
        if head == f["head"] or head in _split_compound(f["head"]):
            score, why = 5.0, [f"it is a {head}"]
        elif head in f["name"]:
            score, why = 3.5, [f"'{head}' is in its name"]
        elif head in f["leaf"]:
            score, why = 3.0, [f"it is filed under {head}"]
        elif head in f["tags"]:
            score, why = 2.5, [f"it is tagged {head}"]
        else:
            continue
        for i, word in enumerate(modifiers):
            weight = 1.0 if i < 3 else 0.4          # the words next to the head count most
            if word in f["name"]:
                score += 1.5 * weight
                why.append(word)
            elif word in f["tags"] or word in f["leaf"]:
                score += 1.0 * weight
                why.append(word)
            elif word in f["cats"] or word in f["text"]:
                score += 0.4 * weight
        if "collection" in _words(entry.get("name")) or len(f["name"]) > 9:
            score -= 1.0                              # a set of many things, not the one thing asked for
        if score >= MIN_SCORE:
            found.append({"entry": entry, "score": round(score, 2), "why": ", ".join(why)})
    found.sort(key=lambda r: (-r["score"], -(r["entry"].get("downloads") or 0), r["entry"]["id"]))
    return found[:max(1, int(limit))]


# --- getting a model's files -----------------------------------------------------------------

def _safe_relative(name):
    path = Path(name)
    if path.is_absolute() or ".." in path.parts or not path.parts:
        raise RuntimeError(f"refused a file with an unsafe name: {name}")
    return path


def fetch(entry):
    """The model file of an entry on this PC, downloading a Poly Haven model the first time. Raises
    RuntimeError with a plain reason when it cannot be had (offline, too big, an unknown host)."""
    if entry.get("library") != "polyhaven":
        path = OBJECTS / (entry.get("file") or "")
        if not entry.get("file") or not path.is_file():
            raise RuntimeError(f"the model file of {entry['id']} is missing")
        return path
    key = entry["id"].split("/", 1)[1]
    if not re.fullmatch(r"[A-Za-z0-9_\-]+", key):
        raise RuntimeError(f"refused an odd model id: {key}")
    folder = OBJECTS / "polyhaven" / key
    main = folder / f"{key}.gltf"
    done = folder / "files.json"
    if main.is_file() and done.is_file():
        listed = json.loads(done.read_text(encoding="utf-8"))
        if all((folder / name).is_file() for name in listed["files"]):
            return main
    try:
        files = json.loads(_http(f"{API}/files/{key}").decode("utf-8"))
        pack = files["gltf"][RESOLUTION]["gltf"]
        wanted = {f"{key}.gltf": pack, **(pack.get("include") or {})}
    except (KeyError, TypeError, ValueError) as exc:
        raise RuntimeError(f"Poly Haven lists no {RESOLUTION} glTF for {key}") from exc
    total = sum(int(f.get("size") or 0) for f in wanted.values())
    if total > MAX_BYTES:
        raise RuntimeError(f"{entry['name']} is {total / 1e6:.0f} MB to download, over the "
                           f"{MAX_BYTES / 1e6:.0f} MB limit for one model")
    got = 0
    for name, info in wanted.items():
        target = folder / _safe_relative(name)
        if target.is_file() and target.stat().st_size == int(info.get("size") or -1):
            got += target.stat().st_size
            continue
        data = _http(info["url"], timeout=300)
        got += len(data)
        if got > MAX_BYTES:
            raise RuntimeError(f"{entry['name']} turned out bigger than the {MAX_BYTES / 1e6:.0f} MB limit")
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_name(target.name + ".part")
        tmp.write_bytes(data)
        tmp.replace(target)
    done.write_text(json.dumps({"files": sorted(wanted), "bytes": got, "source": f"{API}/files/{key}",
                                "license": "CC0, https://polyhaven.com/license"}, indent=1), encoding="utf-8")
    return main


def downloaded_bytes(entry):
    """How much of this entry is on disk (0 when it has not been fetched)."""
    if entry.get("library") != "polyhaven":
        path = OBJECTS / (entry.get("file") or "")
        return path.stat().st_size if path.is_file() else 0
    folder = OBJECTS / "polyhaven" / entry["id"].split("/", 1)[1]
    return sum(p.stat().st_size for p in folder.rglob("*") if p.is_file() and p.name != "files.json") \
        if folder.is_dir() else 0


# --- a request made into an asset -------------------------------------------------------------

def colour_in(text):
    """(name, picker colour) of the colour a text asks for, the longest name first; else (None, None)."""
    low = " " + " ".join(_words(text)) + " "
    for name in sorted(COLOURS, key=len, reverse=True):
        if f" {name} " in low:
            return name, COLOURS[name]
    return None, None


def metres(size):
    """A size as the page stores it ({"width", "depth", "height", "unit"}) in metres, only the sides
    given; None when there is none."""
    if not size:
        return None
    k = UNITS.get(str(size.get("unit") or "m").lower(), 1.0)
    out = {}
    for key in ("width", "depth", "height"):
        try:
            value = float(size.get(key) or 0)
        except (TypeError, ValueError):
            value = 0
        if value > 0:
            out[key] = round(value * k, 4)
    return out or None


SIZE_IN_WORDS = re.compile(r"(\d+(?:\.\d+)?)\s*(mm|cm|m|metres?|meters?|in|inch(?:es)?|ft|feet|foot)\b"
                           r"\s*(tall|high|height)", re.I)
UNIT_WORDS = {"metre": "m", "metres": "m", "meter": "m", "meters": "m", "inches": "in", "feet": "ft", "foot": "ft"}


def height_in(text):
    """A height in metres named in words ("1.2 m tall", "80 cm high"), or None."""
    match = SIZE_IN_WORDS.search(str(text or ""))
    if not match:
        return None
    unit = UNIT_WORDS.get(match.group(2).lower(), match.group(2).lower())
    return round(float(match.group(1)) * UNITS[unit], 4)


def plan(request, entry, size=None):
    """How one catalogue model becomes the asset for a request: its colour (a colour word in the
    request tints it) and its size (the size given on the page, or a height named in the request;
    otherwise its own real-world size)."""
    name, colour = colour_in(request)
    wanted = metres(size)
    if not wanted and height_in(request):
        wanted = {"height": height_in(request)}
    notes = []
    if colour:
        notes.append(f"recoloured {name}: its textures keep their wear and shading in the new colour")
    if (entry.get("triangles") or 0) > 60000:
        notes.append(f"the model is detailed (about {entry['triangles']} faces)")
    return {"asset": entry["id"], "name": entry["name"], "library": entry["library"], "request": str(request or ""),
            "colour": list(colour) if colour else None, "colour_name": name, "size": wanted,
            "max_triangles": None, "note": "; ".join(notes)}


def changed(made, text):
    """The same model with a change asked for in words: a colour ("make it red", "no tint"), a height
    ("1 m tall") or bigger / smaller. What the words do not name stays."""
    new = dict(made)
    low = " " + " ".join(_words(text)) + " "
    name, colour = colour_in(text)
    if colour:
        new["colour"], new["colour_name"] = list(colour), name
    elif any(f" {w} " in low for w in ("untinted", "original colour", "original color", "no tint", "no colour",
                                       "no color", "natural")):
        new["colour"], new["colour_name"] = None, None
    height = height_in(text)
    entry = entry_by_id(made["asset"]) or {}
    base = (made.get("size") or {}).get("height") or (entry.get("size") or {}).get("height")
    if height:
        new["size"] = {"height": height}
    elif base and any(f" {w} " in low for w in ("bigger", "larger", "taller")):
        new["size"] = {"height": round(base * 1.25, 4)}
    elif base and any(f" {w} " in low for w in ("smaller", "shorter", "lower")):
        new["size"] = {"height": round(base * 0.8, 4)}
    new["request"] = f"{made.get('request', '')} | {text}".strip(" |")
    return new


def describe(made):
    """A short phrase for the chat: "Wooden Chair 01 from Poly Haven, recoloured red, 0.9 m tall"."""
    text = f"{made['name']} from {LIBRARY_NAMES.get(made['library'], made['library'])}"
    if made.get("colour_name"):
        text += f", recoloured {made['colour_name']}"
    size = made.get("size") or {}
    if size.get("height"):
        text += f", {size['height']:g} m tall"
    elif size:
        text += ", resized to " + " x ".join(f"{size[k]:g} m" for k in ("width", "depth", "height") if size.get(k))
    return text


def make(made, out_glb, blend_path=None, entries=None):
    """Build a planned asset in Blender: the .glb (textures inside) and, when asked, a .blend the
    project's renders can open, its mesh tagged as a ModelGen part. Returns the plan with what was
    measured: {"after": {"length", "width", "height", "triangles", "ground"}, ...}."""
    entry = entry_by_id(made["asset"], entries)
    if entry is None:
        raise RuntimeError(f"{made['asset']} is not in the catalogue")
    source = fetch(entry)
    size = made.get("size") or {}
    job = {"file": str(source), "asset": made["asset"], "name": _part_name(made["name"]),
           "height": size.get("height") if set(size) == {"height"} else None,
           "size": [size.get("width"), size.get("depth"), size.get("height")] if set(size) - {"height"} else None,
           "tint": made.get("colour"), "max_triangles": made.get("max_triangles"),
           "glb": str(out_glb), "blend": str(blend_path) if blend_path else None}
    Path(out_glb).parent.mkdir(parents=True, exist_ok=True)
    lines, code = car_library.run_blender(f"from bpy_lib import assets\nassets.make({job!r})", timeout=900)
    result = car_library.tagged(lines, "MG_ASSET ")
    if code != 0 or not result or not Path(out_glb).is_file():
        raise RuntimeError("the model was not made:\n" + "\n".join(lines[-30:]))
    return {**made, "glb": job["glb"], "blend": job["blend"], "source": str(source), **result[0]}


def _part_name(name):
    return re.sub(r"[^a-z0-9]+", "_", str(name).lower()).strip("_") or "asset"


def main(args):
    if not args:
        print(__doc__)
        return
    if args[0] == "--build":
        entries = build(refresh="--local" not in args)
        by_lib = {}
        for e in entries:
            by_lib[e["library"]] = by_lib.get(e["library"], 0) + 1
        print(f"{len(entries)} models in {CATALOG}: " + ", ".join(f"{n} {lib}" for lib, n in sorted(by_lib.items())))
        return
    for hit in search(" ".join(args)) or [{"entry": {"id": "nothing fits", "name": ""}, "score": 0, "why": ""}]:
        print(f"{hit['score']:>5}  {hit['entry']['id']:<40} {hit['entry']['name']}  ({hit['why']})")


if __name__ == "__main__":
    main(sys.argv[1:])
