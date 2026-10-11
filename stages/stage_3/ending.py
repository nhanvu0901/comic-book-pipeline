"""Final narration line shared by every mode that saves a spoken script."""
from __future__ import annotations

import re


SUBSCRIBE_CTA = "Make sure to subscribe for more content."


def ensure_subscribe_cta(narration: dict) -> bool:
    """Append the default spoken CTA once, keeping the last visual beat and timing in sync."""
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
    narration["total_word_count"] = sum(
        int(scene.get("word_count") or len(str(scene.get("text") or "").split()))
        for scene in scenes
    )
    narration["estimated_duration_seconds"] = round(narration["total_word_count"] / wps, 2)
    return True
