"""Questions an agent asks the person while it works, and the person's answers.

An agent's ask_human tool, and the spending limit, call ask(); it waits until answer() is called
with the person's choice, or until the timeout, when it returns None and the agent decides itself.
While a question is open the map shows its agent as waiting, with the question and its options.
Open questions live in memory: a build does not survive a restart, so neither do they.
"""

import asyncio
import itertools

from . import db

OPEN = {}
_ids = itertools.count(1)


def _public(q):
    return {"id": q["id"], "agent": q["agent"], "project": q["project"], "text": q["text"],
            "options": list(q["options"]), "asked": q["asked"], "kind": q["kind"]}


async def ask(project, agent, text, options=(), timeout=None, kind="agent"):
    """Ask the person and wait. agent is the map id (builder-2, critic). Returns the answer text,
    or None when the timeout passes first."""
    fut = asyncio.get_running_loop().create_future()
    qid = f"q{next(_ids)}"
    q = {"id": qid, "agent": agent, "project": project, "text": text, "options": [str(o) for o in options][:4],
         "asked": db.now(), "kind": kind, "future": fut}
    OPEN[qid] = q
    db.add_event("question", project=project, agent=agent, data=_public(q))
    try:
        if timeout:
            return await asyncio.wait_for(asyncio.shield(fut), timeout)
        return await fut
    except asyncio.TimeoutError:
        db.add_event("question_timeout", project=project, agent=agent, data={"id": qid, "text": text})
        return None
    finally:
        OPEN.pop(qid, None)


def find(agent=None, qid=None):
    if qid and qid in OPEN:
        return OPEN[qid]
    for q in list(OPEN.values()):
        if agent and (q["agent"] == agent or q["agent"].split("-")[0] == agent):
            return q
    return None


def answer(agent=None, choice="", qid=None):
    """Give the answer to the agent's open question. Returns the question, or None if none is open."""
    q = find(agent, qid)
    if not q or q["future"].done():
        return None
    fut = q["future"]
    fut.get_loop().call_soon_threadsafe(lambda: fut.done() or fut.set_result(choice))
    db.add_event("answered", project=q["project"], agent=q["agent"], data={"id": q["id"], "choice": choice})
    return _public(q)


def for_agent(agent):
    q = find(agent)
    return _public(q) if q else None


def open_questions(project=None):
    return [_public(q) for q in OPEN.values() if project is None or q["project"] == project]
