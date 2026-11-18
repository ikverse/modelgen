"""Shared test setup: every test gets its own empty working folder (data, projects, library), and
tests marked `blender` skip with the install command when Blender is not on this PC."""

import os
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("MODELGEN_WORKDIR", tempfile.mkdtemp(prefix="modelgen-tests-"))

from modelgen import config as mg_config, db  # noqa: E402

SKIP_BLENDER = f"Blender not installed — run: {mg_config.INSTALL_HINT}"
FIXTURES = Path(__file__).resolve().parent / "fixtures"


def pytest_configure(config):
    config.addinivalue_line("markers", "blender: needs Blender installed (skipped when it is not)")


def pytest_collection_modifyitems(config, items):
    if mg_config.blender_path():
        return
    skip = pytest.mark.skip(reason=SKIP_BLENDER)
    for item in items:
        if "blender" in item.keywords:
            item.add_marker(skip)


@pytest.fixture(autouse=True)
def workdir(tmp_path, monkeypatch):
    monkeypatch.setenv("MODELGEN_WORKDIR", str(tmp_path))
    db.close()
    yield tmp_path
    db.close()
