"""Generate Advanced SubStation Alpha (.ass) word-by-word captions."""
import re


WORDS_PER_CHUNK = 3
MIN_CHUNK_DURATION = 0.18
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
    """Build an .ass subtitle file string with word-by-word ALL-WHITE reveal."""
    events: list[str] = []
    chunks = _chunk_words(word_timestamps)
    for chunk in chunks:
        start = max(0.0, float(chunk["start"]))
        end = min(total_duration, float(chunk["end"]))
        if end <= start:
            end = start + MIN_CHUNK_DURATION
        text = chunk["text"].upper()
        line = (
            f"Dialogue: 0,{_fmt_time(start)},{_fmt_time(end)},ComicsUnlocked,,"
            f"0,0,0,,{{\\c&Hffffff&}}{text}"
        )
        events.append(line)
    return ASS_HEADER + "\n".join(events) + "\n"


def _chunk_words(words: list[dict]) -> list[dict]:
    cleaned: list[dict] = []
    for w in words:
        text = str(w.get("word", "")).strip()
        if not text:
            continue
        cleaned.append({
            "word": text,
            "start": float(w.get("start", 0.0)),
            "end": float(w.get("end", 0.0)),
        })

    chunks: list[dict] = []
    i = 0
    n = len(cleaned)
    while i < n:
        group = cleaned[i:i + WORDS_PER_CHUNK]
        if not group:
            break
        text = " ".join(g["word"] for g in group)
        text = _strip_punct_for_display(text)
        chunks.append({
            "text": text,
            "start": group[0]["start"],
            "end": group[-1]["end"],
        })
        i += WORDS_PER_CHUNK
    return chunks


def _strip_punct_for_display(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _fmt_time(seconds: float) -> str:
    seconds = max(0.0, float(seconds))
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = seconds - (h * 3600 + m * 60)
    return f"{h}:{m:02d}:{s:05.2f}"
