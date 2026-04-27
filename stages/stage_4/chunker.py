"""
Align Cartesia word timings to the narration's scene boundaries and produce
caption chunks for Stage 5.

Two outputs:
  - SceneTiming[]   — one per narration scene, (start, end) from first/last word
  - CaptionChunk[]  — sentence/phrase chunks with auto-break on long sentences
"""
import re

from .schema import CaptionChunk, SceneTiming, WordTiming


def align_scenes_to_words(
    scenes: list[dict],
    words: list[dict],
) -> list[SceneTiming]:
    """
    Walk through word timestamps sequentially, consuming each scene's
    word_count. Produces scene-level (start, end) timings.
    """
    timings: list[SceneTiming] = []
    cursor = 0
    for s in scenes:
        wc = int(s.get("word_count", 0))
        if wc <= 0:
            wc = max(1, len(str(s.get("text", "")).split()))
        if cursor >= len(words):
            break
        end_idx = min(cursor + wc, len(words))
        start = float(words[cursor]["start"])
        end = float(words[end_idx - 1]["end"])
        timings.append(SceneTiming(
            scene_id=int(s.get("scene_id", len(timings) + 1)),
            text=str(s.get("text", "")),
            start=round(start, 3),
            end=round(end, 3),
            page_ref=int(s.get("page_ref", 0) or 0),
            panel_ref=int(s.get("panel_ref", -1) if s.get("panel_ref") is not None else -1),
        ))
        cursor = end_idx
    return timings


def build_caption_chunks(
    scenes: list[dict],
    words: list[dict],
    *,
    max_words_per_chunk: int = 7,
) -> list[CaptionChunk]:
    """
    Split each scene into caption chunks for display. Sentence boundary first,
    then force-break long sentences at ~7 words.
    """
    chunks: list[CaptionChunk] = []
    cursor = 0
    for s in scenes:
        scene_id = int(s.get("scene_id", 0))
        text = str(s.get("text", "")).strip()
        wc = int(s.get("word_count", 0)) or len(text.split())
        if wc == 0 or cursor >= len(words):
            continue

        scene_words = words[cursor : cursor + wc]
        cursor += wc
        if not scene_words:
            continue

        # 1. split text into sentences
        sentences = _split_sentences(text)

        # 2. walk sentences, pull matching word timings from scene_words
        w_idx = 0
        for sent in sentences:
            sent_tokens = sent.split()
            if not sent_tokens:
                continue
            sent_end_idx = min(w_idx + len(sent_tokens), len(scene_words))
            sent_words = scene_words[w_idx:sent_end_idx]
            w_idx = sent_end_idx
            if not sent_words:
                continue

            # 3. if sentence too long, split into chunks of max_words_per_chunk
            if len(sent_tokens) <= max_words_per_chunk:
                chunks.append(CaptionChunk(
                    text=sent.strip(),
                    start=round(float(sent_words[0]["start"]), 3),
                    end=round(float(sent_words[-1]["end"]), 3),
                    scene_id=scene_id,
                ))
            else:
                for i in range(0, len(sent_tokens), max_words_per_chunk):
                    piece_tokens = sent_tokens[i : i + max_words_per_chunk]
                    piece_words = sent_words[i : i + max_words_per_chunk]
                    if not piece_words:
                        continue
                    chunks.append(CaptionChunk(
                        text=" ".join(piece_tokens),
                        start=round(float(piece_words[0]["start"]), 3),
                        end=round(float(piece_words[-1]["end"]), 3),
                        scene_id=scene_id,
                    ))
    return chunks


def _split_sentences(text: str) -> list[str]:
    """Cheap regex sentence splitter — good enough for 150-word narration."""
    pieces = re.split(r"(?<=[.!?])\s+(?=[A-Z0-9])", text.strip())
    return [p.strip() for p in pieces if p.strip()]


def words_from_dicts(words: list[dict]) -> list[WordTiming]:
    return [WordTiming(word=w["word"], start=float(w["start"]), end=float(w["end"])) for w in words]
