"""
Launch the Flet UI:
    python -m ui
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import flet as ft
from .app import main


if __name__ == "__main__":
    ft.app(target=main)
