"""Stage 3 embed: hard wall-clock deadline + default-off switch.

Guards the 2026-07-27 hang — an OpenRouter embed call blocked a recap run for 31
minutes inside an SSL read because urlopen(timeout=) bounds each socket op, not
the whole request. _call_with_deadline puts a real ceiling on top.
"""
import time

import config
from stages._embedding import _call_with_deadline


def test_deadline_abandons_a_wedged_tier():
    """A tier that never returns must degrade to None, not block the caller."""
    def _wedged(texts, timeout):
        time.sleep(30)                      # far past the wall below
        return ["never gets here"]

    started = time.monotonic()
    got = _call_with_deadline(_wedged, ["x"], timeout=30.0, wall=0.3)
    elapsed = time.monotonic() - started

    assert got is None, "wedged tier must report failure so the chain falls over"
    assert elapsed < 5, f"deadline did not fire — caller blocked {elapsed:.1f}s"


def test_deadline_passes_through_a_healthy_tier():
    def _ok(texts, timeout):
        return [[0.0, 1.0] for _ in texts]

    assert _call_with_deadline(_ok, ["a", "b"], timeout=30.0, wall=5.0) == [[0.0, 1.0]] * 2


def test_a_raising_tier_is_reported_as_down():
    def _boom(texts, timeout):
        raise ConnectionError("server down")

    assert _call_with_deadline(_boom, ["x"], timeout=1.0, wall=5.0) is None


def test_stage3_embed_is_off_by_default(monkeypatch):
    """Manual-first: hand-picked panels overwrite the vector pins anyway."""
    monkeypatch.delenv("STAGE3_NO_EMBED", raising=False)
    assert config.stage3_no_embed() is True

    monkeypatch.setenv("STAGE3_NO_EMBED", "0")
    assert config.stage3_no_embed() is False

    # cli.py sets the env var AFTER config import, so it must be read per call.
    monkeypatch.setenv("STAGE3_NO_EMBED", "1")
    assert config.stage3_no_embed() is True
