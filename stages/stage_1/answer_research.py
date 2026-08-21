"""Answer Research — Stage 1 mode `explore_answer` (Q&A video).

See EXPLORE_ANSWER_DESIGN.md (root, incl. ADDENDUM 2026-07-04). This is build
piece #1 of the "Explore Answer" mode: turn a QUESTION into a countdown listicle
of comic-grounded answers that the existing Stages 2->5 render as a ~60-76s Short.

Grounding INVERTS vs narrate mode (design "Core insights" #1): narrate has panels
and writes text; Q&A has FACTS (web research) and must FIND the panel per fact.
So this module's ONLY job is the FACTS half — research + verify N answer items,
then materialise them into the two project files Stages 2->5 already understand.

Anti-fabrication mirrors stages/stage_1/tools/gather_plot_sdk.py `_SYSTEM`: real
source URLs required, refuse-if-unsure (drop an item rather than invent it). A
fact -> WRONG issue is the #1 risk in the design; verification is not optional.
"""
import json
import re
import socket
import urllib.error
import urllib.request
from datetime import date
from pathlib import Path

import config
from stages.research_scout.youcom import YouComClient
from stages.stage_1.storage import save_comic_context, slugify
from stages.stage_1.comicvine import verify_issue
from utils.comic_scraper import discover_issues
from config import get_project_dirs

# Items below this can't make a countdown listicle (design format spec: 3-6 items).
_MIN_ITEMS = 3
# Least->most shocking maps to presentation order; the shock is the finale (last).
# (design ADDENDUM "Order by SURPRISE ascending ... most shocking entry LAST").
_SURPRISE_RANK = {"low": 0, "medium": 1, "high": 2}
_ITEM_FIELDS = (
    "entity", "how_or_why", "source_comic", "source_year",
    "reader_url", "drawable_moment", "verification_note", "surprise_level",
)
# ADDITIVE story-context fields (2026-07-24): richer WHO/WHY the writer needs so a
# zero-context viewer isn't left watching an action with no idea why it lands (the
# harley-quinn "why is the Joker in a cell / what did he do to her" miss). OPTIONAL —
# an item missing them is NOT dropped (unlike _ITEM_FIELDS), and old answer_context.json
# without them still loads unchanged. `relationships` = what the entity IS to the other
# characters in the moment; `stakes_why` = why this moment is remarkable / what it costs.
_OPTIONAL_ITEM_FIELDS = ("relationships", "stakes_why")

_ANSWER_SYSTEM = """You are a comic-feats research analyst. You are given a QUESTION \
about comics and RAW You.com Web Search evidence. Use only that supplied evidence to \
find REAL, verifiable comic moments that answer it, then return them as a countdown listicle.

Anti-fabrication is the whole job — a wrong comic here becomes a wrong download and \
a wrong video downstream. Follow these rules exactly:

- ANSWER THE QUESTION with 3 to {max_items} items. Each item is one entity \
(character / being / object) that genuinely satisfies the question, grounded in a \
SPECIFIC published comic. Fewer real items is ALWAYS better than padding with \
invented ones — if you can only verify 3, return 3.

- PER ITEM, fill every field from what the sources actually say (never guess):
  * entity — the name as comics know it (e.g. "Deadpool", "Danny Ketch").
  * how_or_why — 1-2 PLAIN sentences of what HAPPENS (events, not opinions; drop \
"epic", "shocking", "underrated"). This is the fact the video narrates.
  * source_comic — series + issue, e.g. "Thanos" #13 or "Ghost Rider" (1990) #12.
  * source_year — the publication year of THAT issue (4 digits).
  * drawable_moment — ONE concrete visual a single panel would show (a pose, a face, \
an action). Downstream we must FIND a panel for this, so make it panel-sized and \
literal, not a whole scene.
  * verification_note — cite TWO OR MORE independent real sources that confirm the \
feat (name them / their URLs). If you found only ONE source, still include it but \
begin this note with "WEAK:" so the caller knows.
  * surprise_level — "low" | "medium" | "high": how shocking this entry is to a \
comics-literate viewer (a famous hero = low; an obscure/cross-universe/absurd answer \
= high).
  * relationships — 1 PLAIN sentence naming what the entity IS to the other characters \
in THIS moment, so a stranger feels the weight ("Blackheart is Mephisto's own SON", \
"Gwen is Peter Parker's first love"). "" if the moment involves no such relationship. \
Never assume the viewer already knows any character's history.
  * stakes_why — 1 PLAIN sentence on why THIS moment is remarkable: the unspoken rule it \
breaks or what it costs (not hype words like "epic" — the concrete reason it matters).
  * reader_url — a batcave.biz reader URL for the source series, form \
"https://batcave.biz/reader/<news_id>/<chapter_id>" (two numeric ids). Search \
batcave.biz for the series and COPY a real reader URL. If you only find a series \
landing page ("...-<name>.html") and cannot open a real reader URL with both ids, \
set reader_url to "" — NEVER invent or guess the chapter id.

- ORDER the items by surprise ASCENDING: the most mainstream-recognisable, expected \
answers FIRST; the single most shocking / obscure / wildest answer LAST (it is the \
video's finale and retention payoff). Do not label them "number five/four".

- answer_summary — ONE sentence that restates the question as a promise and TEASES \
the final shock WITHOUT naming that entity (e.g. "...and one of them will surprise you").

- constant_broken — ONE sentence naming the famous "unbreakable" constant these answers \
violate ("Nobody survives the Penance Stare"). "" if the question has no such constant.

- viewer_context — 1-2 PLAIN, spoiler-free sentences of the bare context a viewer who \
knows NOTHING must hear BEFORE the items make sense: who/what the question is about and \
the baseline rule. This is the ground a stranger stands on to follow the whole video.

- Reconcile the supplied sources: Marvel/DC Fandom \
(marvel.fandom.com, dc.fandom.com), Comic Vine (comicvine.gamespot.com), Wikipedia, \
CBR / ScreenRant feats lists, Reddit r/comicbooks / r/whowouldwin threads for leads \
(then CONFIRM the issue on a wiki — forum claims alone are WEAK). Do not claim a source \
or issue that is absent from the supplied evidence.

- YEAR/VOLUME MATCH (critical): many characters and titles repeat across years/volumes, \
and ambiguous names mis-resolve (a search for one issue can surface a different one). \
Pin the exact issue+year the feat happened in; if you cannot, mark the item WEAK.

- If you cannot verify an item from real sources, DROP it. Do not invent feats, \
issues, years, or reader URLs. Output STRICT JSON and nothing else."""


