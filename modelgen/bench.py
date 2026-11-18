"""The test set: build a fixed set of saved specs and report how well the team does on each.

Run it only when you want to measure a change to the agents (it spends real money, about $1-2 a
version):

    .venv\\Scripts\\python.exe -m modelgen.bench --label before
    .venv\\Scripts\\python.exe -m modelgen.bench --label after --only mug,suv --versions 2

Each saved spec in tests\\bench\\ becomes a new model named bench-<id>-<label>. The brief step is
skipped (the saved spec is used as is), so every run starts from the same spec. The first version
is built, then --versions - 1 next versions from the critic's change lists, exactly as the
"Build next version" button does. The table of scores, gates, landed changes, cost and time goes to
data\\bench\\<date>-<label>.json and is printed at the end.

The normal spending limits are replaced for the bench by one cap for the whole run (--max-usd):
when the bench has spent that much, the build in progress stops and the bench ends.
"""

import argparse
import asyncio
import json
import time
from datetime import datetime
from pathlib import Path

from . import budget, config, db, pipeline

BENCH_DIR = config.ROOT / "tests" / "bench"


def load_specs(only=None):
    specs = [json.loads(p.read_text(encoding="utf-8")) for p in sorted(BENCH_DIR.glob("*.json"))]
    if only:
        specs = [s for s in specs if s["id"] in only]
    return specs


def _spent(projects):
    return sum(db.spend(p)["model"] for p in projects)


def _summary(project, seconds):
    rows = []
    for v in db.versions(project):
        review = v.get("review") or {}
        checks = v.get("checks") or {}
        landed = review.get("landed") or []
        rows.append({"version": v["number"], "status": v["status"], "score": v.get("score"),
                     "passed": bool(v.get("passed")), "scores": review.get("scores"),
                     "gates_passed": checks.get("passed"),
                     "landed": f"{sum(1 for m in landed if m.get('landed'))}/{len(landed)}" if landed else None})
    scored = [r["score"] for r in rows if r["score"] is not None]
    return {"project": project, "versions": rows, "first": scored[0] if scored else None,
            "best": max(scored) if scored else None, "cost_usd": round(db.spend(project)["model"], 2),
            "minutes": round(seconds / 60, 1)}


async def _one(entry, label, versions, projects, gate):
    async with gate:
        proj = db.create_project(f"bench {entry['id']} {label}", entry.get("request") or entry["title"])
        name = proj["name"]
        projects.append(name)
        started = time.monotonic()
        outcome = await pipeline.run(name, request=entry.get("request") or entry["title"])
        for _ in range(versions - 1):
            if not outcome.get("ok", True) and outcome.get("error") in ("stopped", "cancelled", "blender_missing"):
                break
            if not pipeline.open_changes(name):
                break
            outcome = await pipeline.run(name, next_version=True)
        result = _summary(name, time.monotonic() - started)
        result["id"] = entry["id"]
        print(f"{entry['id']}: " + ", ".join(f"v{r['version']} {r['score']}" for r in result["versions"])
              + f"  ${result['cost_usd']}  {result['minutes']} min", flush=True)
        return result


async def main(label, only, versions, parallel, max_usd):
    specs = load_specs(only)
    if not specs:
        raise SystemExit(f"no saved specs in {BENCH_DIR}")
    by_request = {(s.get("request") or s["title"]): s["spec"] for s in specs}
    projects = []

    async def saved_brief(project, request):  # the saved spec, not a new one, so every run starts the same
        return by_request[request]

    async def bench_limit(project, agent_name):
        if _spent(projects) >= max_usd:
            raise budget.BudgetStop(f"stopped: the bench reached its ${max_usd} cap")

    pipeline._brief = saved_brief
    budget.ensure = bench_limit
    gate = asyncio.Semaphore(parallel)
    results = await asyncio.gather(*(_one(s, label, versions, projects, gate) for s in specs))
    report = {"label": label, "date": datetime.now().isoformat(timespec="seconds"), "versions": versions,
              "total_cost_usd": round(_spent(projects), 2), "results": results}
    out = config.ROOT / "data" / "bench" / f"{datetime.now():%Y-%m-%d-%H%M}-{label}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=1), encoding="utf-8")
    print(f"\n{'model':<12} {'first':>6} {'best':>6} {'cost':>7} {'min':>6}  versions")
    for r in results:
        print(f"{r['id']:<12} {r['first'] or '-':>6} {r['best'] or '-':>6} {r['cost_usd']:>7} {r['minutes']:>6}  "
              + "  ".join(f"v{v['version']}:{v['score']}({v['landed'] or '-'})" for v in r["versions"]))
    print(f"\ntotal ${report['total_cost_usd']}; report: {out}")
    return report


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Build the saved test specs and report scores and cost.")
    ap.add_argument("--label", default="run", help="a short name for this run, e.g. before or after")
    ap.add_argument("--only", default="", help="comma-separated spec ids (default: all)")
    ap.add_argument("--versions", type=int, default=2, help="versions per model (default 2)")
    ap.add_argument("--parallel", type=int, default=2, help="models built at once (default 2)")
    ap.add_argument("--max-usd", type=float, default=25.0, help="stop the bench after spending this much")
    a = ap.parse_args()
    only = {s.strip() for s in a.only.split(",") if s.strip()} or None
    asyncio.run(main(a.label, only, max(1, a.versions), max(1, a.parallel), a.max_usd))
