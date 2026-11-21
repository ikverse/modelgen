"""The tools each agent is given, wired to Blender, the checks, the library and the project files.

Each function here returns a list of runner.AgentTool for one agent run. `rnd` is the pipeline's
current round (pipeline.Round): its folder, spec, plan, and a `sink` dict where submitted results
land for the pipeline to pick up.
"""

import ast
import json
import re
from pathlib import Path

import yaml

from .. import config, db, families
from ..bpy_lib.shells import car_archetypes, car_sheet
from ..runner import AgentTool
from . import blender, checks, render, silhouette
from .library import Library

OBJ = "object"


def _schema(properties, required=None):
    return {"type": OBJ, "properties": properties, "required": required or list(properties)}


def api_reference(path, prefix, skip=("finalize_part", "reset_scene", "apply_modifiers"), only=None):
    """A compact reference of a library module's public functions for an agent's prompt; `only`
    keeps just the functions named."""
    tree = ast.parse(Path(path).read_text(encoding="utf-8"))
    lines = [ast.get_docstring(tree) or "", ""]
    for node in tree.body:
        if only is not None and getattr(node, "name", None) not in only:
            continue
        if isinstance(node, ast.FunctionDef) and not node.name.startswith("_") and node.name not in skip:
            doc = (ast.get_docstring(node) or "").replace("\n", "\n    ")
            lines.append(f"{prefix}.{node.name}({ast.unparse(node.args)})\n    {doc}")
    return "\n".join(lines)


def helpers_reference():
    return api_reference(config.BPY_LIB / "helpers.py", "h")


def materials_reference():
    return api_reference(config.BPY_LIB / "materials.py", "m")


def kits_reference(family):
    """The kit functions (bpy_lib.kits, imported as k) a family's builders start from, as a prompt
    section; "" for a family without a kit."""
    names = families.kit(family)
    path = config.BPY_LIB / "kits.py"
    if not names or not path.is_file():
        return ""
    return ("## Kit: tested building blocks for this kind of object (from bpy_lib import kits as k)\n"
            "Start from these instead of primitives wherever one fits; they are closed solids with the right "
            "construction, and they carry named material slots.\n" + api_reference(path, "k", skip=(), only=names))


def motion_reference():
    return api_reference(config.BPY_LIB / "motion.py", "m", skip=("configure", "report", "motion_objects"))


def has_motion(spec):
    """Whether the spec asks for motion: wheels to turn, a travel speed, or smoke and steam."""
    m = (spec or {}).get("motion") or {}
    return bool(m.get("wheels") or m.get("effects") or m.get("speed_kmh") or (spec or {}).get("mechanism"))


# --- shell objects (a car is one continuous skin built from a sheet) -----------------------

SHELL_TEMPLATES = {"car": car_sheet}


CAR_WORDS = re.compile(r"\b(cars?|coupes?|coupés?|sedans?|saloons?|hatchbacks?|suvs?|minivans?|vans?|"
                       r"crossovers?|station wagons?|supercars?|hypercars?|roadsters?|convertibles?|cabriolets?|"
                       r"racecars?)\b(?!\s+(seats?|keys?|chargers?|mats?|covers?|wash|park|parking|tyres?|tires?|"
                       r"wheels?|engines?|doors?|mirrors?|batter(y|ies)|jacks?|parts?|stereo|radio|freshener)\b)",
                       re.IGNORECASE)
NOT_CAR_WORDS = re.compile(r"\b(trucks?|pickups?|lorr(y|ies)|motorbikes?|motorcycles?|scooters?|bicycles?|bikes?|"
                           r"bus(es)?|tractors?|forklifts?|trains?|karts?|carts?)\b", re.IGNORECASE)


def looks_like_car(obj):
    """Whether a spec's object names a car body (a car, coupe, SUV, van...), not a truck, a bike, a
    car accessory such as a car seat, or a part of one ("car engine piston" is a mechanical part)."""
    text = str(obj or "")
    if families.mechanical_word(text):
        return False
    return bool(CAR_WORDS.search(text)) and not NOT_CAR_WORDS.search(text)


def shell_template(spec=None, plan=None):
    """The shell template ("car") a plan or spec is built with, or None for the general way. A spec
    without a category is a car when its object names one."""
    if plan and plan.get("kind") == "shell":
        return plan.get("template")
    spec = spec or {}
    category = str(spec.get("category") or "")
    if not category and looks_like_car(spec.get("object")):
        category = "shell:car"
    name = category.split(":", 1)[1] if category.startswith("shell:") else None
    return name if name in SHELL_TEMPLATES else None


def check_spec(rnd):
    """The spec the geometric checks judge against: a shell plan's overall size (mirrors, wing and
    exhaust included) replaces the body size the brief gave."""
    dims = (rnd.plan or {}).get("dimensions")
    return {**rnd.spec, "dimensions": dims} if dims else rnd.spec


def _source_between(path, start, end):
    text = Path(path).read_text(encoding="utf-8")
    return text[text.index(start):text.index(end)].strip()


def _rounded(value):
    if isinstance(value, float):
        return round(value, 3)
    if isinstance(value, (list, tuple)):
        return [_rounded(v) for v in value]
    if isinstance(value, dict):
        return {k: _rounded(v) for k, v in value.items()}
    return value


DETAIL_FORMAT = (
    'A recess: {"name", "kind": "recess", "view", "outline": [[u, v], ...] or {"rect": [cu, cv, w, h, r]} or '
    '{"circle": [cu, cv, r]} or {"poly": [[u, v], ...]}, "depth", "material" (a slot), "until", "frame", '
    '"frame_material", "frame_depth", "mirror_x", "both_sides"}. A groove (panel line): {"name", "kind": "groove", '
    '"view", "path": [[u, v], ...], "width", "material", "until", "mirror_x"}. "view" is "side", "front", "rear", '
    '"top", or "aim" with "aim": [x, y, z] (the direction the cut travels into the skin) and "at": [x, y, z] (where '
    'the outline is centred) for a detail on a sloping surface. "optional": true leaves a detail out (printed) '
    'instead of stopping the build when it cannot be cut cleanly. To change a default detail give its '
    'name with the new values; to drop one give {"name": ..., "remove": true}.')


def car_reference(sheet=None):
    """What the car architect and the body builder need to know about car sheets: every number with
    its preset values, the detail format, and the default details this sheet gets."""
    path = config.BPY_LIB / "shells" / "car_sheet.py"
    s = car_sheet.resolve(sheet or {})
    defaults = [{k: _rounded(v) for k, v in f.items()} for f in car_sheet.default_features(s)]
    derived = {k: _rounded(s[k]) for k in ("y_front", "y_rear", "axle_front", "axle_rear", "wheel_radius",
                                          "arch_radius", "half_width", "well_x", "mirror_y", "mirror_z",
                                          "wing_y", "wing_spacing", "exhaust_x", "exhaust_z")}
    shell_doc = ast.get_docstring(ast.parse((config.BPY_LIB / "shell.py").read_text(encoding="utf-8"))) or ""
    return "\n".join([
        car_sheet.__doc__.strip(), "",
        "### Every number: the style presets and the shared defaults", "```python",
        _source_between(path, "STYLES = ", "Y_KEYS = "), "```", "",
        "### Derived from this sheet (set mirror_*, wing_*, exhaust_* in the sheet to move those pieces)",
        dumps(derived), "",
        "### Details (`features`)", shell_doc, "",
        DETAIL_FORMAT + " Slots: " + ", ".join(car_sheet.MATERIAL_SLOTS) + ".", "",
        "### The default details of this sheet", dumps(defaults), "",
        "### Example sheets (tuned and checked; start from the closest and change only what this car needs)",
        car_archetypes.__doc__.strip(), "",
        *[f"- **{name}**: {a['description']}\n  " + json.dumps({"sheet": a["sheet"], "accessories": a["accessories"]})
          for name, a in car_archetypes.ARCHETYPES.items()],
    ])


CAR_PLAN_SCHEMA = {"type": "object", "properties": {"submission": {"type": "object", "properties": {
    "sheet": {"type": "object"}, "accessories": {"type": "object"}, "reference_views": {"type": "object"},
    "paint": {"type": "array", "items": {"type": "number"}},
    "style": {"type": "string", "enum": ["stylised", "realistic"]}, "notes": {"type": "string"}},
    "required": ["sheet"]}}, "required": ["submission"]}


FIT_SCHEMA = _schema({"reference": {"type": "integer"}, "sheet": {"type": OBJ}})


def fit_tools(references):
    """fit_side_profile, when the person attached reference images: the sheet's side-profile numbers
    fitted to one reference that is a flat side view."""
    references = list(references or [])
    if not references:
        return []

    def fit(args):
        n = int(args.get("reference") or 0)
        if not 1 <= n <= len(references):
            return {"ok": False, "error": f"reference must be 1 to {len(references)}"}
        try:
            result = silhouette.fit_with_picture(references[n - 1], args.get("sheet") or {})
        except (ValueError, OSError) as exc:
            return {"ok": False, "error": str(exc)}
        if result.get("picture"):
            result["_images"] = [result.pop("picture")]
        return result

    return [AgentTool(
        "fit_side_profile", "Fit the sheet's side-profile numbers (height, wheelbase, overhangs, wheel size, ride "
        "height, nose, cowl, windshield, roof, deck and tail) to reference number `reference`, which must be a flat "
        "side view of the car on a plain background. The sheet's length is kept as the scale. Returns the sheet "
        "with the fitted numbers, what changed, how well the outlines overlap (0 to 1) and a picture: the "
        "reference cut out in grey, your sheet in blue, the fitted sheet in red. Use the fitted numbers only when "
        "`confident` is true and the red outline follows the grey car in the picture.", FIT_SCHEMA, fit)]


def shell_architect_tools(sink, library=None, references=()):
    library = library or Library()

    def submit(args):
        sub = args.get("submission") or {}
        bad = {k: v for k, v in (sub.get("reference_views") or {}).items() if v not in render.CAR_VIEWS}
        if bad:
            return {"ok": False, "error": f"unknown views {bad}; use one of {render.CAR_VIEWS}"}
        try:
            plan = car_sheet.make_plan(sub)
        except (ValueError, TypeError) as exc:
            return {"ok": False, "error": f"the sheet cannot be built: {exc}"}
        sink["plan"] = plan
        out = {"ok": True, "parts": plan_parts(plan), "overall_size": plan["dimensions"]}
        notes = car_sheet.proportion_notes(car_sheet.resolve(plan["sheet"]))
        if notes:
            out["proportions"] = notes + ["These are advice: keep a number outside the usual range only when this "
                                          "car really has it (a muscle car's long hood), and submit again if not."]
        return out

    return [*_library_tools(library), *fit_tools(references),
            AgentTool("submit_plan", "Submit the car: sheet, accessories, reference_views, paint, style and notes.",
                      CAR_PLAN_SCHEMA, submit)]


