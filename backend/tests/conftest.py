"""Hace importable el paquete wolf_devsecops sin instalarlo (dev/CI)."""

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
