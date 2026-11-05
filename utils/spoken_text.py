"""Deterministic spoken-form normaliser for TTS narration (English).

Local Chatterbox does no number/symbol normalisation, so "Venom #2" or "FBI" comes out
garbled. ``to_spoken`` rewrites such text into plain words. It is deterministic,
idempotent (``to_spoken(to_spoken(x)) == to_spoken(x)``), dependency-free and GENERAL:
no title, character or comic names live here, only mechanical rules.

Passes run in a fixed order (issue numbers before plain numbers, years before cardinals).
"""
from __future__ import annotations

import re

_ONES = ["zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine",
         "ten", "eleven", "twelve", "thirteen", "fourteen", "fifteen", "sixteen",
         "seventeen", "eighteen", "nineteen"]
_TENS = ["", "", "twenty", "thirty", "forty", "fifty", "sixty", "seventy", "eighty", "ninety"]
_SCALES = ["", "thousand", "million", "billion", "trillion"]


# --------------------------------------------------------------------------- number words
def _below_100(n: int) -> str:
    if n < 20:
        return _ONES[n]
    t, o = divmod(n, 10)
    return _TENS[t] + (f"-{_ONES[o]}" if o else "")


def _below_1000(n: int) -> str:
    h, r = divmod(n, 100)
    parts = []
    if h:
        parts.append(f"{_ONES[h]} hundred")
    if r:
        parts.append(_below_100(r))
    return " ".join(parts)


def cardinal(n: int) -> str:
    """int -> English words ('one hundred twenty-five'). Beyond trillions: digit by digit."""
    if n < 0:
        return "minus " + cardinal(-n)
    if n < 20:
        return _ONES[n]
    if n >= 10 ** 15:
        return " ".join(_ONES[int(d)] for d in str(n))
    groups = []
    i = 0
    while n:
        n, g = divmod(n, 1000)
        if g:
            groups.append(_below_1000(g) + (" " + _SCALES[i] if i else ""))
        i += 1
    return " ".join(reversed(groups))


_ORD_IRREG = {"one": "first", "two": "second", "three": "third", "five": "fifth",
              "eight": "eighth", "nine": "ninth", "twelve": "twelfth"}


def ordinal(n: int) -> str:
    words = cardinal(n)
    last = re.split(r"[ -]", words)[-1]
    prefix = words[: len(words) - len(last)]
    if last in _ORD_IRREG:
        last = _ORD_IRREG[last]
    elif last.endswith("y"):
        last = last[:-1] + "ieth"
    else:
        last += "th"
    return prefix + last


def year_words(n: int) -> str:
    """1984 -> 'nineteen eighty-four', 2008 -> 'two thousand eight', 1900 -> 'nineteen hundred'."""
    c, yy = divmod(n, 100)
    if 2000 <= n <= 2009:
        return "two thousand" + (f" {_ONES[yy]}" if yy else "")
    if yy == 0:
        return f"{cardinal(c)} hundred"
    if yy < 10:
        return f"{cardinal(c)} oh {_ONES[yy]}"
    return f"{cardinal(c)} {_below_100(yy)}"


def _issue_words(n: int) -> str:
    """Comic convention for 101-999 (not multiples of 100): 125 -> 'one twenty-five'."""
    if 101 <= n <= 999 and n % 100:
        h, r = divmod(n, 100)
        return f"{_ONES[h]} " + (f"oh {_ONES[r]}" if r < 10 else _below_100(r))
    return cardinal(n)


def _digits(s: str) -> str:
    return " ".join(_ONES[int(d)] for d in s)


def _num_words(int_part: str, frac: str | None = None, *, year_ok: bool = True) -> str:
    """Words for a digit string (thousands commas allowed in int_part)."""
    clean = int_part.replace(",", "")
    if frac is not None:
        base = _digits(clean) if len(clean) > 1 and clean.startswith("0") else cardinal(int(clean))
        return f"{base} point {_digits(frac)}"
    if len(clean) > 1 and clean.startswith("0"):
        return _digits(clean)
    n = int(clean)
    if year_ok and "," not in int_part and len(clean) == 4 and 1100 <= n <= 2099:
        return year_words(n)
    return cardinal(n)


def _plural_last(words: str) -> str:
    """'nineteen eighty' -> 'nineteen eighties'."""
    m = re.match(r"^(.*?)([a-z]+)$", words)
    head, last = m.group(1), m.group(2)
    return head + (last[:-1] + "ies" if last.endswith("y") else last + "s")