def _check_code(code, func):
    """Catch syntax errors and a missing entry function before spending a Blender run."""
    try:
        tree = ast.parse(code)
    except SyntaxError as exc:
        return {"ok": False, "error": f"syntax error on line {exc.lineno}: {exc.msg}", "line": exc.text}
    if not any(isinstance(n, ast.FunctionDef) and n.name == func for n in tree.body):
        return {"ok": False, "error": f"the module must define a top-level function {func}()"}
    return None


def plan_parts(plan):
    return [p["name"] for p in (plan or {}).get("parts", []) if p.get("name")]


def model_steps(rnd, assembly=True, materials=True, motion=True):
    """Blender steps that rebuild the model of this round from its files (the motion last, once the
    animator has written it)."""
    steps, missing = [], []
    for name in plan_parts(rnd.plan):
        if rnd.files.exists(f"parts/{name}.py"):
            steps.append(blender.part_step(name, rnd.files.path(f"parts/{name}.py")))
        else:
            missing.append(name)
    if assembly and rnd.files.exists("assembly.py"):
        steps.append(blender.assembly_step(rnd.files.path("assembly.py")))
    if materials and rnd.files.exists("materials.py"):
        steps.append(blender.materials_step(rnd.files.path("materials.py")))
    if motion and rnd.files.exists("motion.py"):
        steps.append(blender.motion_step(rnd.files.path("motion.py")))
    return steps, missing


def _sheet(paths, out, labels=None):
    return render.contact_sheet(paths, out, columns=2, tile=384, labels=labels)


def _attempt(rnd, key):
    rnd.attempts[key] = rnd.attempts.get(key, 0) + 1
    return rnd.attempts[key]


def _live(rnd, key, n, result, kind):
    """Tell the page a new live view of this build step is ready (the GLB the job exported last)."""
    if not result.ok or not (result.outputs or {}).get("live"):
        return
    db.add_event("live_model", project=rnd.project,
                 data={"version": rnd.number, "key": key, "attempt": n, "kind": kind,
                       "file": f"v{rnd.number}/previews/{key}/{n:02d}/live.glb"})


EDIT_SCHEMA = {"type": OBJ, "properties": {"edits": {"type": "array", "items": {
    "type": OBJ, "properties": {"old": {"type": "string"}, "new": {"type": "string"}}, "required": ["old", "new"]}}},
    "required": ["edits"]}


def patched(code, edits):
    """A module's code with each edit applied in order: `old` (text found exactly once in the code as
    it stands when its turn comes) replaced by `new`. Returns (code, None), or (None, why not)."""
    if not (code or "").strip():
        return None, "there is no code to edit yet: write the whole file first"
    edits = [e for e in (edits or []) if isinstance(e, dict)]
    if not edits:
        return None, "give at least one edit: {\"old\": text in the file, \"new\": what replaces it}"
    for i, e in enumerate(edits, 1):
        old, new = str(e.get("old") or ""), str(e.get("new") or "")
        found = code.count(old) if old else 0
        if found != 1:
            return None, (f"edit {i}: `old` must be text that appears exactly once in the file as it is now; it "
                          f"appears {found} times. Copy it exactly (spaces and line breaks too)"
                          + (" and add the lines around it so it is unique" if found > 1 else ""))
        code = code.replace(old, new, 1)
    return code, None


def _edit_tool(rnd, name, path, write, writer):
    """The edit tool that goes with a write tool: it changes a few lines of the file already there
    and then does exactly what the write tool does with the result (build, measure, pictures)."""
    async def edit(args):
        current = rnd.files.read(path) if rnd.files.exists(path) else ""
        code, problem = patched(current, args.get("edits"))
        if problem:
            return {"ok": False, "error": problem}
        return await write({"code": code})

    return AgentTool(name, "Change a few lines of the file you already have instead of sending it whole: each edit "
                     "replaces `old` (text that appears exactly once in the current file) with `new`, in order. "
                     f"Then it builds and answers exactly as `{writer}` does. Use it for a small change; use "
                     f"`{writer}` for a new file or a rewrite.", EDIT_SCHEMA, edit)


NOTE_ANSWER = {"type": OBJ, "properties": {"note": {"type": "integer"}, "done": {"type": "boolean"},
                                           "how": {"type": "string"}}, "required": ["note", "done"]}


def _finish_tool(rnd, key, problems=None, blocking=None, evidence=False, ask=None):
    """The finish tool. With notes for this round, every note must be answered by number in
    notes_done; a note not done sends the agent back once. problems(): what is still wrong with the
    work (a builder's open edges or wrong size); it refuses once, and a second finish with a reason
    is accepted. blocking(): what refuses finish every time (no reason gets past it). evidence: each
    note marked done must say in `how` what in the latest pictures or measurements shows it."""
    notes = (getattr(rnd, "notes", None) or {}).get(key) or []

    def finish(args):
        refused = rnd.sink.setdefault("finish_refused", {}).setdefault(key, set())
        reason = str(args.get("reason") or "").strip()
        stop = blocking() if blocking else []
        if stop:
            return {"ok": False, "error": "not finished yet: " + "; ".join(stop)}
        if ask and len(str(args.get(ask["field"]) or "").strip()) < 20 and "ask" not in refused:
            refused.add("ask")  # asked for once; a second finish without it is accepted
            return {"ok": False, "error": ask["error"]}
        if notes:
            answers = {int(a.get("note", 0)): a for a in (args.get("notes_done") or []) if isinstance(a, dict)}
            missing = [i for i in range(1, len(notes) + 1) if i not in answers]
            if missing:
                return {"ok": False, "error": "answer every note of this round by its number in notes_done "
                        f"(done or not, and how); missing: {missing}",
                        "notes": {i: notes[i - 1] for i in missing}}
            if evidence:
                bare = [i for i in range(1, len(notes) + 1)
                        if answers[i].get("done") and len(str(answers[i].get("how") or "").strip()) < 20]
                if bare:
                    return {"ok": False, "error": f"notes {bare} are marked done without evidence: in each one's "
                            "`how`, say what in your latest pictures (the part views or the part in the model) or "
                            "measurements shows the change, e.g. 'handle measures 14 x 10 mm; the orange handle "
                            "meets the body with no gap in the close-up'",
                            "notes": {i: notes[i - 1] for i in bare}}
            not_done = [i for i in range(1, len(notes) + 1) if not answers[i].get("done")]
            if not_done and "notes" not in refused:
                refused.add("notes")
                return {"ok": False, "error": f"notes {not_done} are not done. Do them now; if one really cannot "
                        "be done, call finish again and say why in its `how`.",
                        "notes": {i: notes[i - 1] for i in not_done}}
        issues = problems() if problems else []
        if issues and ("work" not in refused or not reason):
            refused.add("work")
            return {"ok": False, "error": "not finished yet: " + "; ".join(issues) + ". Fix it with another attempt; "
                    "if it is right as it is (e.g. the plan says it is a thin sheet), call finish again with `reason`."}
        rnd.sink.setdefault("finished", {})[key] = args.get("notes", "")
        if notes:
            rnd.sink.setdefault("notes_done", {})[key] = args.get("notes_done")
        return {"ok": True}

    props = {"notes": {"type": "string"},
             "notes_done": {"type": "array", "items": NOTE_ANSWER,
                            "description": "one answer per note of this round, by its number"
                                           + ("; `how` says what in the latest pictures or measurements shows it"
                                              if evidence else "")},
             "reason": {"type": "string", "description": "why the work is right although finish refused it once"}}
    if ask:
        props[ask["field"]] = {"type": "string", "description": ask["description"]}
    return AgentTool("finish", "Say you are done, with one or two sentences on what you made or changed. With notes "
                     "for this round, answer each one by number in notes_done.", _schema(props, ["notes"]), finish)


def previous_renders(rnd, key=None):
    """The renders of the version this one starts from that its notes were written about: the
    turntable, and for a part its close-up (for materials, the close-ups sheet). [] when there are none."""
    prev = getattr(rnd, "prev_dir", None)
    if not prev or not (getattr(rnd, "notes", None) or {}):
        return []
    folder = Path(prev) / "renders"
    found = [folder / "sheet_turntable.jpg"]
    if key == "materials":
        found.append(folder / "sheet_closeups.jpg")
    elif key == "motion":
        found.append(folder / "sheet_motion.jpg")
    elif key and key != "assembly":
        found.append(folder / f"closeup_{key}.png")
    return [p for p in found if p.is_file()]


def _previous_tool(rnd, key):
    """view_previous_version, when the notes for this agent come with renders it can see."""
    if not (rnd.notes or {}).get(key) or not previous_renders(rnd, key):
        return []

    def view(args):
        shown = previous_renders(rnd, key)
        return {"pictures": [p.name for p in shown], "version": Path(rnd.prev_dir).name,
                "about": "the renders the critic judged when writing your notes: the eight-angle turntable"
                         + ((" and the motion frames" if key == "motion" else " and the close-ups")
                            if len(shown) > 1 else ""),
                "_images": [str(p) for p in shown]}
    return [AgentTool("view_previous_version", "See the renders of the version your notes were written about.",
                      _schema({}, []), view)]


def _library_tools(library):
    def search(args):
        return {"results": library.search(args.get("query", ""), args.get("kind") or None)}

    def get_part(args):
        return {"name": args["name"], "code": library.get(args["name"], "part", count=True)}

    return [
        AgentTool("search_library", "Search the shared library of working part functions and material recipes "
                  "by a few words (e.g. 'turned wooden leg').",
                  _schema({"query": {"type": "string"}, "kind": {"type": "string", "enum": ["part", "material"]}},
                          ["query"]), search),
        AgentTool("get_library_part", "Get the code of a library part to start from.",
                  _schema({"name": {"type": "string"}}), get_part),
    ]


# --- brief and architect ----------------------------------------------------------------

SIZE = {"type": OBJ, "properties": {"width": {"type": "number"}, "depth": {"type": "number"},
                                    "height": {"type": "number"}}}

