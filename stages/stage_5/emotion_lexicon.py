"""Tiny keyword → emotion mapping for chunk-text emotion detection.

Used in Stage 5 hybrid panel scoring to match chunk emotion against panel's
`dominant_emotion` field (which is one of the VLM-output emotion strings).

Keep this list focused on common comic-narration emotion words. Do not
over-engineer — emotion detection here is a tiebreaker boost, not a primary
signal.
"""
from __future__ import annotations

# Each lexicon entry maps a keyword that might appear in chunk text →
# the matching `dominant_emotion` value VLM outputs in Stage 2.
_LEXICON: dict[str, str] = {
    # Anger / fury
    "rage": "angry", "raging": "angry", "fury": "angry", "furious": "angry",
    "outraged": "angry", "wrathful": "angry", "wrath": "angry",
    "snarl": "angry", "snarled": "angry", "snarling": "angry",
    "screams": "angry", "shouted": "angry",
    # Fear
    "trapped": "scared", "haunted": "scared", "terrified": "scared",
    "fear": "scared", "feared": "scared", "horror": "scared",
    "horrified": "scared", "panic": "scared", "panicked": "scared",
    "fled": "scared", "flees": "scared", "fleeing": "scared",
    # Triumph / victory
    "triumphant": "triumph", "victorious": "triumph", "wins": "triumph",
    "won": "triumph", "victory": "triumph",
    # Sadness / grief
    "grief": "sad", "grieving": "sad", "mourned": "sad", "mourning": "sad",
    "wept": "sad", "weeping": "sad", "tears": "sad", "tearful": "sad",
    "sorrow": "sad", "lament": "sad", "broken": "sad",
    # Determination
    "vows": "determined", "vowed": "determined", "determined": "determined",
    "resolved": "determined", "swore": "determined", "promises": "determined",
    # Surprise / shock
    "shocked": "surprised", "stunned": "surprised", "gasped": "surprised",
    "astonished": "surprised", "reveals": "surprised", "twist": "surprised",
    # Confusion
    "confused": "confused", "bewildered": "confused", "puzzled": "confused",
    # Calm / contemplative
    "calmly": "contemplative", "contemplates": "contemplative",
    "ponders": "contemplative", "reflects": "contemplative",
}


def detect_chunk_emotion(text: str) -> str | None:
    """Return one of the VLM dominant_emotion strings if a keyword in the
    lexicon appears in the chunk text. None if no match."""
    if not text:
        return None
    words = {w.lower().strip(",.!?:;\"'") for w in text.split()}
    for keyword, emotion in _LEXICON.items():
        if keyword in words:
            return emotion
    return None
