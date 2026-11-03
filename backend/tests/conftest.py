"""Hace importable el paquete wolf_devsecops sin instalarlo (dev/CI)."""

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

import pytest  # noqa: E402

from wolf_devsecops.settings import get_settings  # noqa: E402


@pytest.fixture(autouse=True)
def _edition_enterprise():
    """Los tests de features corren con la edición desbloqueada (enterprise). Los
    tests del propio gate (test_edition) la fijan explícitamente dentro del test."""
    s = get_settings()
    prev = s.edition
    s.edition = "enterprise"
    yield
    s.edition = prev