SPEC_SCHEMA = _schema({"spec": {"type": OBJ, "properties": {
    "object": {"type": "string"}, "summary": {"type": "string"}, "dimensions": SIZE,
    "style": {"type": "string"}, "use": {"type": "string", "enum": ["render", "game", "print"]},
    "materials": {"type": "array", "items": {"type": "string"}},
    "parts": {"type": "array", "items": {"type": OBJ, "properties": {
        "name": {"type": "string"}, "description": {"type": "string"}, "size": SIZE,
        "count": {"type": "integer"}, "material": {"type": "string"}, "attaches_to": {"type": "string"}}}},
    "notes": {"type": "string"},
    "category": {"type": "string", "enum": ["general", "shell:car"]},
    "family": {"type": "string", "enum": families.NAMES},
    "motion": {"type": OBJ, "description": "only when the model should move; leave it out for a still object",
               "properties": {
                   "speed_kmh": {"type": "number"},
                   "wheels": {"type": "array", "items": {"type": "string"},
                              "description": "the names of the parts that turn as the vehicle rolls"},
                   "effects": {"type": "array", "items": {"type": OBJ, "properties": {
                       "kind": {"type": "string", "enum": ["smoke", "steam"]},
                       "from": {"type": "string", "description": "the part it rises from, e.g. chimney"}},
                       "required": ["kind", "from"]}},
                   "notes": {"type": "string"}}}},
    "required": ["object", "dimensions", "parts", "category"]}})


def brief_tools(sink):
    def submit(args):
        spec = args["spec"]
        dims = spec.get("dimensions") or {}
        if not all(float(dims.get(k, 0) or 0) > 0 for k in ("width", "depth", "height")):
            return {"ok": False, "error": "dimensions need positive width, depth and height in metres"}
        if not spec.get("parts"):
            return {"ok": False, "error": "list the parts"}
        sink["spec"] = spec
        return {"ok": True}
    return [AgentTool("submit_spec", "Submit the finished structured spec.", SPEC_SCHEMA, submit)]


PLAN_SCHEMA = _schema({"plan": {"type": OBJ, "properties": {
    "parts": {"type": "array", "items": {"type": OBJ, "properties": {
        "name": {"type": "string"}, "description": {"type": "string"}, "method": {"type": "string"},
        "dimensions": SIZE, "anchor": {"type": "string"}, "details": {"type": "string"},
        "location": {"type": "array", "items": {"type": "number"}},
        "rotation_deg": {"type": "array", "items": {"type": "number"}},
        "count": {"type": "integer"},
        "instances": {"type": "array", "items": {"type": OBJ}},
        "attaches_to": {"type": "string"}, "material": {"type": "string"},
        "library_part": {"type": "string"},
        "triangles": {"type": "integer", "description": "for a game asset: this part's share of the model's "
                                                        "triangle limit, all its copies together"},
        "asset": {"type": "string", "description": "the id of a ready-made model from search_assets, placed "
                                                   "whole as this part"}},
        "required": ["name", "method", "dimensions"]}},
    "assembly_notes": {"type": "string"},
    "research_features": {"type": "array", "description": "every feature of the spec's research, by its number, "
                                                          "with the part that carries it",
                          "items": {"type": OBJ, "properties": {"feature": {"type": "integer"},
                                                                "part": {"type": "string"},
                                                                "how": {"type": "string"}},
                                    "required": ["feature", "part"]}},
    "rebuild": {"type": "array", "items": {"type": "string"}}}, "required": ["parts"]}})


def architect_tools(sink, library=None, assets=False, features=None, budget=None):
    """assets: also give search_assets (a scene may place ready-made models as parts). features: the
    research's features; the plan must say which part carries each (asked for once). budget: a game
    asset's triangle limit; every part needs a share and the shares must fit it (asked for once)."""
    library = library or Library()
    features = list(features or [])

    def submit(args):
        plan = args["plan"]
        names = plan_parts(plan)
        if not names:
            return {"ok": False, "error": "the plan has no parts"}
        if len(set(names)) != len(names):
            return {"ok": False, "error": "part names must be unique"}
        bad = [n for n in names if not n.isidentifier()]
        if bad:
            return {"ok": False, "error": f"part names must be snake_case identifiers: {bad}"}
        if budget and not sink.get("shares_asked"):
            built = [p for p in plan["parts"] if not p.get("asset")]
            without = [p["name"] for p in built if not int(p.get("triangles") or 0) > 0]
            total = sum(int(p.get("triangles") or 0) for p in built)
            if without or total > budget * 1.1:
                sink["shares_asked"] = True  # once: a second submit is accepted as it is
                problem = (f"these parts have no `triangles` share: {without}" if without else
                           f"the parts' `triangles` shares add up to {total}, over the model's limit of {budget}")
                return {"ok": False, "error": f"this is a game asset of at most {budget} triangles, and {problem}. "
                        "Give every part a `triangles` share (all its copies together) that its builder must keep "
                        "to: the main body the most, a small piece 100 to 400; together within the limit. Fewer, "
                        "simpler parts if they do not fit. Then submit again."}
        if features and not sink.get("features_asked"):
            mapped = [m for m in (plan.get("research_features") or []) if isinstance(m, dict)]
            covered = {int(m.get("feature") or 0) for m in mapped if m.get("part") in names}
            missing = [i for i in range(1, len(features) + 1) if i not in covered]
            if missing:
                sink["features_asked"] = True  # once: a second submit is accepted as it is
                return {"ok": False, "error": "the research lists features of the real object that no part of "
                        f"your plan carries: numbers {missing}. Give each to a part (in that part's `details`, "
                        "with its size and place, or as a part of its own) and list it in `research_features` "
                        "with that part's name. Leave one out only when it cannot be seen from outside; say so "
                        "in `assembly_notes`. Then submit again.",
                        "features": {i: features[i - 1] for i in missing}}
        wanted = {p["name"]: p["asset"] for p in plan["parts"] if p.get("asset")}
        if wanted:
            from . import asset_catalog
            known = {e["id"] for e in asset_catalog.load()} if asset_catalog.CATALOG.is_file() else set()
            unknown = {n: a for n, a in wanted.items() if a not in known}
            if unknown:
                return {"ok": False, "error": f"these `asset` ids are not in the catalogue: {unknown}; use an id "
                                              "search_assets returned, or leave `asset` out"}
        sink["plan"] = plan
        return {"ok": True}
    return [_library_tools(library)[0], *(asset_tools() if assets else []),
            AgentTool("submit_plan", "Submit the whole build plan.", PLAN_SCHEMA, submit)]



# --- casting and research (before the spec) ---------------------------------------------

MECHANISM_SCHEMA = {"type": OBJ, "properties": {
    "mechanism": {"type": OBJ, "description": "{parts: [{name, type, ...numbers, meshes_with, on_shaft, at}], "
                                              "driver, rpm, direction}"},
    "custom_parts": {"type": "array", "items": {"type": OBJ, "properties": {
        "name": {"type": "string"}, "description": {"type": "string"}}, "required": ["name", "description"]},
        "description": "parts the request needs that no part tool makes (a housing, a bracket); [] when none"},
    "notes": {"type": "string", "description": "what you chose and why, in a sentence or two"}},
    "required": ["mechanism"]}


def engineer_tools(sink):
    """submit_mechanism: the sheet is checked by the same code the part tools use (tools.mechanism);
    a sheet that cannot be built comes back with the field named, to fix and submit again."""
    from . import mechanism

    def submit(args):
        try:
            sheet, assumed = mechanism.clean(args.get("mechanism") or {})
        except mechanism.MechanismError as exc:
            return {"ok": False, "error": str(exc)}
        custom = [c for c in (args.get("custom_parts") or []) if isinstance(c, dict) and c.get("name")]
        sink.update(mechanism=sheet, assumed=assumed, custom=custom, notes=str(args.get("notes") or ""))
        return {"ok": True, "parts": [p["name"] for p in sheet["parts"]], "filled_in": assumed,
                "sizes_mm": mechanism.size_mm(sheet)}

    return [AgentTool("submit_mechanism", "Submit the mechanism sheet. It is checked at once: a mistake comes back "
                      "with the field named; fix it and submit again. Returns what was filled in by default.",
                      MECHANISM_SCHEMA, submit)]


CHARACTER_SCHEMA = {"type": OBJ, "properties": {
    "character": {"type": OBJ, "description": "{name, age, sex, height_cm, build, muscle, weight, proportions, "
                                              "ancestry, skin, eyes, style, look}; leave out what you have no "
                                              "reason to set"},
    "notes": {"type": "string", "description": "anything the person should know: a style the tools cannot make "
                                               "yet, a named character you did not copy"}},
    "required": ["character"]}


def character_designer_tools(sink):
    """submit_character: the sheet is checked by the same code that builds it (tools.character); a
    sheet that cannot be built comes back with the field named, to fix and submit again."""
    from . import character

    def submit(args):
        try:
            sheet, assumed = character.clean(args.get("character") or {})
        except character.CharacterError as exc:
            return {"ok": False, "error": str(exc)}
        sink.update(character=sheet, assumed=assumed, notes=str(args.get("notes") or ""))
        return {"ok": True, "character": character.describe(sheet), "filled_in": assumed}

    return [AgentTool("submit_character", "Submit the character sheet. It is checked at once: a mistake comes back "
                      "with the field named; fix it and submit again. Returns what was filled in by default.",
                      CHARACTER_SCHEMA, submit)]


def character_tailor_tools(sink, sheet):
    """submit_outfit: the outfit and accessories are checked on the character's sheet by the same
    code that dresses it (tools.character.clean_look)."""
    from . import character

    def submit(args):
        raw = {"outfit": args.get("outfit"), "accessories": args.get("accessories") or [],
               "hair": sheet.get("hair"), "brows": sheet.get("brows"), "face": sheet.get("face")}
        try:
            got = character.clean_look(raw, sheet, [])
        except character.CharacterError as exc:
            return {"ok": False, "error": str(exc)}
        sink.update(outfit=got["outfit"], accessories=got["accessories"], notes=str(args.get("notes") or ""))
        return {"ok": True, "outfit": [g["type"] for g in got["outfit"]],
                "accessories": [a["type"] for a in got["accessories"]]}

    schema = {"type": OBJ, "properties": {
        "outfit": {"type": "array", "items": {"type": OBJ, "properties": {
            "type": {"type": "string", "enum": list(character.GARMENT_TYPES)},
            "colour": {"type": "string"},
            "length": {"type": "string", "enum": list(character.SKIRT_LENGTHS)}}, "required": ["type", "colour"]}},
        "accessories": {"type": "array", "items": {"type": OBJ}},
        "notes": {"type": "string"}}, "required": ["outfit"]}
    return [AgentTool("submit_outfit", "Submit what the character wears: outfit [{type, colour, length}] and "
                      "accessories [{type: glasses, shape, frame, colour}]. Checked at once; a mistake comes back "
                      "with the field named.", schema, submit)]


