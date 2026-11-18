"""A small client for ModelGen's /v1 API: copy this one file into another project on this PC.

Standard library only. It finds ModelGen's key in ModelGen's data folder by itself (or give it the
folder, or set MODELGEN_DIR / MODELGEN_KEY / MODELGEN_URL).

    from modelgen_client import ModelGen
    mg = ModelGen(client="village")
    if mg.health().get("ok"):
        job = mg.get("fire hydrant", notes="old cast iron, red", size={"h": 80, "unit": "cm"},
                     outputs=["game", "views"], style="flat", timeout=3600)
        if job["status"] == "done" and job["passed"]:
            front = job["files"]["views/flat-persp/front.png"]["path"]

The full contract (statuses, files, errors) is in ModelGen's API.md.
"""

import json
import os
import time
import urllib.error
import urllib.request
from pathlib import Path

DEFAULT_URL = "http://127.0.0.1:8770"
DEFAULT_DIR = Path.home() / "Documents" / "Claude" / "Claude Code" / "ModelGen"
FINISHED = ("done", "failed", "cancelled")


class ModelGenError(Exception):
    """ModelGen answered with an error (status is its HTTP status; 0 when it could not be reached)."""

    def __init__(self, status, message):
        super().__init__(f"{status}: {message}" if status else message)
        self.status, self.message = status, message


class ModelGen:
    def __init__(self, client, url=None, key=None, modelgen_dir=None, timeout=30):
        self.client = client
        self.url = (url or os.environ.get("MODELGEN_URL") or DEFAULT_URL).rstrip("/")
        self.dir = Path(modelgen_dir or os.environ.get("MODELGEN_DIR") or DEFAULT_DIR)
        self._key = key or os.environ.get("MODELGEN_KEY")
        self.timeout = timeout

    # --- plumbing ------------------------------------------------------------------------

    def key(self):
        if not self._key:
            path = self.dir / "data" / "api_key.txt"
            if not path.is_file():
                raise ModelGenError(0, f"no ModelGen key at {path}; start ModelGen once, or pass key=")
            self._key = path.read_text(encoding="utf-8").strip()
        return self._key

    def _call(self, method, path, body=None, key=True):
        data = json.dumps(body).encode("utf-8") if body is not None else None
        headers = {"Content-Type": "application/json"} if data is not None else {}
        if key:
            headers["X-ModelGen-Key"] = self.key()
        req = urllib.request.Request(self.url + path, data=data, method=method, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                return json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            try:
                detail = json.loads(exc.read().decode("utf-8")).get("detail")
            except Exception:
                detail = exc.reason
            raise ModelGenError(exc.code, str(detail)) from None
        except (urllib.error.URLError, OSError) as exc:
            raise ModelGenError(0, f"ModelGen is not running at {self.url} ({exc})") from None

    # --- the API -------------------------------------------------------------------------

    def health(self):
        """{"ok": True, "blender", "claudein", "queued", "paused_for_usage", ...}, or {"ok": False,
        "error"} when ModelGen is not running. Never raises."""
        try:
            return self._call("GET", "/v1/health", key=False)
        except ModelGenError as exc:
            return {"ok": False, "error": str(exc)}

    def find(self, q, limit=10):
        """{"models": [made models, best first], "ready_made": [catalogue candidates]}."""
        from urllib.parse import quote
        return self._call("GET", f"/v1/models?q={quote(q)}&limit={int(limit)}")

    def request(self, object, **options):
        """Queue a job; returns it at once. options: notes, size {w, d, h, unit}, references,
        request_key, outputs, style, camera, angle, colours, mechanism, design, design_module,
        design_mm_per_px, reuse, max_versions, min_score, budget_usd. object may be None when a
        mechanism or a design is given."""
        body = {"client": self.client, **options}
        if object:
            body["object"] = object
        return self._call("POST", "/v1/jobs", body)

    def parts(self):
        """Every part type ModelGen's part tools build, with its fields, units, ranges and defaults."""
        return self._call("GET", "/v1/parts")

    def mechanism(self, mechanism, timeout=1800, poll=5, **options):
        """A mechanism built exactly from its numbers (no agents, a few minutes), waited for.
        Example: mg.mechanism({"parts": [{"name": "pinion", "type": "spur_gear", "module": 2,
        "teeth": 12, "meshes_with": "wheel"}, {"name": "wheel", "type": "spur_gear", "module": 2,
        "teeth": 24}], "rpm": 20}, outputs=["motion", "game"], style="explainer")."""
        job = self.request(None, mechanism=mechanism, **options)
        return job if job["status"] in FINISHED else self.wait(job["id"], timeout, poll)

    def design(self, design, timeout=1800, poll=5, **options):
        """The 3D part matching one of the Village's vector designs (its JSON object, or the full path of
        its design.json on this PC), waited for. Gears in it are matched tooth for tooth; the job's
        "design" says the scale (mm_per_px), what each part came from, and what could not be matched.
        options as for request, plus design_module (mm) or design_mm_per_px to set the scale."""
        job = self.request(None, design=design, **options)
        return job if job["status"] in FINISHED else self.wait(job["id"], timeout, poll)

    def job(self, job_id):
        return self._call("GET", f"/v1/jobs/{job_id}")

    def jobs(self, status=None, limit=50):
        q = f"?client={self.client}&limit={int(limit)}" + (f"&status={status}" if status else "")
        return self._call("GET", f"/v1/jobs{q}")

    def cancel(self, job_id):
        return self._call("POST", f"/v1/jobs/{job_id}/cancel")

    def wait(self, job_id, timeout=3600, poll=10, on_update=None):
        """Poll until the job is done, failed or cancelled, or until timeout seconds have passed;
        returns the job as it last was (check its status). on_update(job) is called each poll."""
        end = time.monotonic() + timeout
        while True:
            job = self.job(job_id)
            if on_update:
                on_update(job)
            if job["status"] in FINISHED or time.monotonic() >= end:
                return job
            time.sleep(poll)

    def get(self, object, timeout=3600, poll=10, **options):
        """The object's job, waited for: ModelGen reuses a model it made before when one fits
        (reuse=True, the default), else builds it. Returns the job; a timeout leaves it running."""
        job = self.request(object, **options)
        return job if job["status"] in FINISHED else self.wait(job["id"], timeout, poll)
