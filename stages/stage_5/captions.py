"""Generate Advanced SubStation Alpha (.ass) word-by-word captions."""
import re

from config import CAPTION_FONT_SIZE, CAPTION_ALIGNMENT, CAPTION_MARGIN_V, CAPTION_OUTLINE


WORDS_PER_CHUNK = 3
MIN_CHUNK_DURATION = 0.18

# Karaoke-fill colours (ASS uses BGR hex, not RGB).
_SPOKEN_COLOR = "&H00FFFF&"   # yellow  — words already reached by the voice
_UNSPOKEN_COLOR = "&Hffffff&"  # white   — words not yet spoken

# ponytail: outline formatted with :g so the default (8.0) prints as "8",
# byte-identical to the header before these knobs were parameterized.
ASS_HEADER = f"""[Script Info]
ScriptType: v4.00+
PlayResX: 1080
PlayResY: 1920
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: ComicsUnlocked,Anton,{CAPTION_FONT_SIZE},&H00FFFFFF,&H00000000,&H00000000,&H00000000,1,0,0,0,100,100,0,0,1,{CAPTION_OUTLINE:g},0,{CAPTION_ALIGNMENT},60,60,{CAPTION_MARGIN_V},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""

# Entrance pop for a chunk's first frame on screen: scale 100% -> 108% -> 100% over
# ~120ms (60ms up, 60ms down). Prepended only to the FIRST word-event of a chunk —
# the karaoke-fill events that follow (as the voice advances word by word) don't
# re-pop. \t timing is relative to that event's own Start, so this is safe per-event.
_POP_TAG = r"{\fscx100\fscy100\t(0,60,\fscx108\fscy108)\t(60,120,\fscx100\fscy100)}"


def build_ass(word_timestamps: list[dict], total_duration: float,
             caption_pop: bool | None = None) -> str:
    """Build an .ass subtitle string with karaoke-fill highlighting: within each
    3-word chunk, every word starts white and turns yellow as the voice reaches
    it (already-spoken words stay yellow). One Dialogue event per word — each
    spans from that word's onset to the next word's onset, so the fill advances
    with no gap or flicker. caption_pop=None reads config.CAPTION_POP."""
    if caption_pop is None:
        from config import CAPTION_POP as caption_pop
    events: list[str] = []
    for chunk in _chunk_words(word_timestamps):
        words = chunk["words"]  # [{"text","start","end"}], display-normalized, non-empty
        if not words:
            continue
        chunk_end = min(total_duration, float(words[-1]["end"]))
        for k, w in enumerate(words):
            seg_start = max(0.0, float(w["start"]))
            # Hold each highlight until the next word begins (chunk end for the last).
            seg_end = float(words[k + 1]["start"]) if k + 1 < len(words) else chunk_end
            seg_end = min(total_duration, seg_end)
            if seg_end <= seg_start:
                seg_end = seg_start + MIN_CHUNK_DURATION
            text = _colorize(words, k)
            if caption_pop and k == 0:
                text = _POP_TAG + text
            events.append(
                f"Dialogue: 0,{_fmt_time(seg_start)},{_fmt_time(seg_end)},ComicsUnlocked,,"
                f"0,0,0,,{text}"
            )
    return ASS_HEADER + "\n".join(events) + "\n"


def _colorize(words: list[dict], spoken_upto: int) -> str:
    """Render the chunk with words[0..spoken_upto] yellow, the rest white."""
    parts = []
    for j, w in enumerate(words):
        color = _SPOKEN_COLOR if j <= spoken_upto else _UNSPOKEN_COLOR
        parts.append(f"{{\\c{color}}}{w['text'].upper()}")
    return " ".join(parts)


def _chunk_words(words: list[dict]) -> list[dict]:
    cleaned: list[dict] = []
    for w in words:
        raw = str(w.get("word", "")).strip()
        if not raw:
            continue
        disp = _strip_punct_for_display(raw)
        if not disp:  # pure-punctuation token — keep timing off the captions
            continue
        cleaned.append({
            "text": disp,
            "start": float(w.get("start", 0.0)),
            "end": float(w.get("end", 0.0)),
        })

    chunks: list[dict] = []
    for i in range(0, len(cleaned), WORDS_PER_CHUNK):
        group = cleaned[i:i + WORDS_PER_CHUNK]
        if not group:
            break
        chunks.append({
            "words": group,
            "start": group[0]["start"],
            "end": group[-1]["end"],
        })
    return chunks


_ASCII_NORMALIZE = {
    # Anton (display font) lacks glyphs for these — they render as garbage (€/boxes).
    "—": " - ",   # em-dash
    "–": "-",     # en-dash
    "‘": "'",     # left single quote
    "’": "'",     # right single quote / apostrophe
    "“": '"',     # left double quote
    "”": '"',     # right double quote
    "…": "...",   # ellipsis
    " ": " ",     # non-breaking space
    "­": "",      # soft hyphen
}


def _fix_double_encoded(text: str) -> str:
    """Cartesia sometimes returns text that's UTF-8 bytes mis-decoded as Latin-1
    (em-dash → 'â\\x80\\x94'). If Latin-1-range chars are present, try the
    'latin-1 → utf-8' round-trip to recover the real Unicode codepoint."""
    if not any(0x80 <= ord(c) <= 0xFF for c in text):
        return text
    try:
        return text.encode("latin-1").decode("utf-8")
    except (UnicodeEncodeError, UnicodeDecodeError):
        return text


def _strip_punct_for_display(text: str) -> str:
    text = _fix_double_encoded(text)
    for ch, rep in _ASCII_NORMALIZE.items():
        text = text.replace(ch, rep)
    # Final guard: any char still outside ASCII gets replaced with space — Anton
    # has no fallback glyphs and would render boxes/euro signs otherwise.
    text = "".join(c if ord(c) < 128 else " " for c in text)
    return re.sub(r"\s+", " ", text).strip()


def _fmt_time(seconds: float) -> str:
    seconds = max(0.0, float(seconds))
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = seconds - (h * 3600 + m * 60)
    return f"{h}:{m:02d}:{s:05.2f}"
