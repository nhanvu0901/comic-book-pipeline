"""Shared test fixtures.

PANEL_TEXT_EMBED flipped to OFF by default (Master 2026-07-24: panels are hand-picked in the
review UI, so the Qwen text-embed + cosine matcher no longer drives the render). The many
panel-matching suites here validate that COSINE pipeline, which is still present and
env-reactivatable. This autouse fixture turns it back ON for tests so those suites keep
exercising the real path; the no-embed suite (test_no_embed_refactor.py) opts out locally by
setting the module attr back to False.

STAGE3_NO_EMBED got the same treatment on 2026-07-27 (default flipped to ON = skip, after an
OpenRouter embed call hung a recap run for 31 minutes). Same reasoning, same remedy: the
grounding/pin suites still validate the vector path, so pin it ON here and let individual
tests set STAGE3_NO_EMBED=1 when they mean to assert the skip.
"""
import pytest


@pytest.fixture(autouse=True)
def _stage3_vector_path_on(monkeypatch):
    """Legacy default — tests exercise the vector path unless they opt out explicitly."""
    monkeypatch.setenv("STAGE3_NO_EMBED", "0")
    yield


@pytest.fixture(autouse=True)
def _cosine_panel_path_on(monkeypatch):
    for modname in ("stages.stage_5.shots", "stages.review_gate", "stages.sentence_match"):
        try:
            mod = __import__(modname, fromlist=["_"])
        except Exception:
            continue
        if hasattr(mod, "PANEL_TEXT_EMBED"):
            monkeypatch.setattr(mod, "PANEL_TEXT_EMBED", True)
    yield
