"""ModelGen's API for other programs on this PC, at /v1 (documented in API.md; the work is in jobs.py).

Only programs on this PC can call it: a request from any other address is refused, and so is one a
web page sends (it carries an Origin header), so a website open in the browser cannot start builds.
Every call but /v1/health also needs the key in data\\api_key.txt, sent as X-ModelGen-Key.
"""

import secrets

from fastapi import APIRouter, Body, Depends, HTTPException, Request

from . import db, jobs

LOCAL = {"127.0.0.1", "::1", "localhost", "testclient"}  # testclient: FastAPI's in-process test client


def local_only(request: Request):
    host = request.client.host if request.client else ""
    if host not in LOCAL:
        raise HTTPException(403, "the ModelGen API only answers programs on this PC")
    if request.headers.get("origin"):
        raise HTTPException(403, "the ModelGen API does not answer web pages; call it from a program")


def with_key(request: Request):
    local_only(request)
    given = request.headers.get("x-modelgen-key") or ""
    if not secrets.compare_digest(given.encode(), jobs.api_key().encode()):
        raise HTTPException(401, "send the key from ModelGen's data\\api_key.txt as the X-ModelGen-Key header")


router = APIRouter(prefix="/v1")


@router.get("/health", dependencies=[Depends(local_only)])
def health():
    return jobs.health()


@router.post("/jobs", dependencies=[Depends(with_key)])
def new_job(payload: dict = Body(...)):
    try:
        return jobs.submit(payload)
    except jobs.JobError as exc:
        raise HTTPException(400, str(exc))


@router.get("/jobs", dependencies=[Depends(with_key)])
def list_jobs(client: str | None = None, status: str | None = None, limit: int = 50):
    wanted = [s for s in (status or "").split(",") if s] or None
    return [jobs.public(j) for j in db.list_jobs(client, wanted, min(max(limit, 1), 500))]


@router.get("/jobs/{job_id}", dependencies=[Depends(with_key)])
def get_job(job_id: str):
    job = db.get_job(job_id)
    if not job:
        raise HTTPException(404, "no such job")
    return jobs.public(job)


@router.post("/jobs/{job_id}/cancel", dependencies=[Depends(with_key)])
def cancel_job(job_id: str):
    out = jobs.cancel(job_id)
    if out is None:
        raise HTTPException(404, "no such job")
    return out


@router.get("/parts", dependencies=[Depends(with_key)])
def part_types():
    return jobs.parts_catalogue()


@router.get("/models", dependencies=[Depends(with_key)])
def find_models(q: str = "", limit: int = 10, min_score: float = jobs.DEFAULTS["min_score"]):
    if not q.strip():
        raise HTTPException(400, "give q: the object to look for (\"fire hydrant\")")
    if not 0 <= min_score <= 100:
        raise HTTPException(400, "min_score must be 0 to 100")
    return jobs.find_models(q, min(max(limit, 1), 50), min_score)


@router.get("/models/{name}", dependencies=[Depends(with_key)])
def get_model(name: str):
    proj = db.get_project(name)
    number = (proj or {}).get("accepted_version")
    v = db.get_version(name, number) if number else None
    if not v:
        raise HTTPException(404, "no accepted model by that name")
    return jobs._model_info(proj, v, jobs.OUTPUTS)
