"""Settings the person changes on the page: the spending limits, the Blender path and whether an
installed copy checks for updates.

They are saved in data\\settings.json on top of config.yaml, which keeps the defaults, so the page
never rewrites config.yaml.
"""

import json
import threading

from . import config

_lock = threading.Lock()


def defaults():
    return {
        "daily_usd": float(config.get("budget", "daily_usd", 25)),
        "per_model_usd": float(config.get("budget", "per_model_usd", 6)),
        "blender_path": (config.get("blender", "path") or "").strip(),
        "auto_update": bool(config.get("updates", "auto_check", True)),
    }


def _path():
    return config.data_dir() / "settings.json"


def load():
    out = defaults()
    p = _path()
    if p.is_file():
        try:
            saved = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            saved = {}
        out.update({k: v for k, v in saved.items() if k in out})
    return out


def save(changes):
    """Validate and save the changed settings. Returns the full settings, or raises ValueError."""
    clean = {}
    for key in ("daily_usd", "per_model_usd"):
        if key in changes and changes[key] is not None:
            try:
                value = round(float(changes[key]), 2)
            except (TypeError, ValueError):
                raise ValueError(f"{key} must be a number")
            if value < 0:
                raise ValueError(f"{key} cannot be negative")
            clean[key] = value
    if "blender_path" in changes:
        clean["blender_path"] = str(changes["blender_path"] or "").strip().strip('"')
    if "auto_update" in changes:
        if not isinstance(changes["auto_update"], bool):
            raise ValueError("auto_update must be true or false")
        clean["auto_update"] = changes["auto_update"]
    with _lock:
        current = load()
        current.update(clean)
        p = _path()
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps(current, indent=2, ensure_ascii=False), encoding="utf-8")
        tmp.replace(p)
    return current