def character_hair_tools(sink, sheet):
    """submit_hair: hair, brows and face sliders, checked by tools.character.clean_look."""
    from . import character

    def submit(args):
        raw = {"outfit": sheet.get("outfit"), "accessories": sheet.get("accessories"),
               "hair": args.get("hair"), "brows": args.get("brows") or {}, "face": args.get("face") or {}}
        try:
            got = character.clean_look(raw, sheet, [])
        except character.CharacterError as exc:
            return {"ok": False, "error": str(exc)}
        sink.update(hair=got["hair"], brows=got["brows"], face=got["face"], notes=str(args.get("notes") or ""))
        return {"ok": True, "hair": got["hair"], "brows": got["brows"], "face": got["face"]}

    schema = {"type": OBJ, "properties": {
        "hair": {"type": OBJ, "properties": {"style": {"type": "string", "enum": list(character.HAIR_STYLES)},
                                             "colour": {"type": "string"}, "length": {"type": "number"}},
                 "required": ["style", "colour"]},
        "brows": {"type": OBJ, "properties": {"colour": {"type": "string"}, "thickness": {"type": "number"},
                                              "arch": {"type": "number"}}},
        "face": {"type": OBJ, "description": "{slider: -1..1} from the list in the request"},
        "notes": {"type": "string"}}, "required": ["hair"]}
    return [AgentTool("submit_hair", "Submit the hair {style, colour}, brows {colour, thickness, arch} and face "
                      "sliders {name: -1..1}. Checked at once; a mistake comes back with the field named.",
                      schema, submit)]


def caster_tools(sink, ids):
    """choose: the caster names the ready-made model that is the object asked for, or "none"."""
    def choose(args):
        asset = str(args.get("asset") or "").strip()
        if asset.lower() in ("", "none"):
            sink["asset"] = None
            return {"ok": True, "chosen": "none"}
        if asset not in ids:
            return {"ok": False, "error": f"unknown id {asset!r}; use one of {ids} or \"none\""}
        sink["asset"] = asset
        return {"ok": True, "chosen": asset}

    return [AgentTool("choose", "Name the one ready-made model that is the object the person asked for, by its "
                      "id, or \"none\" when none of them is.",
                      _schema({"asset": {"type": "string"}, "reason": {"type": "string"}}, ["asset"]), choose)]


RESEARCH_SCHEMA = _schema({"research": {"type": OBJ, "properties": {
    "object": {"type": "string"},
    "dimensions": {"type": OBJ, "description": "the real overall size in metres",
                   "properties": {"width": {"type": "number"}, "depth": {"type": "number"},
                                  "height": {"type": "number"}}},
    "proportions": {"type": "array", "description": "relations that fix the look, each with its number and where "
                                                     "the number comes from; never a guess",
                    "items": {"type": OBJ, "properties": {
                        "text": {"type": "string"},
                        "basis": {"type": "string", "enum": ["published", "photo"],
                                  "description": "published: a figure read on a page; photo: measured by you on a "
                                                 "photo you kept"},
                        "source": {"type": "string", "description": "the page, or which kept photo"}},
                        "required": ["text", "basis"]}},
    "unverified": {"type": "array", "items": {"type": "string"},
                   "description": "sizes and relations you could neither find nor measure: your best guess, "
                                  "clearly a guess. The team may change these freely."},
    "features": {"type": "array", "items": {"type": "string"},
                 "description": "the parts and details the real thing has and where each sits; a size only when "
                                "it is published or measured on a kept photo"},
    "colours": {"type": "array", "items": {"type": "string"}, "description": "real colours as hex, with where"},
    "notes": {"type": "string"},
    "sources": {"type": "array", "items": {"type": "string"}}},
    "required": ["object", "dimensions", "features"]}})
MAX_FOUND_IMAGES = 4
MAX_IMAGE_BYTES = 12 * 1024 * 1024
GUESS_WORDS = re.compile(r"\b(typical(ly)?|estimated?|assumed|guess(ed|ing)?|probably|presumably)\b", re.IGNORECASE)
BASIS_WORDS = {"published": "published", "photo": "measured on a kept photo"}


WIKIMEDIA = "upload.wikimedia.org"


def _wikimedia_original(url):
    """A Wikimedia Commons thumbnail's address turned into the address of the picture itself: the
    thumbnail servers turn away plain clients, the originals are served. Other addresses come back
    unchanged. (The picture is scaled down when it is kept.)"""
    import urllib.parse
    parts = urllib.parse.urlsplit(str(url or ""))
    if (parts.hostname or "").lower() != WIKIMEDIA or "/thumb/" not in parts.path:
        return url
    path = parts.path.replace("/thumb/", "/", 1).rsplit("/", 1)[0]  # .../thumb/a/ab/Name.jpg/640px-Name.jpg
    return urllib.parse.urlunsplit((parts.scheme, parts.netloc, path, "", ""))


IMAGE_GAP_S = 6.0            # seconds between two downloads from the same host
IMAGE_RETRY_S = (10.0, 25.0)  # waits before the second and third try when a host says "too many requests"
_LAST_IMAGE = {}              # host -> when it was last asked


def _fetch_image(url):
    """Download one picture from the web as a data URL for uploads.save. Only http(s) addresses of
    named public hosts; nothing about the person or the project is sent."""
    import base64
    import ipaddress
    import urllib.parse
    import urllib.request
    parts = urllib.parse.urlsplit(str(url or ""))
    host = (parts.hostname or "").lower()
    if parts.scheme not in ("http", "https") or "." not in host or host.endswith((".local", ".internal")):
        raise ValueError("give the direct http(s) address of an image on a public site")
    try:
        ipaddress.ip_address(host)
        raise ValueError("an address by number is not accepted; use the site's name")
    except ValueError as exc:
        if "not accepted" in str(exc):
            raise
    import time
    import urllib.error
    req = urllib.request.Request(_wikimedia_original(url), headers={"User-Agent": "ModelGen/1.0", "Accept": "image/*"})
    for attempt in range(3):
        # picture hosts turn away downloads that come seconds apart: leave a gap after the last one
        wait = IMAGE_GAP_S - (time.monotonic() - _LAST_IMAGE.get(host, -IMAGE_GAP_S))
        if wait > 0:
            time.sleep(wait)
        _LAST_IMAGE[host] = time.monotonic()
        try:
            with urllib.request.urlopen(req, timeout=25) as resp:
                kind = (resp.headers.get("Content-Type") or "").split(";")[0].strip().lower()
                if not kind.startswith("image/"):
                    raise ValueError(f"that address is a {kind or 'page'}, not an image file")
                raw = resp.read(MAX_IMAGE_BYTES + 1)
            break
        except urllib.error.HTTPError as exc:
            if exc.code != 429 or attempt == 2:  # asked too fast: wait and ask again, twice at most
                raise
            time.sleep(IMAGE_RETRY_S[attempt])
    if len(raw) > MAX_IMAGE_BYTES:
        raise ValueError("the image is larger than 12 MB")
    return f"data:{kind};base64," + base64.b64encode(raw).decode("ascii")


def researcher_tools(sink, project=None, side_view=False):
    """save_reference_image keeps a photo found on the web with the model (the team and the critic
    then see it with view_reference); submit_research hands over what was found. Every proportion
    names where its number comes from (published, or measured on a kept photo); a guess is not a
    proportion and goes on the `unverified` list. side_view: the object is a vehicle, whose look is
    fixed by its side view, so a flat side-view photo and proportions measured on it are asked for."""
    from .. import uploads

    async def save_image(args):
        import asyncio
        sink["image_tries"] = sink.get("image_tries", 0) + 1
        images = sink.setdefault("images", [])
        if len(images) >= MAX_FOUND_IMAGES:
            return {"ok": False, "error": f"{MAX_FOUND_IMAGES} photos are kept already; that is enough"}
        try:
            # off the event loop: a download may wait out a host's rate limit for half a minute
            saved = uploads.save(await asyncio.to_thread(_fetch_image, args.get("url")))
        except Exception as exc:
            return {"ok": False, "error": f"that picture could not be kept: {exc}"[:300]}
        images.append(saved["id"])
        if project:
            options = dict((db.get_project(project) or {}).get("options") or {})
            options["found_references"] = list(options.get("found_references") or []) + [saved["id"]]
            db.update_project(project, options=options)
        path = uploads.path(saved["id"])
        return {"ok": True, "kept": len(images), "what": args.get("what", ""),
                "check": "this is the picture as kept: if it does not show the object clearly, say so in your notes",
                "_images": [str(path)] if path else []}

    def submit(args):
        research = args.get("research") or {}
        dims = research.get("dimensions") or {}
        if not all(float(dims.get(k, 0) or 0) > 0 for k in ("width", "depth", "height")):
            return {"ok": False, "error": "dimensions need a positive width, depth and height in metres"}
        if not research.get("features"):
            return {"ok": False, "error": "list the features the real object has"}
        if not sink.get("image_tries") and not sink.get("asked_for_image"):
            sink["asked_for_image"] = True  # once: a picture is what the builders and the critic compare with
            return {"ok": False, "error": "you have not tried to keep a reference photo. Read the object's "
                    "Wikipedia or Wikimedia Commons page with WebFetch, ask for the full addresses of its image "
                    "files (https://upload.wikimedia.org/...), and call save_reference_image with a clear side "
                    "or three-quarter view. If two tries fail, submit again as it is."}
        proportions = research.get("proportions") or []
        wrong = [i for i, p in enumerate(proportions, 1)
                 if not isinstance(p, dict) or p.get("basis") not in BASIS_WORDS or not str(p.get("text") or "").strip()]
        if wrong:
            return {"ok": False, "error": f"proportions {wrong} do not say where their number comes from. Each is "
                    "{text, basis, source} with basis `published` (a figure you read on a page) or `photo` (you "
                    "measured it on a photo you kept). A number that is neither is a guess: put it in "
                    "`unverified`, not in `proportions`."}
        kept = len(sink.get("images") or [])
        if not kept and any(p["basis"] == "photo" for p in proportions):
            return {"ok": False, "error": "some proportions say they were measured on a photo, but no photo is "
                    "kept. Keep the photo with save_reference_image first, or move those numbers to `unverified`."}
        guessed = [text for text in [p["text"] for p in proportions] + [str(f) for f in research.get("features") or []]
                   if GUESS_WORDS.search(text)]
        if guessed and not sink.get("asked_guesses"):
            sink["asked_guesses"] = True  # once
            return {"ok": False, "error": "these carry a guessed size (typical, estimated, assumed). The team builds "
                    "to every number you give, so a guess becomes the model's shape. For each: find the figure, or "
                    "measure it on a kept photo, or take the number out and put it in `unverified` (keep the "
                    "feature itself, without the size). Then submit again.", "guessed": guessed[:12]}
        on_photo = [p for p in proportions if p["basis"] == "photo"]
        if side_view and (not kept or len(on_photo) < 3) and not sink.get("asked_side_view"):
            sink["asked_side_view"] = True  # once: when no usable side view can be found, the research still goes on
            return {"ok": False, "error": "this is a vehicle: its look is fixed by its side view, and published "
                    "sheets rarely give it. Keep a flat side-view photo or drawing of it (the whole vehicle seen "
                    "square from the side: search for '<the vehicle> side view' or 'profile', and try Wikimedia "
                    "Commons), then measure on it with your own eyes, as shares of the overall length: where the "
                    "hood or nose ends and the cab or body begins, where each axle sits, the front and rear "
                    "overhang; and as shares of the overall height: the hood top, the window sill, the roof. "
                    "Give at least three of these as proportions with basis `photo`. If no side view can be kept "
                    "after two tries, submit again and list what you could not measure in `unverified`."}
        research["proportions"] = [
            f"{p['text'].strip()} [{BASIS_WORDS[p['basis']]}" + (f": {p['source']}" if p.get("source") else "") + "]"
            for p in proportions]
        sink["research"] = research
        return {"ok": True}

    return [AgentTool("save_reference_image", "Keep one photo or drawing of the real object with the model, from the "
                      "direct address of the image file (ending in .jpg, .png or .webp). Returns the picture so you "
                      "can check it. At most four: a clear side or three-quarter view first.",
                      _schema({"url": {"type": "string"}, "what": {"type": "string"}}, ["url"]), save_image),
            AgentTool("submit_research", "Hand over what you found: the real size, proportions, features and colours.",
                      RESEARCH_SCHEMA, submit)]


