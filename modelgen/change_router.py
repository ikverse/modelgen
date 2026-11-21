"""What a person's change to a finished model needs, worked out before anyone builds.

A change used to re-run nearly the whole team. The router (agents\\router.md) reads the change and
the model's parts and names the work it needs: which parts' builders, and whether the assembler,
the materials agent and the animator. Everything it does not name keeps the files of the version
the change starts from. When it cannot say (a part to add, a doubt, a failed run) it returns None
and the architect plans the change as before, so a wrong guess never costs the change itself.

direct_character and direct_mechanism read a change that only sets one plain value of a sheet (a
height, a build, the driving speed) with no agent at all; anything else returns None and
goes to the character designer or the engineer.
"""

import re

from . import config, db, runner
from .tools import agent_tools as at

STAGES = ("assembly", "materials", "motion")
ROUTE_SCHEMA = {"type": "object", "properties": {
    "parts": {"type": "array", "items": {"type": "string"}},
    "assembly": {"type": "boolean"}, "materials": {"type": "boolean"}, "motion": {"type": "boolean"},
    "plan": {"type": "boolean"}, "why": {"type": "string"}},
    "required": ["parts", "assembly", "materials", "motion", "plan"]}


def _caps():
    """The router's turn and dollar caps: config.yaml's caps.router line, else these."""
    table = (config.settings().get("caps") or {}).get("router") or {}
    return {"max_turns": 3, "max_budget_usd": 0.10, **table}


def router_tools(sink, names):
    def route(args):
        parts = [str(p) for p in (args.get("parts") or [])]
        unknown = [p for p in parts if p not in names]
        if unknown:
            return {"ok": False, "error": f"unknown parts {unknown}; use the names exactly as listed: {names}. "
                                          "If the change needs a part that is not listed, answer plan: true."}
        sink["route"] = {"parts": list(dict.fromkeys(parts)), "plan": bool(args.get("plan")),
                         **{k: bool(args.get(k)) for k in STAGES}, "why": str(args.get("why") or "")[:300]}
        return {"ok": True}

    return [runner.AgentTool("route", "Say which work the change needs: the parts to reshape, and whether the "
                             "assembly, the materials, the motion or the plan itself must change.", ROUTE_SCHEMA,
                             route)]


def _prompt(changes, spec, plan):
    car = bool(at.shell_template(plan=plan))
    parts = [{k: p.get(k) for k in ("name", "description", "material", "attaches_to", "count") if p.get(k) is not None}
             for p in plan.get("parts", []) if p.get("name")]
    lines = ["Say which work this change needs.", "", "## The change, in the person's words", str(changes), "",
             "## The model", at.dumps({k: spec.get(k) for k in ("object", "summary", "dimensions", "style")
                                       if spec.get(k) is not None}), "",
             f"It {'moves: it has motion' if at.has_motion(spec) else 'does not move: motion is always false'}.",
             *(["It is a car built from a sheet: `body` is the whole skin, and where the wheels, mirrors, wing and "
                "exhaust sit comes from numbers on the body."] if car else []), "",
             "## Its parts", at.dumps(parts), "", "Call `route` once."]
    return "\n".join(lines)


async def route(project, changes, spec, plan):
    """What the change needs: {"parts": [...], "assembly", "materials", "motion": bool, "why"}; or
    None when the architect should plan it (the router said so, named nothing, or did not answer)."""
    names = at.plan_parts(plan)
    if not names or not str(changes or "").strip():
        return None
    sink = {}
    res = await runner.run_agent("router", _prompt(changes, spec, plan), router_tools(sink, names), project=project,
                                 doing="reading your change", step=True, caps=_caps())
    found = sink.get("route")
    if not found:
        db.add_event("warning", project=project, agent="director",
                     data={"text": "the change could not be routed" + (f" ({res.error[:200]})" if res and res.error
                                                                       else "") + "; the architect plans it"})
        return None
    if not at.has_motion(spec):
        found["motion"] = False
    if found["plan"] or not (found["parts"] or any(found[k] for k in STAGES)):
        return None
    return found


def describe(found):
    """A routed change in a few words, for the run panel: who works on it."""
    who = [f"the builder{'s' if len(found['parts']) != 1 else ''} of {', '.join(found['parts'])}"] if found["parts"] else []
    who += [{"assembly": "the assembler", "materials": "the materials agent", "motion": "the animator"}[k]
            for k in STAGES if found[k]]
    return ", ".join(who)


# --- a change that only sets one plain value of a sheet: no agent ------------------------------

MAX_DIRECT_WORDS = 14
_NUMBER = r"(\d+(?:\.\d+)?)"


def _plain(text):
    """The change in lower case when it is one short sentence with exactly one number or none; else
    None: anything longer or with several numbers is the designer's or the engineer's to read."""
    text = " ".join(str(text or "").lower().split()).strip(" .!")
    if not text or len(text.split()) > MAX_DIRECT_WORDS or re.search(r"[;\n]|\band\b|\bbut\b|\balso\b|,", text):
        return None
    return text if len(re.findall(_NUMBER, text)) <= 1 else None


def direct_character(changes, sheet, builds=(), height_range=(40.0, 230.0)):
    """The sheet's own fields a change sets directly, or None. Only a height in centimetres ("make
    her 180 cm tall", "height 172 cm") or a build named alone ("make him muscular")."""
    text = _plain(changes)
    if text is None or not isinstance(sheet, dict):
        return None
    m = re.search(_NUMBER + r"\s*cm\b", text)
    if m and re.search(r"\b(tall|taller|shorter|height|high)\b", text):
        height = float(m.group(1))
        if re.search(r"\b(taller|shorter|more|less|by)\b", text):
            return None  # "5 cm taller" is a difference, not a height
        if height_range[0] <= height <= height_range[1]:
            return {"height_cm": height}
        return None
    if re.search(_NUMBER, text):
        return None
    named = [b for b in builds if re.search(rf"\b{re.escape(b)}\b", text)]
    words = set(re.findall(r"[a-z]+", text))
    filler = {"make", "her", "him", "them", "it", "the", "character", "a", "an", "more", "build", "body", "to", "be",
              "give", "change", "set", "is", "should", "look", "bit", "little", "much", "please", "person", "he", "she"}
    if len(named) == 1 and words - filler == {named[0]} and "more" not in words and sheet.get("build") != named[0]:
        return {"build": named[0], "muscle": None, "weight": None}
    return None


def direct_mechanism(changes, sheet):
    """The mechanism sheet with the one plain value a change sets, or None. Only the driver's speed
    ("run it at 120 rpm"): a tooth count or a size changes values the engineer derives from it (the
    profile shift, the bore), so those stay the engineer's. The sheet is used as it is, so the
    caller checks the speed is in range."""
    text = _plain(changes)
    if text is None or not isinstance(sheet, dict) or sheet.get("rpm") is None:
        return None
    m = re.search(_NUMBER + r"\s*rpm\b", text)
    if not m or re.search(r"\b(faster|slower|more|less|by|teeth|tooth|module)\b", text):
        return None
    return {**sheet, "rpm": float(m.group(1))}
