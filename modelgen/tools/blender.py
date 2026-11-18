"""Run Blender headless on a job and get back its stdout, any traceback, and what it produced.

A job is a list of steps that a small bootstrap script carries out inside Blender: build parts
from their files, run the assembly and materials scripts, save the .blend, export, measure and
render. Blender never opens a window (background mode, and no console window on Windows).
"""

import asyncio
import json
import os
import subprocess
import tempfile
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from .. import config

BOOTSTRAP = r'''
import importlib.util, json, sys, traceback
import bpy

job = json.load(open(sys.argv[sys.argv.index("--") + 1], encoding="utf-8"))
sys.path.insert(0, job["lib_parent"])
result = {"ok": True, "steps": [], "outputs": {}}
current = "start"


def load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


try:
    from bpy_lib import helpers as h, measure, rig
    if not job.get("opened"):
        h.reset_scene()
    parts = {}
    rs = job.get("render", {})
    for step in job["steps"]:
        kind = step["kind"]
        current = step.get("label") or kind
        if kind == "part":
            module = load(step["file"], "mg_part_" + step["name"])
            built = getattr(module, step.get("func", "build"))()
            parts[step["name"]] = h.finalize_part(built, step["name"], job.get("bevel_width", 0.0015))
        elif kind == "assembly":
            module = load(step["file"], "mg_assembly")
            getattr(module, step.get("func", "assemble"))(dict(parts))
        elif kind == "materials":
            groups = {}
            for obj in rig.model_objects():
                groups.setdefault(obj[h.PART_KEY], []).append(obj)
            module = load(step["file"], "mg_materials")
            getattr(module, step.get("func", "apply"))(groups)
        elif kind == "motion":
            from bpy_lib import motion
            motion.configure(job.get("motion"))
            groups = {}
            for obj in rig.model_objects():
                groups.setdefault(obj[h.PART_KEY], []).append(obj)
            module = load(step["file"], "mg_motion")
            getattr(module, step.get("func", "animate"))(groups)
            result["outputs"]["motion"] = motion.report()
        elif kind == "save":
            bpy.ops.wm.save_as_mainfile(filepath=step["path"])
        elif kind == "export":
            result["outputs"]["exports"] = rig.export_all(step["dir"], step.get("basename", "model"))
        elif kind == "live":
            # the page's live view only: a failure here never fails the agent's attempt
            try:
                result["outputs"]["live"] = rig.export_glb(step["path"])
            except Exception as exc:
                result["outputs"]["live_error"] = str(exc)
        elif kind == "game":
            from bpy_lib import game
            result["outputs"]["game"] = game.export_game(step["dir"], step.get("basename", "model"),
                                                         step.get("budget", 12000), step.get("lods", (0.5, 0.2)))
        elif kind == "measure":
            result["outputs"]["measure"] = measure.measure_scene(step.get("floor_tolerance", 0.002),
                                                                 step.get("contact_tolerance", 0.0015))
        elif kind == "mesh_check":
            result["outputs"]["mesh_check"] = measure.mesh_report(step["pairs"], step.get("moments", 12))
        elif kind == "part_measure":
            result["outputs"].setdefault("part_measure", {})[step["part"]] = measure.part_report(
                step["part"], step.get("contact_tolerance", 0.0015), step.get("floor_tolerance", 0.002))
        elif kind == "material_report":
            from bpy_lib import materials as mat_lib
            result["outputs"]["material_report"] = mat_lib.slot_report(rig.model_objects())
        elif kind == "render":
            rig.set_studio(step.get("studio"))
            used = rig.setup_render(step.get("size", rs.get("size", 768)), step.get("samples", rs.get("samples", 64)),
                                    rs.get("engine", "CYCLES"), rs.get("device", "auto"))
            result["outputs"]["device"] = used
            which = step["set"]
            if which == "views":
                paths = rig.render_views(step["dir"], step["views"], step.get("prefix", "view"))
            elif which == "turntable":
                paths = rig.render_turntable(step["dir"], step.get("count", 8))
            elif which == "wireframe":
                paths = rig.render_wireframe(step["dir"])
            elif which == "closeups":
                paths = rig.render_closeups(step["dir"], step.get("count", 3))
            elif which == "frames":
                paths = rig.render_frames(step["dir"], step.get("count", 6))
            elif which == "context":
                paths = rig.render_in_context(step["dir"], step["part"], step.get("views") or
                                              ("three_quarter", "back_three_quarter"))
            elif which == "asset_views":
                made = rig.render_asset_views(step["dir"], step.get("camera", "persp"), step.get("style", "realistic"),
                                              step.get("shadows", True), step.get("turntable", 0), step.get("only"),
                                              step.get("colours"))
                result["outputs"].setdefault("asset_views", []).append(made)
                paths = made["files"]
            elif which == "motion":
                made = rig.render_motion(step["dir"], step.get("camera", "persp"), step.get("style", "explainer"),
                                         step.get("angle", "front34"), step.get("first"), step.get("last"),
                                         step.get("colours"))
                result["outputs"].setdefault("motion_frames", []).append(made)
                paths = made["files"]
            else:
                raise ValueError("unknown render set " + which)
            result["outputs"].setdefault("renders", {})[step.get("key", which)] = paths
        else:
            raise ValueError("unknown step " + kind)
        result["steps"].append(current)
except Exception:
    result["ok"] = False
    result["failed_step"] = current
    result["traceback"] = traceback.format_exc()

with open(job["result_path"], "w", encoding="utf-8") as f:
    json.dump(result, f, default=str)
'''


