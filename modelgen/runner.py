"""Runs one ModelGen agent through ClaudeIn and returns its text, tool calls and cost.

ClaudeIn (the tray program on this PC) asks Claude Code with this PC's Claude login, locked down
its own way, so no API key is ever used. Each agent gets its system prompt from its file in
agents\\, no built-in file or shell tools, only the tools ModelGen hands it (run here, in
ModelGen), and a turn cap and a dollar cap from config.yaml. Web search and reading are given only
to an agent whose file says `web: true` (the researcher).
"""

import asyncio
import base64
import inspect
import json
import re
import threading
import time
from contextlib import aclosing
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Callable

import claudein_client
import yaml

from . import budget, config, db, questions
from .claudein_query import MAX_TOOL_TIMEOUT_S, query
from .messages import AgentOptions, AssistantMessage, ResultMessage, TextBlock, ToolUseBlock

ASKERS = {"brief", "architect", "builder", "assembler", "materials"}  # may ask the person mid-run
MAX_CALLS = 12
WEB_TOOLS = ("WebSearch", "WebFetch")  # ClaudeIn's own web tools, for an agent file with `web: true`


@dataclass
class AgentTool:
    """One tool an agent may call. fn takes the arguments dict and returns a dict (or awaits one).
    A result may list PNG/JPEG paths under "_images"; the agent then sees those pictures too."""
    name: str
    description: str
    schema: dict
    fn: Callable[[dict], Any]


@dataclass
class Agent:
    name: str
    model: str
    role: str
    prompt: str
    path: Path
    seat: str = ""  # the character this agent sits in on the map (its own name unless the file says)
    web: bool = False  # may search and read the web (its file says `web: true`); no other agent can


@dataclass
class RunResult:
    agent: str
    text: str = ""
    tool_calls: list = field(default_factory=list)
    cost_usd: float = 0.0
    turns: int | None = None
    ok: bool = True
    error: str | None = None


_ACCOUNT_LIMIT = re.compile(r"hit your (?:\w+ )?limit|usage limit|session limit|weekly limit", re.I)
_RESETS = re.compile(r"resets\s+([^()\n]+?)\s*(?:\(|$)", re.I)


def account_limit(error):
    """When an agent failed because this PC's Claude account is out of usage (not one of the
    agent's own turn or dollar caps), a short line saying so and when it resets; else None.
    Trying again before the reset fails the same way."""
    if not error or not _ACCOUNT_LIMIT.search(error):
        return None
    when = _RESETS.search(error)
    return "Claude session limit reached" + (f", resets {when.group(1).strip()}" if when else "")


# --- agent files ------------------------------------------------------------------

def load_agent(name):
    """An agent file is YAML settings between two '---' lines, then the role prompt."""
    path = config.AGENTS_DIR / f"{name}.md"
    text = path.read_text(encoding="utf-8")
    meta, body = {}, text
    if text.startswith("---"):
        _, head, body = text.split("---", 2)
        meta = yaml.safe_load(head) or {}
    name = meta.get("name", name)
    return Agent(name=name, model=meta["model"], role=meta.get("role", ""), prompt=body.strip(), path=path,
                 seat=meta.get("seat") or name, web=bool(meta.get("web")))


def list_agents():
    return [load_agent(p.stem) for p in sorted(config.AGENTS_DIR.glob("*.md"))]


# --- live status of each character on the map ------------------------------------------
#
# One entry per seat: each agent has one seat named after it, except the builder, which has one
# seat per parallel builder (builder-1 ... builder-4). The studio snapshot reads these entries.

STATUS = {}
_status_lock = threading.RLock()
PUBLIC = ("id", "name", "role", "state", "doing", "since", "tool", "model")


def builder_seats():
    return max(1, int(config.get("loop", "parallel_builders", 4)))


def _entry(seat, agent):
    with _status_lock:
        return STATUS.setdefault(seat, {
            "id": seat, "name": agent.seat.capitalize(), "role": agent.seat, "job": agent.role, "state": "idle",
            "doing": "", "since": db.now(), "tool": None, "calls": [], "tasks": [], "model": agent.model,
            "project": None, "error": None})