def _extract_json(text: str) -> dict | None:
    """Grab the first {...} object from the model text and parse it.

    Copied from gather_plot_sdk.py:54 (design says reuse that tolerant pattern):
    `\\{.*\\}` spans first "{" to last "}", so it survives ```json fences and any
    prose the model wraps around the object."""
    if not text:
        return None
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if m:
        try:
            obj = json.loads(m.group(0))
            if isinstance(obj, dict) and obj:      # non-empty only
                return obj
        except Exception:
            pass
    # Greedy span failed (model wrapped the JSON in prose that itself contains a
    # brace — first-{ .. last-} then spans garbage). Brace-matched fallback: try a
    # strict decode from every '{' and take the first USABLE dict. Skip empty `{}`
    # and prefer a dict carrying the payload keys, so a stray `{}` in the prose before
    # the real object can't shadow it (would else raise "too few items" and swallow
    # the diagnostic snippet).
    dec = json.JSONDecoder()
    first_nonempty = None
    for i, ch in enumerate(text):
        if ch != "{":
            continue
        try:
            obj, _end = dec.raw_decode(text[i:])
        except Exception:
            continue
        if isinstance(obj, dict) and obj:
            if "items" in obj or "answer_summary" in obj:
                return obj
            if first_nonempty is None:
                first_nonempty = obj
    return first_nonempty


def _clean_items(raw_items: list) -> list[dict]:
    """Keep only well-formed item dicts (all fields present) and normalise them.

    Refuse-if-unsure at the data layer: a partial item (missing how_or_why, no
    source, etc.) is a half-fabrication, so we drop it rather than ship a blank."""
    out: list[dict] = []
    for it in raw_items or []:
        if not isinstance(it, dict):
            continue
        item = {k: str(it.get(k, "") or "").strip() for k in _ITEM_FIELDS}
        # Optional story-context fields: carried through when present, "" when absent —
        # they never gate the drop below, so old research without them still passes.
        for k in _OPTIONAL_ITEM_FIELDS:
            item[k] = str(it.get(k, "") or "").strip()
        lvl = item["surprise_level"].lower()
        item["surprise_level"] = lvl if lvl in _SURPRISE_RANK else "medium"
        # Every field except reader_url must be non-empty (reader_url "" is allowed
        # here — build_contexts is where an empty URL becomes a fail-loud error, so
        # the caller sees WHICH items lack a downloadable source, not a silent drop).
        if all(item[k] for k in _ITEM_FIELDS if k != "reader_url"):
            out.append(item)
    return out