# --------------------------------------------------------------------------- abbreviations
_ABBREV = [
    (re.compile(r"\bMr\."), "Mister"), (re.compile(r"\bMrs\."), "Missus"),
    (re.compile(r"\bDr\."), "Doctor"), (re.compile(r"\bProf\."), "Professor"),
    (re.compile(r"\bCapt\."), "Captain"), (re.compile(r"\bSgt\."), "Sergeant"),
    (re.compile(r"\bLt\."), "Lieutenant"), (re.compile(r"\bGen\."), "General"),
    (re.compile(r"\bapprox\.", re.I), "about"),
    (re.compile(r"\be\.g\."), "for example"), (re.compile(r"\bi\.e\."), "that is"),
]
_VS = re.compile(r"(?<=\w)\s+(?:vs\.?|v\.)\s+(?=\w)", re.I)
_ETC = re.compile(r"\betc\.(?P<end>(?=\s+[\"“'(]?[A-Z])|(?=\s*$))?")


def _abbrev(t: str) -> str:
    for rx, rep in _ABBREV:
        t = rx.sub(rep, t)
    t = _VS.sub(" versus ", t)
    # 'etc.' keeps its period only when it also ends the sentence
    return _ETC.sub(lambda m: "and so on" + ("." if m.group("end") is not None else ""), t)


# --------------------------------------------------------------------------- acronyms
_COMMON_SHORT = set("""an as at be by do go he if in is it me my no of on or so to up we oh hi
the and but for not you all any can her was one our out day get has him his how its new now old see
two way who boy did let put say she too use war end yes yet off own sir man men big bad far few god
hit key low top win won""".split())
_ONSET2 = {"bl", "br", "ch", "cl", "cr", "dr", "fl", "fr", "gl", "gr", "kn", "ph", "pl", "pr",
           "sc", "sh", "sk", "sl", "sm", "sn", "sp", "st", "sw", "th", "tr", "tw", "wh", "wr",
           "qu", "gn", "ps"}
_ONSET3 = {"scr", "spr", "spl", "str", "shr", "thr", "sch", "squ", "chr"}
_VOWELS = set("aeiou")


def _pronounceable(w: str) -> bool:
    """4+ letters, has a vowel, no long consonant runs, plausible English onset.
    A trailing cluster of up to 3 (4 when it ends in s) is tolerated: HOLDS, NIGHTS."""
    lw = w.lower()
    if len(lw) < 4 or not (set(lw) & _VOWELS):
        return False
    for m in re.finditer(r"[^aeiou]+", lw):
        s, e = m.span()
        n = e - s
        if s == 0:
            if n >= 4 or (n == 3 and lw[:3] not in _ONSET3) or (n == 2 and lw[:2] not in _ONSET2):
                return False
        elif e == len(lw):
            if n > 4 or (n == 4 and not lw.endswith("s")) or (n == 3 and lw[-1] not in "std"):
                return False
        elif n >= 3:
            return False
    return True


def _initials(letters: str) -> str:
    return " ".join(c + "." for c in letters)


_DOTTED = re.compile(r"(?<![\w.])(?P<a>(?:[A-Z]\.){2,})(?P<end>(?=\s+[\"“'(]?[A-Z])|(?=\s*$))?")
_CAPS = re.compile(r"(?<![\w'’\-.])([A-Z]{2,})(?:(['’]s)(?!\w))?(?![\w'’\-])(\.)?")


def _dotted_sub(m: re.Match) -> str:
    letters = m.group("a").replace(".", "")
    if len(letters) >= 4 and _pronounceable(letters):
        return letters.capitalize() + ("." if m.group("end") is not None else "")
    return _initials(letters)


def _caps_sub(m: re.Match) -> str:
    w, poss, dot = m.group(1), m.group(2) or "", m.group(3) or ""
    if w == "OK":
        return "okay" + poss + dot
    if set(w) <= set("IVX"):  # roman-numeral shaped (II, XI, ...) stays
        return m.group(0)
    if (len(w) <= 3 and w.lower() in _COMMON_SHORT) or (len(w) >= 4 and _pronounceable(w)):
        return w.capitalize() + poss + dot
    # spaced initials; their own final period doubles as the sentence period
    return _initials(w) + poss


# --------------------------------------------------------------------------- issue numbers
_NOT_TITLE = set("""The A An It This That These Those He She They We I You And But So Then As In On At
Of For Is Was Be Not Or If When While Who What Why How There Here His Her Its Their Our My Your One
No Yes By From With Into After Before Over Under Number Issue Issues""".split())
_HASH = re.compile(
    r"(?P<iss>\b[Ii]ssues?\s+)?#(?P<n>\d+(?:\.\d+)?)(?:\s*[-–]\s*(?P<m>\d+))?(?!\w|\.?\d)")
_PREV_WORD = re.compile(r"([A-Za-z][\w'’\-]*)\s*(?:\(\d{4}\))?\s*$")
_PREV_VOL = re.compile(r"\bvol(?:ume)?\.?\s+\w+\s*$", re.I)