def asset_tools():
    """search_assets, for a scene's architect: free ready-made models that can be placed as parts."""
    from . import asset_catalog
    if not asset_catalog.CATALOG.is_file():
        return []

    def search(args):
        hits = asset_catalog.search(args.get("query", ""), limit=5)
        return {"results": [{"id": h["entry"]["id"], "name": h["entry"]["name"],
                             "description": h["entry"].get("description"), "size_m": h["entry"].get("size"),
                             "triangles": h["entry"].get("triangles"), "style": h["entry"].get("style")}
                            for h in hits],
                "how": "to place one as a part, give that part's plan entry `asset` its id and the height you "
                       "want in `dimensions`; it needs no builder and keeps its own textures"}

    return [AgentTool("search_assets", "Search the free ready-made models (realistic, textured: plants, rocks, "
                      "furniture, props, street things) by a few words, e.g. 'park bench' or 'oak tree'.",
                      _schema({"query": {"type": "string"}}), search)]


# --- builder, assembler, materials ------------------------------------------------------

def _pair_views(rnd):
    return {k: v for k, v in (rnd.plan.get("reference_views") or {}).items() if v in render.CAR_VIEWS}


def builder_tools(rnd, part, library=None):
    library = library or Library()
    entry = next((p for p in rnd.plan.get("parts", []) if p.get("name") == part), {})
    shell_body = entry.get("builder") == "shell"
    views = render.PREVIEW_VIEWS
    if shell_body:  # the body of a car: the car views, plus any view a reference image is paired with
        views = render.CAR_PREVIEW_VIEWS + [v for v in _pair_views(rnd).values() if v not in render.CAR_PREVIEW_VIEWS]

    async def write_part(args):
        code = args.get("code", "")
        problem = _check_code(code, "build")
        if problem:
            return problem
        rnd.files.write(f"parts/{part}.py", code)
        n = _attempt(rnd, part)
        out = rnd.files.path(f"previews/{part}/{n:02d}")
        result = await blender.run_job_async([blender.part_step(part, rnd.files.path(f"parts/{part}.py")),
                                  blender.measure_step(), render.preview_step(out, views),
                                  blender.live_step(out / "live.glb")])
        _live(rnd, part, n, result, "part")
        reply = result.summary()
        rnd.part_ok[part] = result.ok
        if not result.ok:
            rnd.part_errors[part] = reply.get("traceback") or reply.get("error", "")
            return reply
        rnd.part_errors.pop(part, None)
        report = checks.part_report(result.outputs.get("measure"), entry.get("dimensions"))
        rnd.part_reports[part] = report
        paths = (result.outputs.get("renders") or {}).get("preview", [])
        sheet = _sheet(paths, out / "sheet.jpg", labels=views)
        reply.update(report)
        reply["picture"] = ("views: " + ", ".join(views)) if sheet else "no picture"
        reply["_images"] = [sheet] if sheet else []
        if shell_body and paths:
            pairs = render.reference_pairs(_pair_views(rnd), reference_paths(rnd.project), dict(zip(views, paths)))
            paired = render.pair_sheet(pairs, out / "pairs.jpg") if pairs else None
            if paired:
                reply["_images"].append(paired)
                reply["picture"] += "; then each reference image beside the same view of the body"
        if rnd.files.exists("assembly.py"):  # a next version: the model exists, so show the part in it
            ctx = await _in_model(rnd, part, out / "context")
            sheet_in_model = ctx.pop("in_model_picture", None)
            if sheet_in_model:
                reply["_images"].append(sheet_in_model)
                reply["picture"] += ("; then your part in the whole model: two close-ups with its neighbours, and "
                                     "the whole model with your part orange")
            reply.update(ctx)
        return reply

    def problems():
        """What the last write_part measured that is still wrong: open edges, a size off the plan."""
        report = rnd.part_reports.get(part) or {}
        issues = []
        if report.get("non_manifold_edges"):
            issues.append(f"the part still has {report['non_manifold_edges']} open (non-manifold) edges, so it is "
                          "not a closed solid")
        if str(report.get("size_vs_plan", "")).startswith("DOES NOT MATCH"):
            issues.append("its size " + report["size_vs_plan"][len("DOES NOT MATCH: "):])
        share = int(entry.get("triangles") or 0)
        copies = max(1, int(entry.get("count") or 1), len(entry.get("instances") or []))
        have = int(report.get("triangles") or 0) * copies
        if share and str((rnd.spec or {}).get("use") or "").lower() == "game" and have > share * 1.5:
            issues.append(f"the part has {have} triangles" + (f" over its {copies} copies" if copies > 1 else "")
                          + f", but its share of the model's limit is {share}. Rebuild it lighter: fewer segments "
                          "round curves (12 to 24), bevels of 1 segment or none, no detail smaller than can be "
                          "seen, and a flat board instead of modelled lettering")
        if (not had_code and not shell_body and not (rnd.notes or {}).get(part)
                and rnd.attempts.get(part, 0) - built_before == 1):
            issues.append("you built this part once and stopped. Look at the picture again against your plan "
                          "entry, the research and the reference photos: name what is still primitive, missing or "
                          "out of proportion, and build it again better")
        return issues

    built_before = rnd.attempts.get(part, 0)  # attempts made before this run started
    had_code = rnd.files.exists(f"parts/{part}.py")  # a part with code already is being changed, not written

    def blocking():
        """What always stops finish: notes for this round and no attempt built since they came; and
        reference images this builder was given and has not looked at."""
        if (rnd.notes or {}).get(part) and rnd.attempts.get(part, 0) == built_before:
            return ["you have notes for this round but have not built your part since getting them: call "
                    "write_part or edit_part with the changes, look at the pictures (in the model too), then finish"]
        if (not shell_body and part in rnd.sink.get("refs_offered", ())
                and part not in rnd.sink.get("refs_seen", ())):
            return ["this model has reference images and you have not looked at them: call view_reference, "
                    "compare your part's pictures with them, improve it if it differs, then finish"]
        return []

    research = (rnd.spec or {}).get("research") or {}
    ask = None
    if not shell_body and (research.get("features") or research.get("proportions")):
        ask = {"field": "research_check",
               "description": "which of the research's features and proportions belong to your part, and for each "
                              "whether it is built and what it measures",
               "error": "the spec has research on the real object. In `research_check`, list the features and "
                        "proportions that belong to your part and say for each whether you built it and what it "
                        "measures; build what is missing first"}

    return [*_library_tools(library), *_previous_tool(rnd, part),
            AgentTool("write_part", "Save your part module (the whole file, with build()) and build it alone in "
                      "Blender. Returns the traceback, or the measured size, piece count, open edges and a picture; "
                      "in a next version also the part placed in the whole model (painted orange in the whole "
                      "views) with its measured gaps to the parts around it.",
                      _schema({"code": {"type": "string"}}), write_part),
            _edit_tool(rnd, "edit_part", f"parts/{part}.py", write_part, "write_part"),
            _finish_tool(rnd, part, problems, blocking=blocking, evidence=True, ask=ask)]


async def _in_model(rnd, part, out):
    """The part where it sits in the whole model: the model rebuilt from this round's files with the
    previous assembly and materials, the part measured in place, and a picture. Never fails the
    builder's attempt: a model that cannot be built right now (another part mid-change) says so."""
    steps, _ = model_steps(rnd, motion=False)
    car = bool(shell_template(plan=rnd.plan))
    views = ["front_left", "rear_right"] if car else ["three_quarter", "back_three_quarter"]
    steps += [blender.part_measure_step(part), render.context_step(out, part, views)]
    result = await blender.run_job_async(steps)
    if not result.ok:
        info = result.summary()
        last = (info.get("traceback") or info.get("error") or "").strip().splitlines()[-1:] or [""]
        return {"in_model": f"could not show the part in the whole model this time ({result.failed_step}: "
                            f"{last[0][:200]}); judge it from the part views"}
    report = (result.outputs.get("part_measure") or {}).get(part) or {}
    paths = (result.outputs.get("renders") or {}).get("context", [])
    labels = render.CONTEXT_LABELS + [f"whole model, your part orange: {v}" for v in views]
    sheet = render.contact_sheet(paths, Path(out) / "sheet.jpg", columns=2, tile=384, labels=labels)
    reply = {"in_model": report}
    gap = report.get("gap_to_attached_m") or {}
    loose = [f"{n} ({d * 1000:.1f} mm)" for n, d in gap.items()
             if d > float(config.get("model", "contact_tolerance_m", 0.0015))]
    if loose:
        reply["in_model_warning"] = ("in the model your part does not touch what it attaches to: gap to "
                                     + ", ".join(loose) + ". Fit it to that part as it is placed")
    if report.get("pieces_touching_nothing"):
        reply["in_model_warning"] = (reply.get("in_model_warning", "") + "; " if reply.get("in_model_warning")
                                     else "") + (f"{report['pieces_touching_nothing']} piece(s) of your part touch "
                                                 "nothing in the model (they float)")
    if sheet:
        reply["in_model_picture"] = sheet
    return reply