def take_seat(agent):
    """The seat this run sits in: the first free builder seat for a builder, else the agent's own."""
    with _status_lock:
        if agent.seat != "builder":
            seat = agent.seat
        else:
            names = [f"builder-{i}" for i in range(1, builder_seats() + 1)]
            seat = next((n for n in names if not STATUS.get(n, {}).get("tasks")), None)
            if seat is None:
                seat = min(names, key=lambda n: len(STATUS.get(n, {}).get("tasks", [])))
        _entry(seat, agent)
        return seat


def _publish(entry, project):
    db.add_event("agent", project=project, agent=entry["id"], data={k: entry.get(k) for k in PUBLIC})


def _set_status(seat, agent, project, add=None, remove=None, ok=True, finish="idle", error=None):
    with _status_lock:
        entry = _entry(seat, agent)
        if add:
            entry["tasks"].append(add)
            entry["project"] = project
            entry["error"] = None
        if remove is not None and remove in entry["tasks"]:
            entry["tasks"].remove(remove)
        if entry["tasks"]:
            entry["state"], entry["doing"] = "working", entry["tasks"][-1]
        else:
            state = finish if ok else "failed"
            entry["state"] = state if state in ("idle", "done", "failed") else "idle"
            entry["doing"] = (remove or entry["doing"]) if entry["state"] != "idle" else ""
            entry["tool"] = None
            if not ok:
                entry["error"] = error
        entry["since"] = db.now()
        _publish(entry, project)


def _call_text(args, doing):
    for key in ("question", "query", "name", "from_part", "title", "changes", "text"):
        value = (args or {}).get(key)
        if isinstance(value, str) and value.strip():
            value = " ".join(value.split())
            return value[:70] + ("..." if len(value) > 70 else "")
    return doing or ""


def _result_text(result):
    if not isinstance(result, dict):
        return ""
    if result.get("error"):
        return "error: " + " ".join(str(result["error"]).split())[:90]
    if result.get("traceback"):
        return "failed with a traceback"
    if result.get("ok") is False:
        return "failed"
    if "answered" in result:
        return f"answer: {result.get('answer')}" if result["answered"] else "no answer in time"
    if result.get("score") is not None:
        return f"score {result['score']}"
    found = result.get("checks")
    if isinstance(found, dict) and "passed" in found:
        return "checks pass" if found["passed"] else "some checks fail"
    for key, word in (("results", "found"), ("entries", "in the library"), ("library", "tuned recipes")):
        if isinstance(result.get(key), list):
            return f"{len(result[key])} {word}"
    if result.get("name"):
        return str(result["name"])
    if result.get("pictures"):
        return "looked at " + ", ".join(result["pictures"])
    return "done"


def record_call(seat, project, tool, text):
    """A tool call starts: the character goes where that tool is used. Returns the call record."""
    call = {"tool": tool, "text": text, "at": db.now(), "ok": None}
    with _status_lock:
        entry = STATUS.get(seat)
        if entry is not None:
            entry["tool"] = tool
            entry["calls"] = (entry["calls"] + [call])[-MAX_CALLS:]
    db.add_event("tool_call", project=project, agent=seat, data={"tool": tool, "text": text})
    return call


def record_result(seat, project, call, ok, text):
    with _status_lock:
        call["ok"], call["result"] = ok, text
        if text and text != "done":
            call["text"] = (call["text"] + " · " if call["text"] else "") + text
    db.add_event("tool_done", project=project, agent=seat,
                 data={"tool": call["tool"], "ok": ok, "text": call["text"], "result": text})