def _issue_num(tok: str) -> str:
    if "." in tok:
        a, b = tok.split(".", 1)
        return _num_words(a, b, year_ok=False)
    return _issue_words(int(tok))


def _hash_sub(m: re.Match) -> str:
    n, hi = m.group("n"), m.group("m")
    if hi is not None and "." not in n:
        return f"issues {_num_words(n, year_ok=False)} to {_num_words(hi, year_ok=False)}"
    before = m.string[: m.start()]
    if m.group("iss") or "." in n:
        ctx = "issue"
    else:
        pw = _PREV_WORD.search(before)
        is_title = bool(pw and pw.group(1)[0].isupper() and pw.group(1) not in _NOT_TITLE)
        ctx = "issue" if (is_title or _PREV_VOL.search(before)) else "number"
    return f"{ctx} {_issue_num(n)}"


# --------------------------------------------------------------------------- numbers
_ROMAN = {"II": 2, "III": 3, "IV": 4, "VI": 6, "VII": 7, "VIII": 8, "IX": 9, "XI": 11, "XII": 12,
          "XIII": 13, "XIV": 14, "XV": 15, "XVI": 16, "XVII": 17, "XVIII": 18, "XIX": 19, "XX": 20}
_ROMAN_RX = re.compile(r"\b([A-Z][a-z]+)\s+(%s)\b" % "|".join(sorted(_ROMAN, key=len, reverse=True)))
_NO = re.compile(r"\bNo\.\s*(\d+)\b")
_VOL = re.compile(r"\b[Vv]ol\.?\s*(\d+)\b")
_PT = re.compile(r"\b[Pp]t\.?\s*(\d+)\b")

_NUM = r"\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?"
_MONEY = re.compile(r"\$\s?(%s)(?:\s*(thousand|million|billion|trillion)\b|([KMB])\b)?" % _NUM)
_PERCENT = re.compile(r"(?<![\w.])(%s)\s?%%" % _NUM)
_DECADE4 = re.compile(r"(?<![\w.,])(1[1-9]\d0|20\d0)s\b")
_DECADE2 = re.compile(r"(?<![\w.,])['’]?([2-9]0)s\b")
_ORD = re.compile(r"(?<![\w.,])(\d{1,3}(?:,\d{3})+|\d+)(st|nd|rd|th)\b", re.I)
_RANGE = re.compile(r"(?<![\w.,\-–])(\d+)\s?[-–]\s?(\d+)(?![\w\-–]|[.,]\d)")
_NUMBER = re.compile(r"(?<![\w.,])(\d{1,3}(?:,\d{3})+|\d+)(?:\.(\d+))?(?!\w)")
_SCALE_WORD = {"K": "thousand", "M": "million", "B": "billion"}


def _split_dec(tok: str) -> tuple[str, str | None]:
    a, _, b = tok.partition(".")
    return a, (b or None)


def _money_sub(m: re.Match) -> str:
    a, b = _split_dec(m.group(1))
    scale = m.group(2) or _SCALE_WORD.get(m.group(3) or "", "")
    if scale:
        return f"{_num_words(a, b, year_ok=False)} {scale} dollars"
    if b is not None and len(b) == 2:
        cents = int(b)
        return f"{_num_words(a, year_ok=False)} dollars" + (f" and {cardinal(cents)} cents" if cents else "")
    if b is None and int(a.replace(",", "")) == 1:
        return "one dollar"
    return f"{_num_words(a, b, year_ok=False)} dollars"


def _decade4_sub(m: re.Match) -> str:
    n = int(m.group(1))
    return year_words(n) + "s" if n % 100 == 0 else _plural_last(year_words(n))


# --------------------------------------------------------------------------- symbols
_AND_OR = re.compile(r"\band/or\b", re.I)
_SLASH = re.compile(r"(?<=[A-Za-z])/(?=[A-Za-z])")
_DROP_NOSPACE = re.compile(r"[*~^]+")
_DROP_BEFORE_PUNCT = re.compile(r"\s*[_|<>\[\]{}]+\s*(?=[,.;:!?])")
_DROP_SPACE = re.compile(r"[_|<>\[\]{}]+")
_PAREN = re.compile(r"\s*\(([^()]*)\)\s*")


def _symbols(t: str) -> str:
    t = re.sub(r"\s*&\s*", " and ", t)  # & -> and
    t = re.sub(r"(?<=\w)\s*\+\s*(?=\w)", " plus ", t)  # a + b -> a plus b
    t = re.sub(r"\s*@\s*(?=\w)", " at ", t)  # @ -> at
    t = re.sub(r"\s*=\s*", " equals ", t)  # = -> equals
    t = _AND_OR.sub(lambda m: m.group(0).replace("/", " "), t)  # and/or -> and or
    t = _SLASH.sub(" or ", t)  # word/word -> word or word
    t = _DROP_NOSPACE.sub("", t)  # markdown noise: * ~ ^
    t = _DROP_BEFORE_PUNCT.sub("", t)  # _ | < > [ ] { } before punctuation
    return _DROP_SPACE.sub(" ", t)  # ... or between words