async def _model_feedback(rnd, key, views, check=True, assembly=True, materials=True):
    steps, missing = model_steps(rnd, assembly=assembly, materials=materials)
    if not steps:
        return {"ok": False, "error": "no part has code yet"}
    n = _attempt(rnd, key)
    out = rnd.files.path(f"previews/{key}/{n:02d}")
    steps += [blender.measure_step(), render.preview_step(out, views, prefix=key), blender.live_step(out / "live.glb")]
    result = await blender.run_job_async(steps)
    _live(rnd, key, n, result, key)
    reply = result.summary()
    if missing:
        reply["parts_without_code"] = missing
    if not result.ok:
        return reply
    if check:
        reply["checks"] = checks.evaluate(result.outputs.get("measure"), check_spec(rnd))
        reply["bounding_box"] = (result.outputs.get("measure") or {}).get("bbox")
    paths = (result.outputs.get("renders") or {}).get(key, [])
    sheet = _sheet(paths, out / "sheet.jpg", labels=views)
    reply["_images"] = [sheet] if sheet else []
    return reply


ASSEMBLY_CAR_VIEWS = ["front_left", "rear_right", "side", "top_flat"]
MATERIAL_CAR_VIEWS = ["front_left", "rear_right"]


def assembler_tools(rnd):
    async def write_assembly(args):
        code = args.get("code", "")
        problem = _check_code(code, "assemble")
        if problem:
            return problem
        rnd.files.write("assembly.py", code)
        views = ASSEMBLY_CAR_VIEWS if shell_template(plan=rnd.plan) else render.PREVIEW_VIEWS
        reply = await _model_feedback(rnd, "assembly", views, materials=False)
        rnd.sink["assembly_checks"] = reply.get("checks")
        return reply

    return [*_previous_tool(rnd, "assembly"),
            AgentTool("write_assembly", "Save the assembly module (the whole file, with assemble(parts)), build "
                      "the model and run the geometric checks. Returns the checks and a picture.",
                      _schema({"code": {"type": "string"}}), write_assembly),
            _edit_tool(rnd, "edit_assembly", "assembly.py", write_assembly, "write_assembly"),
            _finish_tool(rnd, "assembly")]


def materials_tools(rnd, library=None):
    library = library or Library()

    def list_recipes(args):
        return {"reference": materials_reference(), "library": library.list("material")}

    def get_recipe(args):
        return {"name": args["name"], "code": library.get(args["name"], "material", count=True)}

    async def write_materials(args):
        code = args.get("code", "")
        problem = _check_code(code, "apply")
        if problem:
            return problem
        rnd.files.write("materials.py", code)
        views = MATERIAL_CAR_VIEWS if shell_template(plan=rnd.plan) else render.MATERIAL_VIEWS
        return await _model_feedback(rnd, "materials", views, check=False)

    async def list_slots(args):
        steps, _ = model_steps(rnd, materials=False, motion=False)
        if not steps:
            return {"ok": False, "error": "no part has code yet"}
        result = await blender.run_job_async(steps + [blender.material_report_step()])
        if not result.ok:
            return result.summary()
        report = result.outputs.get("material_report") or {}
        slots = {k: v.get("parts") for k, v in report.items() if k and not str(k).startswith("slot_")}
        return {"slots": slots,
                "how": "each slot is filled with m.assign_slot(obj, slot, material) on the objects of the parts "
                       "listed; a part not listed under any slot has none and takes m.assign"}

    return [*_previous_tool(rnd, "materials"),
            AgentTool("list_slots", "List the named material slots the built parts carry (parts made with a kit "
                      "or a shell helper have them), and which parts carry each.", _schema({}, []), list_slots),
            AgentTool("list_recipes", "List every material recipe with its parameters, and tuned recipes in the "
                      "library.", _schema({}, []), list_recipes),
            AgentTool("get_library_material", "Get the code of a tuned library material recipe.",
                      _schema({"name": {"type": "string"}}), get_recipe),
            AgentTool("write_materials", "Save the materials module (the whole file, with apply(parts)) and render "
                      "the model in two views with the final lighting.", _schema({"code": {"type": "string"}}),
                      write_materials),
            _edit_tool(rnd, "edit_materials", "materials.py", write_materials, "write_materials"),
            _finish_tool(rnd, "materials")]


# --- animator ---------------------------------------------------------------------------

def animator_tools(rnd):
    def list_helpers(args):
        return {"reference": motion_reference(), "loop": {"seconds": config.get("motion", "seconds", 2.0),
                                                          "fps": config.get("motion", "fps", 24)}}

    async def write_motion(args):
        code = args.get("code", "")
        problem = _check_code(code, "animate")
        if problem:
            return problem
        rnd.files.write("motion.py", code)
        steps, missing = model_steps(rnd)
        if not steps:
            return {"ok": False, "error": "no part has code yet"}
        n = _attempt(rnd, "motion")
        out = rnd.files.path(f"previews/motion/{n:02d}")
        steps += [blender.measure_step(), render.frames_step(out), blender.live_step(out / "live.glb")]
        result = await blender.run_job_async(steps)
        _live(rnd, "motion", n, result, "motion")
        reply = result.summary()
        if missing:
            reply["parts_without_code"] = missing
        if not result.ok:
            return reply
        report = result.outputs.get("motion") or {}
        rnd.sink["motion_report"] = report
        reply["motion"] = report
        found = checks.evaluate(result.outputs.get("measure"), check_spec(rnd))
        reply["checks_at_frame_1"] = {"passed": found["passed"],
                                      "failed": [k for k, g in found["gates"].items() if not g["pass"]]}
        paths = (result.outputs.get("renders") or {}).get("frames", [])
        sheet = render.contact_sheet(paths, out / "sheet.jpg", columns=3, tile=384,
                                     labels=render.frame_labels(paths))
        reply["picture"] = ("frames spread across one loop, from the side close up on the biggest spinning wheel "
                            "(or the whole model when nothing spins), then a close-up of each smoke or steam marker "
                            "as a ball (grey smoke, white steam)" if sheet else "no picture")
        reply["_images"] = [sheet] if sheet else []
        return reply

    def problems():
        """What the spec asks for that the last write_motion did not do."""
        report = rnd.sink.get("motion_report")
        if report is None:
            return ["nothing has been written yet: call write_motion first"]
        wanted = rnd.spec.get("motion") or {}
        issues = []
        if (wanted.get("wheels") or wanted.get("speed_kmh")) and not report.get("animated"):
            issues.append("nothing moves yet: no object was spun or bounced")
        if wanted.get("effects") and not report.get("markers"):
            issues.append("the spec asks for smoke or steam but no marker was placed")
        return issues

    return [*_previous_tool(rnd, "motion"),
            AgentTool("list_motion_helpers", "List every motion helper with its parameters, and the loop length "
                      "and frame rate.", _schema({}, []), list_helpers),
            AgentTool("write_motion", "Save the motion module (the whole file, with animate(parts)), build the "
                      "model, run it, and get back what moved, the markers, the checks at frame 1 and a strip "
                      "of frames across one loop.", _schema({"code": {"type": "string"}}), write_motion),
            _edit_tool(rnd, "edit_motion", "motion.py", write_motion, "write_motion"),
            _finish_tool(rnd, "motion", problems)]


# --- critic and librarian ---------------------------------------------------------------

MECHANICAL_RUBRIC_FILE = config.PACKAGE / "rubric_mechanical.yaml"
CHARACTER_RUBRIC_FILE = config.PACKAGE / "rubric_character.yaml"  # a person built by the character path


def rubric_file(plan=None, spec=None):
    """The rubric a version is scored by: a shell template's own (rubric_car.yaml); a mechanical
    part's (rubric_mechanical.yaml: teeth, meshing, motion); the game rubric for a game asset (the
    spec's use is game); else rubric.yaml."""
    shell = shell_template(plan=plan)
    if shell in config.SHELL_RUBRICS:
        return config.SHELL_RUBRICS[shell]
    if families.of(spec) == "mechanical" and MECHANICAL_RUBRIC_FILE.is_file():
        return MECHANICAL_RUBRIC_FILE
    if families.of(spec) == "person" and CHARACTER_RUBRIC_FILE.is_file():
        return CHARACTER_RUBRIC_FILE
    if str((spec or {}).get("use") or "").lower() == "game" and config.GAME_RUBRIC_FILE.is_file():
        return config.GAME_RUBRIC_FILE
    return config.RUBRIC_FILE


def load_rubric(plan=None, spec=None):
    return yaml.safe_load(rubric_file(plan, spec).read_text(encoding="utf-8"))


def score(scores, rubric=None):
    """Weighted rubric score out of 100."""
    rubric = rubric or load_rubric()
    total = weight = 0.0
    for c in rubric["criteria"]:
        w = float(c.get("weight", 1))
        s = max(0.0, min(10.0, float(scores.get(c["id"], 0) or 0)))
        total += w * s
        weight += w
    return round(10.0 * total / weight, 1) if weight else 0.0


SHEET_ORDER = ("turntable", "car_views", "family_views", "reference_pairs", "wireframe", "closeups", "motion")


MAX_CHANGES = 7
PLAN_TARGET = "plan"  # a change to the plan itself (a part to add, merge or split): it goes to the architect
MATERIAL_WORDS = re.compile(r"\b(colou?rs?|roughness|metallic|metalness|gloss(y|iness)?|matte|satin|glaze|paint(ed)?|"
                            r"textures?|specular|emission|emissive|shader|base colou?r|material)\b|#[0-9a-f]{6}\b",
                            re.IGNORECASE)


HEX = re.compile(r"#([0-9a-f]{6})\b", re.IGNORECASE)


