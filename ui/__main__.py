"""
Launch the Flet UI:
    python -m ui
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from . import _flet_compat  # noqa: F401 — patches flet 0.85 before any screen imports it
import flet as ft
from .app import main


if __name__ == "__main__":
    ft.run(main)
