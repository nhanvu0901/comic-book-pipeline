"""A5: thin wrapper over comic Stage 4 (Cartesia TTS).

Stage 4 resolves paths via its module-level PROJECTS_ROOT; we point that at
ART_PROJECTS_ROOT at call time (runtime attribute override — comic files are
never edited; spec §2/§6.3). The art CLI/app runs in its own process, so the
override never affects a running comic pipeline."""
from .config import ART_PROJECTS_ROOT


def synthesize_art(project_name: str, **kwargs):
    import stages.stage_4.pipeline as s4
    s4.PROJECTS_ROOT = ART_PROJECTS_ROOT
    return s4.synthesize_project(project_name, **kwargs)
