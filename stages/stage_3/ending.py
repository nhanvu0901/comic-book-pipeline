"""Final narration line shared by every mode that saves a spoken script.

The spoken subscribe line is opt-in (``config.SPOKEN_SUBSCRIBE_CTA``, default off): with it
off, the script ends on its real ending, and a copy of the pipeline's own line left over from
an earlier save (the editor pre-fills the saved narration) is taken back off.
"""
from __future__ import annotations

import re


SUBSCRIBE_CTA = "Make sure to subscribe for more content."


def subscribe_cta_enabled() -> bool:
    """The config flag, read at call time so a test or a live toggle takes effect."""
    import config
    return bool(getattr(config, "SPOKEN_SUBSCRIBE_CTA", False))


def _resync_counts(narration: dict, scenes: list) -> None:
    wps = float(narration.get("words_per_second") or 3.4)
    narration["total_word_count"] = sum(
        int(scene.get("word_count") or len(str(scene.get("text") or "").split()))
        for scene in scenes
    )
    narration["estimated_duration_seconds"] = round(narration["total_word_count"] / wps, 2)


def _strip_suffix(text: str) -> str | None:
    """`text` without a trailing SUBSCRIBE_CTA, or None when it does not end with it."""
    stripped = str(text or "").rstrip()
    if not stripped.endswith(SUBSCRIBE_CTA):
        return None
    return stripped[: -len(SUBSCRIBE_CTA)].rstrip()


def remove_subscribe_cta(narration: dict) -> bool:
    """Take the pipeline's own SUBSCRIBE_CTA off the end of the last scene (and its last visual
    beat), keeping counts and timing in sync. Only that exact sentence is removed, and never
    when it is the whole line: any other wording is the author's and stays."""
    scenes = narration.get("scenes") or []
    if not scenes:
        return False
    last = scenes[-1]
    rest = _strip_suffix(last.get("text"))
    if not rest:
        return False

    last["text"] = rest
    beats = last.get("visual_beats") or []
    if beats:
        final_beat = beats[-1]
        if isinstance(final_beat, dict):
            kept = _strip_suffix(final_beat.get("text"))
            if kept:
                final_beat["text"] = kept
            elif kept == "":
                beats.pop()
        else:
            kept = _strip_suffix(final_beat)
            if kept:
                beats[-1] = kept
            elif kept == "":
                beats.pop()

    wps = float(narration.get("words_per_second") or 3.4)
    cta_words = len(SUBSCRIBE_CTA.split())
    last["word_count"] = len(rest.split())
    if last.get("target_seconds"):
        last["target_seconds"] = round(max(float(last["target_seconds"]) - cta_words / wps,
                                           last["word_count"] / wps), 2)
    _resync_counts(narration, scenes)
    return True


def ensure_subscribe_cta(narration: dict, *, enabled: bool | None = None) -> bool:
    """Apply the spoken-CTA setting to a narration about to be saved. Returns True only when
    the CTA was appended.

    Enabled (``enabled=True``, or ``config.SPOKEN_SUBSCRIBE_CTA`` when ``enabled`` is None):
    append the CTA once, keeping the last visual beat and timing in sync. Disabled (the
    default): append nothing and remove a leftover copy of the pipeline's own line."""
    if enabled is None:
        enabled = subscribe_cta_enabled()
    if not enabled:
        remove_subscribe_cta(narration)
        return False

    scenes = narration.get("scenes") or []
    if not scenes:
        return False
    last = scenes[-1]
    original = str(last.get("text") or "").strip()
    if not original or re.search(r"\bsubscribe\b", original, re.IGNORECASE):
        return False

    last["text"] = f"{original} {SUBSCRIBE_CTA}"
    beats = last.get("visual_beats") or []
    if beats:
        final_beat = beats[-1]
        if isinstance(final_beat, dict):
            final_beat["text"] = f"{str(final_beat.get('text') or '').strip()} {SUBSCRIBE_CTA}".strip()
        else:
            beats[-1] = f"{str(final_beat).strip()} {SUBSCRIBE_CTA}".strip()

    cta_words = len(SUBSCRIBE_CTA.split())
    wps = float(narration.get("words_per_second") or 3.4)
    last["word_count"] = len(last["text"].split())
    last["target_seconds"] = round(float(last.get("target_seconds") or len(original.split()) / wps)
                                    + cta_words / wps, 2)
    _resync_counts(narration, scenes)
    return True