def _hex_distance(a, b):
    a, b = a.lstrip("#"), b.lstrip("#")
    return max(abs(int(a[i:i + 2], 16) - int(b[i:i + 2], 16)) for i in (0, 2, 4))


def already_set(changes, report):
    """The material changes whose every colour (#RRGGBB) some slot of the model already has, within a
    few steps: {change number: [(hex, slot), ...]}. Such a change cannot land: the model already has
    it, so what reads wrong is the light, not the material."""
    colours = [(slot, str(v.get("base_colour", ""))[-7:]) for slot, v in (report or {}).items()
               if HEX.search(str(v.get("base_colour", "")))]
    out = {}
    for i, c in enumerate(changes, 1):
        if c.get("target") != "materials":
            continue
        asked = HEX.findall(f"{c.get('parameter', '')} {c.get('change', '')}")
        if not asked:
            continue
        hits = []
        for h in asked:
            slot = next((s for s, have in colours if _hex_distance(h, have) <= 6), None)
            if slot is None:
                break
            hits.append((f"#{h.upper()}", slot))
        else:
            out[i] = hits
    return out


def critic_tools(rnd, sheets, previous_sheet=None, previous_changes=(), previous_stuck=()):
    """previous_sheet: the turntable of the version this one was built from, shown after this one's.
    previous_changes: that version's change list; the review marks each landed or not, and every
    change that did not land must be on the new list again, unless it already failed to land once
    before: then it is stuck (the builders cannot make it) and goes on the review's `stuck` list.
    previous_stuck: that version's stuck list, carried forward. A car's rubric also has areas
    (nose, lamps...), each scored, and a change must aim at one of the two weakest."""
    rubric = load_rubric(rnd.plan, rnd.spec)
    ids = [c["id"] for c in rubric["criteria"]]
    proportions = list(((rnd.spec or {}).get("research") or {}).get("proportions") or [])
    area_ids = [a["id"] for a in rubric.get("areas") or []]
    previous_changes = list(previous_changes or [])
    blend = rnd.files.path("model.blend")
    measured = {}
    materials_seen = {}

    async def inspect_materials(args):
        if "report" in materials_seen:
            return materials_seen["report"]
        if not blend.is_file():
            return {"ok": False, "error": "this version has no saved model to read"}
        result = await blender.run_job_async([blender.material_report_step()], open_blend=blend)
        if not result.ok:
            info = result.summary()
            return {"ok": False, "error": (info.get("traceback") or info.get("error") or "reading failed")[-600:]}
        report = {"slots": result.outputs.get("material_report") or {},
                  "colours": "picker colours (sRGB hex), as the materials agent writes them"}
        materials_seen["report"] = report
        return report

    async def measure_part(args):
        part = str(args.get("part") or "")
        if part not in set(plan_parts(rnd.plan)):
            return {"ok": False, "error": f"unknown part {part!r}; parts: {plan_parts(rnd.plan)}"}
        if part in measured:
            return measured[part]
        if not blend.is_file():
            return {"ok": False, "error": "this version has no saved model to measure"}
        result = await blender.run_job_async([blender.part_measure_step(part)], open_blend=blend)
        if not result.ok:
            info = result.summary()
            return {"ok": False, "error": (info.get("traceback") or info.get("error") or "measuring failed")[-600:]}
        report = (result.outputs.get("part_measure") or {}).get(part) or {}
        report["units"] = "metres"
        measured[part] = report
        return report

    def view(args):
        shown = [k for k in SHEET_ORDER if sheets.get(k)]
        large = [p for p in (sheets.get("turntable_large") or []) if p]
        images = []
        for k in shown:
            images += large if k == "turntable" and large else [sheets[k]]
        reply = {"pictures": shown,
                 "turntable_angles": "eight views around the model, starting front-left, 45 degrees apart"
                                     + (", as two pictures of four views each" if large else ""),
                 "_images": images}
        if "family_views" in shown:
            reply["family_views"] = ("flat views with no perspective: "
                                     + ", ".join(families.views(families.of(rnd.spec)))
                                     + " (side is the right side, left_flat the left)")
        if "reference_pairs" in shown:
            reply["reference_pairs"] = "each reference image (left) beside the same view of the model (right)"
        if "motion" in shown:
            reply["motion"] = ("the last picture: frames spread across one loop of the motion, from the side close up "
                               "on the biggest spinning wheel (its spokes should move between frames), then a close-up "
                               "of each smoke or steam marker as a ball (grey smoke, white steam). The page draws the real smoke and "
                               "steam at those balls; no render ever shows it, so judge only where the balls sit")
        if previous_sheet and Path(previous_sheet).is_file():
            reply["pictures"] = shown + ["previous_turntable"]
            reply["previous_turntable"] = ("the last picture: the turntable of the version this one was built from, "
                                           "to compare with")
            reply["_images"].append(str(previous_sheet))
        return reply

    def submit(args):
        scores = args.get("scores") or {}
        missing = [i for i in ids if i not in scores]
        if missing:
            return {"ok": False, "error": f"score every criterion; missing {missing}"}
        targets = set(plan_parts(rnd.plan)) | {"assembly", "materials"}
        if has_motion(rnd.spec):
            targets.add("motion")
        if not shell_template(plan=rnd.plan):
            targets.add(PLAN_TARGET)
        changes = args.get("changes") or []
        unknown = [c.get("target") for c in changes if c.get("target") not in targets]
        if unknown:
            return {"ok": False, "error": f"unknown targets {unknown}; use one of {sorted(targets)}"}
        if len(changes) > MAX_CHANGES:
            return {"ok": False, "error": f"list at most {MAX_CHANGES} changes, the ones that matter most first"}
        refused = rnd.sink.setdefault("review_refused", set())
        parts = set(plan_parts(rnd.plan))
        accuracy = [a for a in (args.get("accuracy") or []) if isinstance(a, dict)]
        if proportions and "accuracy" not in refused:
            answered = {int(a.get("item") or 0) for a in accuracy}
            unanswered = [i for i in range(1, len(proportions) + 1) if i not in answered]
            if unanswered:
                refused.add("accuracy")
                return {"ok": False, "error": "check the model against every proportion the research gives: in "
                        f"`accuracy`, answer each by its number (met or not, and what you measured); missing "
                        f"{unanswered}. Measure with measure_part where a number can be measured. A proportion "
                        "that is not met should have a change.",
                        "proportions": {i: proportions[i - 1] for i in unanswered}}
        # builders never touch materials, so a material change sent to a part's builder cannot land
        misrouted = [i for i, c in enumerate(changes, 1) if c.get("target") in parts and (
            c.get("criterion") == "materials" or MATERIAL_WORDS.search(f"{c.get('parameter', '')} {c.get('change', '')}"))]
        if misrouted and "material_target" not in refused:
            refused.add("material_target")
            return {"ok": False, "error": f"changes {misrouted} change a material (colour, roughness, texture, "
                    "paint...) but target a part, and part builders never touch materials, so they cannot land. "
                    "Give each material change target `materials` and name the part or slot inside the change; "
                    "keep a part as the target only for a change to its shape. If one really is a shape change, "
                    "submit again as it is.",
                    "misrouted": {i: changes[i - 1] for i in misrouted}}
        unmeasured = sorted({c.get("target") for c in changes if c.get("target") in parts
                             and c.get("target") not in measured
                             and re.search(r"\d", f"{c.get('parameter', '')} {c.get('change', '')}")})
        if unmeasured and "measure" not in refused and blend.is_file():
            refused.add("measure")
            return {"ok": False, "error": f"changes for {unmeasured} name sizes, but you have not measured those "
                    "parts. Call measure_part on each, base the numbers on what it really measures, and drop any "
                    "change the measurement shows is already true; then submit again."}
        areas = {}
        if area_ids:
            areas = args.get("areas") or {}
            missing_areas = [a for a in area_ids if a not in areas]
            if missing_areas:
                return {"ok": False, "error": f"score every area of the car in `areas`; missing {missing_areas}"}
        if blend.is_file() and "materials_set" not in refused:
            material_changes = [i for i, c in enumerate(changes, 1) if c.get("target") == "materials"]
            if material_changes and "report" not in materials_seen:
                refused.add("materials_set")
                return {"ok": False, "error": f"changes {material_changes} change materials, but you have not read "
                        "them. Call inspect_materials: it shows what every slot really is. Drop any change it shows "
                        "is already set (then what reads wrong is the studio light on it, which the materials "
                        "agent cannot change), and submit again."}
            done = already_set(changes, materials_seen.get("report", {}).get("slots"))
            if done:
                refused.add("materials_set")
                return {"ok": False, "error": f"changes {sorted(done)} ask for colours the model already has "
                        f"({done}), so they cannot land. Drop them, or name what really differs (roughness, "
                        "metallic, coat), and submit again.", "already_set": done}
        lowest = min((float(scores.get(i, 10)) for i in ids), default=10)
        weakest = sorted(i for i in ids if float(scores.get(i, 10)) == lowest)
        if changes and not any(c.get("criterion") in weakest for c in changes) and "weakest" not in refused:
            refused.add("weakest")
            return {"ok": False, "error": f"your lowest score is {weakest} ({lowest:g}), but no change targets it. "
                    f"Give at least one change `criterion` {weakest[0]!r}: the change that would raise it most "
                    "(for materials or surface, usually target `materials` or the part whose surface is worst)."}
        if area_ids and changes and "weak_area" not in refused:
            ranked = sorted(area_ids, key=lambda a: float(areas.get(a, 10)))
            worst = ranked[:2]
            if not any(c.get("area") in worst for c in changes):
                refused.add("weak_area")
                return {"ok": False, "error": f"your two weakest areas are {worst} "
                        f"({', '.join(f'{a} {float(areas[a]):g}' for a in worst)}), but no change aims at them. "
                        "Give at least one change the `area` of one of them: the change that would make that area "
                        "look most like the real car. Leave areas that already look right alone."}
        landed, stuck = {}, [s for s in (previous_stuck or []) if isinstance(s, dict)]
        if previous_changes:
            landed = {int(m.get("change", 0)): bool(m.get("landed"))
                      for m in (args.get("landed") or []) if isinstance(m, dict)}
            unmarked = [i for i in range(1, len(previous_changes) + 1) if i not in landed]
            if unmarked:
                return {"ok": False, "error": f"mark every previous change as landed or not in `landed`; "
                                              f"missing numbers {unmarked}"}
            missed = [i for i, ok in landed.items() if not ok and 1 <= i <= len(previous_changes)
                      and previous_changes[i - 1].get("target") in targets]
            # a change that already missed once before and missed again is stuck: off the list, on `stuck`
            twice = [i for i in missed if int(previous_changes[i - 1].get("misses") or 0) >= 1]
            for i in twice:
                c = previous_changes[i - 1]
                stuck.append({k: c.get(k) for k in ("target", "parameter", "change", "short", "area") if c.get(k)}
                             | {"misses": int(c.get("misses") or 0) + 1})
            # the change on the new list that repeats each one that missed: by repeat_of, else the first
            # change with the same target
            taken = set()
            for c in changes:
                if isinstance(c.get("repeat_of"), int):
                    taken.add(c["repeat_of"])
            for i in missed:
                if i in taken or i in twice:
                    continue
                same = next((c for c in changes if c.get("repeat_of") is None
                             and c.get("target") == previous_changes[i - 1].get("target")), None)
                if same is not None:
                    same["repeat_of"] = i
                    taken.add(i)
            dropped = [i for i in missed if i not in twice and i not in taken]
            if dropped:
                return {"ok": False, "error": f"previous changes {dropped} did not land: put each back at the top of "
                                              "the new change list with `repeat_of` its number (it counts toward the "
                                              f"{MAX_CHANGES})",
                        "not_landed": {i: previous_changes[i - 1] for i in dropped}}
            for c in changes:
                i = c.get("repeat_of")
                if isinstance(i, int) and 1 <= i <= len(previous_changes) and i in landed and not landed[i]:
                    c["misses"] = int(previous_changes[i - 1].get("misses") or 0) + 1
            # a stuck change the critic still lists is dropped from the list: it cannot land
            stuck_texts = {s.get("change") for s in stuck}
            changes = [c for c in changes if not (isinstance(c.get("repeat_of"), int) and c["repeat_of"] in twice)
                       and c.get("change") not in stuck_texts]
        rnd.sink["review"] = {"scores": scores, "summary": args.get("summary", ""), "changes": changes,
                              "score": score(scores, rubric)}
        if accuracy:
            rnd.sink["review"]["accuracy"] = accuracy
        if area_ids:
            rnd.sink["review"]["areas"] = {a: areas[a] for a in area_ids}
        if stuck:
            rnd.sink["review"]["stuck"] = stuck
        if previous_changes:
            rnd.sink["review"]["landed"] = [{"change": i, "landed": landed[i]} for i in sorted(landed)]
        return {"ok": True, "score": rnd.sink["review"]["score"],
                **({"stuck": [s.get("short") or s.get("change") for s in stuck]} if stuck else {})}

    score_props = {i: {"type": "number", "minimum": 0, "maximum": 10} for i in ids}
    props = {"scores": {"type": OBJ, "properties": score_props, "required": ids},
             "summary": {"type": "string"},
             "changes": {"type": "array", "maxItems": MAX_CHANGES, "items": {"type": OBJ, "properties": {
                 "target": {"type": "string"}, "parameter": {"type": "string"},
                 "change": {"type": "string"},
                 "short": {"type": "string",
                           "description": "what the change does in plain words, at most 8, for the person "
                                          "(e.g. \"Darker trim, opaque glass, silver rims\")"},
                 "criterion": {"type": "string", "enum": ids,
                               "description": "the rubric criterion this change raises most"},
                 **({"area": {"type": "string", "enum": area_ids,
                              "description": "the area of the car this change improves"}} if area_ids else {}),
                 "repeat_of": {"type": "integer",
                               "description": "when this repeats a previous change that did not land: its number"}},
                 "required": ["target", "change", "short", "criterion"] + (["area"] if area_ids else [])}}}
    required = ["scores", "summary", "changes"]
    if proportions:
        props["accuracy"] = {"type": "array", "description": "every proportion of the research, by its number: "
                                                             "whether the model meets it and what you measured",
                             "items": {"type": OBJ, "properties": {"item": {"type": "integer"},
                                                                   "met": {"type": "boolean"},
                                                                   "measured": {"type": "string"}},
                                       "required": ["item", "met"]}}
    if area_ids:
        props["areas"] = {"type": OBJ, "description": "every area of the car scored 0-10, judged on its own",
                          "properties": {a: {"type": "number", "minimum": 0, "maximum": 10} for a in area_ids},
                          "required": area_ids}
        required.append("areas")
    if previous_changes:
        props["landed"] = {"type": "array", "description": "every change of the previous version's list, by number",
                           "items": {"type": OBJ, "properties": {"change": {"type": "integer"},
                                                                 "landed": {"type": "boolean"}},
                                     "required": ["change", "landed"]}}
        required.append("landed")
    tools = [AgentTool("view_renders", "See the turntable, wireframe and close-up renders of this version (and a "
                       "car's car views and reference pairs, a moving model's frames, and the previous version's "
                       "turntable).",
                       _schema({}, []), view),
             AgentTool("measure_part", "Measure one part in this version's finished model: its real size, piece "
                       "count, wall thickness (min, median, max), the parts it touches and its gaps to the nearest "
                       "parts, in metres. Measure before asking for any change in size, thickness or gap.",
                       _schema({"part": {"type": "string"}}), measure_part)]
    tools.append(AgentTool("inspect_materials", "Read what every material of this version really is, by slot or "
                           "material name: base colour (picker hex), metallic, roughness, transmission, coat, "
                           "emission. Read them before asking for any material change: a colour that looks off in "
                           "the renders but is already set is the studio light, not the material.", _schema({}, []),
                           inspect_materials))
    tools.append(AgentTool("submit_review", f"Submit the scores, a short summary and the numbered change list (at "
                           f"most {MAX_CHANGES}).", _schema(props, required), submit))
    return tools


