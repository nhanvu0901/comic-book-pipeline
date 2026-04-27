"""
Comic Video Pipeline — Flet desktop UI.

6-stage wizard with human approval between each stage. 3-column Inspector
Layout (stepper nav / artifact preview / controls). Autosaves state to the
project's state.json. Runs as a native macOS Flet app.
"""
from .app import main

__all__ = ["main"]
