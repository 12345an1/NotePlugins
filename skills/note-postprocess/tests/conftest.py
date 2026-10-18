"""Make the skill test suite independent of the caller's working directory."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest


SKILL_ROOT = Path(__file__).resolve().parents[1]
for import_root in (SKILL_ROOT, SKILL_ROOT / "scripts"):
    if str(import_root) not in sys.path:
        sys.path.insert(0, str(import_root))


@pytest.fixture(scope="session", autouse=True)
def _run_from_skill_root():
    previous = Path.cwd()
    os.chdir(SKILL_ROOT)
    try:
        yield
    finally:
        os.chdir(previous)
