"""
Stage 3: Narration synthesis.

Given comic_context.json (Stage 1 output) + preprocessed/*.json (Stage 2 output),
produce a ≤58s third-person ComicsExplained-style narration script.

Two sub-steps:
  1. propose_modes() — LLM picks top 3 narration angles from the mode catalog
     (lesson / hot_take / recap / feat / etc.); user selects one.
  2. write_script(mode) — LLM writes the final script as an ordered list of
     scenes, each tagged with a (page_ref, panel_ref) for the video assembler.

Output: projects/<slug>/narration.json.
"""
from .cli import main

__all__ = ["main"]

# Note: the propose_modes/write_script functions are intentionally NOT re-exported
# here because they share names with their submodules (stages.stage_3.propose_modes,
# stages.stage_3.write_script). Re-exporting would shadow the submodules and break
# `import stages.stage_3.write_script`. Import from .pipeline explicitly:
#
#   from stages.stage_3.pipeline import propose_modes, write_script, save_narration