@dataclass
class BlenderResult:
    ok: bool
    outputs: dict = field(default_factory=dict)
    traceback: str = ""
    failed_step: str = ""
    stdout: str = ""
    seconds: float = 0.0
    timed_out: bool = False
    missing: bool = False

    def summary(self):
        """What an agent is shown: short, with the traceback if there is one."""
        if self.missing:
            return {"ok": False, "error": f"Blender is not installed on this PC ({config.INSTALL_HINT})"}
        if self.timed_out:
            return {"ok": False, "error": f"Blender ran longer than the time limit ({self.seconds:.0f} s) and was "
                                          "stopped; the geometry is probably far too dense"}
        if not self.ok:
            return {"ok": False, "failed_step": self.failed_step, "traceback": _tail(self.traceback, 40),
                    "blender_output": "" if self.traceback else _tail(self.stdout, 30)}
        return {"ok": True}


def _tail(text, lines):
    parts = (text or "").strip().splitlines()
    return "\n".join(parts[-lines:])


def available():
    return bool(config.blender_path())


_semaphore = None


def _limit():
    global _semaphore
    if _semaphore is None:
        _semaphore = asyncio.Semaphore(int(config.get("blender", "concurrent", 2)))
    return _semaphore


def run_job(steps, open_blend=None, timeout=None, render=None, on_start=None):
    """Run the steps in one headless Blender process and wait for it. on_start(proc) is called
    with the process as soon as it starts, so the caller can kill it (a stopped build does)."""
    exe = config.blender_path()
    if not exe:
        return BlenderResult(ok=False, missing=True)
    folder = Path(tempfile.gettempdir()) / "modelgen-jobs" / uuid.uuid4().hex[:12]
    folder.mkdir(parents=True, exist_ok=True)
    bootstrap = folder / "bootstrap.py"
    bootstrap.write_text(BOOTSTRAP, encoding="utf-8")
    result_path = folder / "result.json"
    job = {
        "lib_parent": str(config.PACKAGE),
        "steps": steps,
        "opened": bool(open_blend),
        "result_path": str(result_path),
        "bevel_width": float(config.get("model", "bevel_width_m", 0.0015)),
        "render": render or {k: config.get("render", k) for k in ("engine", "device", "size", "samples")},
        "motion": {"seconds": float(config.get("motion", "seconds", 2.0)), "fps": int(config.get("motion", "fps", 24))},
    }
    job_path = folder / "job.json"
    job_path.write_text(json.dumps(job, ensure_ascii=False), encoding="utf-8")
    cmd = [exe, "-b", "--factory-startup"]  # never the user's own Blender preferences
    if open_blend:
        cmd.append(str(open_blend))
    cmd += ["-noaudio", "--python-exit-code", "1", "--python", str(bootstrap), "--", str(job_path)]
    flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    timeout = timeout or float(config.get("blender", "timeout_s", 600))
    started = time.monotonic()
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, creationflags=flags)
    if on_start:
        on_start(proc)
    try:
        out_b, err_b = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        kill(proc)
        out_b, _ = proc.communicate()
        out = (out_b or b"").decode("utf-8", "replace")
        return BlenderResult(ok=False, timed_out=True, stdout=out, seconds=time.monotonic() - started)
    seconds = time.monotonic() - started
    stdout = (out_b or b"").decode("utf-8", "replace") + (err_b or b"").decode("utf-8", "replace")
    if not result_path.is_file():
        return BlenderResult(ok=False, stdout=stdout, seconds=seconds,
                             traceback=_tail(stdout, 40) or f"Blender exited with code {proc.returncode}")
    data = json.loads(result_path.read_text(encoding="utf-8"))
    return BlenderResult(ok=bool(data.get("ok")), outputs=data.get("outputs") or {},
                         traceback=data.get("traceback", ""), failed_step=data.get("failed_step", ""),
                         stdout=stdout, seconds=seconds)