def _order_by_surprise(items: list[dict]) -> list[dict]:
    """Stable sort ascending by surprise so the shock lands LAST (presentation order).

    The prompt already asks for this order; we enforce it defensively (cheap) because
    a mis-ordered finale kills the retention payoff — see design ADDENDUM ordering rule."""
    return sorted(items, key=lambda it: _SURPRISE_RANK.get(it["surprise_level"], 1))


def _message_content(payload: dict) -> str:
    """Read an OpenRouter chat response without coupling research to the Claude SDK."""
    try:
        content = payload["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError):
        return ""
    if isinstance(content, list):
        return "".join(str(part.get("text", "")) for part in content if isinstance(part, dict))
    return str(content or "")


def _research_with_youcom(question: str, hint: str, max_items: int) -> str:
    """Get raw Web Search evidence, then structure it with the fixed OpenRouter model."""
    if not config.OPENROUTER_API_KEY:
        raise RuntimeError("research_answer: OPENROUTER_API_KEY is not set")
    query = " ".join(part for part in (question, hint) if part).strip()
    raw = YouComClient().search(query, profile=None)
    if not raw.ok:
        raise RuntimeError(f"research_answer: You.com Web Search failed: {raw.error}")
    user = (
        f"QUESTION: {question}\n\n"
        f"RESEARCH HINT (verify; do not trust blindly): {hint or 'none'}\n\n"
        f"Return 3 to {max_items} items. STRICT JSON only, no prose around it:\n"
        '{"answer_summary":"","constant_broken":"","viewer_context":"",'
        '"items":[{"entity":"","how_or_why":"","source_comic":"",'
        '"source_year":"","drawable_moment":"","verification_note":"",'
        '"surprise_level":"low|medium|high","relationships":"","stakes_why":"",'
        '"reader_url":""}]}\n\n'
        f"RAW YOU.COM WEB SEARCH EVIDENCE:\n{json.dumps(raw.payload, ensure_ascii=False)}"
    )
    body = {
        "model": config.SCOUT_EVIDENCE_MODEL,
        "messages": [
            {"role": "system", "content": _ANSWER_SYSTEM.format(max_items=max_items)},
            {"role": "user", "content": user},
        ],
        "response_format": {"type": "json_object"},
        "provider": {"require_parameters": True},
    }
    request = urllib.request.Request(
        config.OPENROUTER_BASE_URL.rstrip("/") + "/chat/completions",
        data=json.dumps(body, ensure_ascii=False).encode("utf-8"), method="POST",
        headers={"Authorization": f"Bearer {config.OPENROUTER_API_KEY}",
                 "Content-Type": "application/json", "Accept": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=180) as response:
            return _message_content(json.loads(response.read().decode("utf-8")))
    except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, socket.timeout,
            OSError, ValueError) as exc:
        raise RuntimeError(f"research_answer: OpenRouter DeepSeek failed: {type(exc).__name__}") from exc


# ─── FIX C: batcave auto-resolve (no API key) ────────────────────────────────
# The SDK is told to leave reader_url "" rather than guess a chapter id, so we
# resolve it deterministically here: parse the cited issue, search batcave for
# the series, discover its chapters, and take the one whose number matches.

_YEAR_RE = re.compile(r"\((\d{4})")
_ISSUE_RE = re.compile(r"#\s*(\d+(?:\.\d+)?)")


def _parse_source_comic(source_comic: str) -> tuple[str, str, str]:
    """'Thunderbolts (2013) #29' -> ('Thunderbolts', '2013', '29').

    Returns (series_name, volume_year, issue_number); any part may be "" (a
    one-shot has no '#N', an unnamed volume no '(YYYY)'). Series name is the text
    before the first '(' or '#', stripped of the quotes research likes to add."""
    s = (source_comic or "").strip()
    year = m.group(1) if (m := _YEAR_RE.search(s)) else ""
    issue = m.group(1) if (m := _ISSUE_RE.search(s)) else ""
    name = re.split(r"[(#]", s, maxsplit=1)[0].strip().strip('"“”\'').strip()
    return name, year, issue


def _batcave_search(query: str, *, log=print) -> list[tuple[str, str, str]]:
    """POST batcave's DLE search; return [(news_id, slug, series_url)] deduped.

    Reuses the scraper's already-solved session (guard cookies) — url_mode.py
    likewise imports the scraper's private helpers, so this follows precedent."""
    from utils.comic_scraper.readcomiconline import _get_session, SITE_BASE
    sess = _get_session()
    r = sess.post(f"{SITE_BASE}/index.php?do=search",
                  data={"do": "search", "subaction": "search", "story": query},
                  timeout=25)
    if r.status_code != 200:
        log(f"[answer-resolve] batcave search {query!r} -> status={r.status_code}")
        return []
    hits: list[tuple[str, str, str]] = []
    seen: set[str] = set()
    for m in re.finditer(
            r'href="(?:https?://(?:www\.)?batcave\.biz)?/(\d+)-([a-z0-9-]+)\.html"', r.text):
        news_id, slug = m.group(1), m.group(2)
        if news_id in seen:
            continue
        seen.add(news_id)
        hits.append((news_id, slug, f"{SITE_BASE}/{news_id}-{slug}.html"))
    return hits


# Tokens that appear in half the slugs on the site — matching them says NOTHING
# about the series identity. Real case: "FF Vol. 2 #16" tokenised to [ff, vol, 2]
# and "the-unbeatable-squirrel-girl-VOL-2-2015" scored 2/3 on "vol"+"2" alone,
# beating the correct "ff-2013" (whose slug has no vol/2) — the resolver then
# downloaded a Squirrel Girl issue as Ant-Man's FF #16.
_GENERIC_NAME_TOKENS = frozenset((
    "the", "a", "an", "of", "and", "or", "in", "at",
    "vol", "volume", "v", "book", "part", "no", "issue",
    "comic", "comics", "series",
))


def _rank_series_candidates(hits: list[tuple[str, str, str]], series: str,
                             year_hint: str) -> list[str]:
    """All series URLs scoring >= 0.5 on name-token overlap (+0.5 for a slug year
    match), best first. Generic tokens (the/vol/2/…) and bare numbers are dropped
    before scoring — they match half the slugs on the site and let an unrelated
    series outscore the right one. Requires >= half the distinctive name to
    appear so an unrelated search hit can't win.

    Returns ALL qualifying candidates (not just the top one) so callers such as
    `resolve_reader_url` can fall through to a runner-up when the top pick's slug
    year turns out to be wrong — e.g. two same-named series where only one has a
    year in its slug at all (batcave uses legacy numeric IDs for some series, the
    year only ever shows up in the page/chapter TITLE)."""
    raw = [t for t in re.split(r"[^a-z0-9]+", series.lower()) if t]
    name_tokens = [t for t in raw if t not in _GENERIC_NAME_TOKENS and not t.isdigit()]
    if not name_tokens:
        name_tokens = raw  # all-generic/numeric name (e.g. "2000 AD") — best effort
    if not name_tokens:
        return []
    scored: list[tuple[float, str]] = []
    for _news_id, slug, url in hits:
        slug_tokens = {t for t in slug.split("-") if t}
        score = sum(1 for t in name_tokens if t in slug_tokens) / len(name_tokens)
        if year_hint and year_hint in slug_tokens:
            score += 0.5
        if score >= 0.5:
            scored.append((score, url))
    scored.sort(key=lambda pair: -pair[0])  # stable: ties keep hit order (old tie-break)
    return [url for _score, url in scored]


def _pick_series(hits: list[tuple[str, str, str]], series: str, year_hint: str) -> str:
    """Best series URL — see `_rank_series_candidates`. Kept as its own function
    since it's the simple single-answer case other callers/tests want."""
    candidates = _rank_series_candidates(hits, series, year_hint)
    return candidates[0] if candidates else ""


_SLUG_YEAR_RE = re.compile(r"-((?:19|20)\d{2})(?:-|\.|$)")


def _slug_year(series_url: str) -> str:
    """'.../561-batman.html' -> '' (legacy numeric ID, no year encoded);
    '.../33758-batman-2025.html' -> '2025'."""
    m = _SLUG_YEAR_RE.search(series_url)
    return m.group(1) if m else ""


def _titles_match_year(issues: list[dict], year_hint: str) -> bool:
    """True if any chapter's title (the site's OWN label, e.g. 'Batman (2016-) #16')
    carries the wanted year — either a closed '(YYYY)' or an ongoing '(YYYY-'.
    Slugs are sometimes just legacy numeric IDs with no year in them at all, so the
    title is the more trustworthy source of the volume's real year."""
    pat = re.compile(r"\(" + re.escape(year_hint) + r"[-)]")
    return any(pat.search(it.get("title") or "") for it in issues)


def resolve_reader_url(source_comic: str, source_year: str = "", entity: str = "",
                       *, log=print) -> str:
    """Deterministically find the batcave reader URL for a Q&A item's cited issue.

    'Thunderbolts (2013) #29' -> search 'Thunderbolts', pick the 2013 volume,
    discover its chapters, return the one whose number==29. Returns "" (never
    raises) if it can't be pinned — build_contexts then fails loud so a human
    hand-fills it. `entity` is accepted for symmetry with verify_issue / future
    disambiguation; unused today."""
    name, year_hint, issue = _parse_source_comic(source_comic)
    if not name:
        return ""
    year_hint = year_hint or (source_year[:4] if (source_year or "")[:4].isdigit() else "")
    try:
        hits = _batcave_search(name, log=log)
    except Exception as exc:  # noqa: BLE001 - best-effort; empty -> fail-loud caller
        log(f"[answer-resolve] search failed for {name!r}: {type(exc).__name__}: {exc}")
        return ""
    candidates = _rank_series_candidates(hits, name, year_hint)
    if not candidates:
        log(f"[answer-resolve] no series match for {source_comic!r} ({len(hits)} hit(s))")
        return ""

    # Volume-year cross-check (audit 2026-07-06, generalised 2026-07-09): the top
    # name-token match can still be the WRONG volume (e.g. Thanos 2019 when research
    # verified 2016). A slug year is a strong signal WHEN present, but many series on
    # batcave use a legacy numeric ID with NO year in the slug at all (real case:
    # Batman (2016) lives at "561-batman.html" while an unrelated "Batman (2025)" slug
    # DOES carry a year and can outrank it on name tokens alone) — so a slug-year
    # mismatch is only disqualifying if the series' own chapter TITLES also fail to
    # back up the wanted year. Walk every candidate (not just the top one) and take
    # the first that clears this bar; only refuse once none of them do.
    worst_mismatch = ""
    for series_url in candidates:
        try:
            issues = discover_issues(series_url)
        except Exception as exc:  # noqa: BLE001
            log(f"[answer-resolve] discover_issues failed for {series_url}: {type(exc).__name__}: {exc}")
            continue
        if not issues:
            continue
        if year_hint:
            slug_year = _slug_year(series_url)
            if slug_year and abs(int(slug_year) - int(year_hint)) > 1 \
                    and not _titles_match_year(issues, year_hint):
                worst_mismatch = worst_mismatch or slug_year
                continue
        # One-shot (no '#N', or the series has a single chapter) -> that chapter.
        if not issue or len(issues) == 1:
            return issues[0]["url"]
        # Match by the ISSUE NUMBER in the chapter title ('… Issue #29'), NOT `number`:
        # `number` is the chapter's list position (posi), which drifts off the issue #
        # whenever the series has an extra chapter at the front (a #0 / point-one /
        # special) — batcave's Thunderbolts (2013) does exactly this. Fall back to
        # posi only when the title carries no '#N'.
        want = float(issue)
        for it in issues:
            n = _chapter_issue_number(it)
            if n is not None and n == want:
                return it["url"]
        log(f"[answer-resolve] issue #{issue} not among {len(issues)} chapter(s) at {series_url}")
        return ""

    if worst_mismatch:
        log(f"[answer-resolve] volume-year mismatch for {source_comic!r}: "
            f"batcave slug says {worst_mismatch}, research says {year_hint} — refusing "
            f"(hand-fill reader_url if the slug year is just mislabeled)")
    return ""


def _chapter_issue_number(chapter: dict) -> float | None:
    """The chapter's real issue number: parse '#N' from its title, else fall back
    to `number` (the list position posi, which can be off by the front specials)."""
    m = _ISSUE_RE.search(chapter.get("title") or "")
    if m:
        try:
            return float(m.group(1))
        except ValueError:
            pass
    try:
        return float(chapter.get("number"))
    except (TypeError, ValueError):
        return None


def research_answer(question: str, *, max_items: int = 6, hint: str = "", log=print) -> dict:
    """Research a comic Q&A question into ordered, verified answer items.

    Returns {"question", "answer_summary", "source_engine", "items": [...]} with
    items in PRESENTATION order (least surprising first, shock last). Raises
    RuntimeError/ValueError on unusable research — the whole video depends on this,
    so we fail loud rather than hand an empty/fabricated answer to the pipeline."""
    question = (question or "").strip()
    if not question:
        raise ValueError("research_answer: empty question")
    # Grounding hint (2026-07-06): abstract "Why/How <famous character> <paradox>"
    # questions give the researcher no concrete anchor — it wanders the web and runs
    # out of turns. A scout-supplied hint names the LIKELY story so the agent spends
    # its turns VERIFYING it (and finding the reader URLs) instead of discovering it.
    # Explicitly framed as verify-don't-trust so a wrong hint gets corrected, not echoed.
    hint_block = (
        f"RESEARCH HINT from a prior scout (VERIFY against real sources before using — "
        f"if the sources disagree with the hint, follow the sources): {hint.strip()}\n\n"
        if (hint or "").strip() else ""
    )
    log(f"[answer-research] researching: {question!r} (<= {max_items} items"
        f"{', hinted' if hint_block else ''}) …")
    raw = _research_with_youcom(question, hint.strip(), max_items)
    if not raw:
        raise RuntimeError("research_answer: You.com/OpenRouter returned nothing")

    data = _extract_json(raw)
    if data is None:
        # Include a head/tail snippet so the failure is diagnosable from the log —
        # the raw text was previously discarded, leaving nothing to debug with.
        head, tail = raw[:400], (raw[-400:] if len(raw) > 800 else "")
        raise RuntimeError(
            "research_answer: could not parse JSON from SDK output — raw head: "
            f"{head!r}" + (f" … raw tail: {tail!r}" if tail else "")
        )

    items = _order_by_surprise(_clean_items(data.get("items")))[:max_items]
    if len(items) < _MIN_ITEMS:
        raise ValueError(
            f"research_answer: only {len(items)} verified item(s) — need >= {_MIN_ITEMS} "
            "for a listicle (research produced too few to trust)"
        )
    summary = (data.get("answer_summary") or "").strip()
    log(f"[answer-research] ✓ {len(items)} items (surprise: "
        f"{', '.join(i['surprise_level'] for i in items)})")
    return {
        "question": question,
        "answer_summary": summary,
        # ADDITIVE question-level story context (optional; "" when the model omits it).
        "constant_broken": (data.get("constant_broken") or "").strip(),
        "viewer_context": (data.get("viewer_context") or "").strip(),
        "source_engine": "youcom-web-search+openrouter-deepseek",
        "items": items,
    }


def _answer_digest(question: str, items: list[dict]) -> str:
    """One-line-per-item plot_summary Stages 2->5 read as the 'story'.

    Q&A has no single plot; the digest IS the arc — question + each answer's fact and
    source, in presentation order — so the writer/matcher have grounding text to work
    from (design: reuse saga machinery; plot_summary drives Stage 3)."""
    lines = [f"Q&A: {question}"]
    for i, it in enumerate(items, 1):
        lines.append(
            f"{i}. {it['entity']} — {it['how_or_why']} "
            f"(source: {it['source_comic']}, {it['source_year']})"
        )
    return "\n".join(lines)


def build_contexts(
    question: str, research: dict, project_name: str,
    *, researched_at: str = "", log=print,
) -> tuple[Path, Path]:
    """Materialise research into answer_context.json + comic_context.json.

    - answer_context.json: human-readable record for Master (design constraint #2) —
      question, summary, and each item's sources/whys/URLs, in presentation order.
    - comic_context.json: SAGA-ARC shape so Stages 2->5 run unchanged. Each answer
      item becomes one "issue" of the saga (reuse the multi-issue machinery, design
      "Core insights" #2).

    Fail-loud: raises ValueError naming the offending items if ANY reader_url is empty
    — the pipeline cannot download a source with no reader URL, so the caller must
    decide (re-research / hand-fill) rather than silently ship a listicle with holes.
    Returns (answer_context_path, comic_context_path)."""
    items = research.get("items") or []
    researched_at = researched_at or date.today().isoformat()
    year = (researched_at[:4] if researched_at[:4].isdigit() else str(date.today().year))
    slug = slugify(project_name)

    # --- FIX D then FIX C, per item (design order: verify -> resolve) ----------
    # verify_issue (Comic Vine cross-check): confirm the issue is real + names the
    #   character; FLAG it (verified/verify_note), never drop — a CV hiccup returns
    #   ok=True/"unverified" and must not lose the research.
    # resolve_reader_url (batcave): fill an EMPTY reader_url deterministically so
    #   the fail-loud check below only trips on items we truly cannot download.
    # Both are wrapped non-fatal (they already swallow their own errors).
    for it in items:
        name, _vol_year, issue_no = _parse_source_comic(it["source_comic"])
        try:
            v = verify_issue(name, issue_no, it.get("source_year", ""), it["entity"], log=log)
        except Exception as exc:  # noqa: BLE001 - belt-and-suspenders; verify_issue is already graceful
            v = {"ok": True, "note": f"unverified ({type(exc).__name__})"}
        it["verified"] = bool(v.get("ok"))
        it["verify_note"] = v.get("note", "")
        log(f"[answer-research] {'✓ verified' if v.get('ok') else '⚠ FLAG'}: "
            f"{it['entity']} — {it['source_comic']} :: {v.get('note', '')}")

        if not (it.get("reader_url") or "").strip():
            try:
                url = resolve_reader_url(it["source_comic"], it.get("source_year", ""),
                                         it["entity"], log=log)
            except Exception as exc:  # noqa: BLE001
                url = ""
                log(f"[answer-research] reader-url resolve errored for {it['entity']}: {exc}")
            if url:
                it["reader_url"] = url
                log(f"[answer-research] ↳ auto-resolved reader_url for {it['entity']}: {url}")

    # --- answer_context.json (presentation order; rank 1 first, shock last) ---
    answer_ctx = {
        "question": question,
        "answer_summary": research.get("answer_summary", ""),
        # ADDITIVE story context for the writer (optional; "" when research omitted it).
        "constant_broken": research.get("constant_broken", ""),
        "viewer_context": research.get("viewer_context", ""),
        "researched_at": researched_at,
        "source_engine": research.get("source_engine", ""),
        "items": [
            {
                "rank": i,
                "entity": it["entity"],
                "how_or_why": it["how_or_why"],
                "source_comic": it["source_comic"],
                "source_year": it["source_year"],
                "reader_url": it["reader_url"],
                "drawable_moment": it["drawable_moment"],
                "verification_note": it["verification_note"],
                "surprise_level": it["surprise_level"],
                # ADDITIVE per-item WHO/WHY (optional; "" when research omitted them).
                "relationships": it.get("relationships", ""),
                "stakes_why": it.get("stakes_why", ""),
                # FIX D: Comic Vine cross-check result (flag for review; not a drop).
                "verified": it.get("verified", True),
                "verify_note": it.get("verify_note", ""),
            }
            for i, it in enumerate(items, 1)
        ],
    }
    root = get_project_dirs(slug)["root"]
    root.mkdir(parents=True, exist_ok=True)
    answer_path = root / "answer_context.json"
    answer_path.write_text(json.dumps(answer_ctx, indent=2, ensure_ascii=False))
    log(f"[answer-research] wrote {answer_path}")

    # --- fail-loud on undownloadable items (the design's #1-risk mitigation (b)) ---
    # AFTER persisting answer_context.json: the research must survive the failure so a
    # human can hand-fill the missing reader_url(s) there and resume with
    # `--rebuild-contexts` (raising first would throw the whole research away).
    missing = [it["entity"] for it in items if not (it.get("reader_url") or "").strip()]
    if missing:
        raise ValueError(
            "build_contexts: empty reader_url for item(s): "
            + ", ".join(missing)
            + f" — hand-fill reader_url in {answer_path} then re-run with"
            " --rebuild-contexts (or re-research)"
        )

    # --- comic_context.json (saga-arc shape) ---
    # `issues` (list) is load-bearing: Stage 3's arc path iterates it as per-issue
    # dicts (write_script.py:1340) and the tests assert it. The design's "issues=Q&A"
    # label collides with that key, so the human "Q&A" marker goes on `issue`
    # (singular) — the conventional issue-number field (verify_plot.py:61 reads it).
    comic_ctx = {
        "title": question,
        "series": question,
        "issue": "Q&A",
        "year": year,
        "publisher": "Marvel/DC (mixed)",
        "characters": [it["entity"] for it in items],
        "plot_summary": _answer_digest(question, items),
        "plot_status": "OK",
        # plot_source guards the identity-repair hook (worker C skips rebuild for
        # "answer_research"): a Q&A plot is web-verified facts, NOT one comic's story,
        # so panel-rebuild would destroy it.
        "plot_source": "answer_research",
        "is_arc": True,
        "issue_count": len(items),
        "issues": [
            {
                "label": it["source_comic"],
                "chapter_index": i,
                "plot_summary": it["how_or_why"],
                "wiki_url": "",
            }
            for i, it in enumerate(items, 1)
        ],
        "reader_urls": [it["reader_url"] for it in items],
    }
    # Deliberately NO "summary" key (Stage 2 VLM cold-read fills it from panels) and
    # NO "user_prompt" key (identity Hook 0 no-ops without it) — design map, item 2.
    comic_path_str = save_comic_context(comic_ctx, slug, get_project_dirs)
    return answer_path, Path(comic_path_str)


if __name__ == "__main__":
    # Self-check: no network. Stub the SDK, run the real research+build path into a
    # temp project, and assert the two files come out with the promised shape.
    import tempfile

    _FIXTURE = (
        "```json\n"  # markdown fence — _extract_json must tolerate it
        + json.dumps({
            "answer_summary": "A few heroes shrugged it off — and the last one shouldn't have.",
            "items": [
                {"entity": "Ghost Rider", "how_or_why": "Danny Ketch turns the Stare on "
                 "himself and feels nothing, having no innocent blood on his soul.",
                 "source_comic": '"Ghost Rider" (1990) #12', "source_year": "1991",
                 "drawable_moment": "Ghost Rider's flaming skull staring into a mirror",
                 "verification_note": "marvel.fandom.com + Comic Vine issue page",
                 "surprise_level": "low",
                 "reader_url": "https://batcave.biz/reader/111/222"},
                {"entity": "Deadpool", "how_or_why": "His scrambled mind gives the Stare no "
                 "coherent guilt to burn, so he just laughs it off.",
                 "source_comic": '"Deadpool" #...', "source_year": "2014",
                 "drawable_moment": "Deadpool grinning as hellfire washes over him",
                 "verification_note": "CBR feats list + marvel.fandom.com",
                 "surprise_level": "medium",
                 "reader_url": "https://batcave.biz/reader/333/444"},
                {"entity": "Man-Thing", "how_or_why": "Having no soul to judge, the empathic "
                 "swamp creature is simply unaffected by the Penance Stare.",
                 "source_comic": '"Marvel Comics Presents" #...', "source_year": "1990",
                 "drawable_moment": "Man-Thing looming unmoved before Ghost Rider",
                 "verification_note": "WEAK: single Reddit r/comicbooks thread",
                 "surprise_level": "high",
                 "reader_url": "https://batcave.biz/reader/555/666"},
            ],
        })
        + "\n```"
    )

    # Run via `python -m ...` executes this file as __main__; rebind the web
    # boundary here so the self-check remains network-free.
    _research_with_youcom = lambda *a, **k: _FIXTURE      # noqa: F811,E731
    # Stub the two cross-check/resolve hooks build_contexts now calls, so this
    # self-check stays network-free: verify_issue -> "unverified", and
    # resolve_reader_url -> "" (so the fail-loud path below still trips).
    verify_issue = lambda *a, **k: {"ok": True, "note": "unverified (self-check)"}  # noqa: F811,E731
    resolve_reader_url = lambda *a, **k: ""               # noqa: F811,E731

    q = "Who has survived Ghost Rider's Penance Stare?"
    res = research_answer(q, log=lambda _m: None)
    assert res["source_engine"] == "youcom-web-search+openrouter-deepseek"
    assert [i["surprise_level"] for i in res["items"]] == ["low", "medium", "high"], \
        "items must be surprise-ascending (shock last)"

    with tempfile.TemporaryDirectory() as d:
        get_project_dirs = lambda name: {"root": Path(d)}  # noqa: F811,E731
        a_path, c_path = build_contexts(q, res, "gr_penance", researched_at="2026-07-04",
                                        log=lambda _m: None)
        a = json.loads(a_path.read_text())
        c = json.loads(c_path.read_text())

    assert [it["rank"] for it in a["items"]] == [1, 2, 3]
    assert a["items"][-1]["entity"] == "Man-Thing"  # shock stays last
    assert all("verified" in it and "verify_note" in it for it in a["items"])
    assert a["researched_at"] == "2026-07-04"
    assert c["is_arc"] is True and c["issue_count"] == 3
    assert c["plot_source"] == "answer_research"
    assert "summary" not in c and "user_prompt" not in c
    assert c["reader_urls"] == [it["reader_url"] for it in res["items"]]
    assert isinstance(c["issues"], list) and c["issues"][0]["chapter_index"] == 1

    # fail-loud on an empty reader_url — but answer_context.json must SURVIVE the
    # failure (it's the hand-fill target for --rebuild-contexts)
    res["items"][1]["reader_url"] = ""
    with tempfile.TemporaryDirectory() as d:
        get_project_dirs = lambda name: {"root": Path(d)}  # noqa: F811,E731
        try:
            build_contexts(q, res, "gr_penance", log=lambda _m: None)
            raise AssertionError("expected ValueError for empty reader_url")
        except ValueError as e:
            assert "Deadpool" in str(e)
        assert (Path(d) / "answer_context.json").exists(), \
            "research must persist for hand-fill even when reader_urls are missing"
        assert not (Path(d) / "comic_context.json").exists(), \
            "comic_context must NOT be written with undownloadable reader_urls"

    print("answer_research self-check OK")
