"""Generate Advanced SubStation Alpha (.ass) word-by-word captions."""
import re


WORDS_PER_CHUNK = 3
MIN_CHUNK_DURATION = 0.18

# Karaoke-fill colours (ASS uses BGR hex, not RGB).
_SPOKEN_COLOR = "&H00FFFF&"   # yellow  — words already reached by the voice
_UNSPOKEN_COLOR = "&Hffffff&"  # white   — words not yet spoken

ASS_HEADER = """[Script Info]
ScriptType: v4.00+
PlayResX: 1080
PlayResY: 1920
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: ComicsUnlocked,Anton,84,&H00FFFFFF,&H00000000,&H00000000,&H00000000,1,0,0,0,100,100,0,0,1,8,0,5,60,60,0,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""


def build_ass(word_timestamps: list[dict], total_duration: float) -> str:
    """Build an .ass subtitle string with karaoke-fill highlighting: within each
    3-word chunk, every word starts white and turns yellow as the voice reaches
    it (already-spoken words stay yellow). One Dialogue event per word — each
    spans from that word's onset to the next word's onset, so the fill advances
    with no gap or flicker."""
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
