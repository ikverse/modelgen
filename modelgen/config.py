"""Settings from config.yaml, the folders ModelGen writes to, and finding Blender.

The working folders (data, projects, library) live in the project folder unless MODELGEN_WORKDIR
points somewhere else; the tests use that to stay out of the real ones.
"""

import glob
import os
import shutil
from functools import lru_cache
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
PACKAGE = ROOT / "modelgen"
AGENTS_DIR = PACKAGE / "agents"
BPY_LIB = PACKAGE / "bpy_lib"
WEB_DIR = PACKAGE / "web"
RUBRIC_FILE = PACKAGE / "rubric.yaml"
GAME_RUBRIC_FILE = PACKAGE / "rubric_game.yaml"  # a game asset is judged as one, not as a product photo
SHELL_RUBRICS = {"car": PACKAGE / "rubric_car.yaml"}  # shell templates judged by their own rubric

INSTALL_HINT = "winget install --id BlenderFoundation.Blender -e"


def workdir():
    return Path(os.environ.get("MODELGEN_WORKDIR") or ROOT)


def data_dir():
    return workdir() / "data"


def projects_dir():
    return workdir() / "projects"


def library_dir():
    return workdir() / "library"


@lru_cache(maxsize=1)
def settings():
    path = Path(os.environ.get("MODELGEN_CONFIG") or ROOT / "config.yaml")
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def get(section, key, default=None):
    return (settings().get(section) or {}).get(key, default)


def caps(agent_name):
    """The turn and dollar caps for one run of this agent."""
    table = settings().get("caps") or {}
    base = {"max_turns": 12, "max_budget_usd": 1.0, **(table.get("default") or {})}
    return {**base, **(table.get(agent_name) or {})}


def blender_path():
    """The Blender executable, or "" when Blender is not installed. A path saved on the Settings
    page wins over config.yaml; an empty one means find it automatically."""
    from . import settings_store  # late import: settings_store reads this module's folders
    configured = (settings_store.load().get("blender_path") or "").strip()
    if configured:
        return configured if Path(configured).is_file() else ""
    env = os.environ.get("MODELGEN_BLENDER")
    if env and Path(env).is_file():
        return env
    roots = [os.environ.get("ProgramFiles", r"C:\Program Files"),
             os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"),
             os.path.expandvars(r"%LOCALAPPDATA%\Programs")]
    found = []
    for root in roots:
        found += glob.glob(os.path.join(root, "Blender Foundation", "Blender*", "blender.exe"))
    # the Blender the installer downloads when the PC has none: ModelGen's own blender\Blender 5.2\
    found += glob.glob(os.path.join(ROOT, "blender", "Blender*", "blender.exe"))
    if found:
        return sorted(found, key=_version_key)[-1]  # the newest installed version
    return shutil.which("blender") or ""


def _version_key(path):
    name = Path(path).parent.name.replace("Blender", "").strip()
    try:
        return tuple(int(p) for p in name.split("."))
    except ValueError:
        return (0,)


def blender_version(path=None):
    """'4.5' style version from Blender's install folder name, or '' when it cannot be told."""
    path = path if path is not None else blender_path()
    if not path:
        return ""
    name = Path(path).parent.name.replace("Blender", "").strip()
    return name if name and name[0].isdigit() else ""