def librarian_tools(rnd, library=None):
    library = library or Library()
    source = f"{rnd.project} v{rnd.number}"

    def list_library(args):
        return {"entries": [{k: e.get(k) for k in ("name", "kind", "description", "tags")} for e in library.list()]}

    def add_part(args):
        part = args["from_part"]
        if not rnd.files.exists(f"parts/{part}.py"):
            return {"ok": False, "error": f"there is no part '{part}' in this version"}
        entry = library.add_part(args["name"], args.get("description", ""), rnd.files.read(f"parts/{part}.py"),
                                 args.get("tags", []), f"{source} part {part}",
                                 preview=latest_preview(rnd.dir, part))
        rnd.sink.setdefault("library_added", []).append(entry["name"])
        return {"ok": True, "name": entry["name"]}

    def add_material(args):
        problem = _check_code(args.get("code", ""), "recipe")
        if problem:
            return problem
        entry = library.add_material(args["name"], args.get("description", ""), args["code"],
                                     args.get("tags", []), source)
        rnd.sink.setdefault("library_added", []).append(entry["name"])
        return {"ok": True, "name": entry["name"]}

    tags = {"type": "array", "items": {"type": "string"}}
    return [AgentTool("list_library", "List everything in the shared library.", _schema({}, []), list_library),
            AgentTool("add_part", "Add one of this version's parts to the library under a reusable name.",
                      _schema({"name": {"type": "string"}, "description": {"type": "string"}, "tags": tags,
                               "from_part": {"type": "string"}}, ["name", "description", "from_part"]), add_part),
            AgentTool("add_material", "Add a tuned material recipe (a module with recipe()) to the library.",
                      _schema({"name": {"type": "string"}, "description": {"type": "string"}, "tags": tags,
                               "code": {"type": "string"}}, ["name", "description", "code"]), add_material)]


def latest_preview(version_folder, part):
    """The newest preview picture a builder made of this part in that version, or None."""
    folder = Path(version_folder) / "previews" / part
    sheets = sorted(folder.glob("*/sheet.jpg")) if folder.is_dir() else []
    return sheets[-1] if sheets else None


# --- reference images ---------------------------------------------------------------------

def reference_paths(project):
    from .. import db, uploads
    options = ((db.get_project(project) or {}).get("options") or {}) if project else {}
    ids = list(options.get("references") or []) + list(options.get("found_references") or [])
    return [p for p in (uploads.path(i) for i in ids) if p]


def reference_tools(project=None, paths=None, on_view=None):
    """view_reference, when the person attached reference images to this model (or paths are given).
    on_view() is called each time the agent looks."""
    found = list(paths) if paths is not None else reference_paths(project)
    if not found:
        return []

    def view(args):
        if on_view:
            on_view()
        return {"pictures": [f"reference {i + 1}" for i in range(len(found))], "_images": [str(p) for p in found]}

    return [AgentTool("view_reference", "See the reference images of this model: the ones the person attached, then "
                      "any photos the research found.",
                      _schema({}, []), view)]


# --- director ---------------------------------------------------------------------------

def director_tools(start_build, request_changes, project_status, team_member=None, reference_images=(),
                   stop_build=None, review_again=None):
    """The callables come from director.py; each takes the arguments dict and returns a dict.
    reference_images: files attached to the current request, which the director can look at."""
    extra = []
    if stop_build:
        extra.append(AgentTool(
            "stop_build", "Stop the build that is running now, at once: every agent and Blender job in it ends. "
            "Only when the person asks to stop.", _schema({}, []), stop_build))
    if team_member:
        extra.append(AgentTool(
            "team_member", "What one team member has been doing: recent runs, tool calls, errors, cost and their "
            "latest work. name: their name (Armin, Pieck, Eren, Mikasa, Reiner, Jean, Annie, Historia) or role.",
            _schema({"name": {"type": "string"}}), team_member))
    extra += reference_tools(paths=list(reference_images)) if reference_images else []
    if review_again:
        extra.append(AgentTool(
            "review_again", "Have the critic review a built version of the current model again, nothing rebuilt: "
            "when a version could not be reviewed (no score), or the person asks for it to be scored again. "
            "version: its number.", _schema({"version": {"type": "integer"}}, ["version"]), review_again))
    return extra + [
        AgentTool("start_build", "Start building a new model. title: two to four English words. request: the "
                  "full request in English with everything the person said and your defaults.",
                  _schema({"title": {"type": "string"}, "request": {"type": "string"}}), start_build),
        AgentTool("request_changes", "Ask the team to change the current model. changes: a precise English list.",
                  _schema({"changes": {"type": "string"}}), request_changes),
        AgentTool("project_status", "How the current model is going: status, versions, latest score and notes.",
                  _schema({}, []), project_status),
    ]


def dumps(obj):
    return json.dumps(obj, indent=1, ensure_ascii=False, default=str)