class acting:
    """A pipeline step that is not an agent run, shown on the map as one agent working with a tool
    (the full Blender build on the assembler, rendering on the critic). Set .ok = False if it fails."""

    def __init__(self, name, project, doing, tool):
        self.name, self.project, self.doing, self.tool, self.ok = name, project, doing, tool, True

    def __enter__(self):
        self.agent = load_agent(self.name)
        self.seat = take_seat(self.agent)
        _set_status(self.seat, self.agent, self.project, add=self.doing)
        self.call = record_call(self.seat, self.project, self.tool, self.doing)
        return self

    def __exit__(self, kind, exc, tb):
        ok = self.ok and kind is None
        record_result(self.seat, self.project, self.call, ok, "" if ok else "failed")
        _set_status(self.seat, self.agent, self.project, remove=self.doing, ok=ok, finish="done",
                    error=None if ok else (str(exc) if exc else "failed"))
        return False


def status_snapshot():
    """Every seat's public state: each agent's own seat, and every builder seat used so far."""
    out = []
    for agent in list_agents():
        if agent.seat != agent.name:
            continue  # it works in another agent's seat (the shell planner sits in the architect's)
        seats = [n for n in STATUS if n.startswith("builder-")] if agent.name == "builder" else [agent.name]
        if agent.name == "builder" and "builder-1" not in seats:
            seats.insert(0, "builder-1")
        for seat in sorted(seats):
            entry = _entry(seat, agent)
            out.append({k: entry.get(k) for k in PUBLIC} | {"model": agent.model})
    return out


def ask_tool(project, seat):
    timeout = float(config.get("questions", "timeout_s", 600))

    async def ask(args):
        if db.project_client(project):  # made for another program through the /v1 API: nobody is watching
            return {"answered": False, "note": "No person is available for this model (another program asked for "
                                               "it). Decide yourself, say what you assumed in your finish notes, "
                                               "and carry on."}
        reply = await questions.ask(project, seat, str(args.get("question", "")).strip(),
                                    args.get("options") or [], timeout=timeout)
        if reply is None:
            return {"answered": False, "note": "No answer came in time. Decide yourself, say what you assumed "
                                               "in your finish notes, and carry on."}
        return {"answered": True, "answer": reply}

    return AgentTool(
        "ask_human",
        "Ask the person one short question, only when you are truly blocked by something only they can decide "
        "(a size or a style choice the spec leaves open). Give two to four short options. Most runs need no "
        "question at all. If no answer comes within a few minutes you get none: decide yourself and carry on.",
        {"type": "object", "properties": {"question": {"type": "string"},
                                          "options": {"type": "array", "items": {"type": "string"}}},
         "required": ["question"]},
        ask)


# --- ClaudeIn wiring --------------------------------------------------------------

def tool_content(result):
    """The content blocks the model sees for one tool result: the JSON, then any pictures."""
    images = []
    if isinstance(result, dict):
        images = result.pop("_images", None) or []
    content = [{"type": "text", "text": json.dumps(result, ensure_ascii=False, default=str)}]
    for image in images:
        p = Path(image)
        if not p.is_file():
            continue
        mime = "image/jpeg" if p.suffix.lower() in (".jpg", ".jpeg") else "image/png"
        content.append({"type": "image", "data": base64.b64encode(p.read_bytes()).decode("ascii"),
                        "mimeType": mime})
    return content


def make_tools(agent, tools, project=None, seat=None, doing=""):
    """The agent's tools as ClaudeIn tools. Each handler records the call on the map and returns an
    MCP-style answer: the result as JSON, then any pictures, or the error."""
    seat = seat or agent.seat
    out = []
    for t in tools:
        def make(t=t):
            async def handler(args):
                call = record_call(seat, project, t.name, _call_text(args, doing))
                try:
                    result = t.fn(args or {})
                    if inspect.isawaitable(result):
                        result = await result
                except Exception as exc:  # the agent sees the error and can adapt
                    record_result(seat, project, call, False, f"error: {exc}"[:100])
                    return {"content": [{"type": "text", "text": f"error: {exc}"}], "is_error": True}
                ok = not (isinstance(result, dict) and (result.get("ok") is False or result.get("error")))
                record_result(seat, project, call, ok, _result_text(result))
                return {"content": tool_content(result)}
            return claudein_client.Tool(t.name, t.description or "", t.schema, handler)
        out.append(make())
    return out


