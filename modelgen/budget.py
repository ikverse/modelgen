"""The daily and per-model spending limits.

Before each pipeline agent run, ensure() checks whether that run could take the spend past either
limit (spent so far plus the agent's own dollar cap). If it could, the director asks the person
whether to raise the limit and keep going or stop the build, and the run waits for the answer.
"""

import asyncio
import weakref

from . import config, db, questions, settings_store

RAISE = "Raise the limit and continue"
STOP = "Stop the build"
_locks = weakref.WeakKeyDictionary()  # one lock per event loop (the tests run several loops)


def _lock():
    loop = asyncio.get_running_loop()
    if loop not in _locks:
        _locks[loop] = asyncio.Lock()
    return _locks[loop]


class BudgetStop(Exception):
    """The person chose to stop the build at a spending limit."""


def limits(project=None):
    s = settings_store.load()
    per_model = s["per_model_usd"]
    if project:
        proj = db.get_project(project) or {}
        own = (proj.get("options") or {}).get("budget_usd")
        if own:
            per_model = float(own)
    return {"daily_usd": s["daily_usd"], "per_model_usd": per_model}


def check(project, agent_name):
    """The limit this run could break, as (which, spent, limit), or None."""
    cap = float(config.caps(agent_name)["max_budget_usd"])
    lim = limits(project)
    spent = db.spend(project)
    if lim["daily_usd"] and spent["today"] + cap > lim["daily_usd"]:
        return "daily", spent["today"], lim["daily_usd"]
    if project and lim["per_model_usd"] and spent["model"] + cap > lim["per_model_usd"]:
        return "model", spent["model"], lim["per_model_usd"]
    return None


def _raise(project, which, spent, limit):
    step = max(5.0 if which == "daily" else 2.0, limit * 0.5)
    new = round(max(limit, spent) + step, 2)
    if which == "daily":
        settings_store.save({"daily_usd": new})
    else:
        proj = db.get_project(project)
        db.update_project(project, options={**(proj.get("options") or {}), "budget_usd": new})
    db.add_event("limit_raised", project=project, agent="director", data={"which": which, "limit": new})
    return new


async def ensure(project, agent_name):
    """Wait until the run may start. Raises BudgetStop when the person stops the build."""
    async with _lock():  # parallel builders ask once, not four times
        while True:
            found = check(project, agent_name)
            if not found:
                return
            which, spent, limit = found
            what = "today's limit" if which == "daily" else "this model's limit"
            if db.project_client(project):  # another program's model: nobody to ask, so it stops here
                raise BudgetStop(f"stopped at {what} (${spent:.2f} of ${limit:.2f})")
            text = (f"The next step could take spending past {what} (${spent:.2f} spent of ${limit:.2f}). "
                    f"Raise the limit and keep going?")
            choice = await questions.ask(project, "director", text, [RAISE, STOP], kind="budget")
            if choice and choice.strip().lower().startswith("raise"):
                _raise(project, which, spent, limit)
                continue
            raise BudgetStop(f"stopped at {what} (${spent:.2f} of ${limit:.2f})")