def kill(proc):
    """End a Blender process and anything it started. Safe to call on one that has already ended."""
    if proc.poll() is not None:
        return
    if os.name == "nt":
        subprocess.run(["taskkill", "/T", "/F", "/PID", str(proc.pid)], capture_output=True,
                       creationflags=subprocess.CREATE_NO_WINDOW)
    if proc.poll() is None:
        proc.kill()


async def run_job_async(steps, open_blend=None, timeout=None, render=None):
    """The same, from async code, with at most `blender.concurrent` Blender processes at once.
    When the calling task is cancelled (the build was stopped), its Blender process is killed
    instead of being left to run to the end in its thread."""
    lock = threading.Lock()
    state = {"proc": None, "cancelled": False}

    def started(proc):
        with lock:
            state["proc"] = proc
            if state["cancelled"]:  # stopped before Blender had even started
                kill(proc)

    async with _limit():
        try:
            return await asyncio.to_thread(run_job, steps, open_blend, timeout, render, started)
        except asyncio.CancelledError:
            with lock:
                state["cancelled"] = True
                proc = state["proc"]
            if proc:
                kill(proc)
            raise


def part_step(name, file, func="build"):
    return {"kind": "part", "name": name, "file": str(file), "func": func, "label": f"build part '{name}'"}


def assembly_step(file, func="assemble"):
    return {"kind": "assembly", "file": str(file), "func": func, "label": "assembly script"}


def materials_step(file, func="apply"):
    return {"kind": "materials", "file": str(file), "func": func, "label": "materials script"}


def motion_step(file, func="animate"):
    """Run the animator's script after assembly and materials; the job reports what moved."""
    return {"kind": "motion", "file": str(file), "func": func, "label": "motion script"}


def live_step(path):
    """Export a GLB of the scene as it is for the page's live view. Put it last in a job."""
    return {"kind": "live", "path": str(path), "label": "export the live view"}


def game_step(out_dir, basename, budget, lods=(0.5, 0.2)):
    """Write the game-ready files of the opened model (bpy_lib.game.export_game). Run it in a job of
    its own on the saved .blend: it reduces the scene's meshes."""
    return {"kind": "game", "dir": str(out_dir), "basename": basename, "budget": int(budget),
            "lods": [float(v) for v in lods], "label": "game-ready export"}


def asset_views_step(out_dir, camera="persp", style="realistic", only=("views",), turntable=0, size=1024,
                     samples=None, colours=None):
    """Transparent angle pictures of the opened model (bpy_lib.rig.render_asset_views), for the /v1
    API's "views". only picks the sets this run renders: "views", "shadows", "turntable". colours:
    {part: "#rrggbb"} for the explainer style."""
    return {"kind": "render", "set": "asset_views", "key": "asset_views", "dir": str(out_dir), "camera": camera,
            "style": style, "only": list(only), "turntable": int(turntable), "shadows": "shadows" in only,
            "size": int(size), "samples": int(samples or config.get("render", "samples", 96)),
            "colours": colours or {}, "label": f"render angle pictures ({', '.join(only)})"}


def motion_frames_step(out_dir, camera="persp", style="explainer", angle="front34", first=None, last=None,
                       size=1024, samples=None, colours=None):
    """Transparent pictures of the opened model moving through its loop (bpy_lib.rig.render_motion),
    frames first..last of the loop (all of them when left out), for the /v1 API's "motion"."""
    return {"kind": "render", "set": "motion", "key": "motion", "dir": str(out_dir), "camera": camera, "style": style,
            "angle": angle, "first": first, "last": last, "size": int(size), "colours": colours or {},
            "samples": int(samples or config.get("render", "samples", 96)),
            "label": f"render the motion frames {first or ''}-{last or ''}"}


def mesh_check_step(pairs, moments=12):
    """Measure meshing parts at moments across the loop (bpy_lib.measure.mesh_report)."""
    return {"kind": "mesh_check", "pairs": list(pairs), "moments": int(moments), "label": "the mesh check"}


def measure_step():
    return {"kind": "measure", "floor_tolerance": float(config.get("model", "floor_tolerance_m", 0.002)),
            "contact_tolerance": float(config.get("model", "contact_tolerance_m", 0.0015))}


def material_report_step():
    """Report every material on the model by slot (bpy_lib.materials.slot_report)."""
    return {"kind": "material_report", "label": "read the materials"}


def part_measure_step(part):
    """Measure one part in the scene as it is (bpy_lib.measure.part_report)."""
    return {"kind": "part_measure", "part": part, "label": f"measure part '{part}'",
            "floor_tolerance": float(config.get("model", "floor_tolerance_m", 0.002)),
            "contact_tolerance": float(config.get("model", "contact_tolerance_m", 0.0015))}