def _parens(t: str) -> str:
    if "(" not in t and ")" not in t:
        return t
    t = _PAREN.sub(lambda m: f", {m.group(1).strip()}, ", t)  # (x) -> , x,
    t = t.replace("(", " ").replace(")", " ")  # unbalanced leftovers
    t = re.sub(r"\s+,", ",", t)
    t = re.sub(r",(?:\s*,)+", ",", t)  # no doubled commas
    t = re.sub(r",\s*([.!?;:])", r"\1", t)  # ", ." -> "."
    t = re.sub(r"([.!?])\s*,", r"\1", t)  # ". ," -> "."
    t = re.sub(r"^\s*,\s*", "", t)
    return re.sub(r",(?=[^\s\d])", ", ", t)


def to_spoken(text: str) -> str:
    """Rewrite narration into spoken-form English. Deterministic and idempotent."""
    t = str(text or "")
    if not t.strip():
        return t
    t = _abbrev(t)  # 1. Mr./vs./etc./e.g. -> words
    t = _DOTTED.sub(_dotted_sub, t)  # 2. S.H.I.E.L.D. / U.S. -> Shield / U. S.
    t = _HASH.sub(_hash_sub, t)  # 3. "Venom #2" -> "Venom issue two" (before plain numbers)
    t = _NO.sub(lambda m: f"number {_num_words(m.group(1), year_ok=False)}", t)  # 4. No. 5
    t = _VOL.sub(lambda m: f"volume {_num_words(m.group(1), year_ok=False)}", t)  # Vol. 2
    t = _PT.sub(lambda m: f"part {_num_words(m.group(1), year_ok=False)}", t)  # Pt. 2
    t = _ROMAN_RX.sub(lambda m: f"{m.group(1)} {cardinal(_ROMAN[m.group(2)])}", t)  # 5. War II
    t = _symbols(t)  # 6. & + @ = / and markdown noise
    t = _parens(t)  # 7. (x) -> , x,
    t = _MONEY.sub(_money_sub, t)  # 8. $5 -> five dollars
    t = _PERCENT.sub(lambda m: f"{_num_words(*_split_dec(m.group(1)), year_ok=False)} percent", t)
    t = _DECADE4.sub(_decade4_sub, t)  # 9. 1980s -> nineteen eighties
    t = _DECADE2.sub(lambda m: _plural_last(_below_100(int(m.group(1)))), t)  # '80s -> eighties
    t = _ORD.sub(lambda m: ordinal(int(m.group(1).replace(",", ""))), t)  # 10. 22nd -> twenty-second
    t = _RANGE.sub(lambda m: f"{_num_words(m.group(1))} to {_num_words(m.group(2))}", t)  # 11. 3-5
    t = _NUMBER.sub(lambda m: _num_words(m.group(1), m.group(2)), t)  # 12. years/decimals/cardinals
    t = _CAPS.sub(_caps_sub, t)  # 13. FBI -> F. B. I., NEVER -> Never
    return re.sub(r"[ \t]{2,}", " ", t).strip()  # 14. tidy spaces


def spoken_narration_dict(data: dict) -> dict:
    """Normalise, in place, every SPOKEN field of a narration dict: scene text, the matching
    visual_beats text, and the hook. Display-only fields (title, banner_title, panel
    descriptions...) are untouched. Per-scene word_count and the totals follow the new text."""
    for s in data.get("scenes") or []:
        if not isinstance(s, dict):
            continue
        if isinstance(s.get("text"), str):
            old_wc = s.get("word_count")
            s["text"] = to_spoken(s["text"])
            if old_wc is not None:
                s["word_count"] = len(s["text"].split())
        beats = s.get("visual_beats")
        if isinstance(beats, list):
            for i, b in enumerate(beats):
                if isinstance(b, str):
                    beats[i] = to_spoken(b)
                elif isinstance(b, dict) and isinstance(b.get("text"), str):
                    b["text"] = to_spoken(b["text"])
    if isinstance(data.get("hook"), str):
        data["hook"] = to_spoken(data["hook"])
    if data.get("total_word_count") is not None:
        wps = float(data.get("words_per_second") or 0)
        data["total_word_count"] = sum(len(str(s.get("text") or "").split())
                                       for s in data.get("scenes") or [])
        if wps > 0:
            data["estimated_duration_seconds"] = round(data["total_word_count"] / wps, 2)
    return data