def build_options(agent, tools, project=None, seat=None, doing="", caps=None):
    caps = caps or config.caps(agent.name)
    return AgentOptions(
        model=agent.model,
        system=agent.prompt,
        tools=make_tools(agent, tools, project, seat, doing),
        # no built-in file or shell tools; web search and reading only for an agent whose file allows it
        builtin_tools=list(WEB_TOOLS) if agent.web else [],
        max_turns=int(caps["max_turns"]),
        max_budget_usd=float(caps["max_budget_usd"]),
        # ClaudeIn waits at most 10 minutes for one tool answer; a question to the person waits a little
        # less (questions.timeout_s), and a slow Blender run gets the same 10 minutes.
        tool_timeout_s=MAX_TOOL_TIMEOUT_S,
    )


async def run_agent(name, prompt, tools=(), *, project=None, version=None, doing="", query_fn=None,
                    step=False, caps=None, finish=None, seat=None, model=None):
    """Run one agent to the end. Never raises for the agent's own failures: the result says
    ok=False with the error, and the cost spent so far is recorded either way.

    step=True marks a pipeline step: it first waits for the spending limits (raising
    budget.BudgetStop if the person stops the build), gives the agent ask_human if it is one that
    asks, and leaves the character showing done or failed when it ends. Other runs (chat, talk)
    leave it free. caps overrides the agent's turn and dollar caps. seat picks the character (a
    message to builder-3 is answered by builder-3); by default the first free seat is taken. model
    runs this one run on another model than the agent's file names."""
    query_fn = query_fn or query
    agent = load_agent(name)
    if model:  # this run only: the same agent on another model (the builder of a model's main part)
        agent = replace(agent, model=model)
    tools = list(tools)
    if step and project:
        await budget.ensure(project, agent.name)
    task_label = doing or name
    if seat:
        _entry(seat, agent)
    else:
        seat = take_seat(agent)
    if step and project and agent.seat in ASKERS:
        tools.append(ask_tool(project, seat))
    finish = finish or ("done" if step else "idle")
    run_id = db.start_run(agent.name, agent.model, project, version, doing, seat=seat)
    _set_status(seat, agent, project, add=task_label)
    out = RunResult(agent=agent.name)
    started = time.monotonic()
    result = None
    try:
        options = build_options(agent, tools, project, seat, doing, caps)
        async with aclosing(query_fn(prompt=prompt, options=options)) as stream:
            async for msg in stream:
                if isinstance(msg, AssistantMessage):
                    for block in msg.content:
                        if isinstance(block, TextBlock) and block.text.strip():
                            out.text = block.text
                        elif isinstance(block, ToolUseBlock):
                            out.tool_calls.append({"name": block.name.split("__")[-1], "input": block.input})
                elif isinstance(msg, ResultMessage):
                    result = msg
        if result is None:
            out.ok, out.error = False, "ClaudeIn returned no result"
        else:
            out.cost_usd = float(result.total_cost_usd or 0.0)
            out.turns = result.num_turns
            if result.result and not result.is_error:
                out.text = result.result
            if result.subtype in ("error_max_turns", "error_max_budget_usd"):
                out.ok, out.error = False, f"limit hit: {result.subtype}"
            elif result.is_error:
                detail = "; ".join(result.errors or []) or result.result or result.subtype
                out.ok, out.error = False, detail
    except asyncio.CancelledError:
        out.ok, out.error = False, "stopped"  # the build was stopped; recorded below, then passed on
        raise
    except Exception as exc:
        out.ok, out.error = False, f"{type(exc).__name__}: {exc}"
    finally:
        db.finish_run(run_id, out.cost_usd, out.turns, out.ok, out.error)
        _set_status(seat, agent, project, remove=task_label, ok=out.ok, finish=finish, error=out.error)
        db.add_event("run", project=project, agent=seat,
                     data={"doing": task_label, "ok": out.ok, "error": out.error, "cost_usd": out.cost_usd,
                           "turns": out.turns, "seconds": round(time.monotonic() - started, 1),
                           "model": agent.model, "role": agent.seat, "version": version})
    return out
