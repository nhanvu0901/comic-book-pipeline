"""You.com Research API scout — the 3-stage search pipeline (Master 2026-08-05).

    DISCOVER  -> find a QUESTION fans actually ask     (5 angled queries, effort=standard)
    ENUMERATE -> find MORE answer items for a question (angle-matrix queries, effort=deep)
    CONFIRM   -> verify ONE item, refutation-framed    (1 query per item,   effort=deep)

The recall lesson from the 2026-08-05 pilot: You.com only finds what its own PLAN
step thinks to search for (it missed 3/3 known answers on one broad query). So WE
own the plan — every stage fans out into narrow angled queries — and You.com owns
the digging. Its domain whitelist is hard (0 leaks / 11 pilot queries); its labels
and dedup are NOT trusted (UK reprints double-counted, "deep cut" mislabeled) — the
authoritative filters stay on our side.

Usage:
    python -m stages.youcom_scout digest                    # print the SCOUTED digest
    python -m stages.youcom_scout discover                  # 5 angled queries -> candidates
    python -m stages.youcom_scout micro [--years "in 2025 or 2026"]   # single MOMENTS
    python -m stages.youcom_scout enumerate --question "..." [--have "item1; item2"]
    python -m stages.youcom_scout confirm --issue "Series #N (year)" --claim "..."

Needs YDC_API_KEY in the environment or .env. Results land in scout_runs/<UTC stamp>/.
"""
from __future__ import annotations

import argparse
import csv
import datetime
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
API = "https://api.you.com/v1/research"

# The 8 whitelisted sources (Master 2026-08-04): legit comic sources + Reddit, no YouTube.
DOMAINS = [
    "marvel.fandom.com", "dc.fandom.com", "comicbookroundup.com",
    "aiptcomics.com", "multiversitycomics.com", "cbr.com",
    "leagueofcomicgeeks.com", "reddit.com",
]

# ─── SCOUTED digest — built fresh from the files of record on every run ─────────

def _banlist_lines() -> list[str]:
    """One compact line per banned/produced/rejected entry in qa_question_banlist.md."""
    p = REPO / "qa_question_banlist.md"
    if not p.exists():
        return []
    out = []
    for line in p.read_text().splitlines():
        if not line.startswith("|") or line.startswith(("| Date", "|---", "|------")):
            continue
        cells = [c.strip() for c in line.strip("|").split("|")]
        if len(cells) >= 2 and cells[1]:
            out.append(cells[1][:160])
    return out


def _project_questions() -> list[str]:
    """Every produced project's question — projects/ is the ground truth the banlist
    sometimes lags behind (5 produced projects were missing from it on 2026-08-05)."""
    out = []
    for f in sorted((REPO / "projects").glob("*/answer_context.json")):
        try:
            q = json.loads(f.read_text()).get("question", "")
        except json.JSONDecodeError:
            continue
        if q:
            out.append(f"{q} (project: {f.parent.name})")
    return out


def _csv_lines() -> list[str]:
    """Rejected/produced rows of comic_candidates.csv (recap lane, still dedup fodder)."""
    p = REPO / "comic_candidates.csv"
    if not p.exists():
        return []
    out = []
    try:
        for row in csv.reader(p.read_text().splitlines()):
            joined = " ".join(row).lower()
            if any(w in joined for w in ("reject", "produced", "banned")) and row:
                out.append(row[0][:120])
    except csv.Error:
        pass
    return out


def build_scouted_digest() -> str:
    produced = _project_questions()
    banned = _banlist_lines()
    recap = _csv_lines()
    parts = [
        "=== ALREADY DONE — NEVER PROPOSE THESE, NOR RE-SKINS ===",
        "(re-skin = same question with synonyms, same subject + same power, "
        "same answer set reordered)",
        "",
        "PRODUCED QUESTIONS / PROJECTS:",
        *[f"- {q}" for q in produced],
        "",
        "BANNED / REJECTED / PRODUCED (from the ban list, with reasons where they matter):",
        *[f"- {q}" for q in banned],
    ]
    if recap:
        parts += ["", "RECAP-LANE TITLES ALREADY HANDLED:", *[f"- {t}" for t in recap]]
    parts += [
        "",
        "HARD RULES (these killed candidates before):",
        "- Answer must span 3+ DIFFERENT comics, each item published 2010+, "
        "subject A-tier famous",
        "- 'Who CAN beat X' hypotheticals are not questions — only 'who DID, "
        "in a specific issue'",
        "- Talking-heads moments, cute gags without stakes, lore-required moments: "
        "all rejected before",
    ]
    digest = "\n".join(parts)
    # input hard cap is 40k chars; leave >30k headroom for the task text
    return digest[:9000]


# ─── shared API call ─────────────────────────────────────────────────────────────

def _schema(item_props: dict, extra_root: dict | None = None) -> dict:
    """You.com validates OpenAI-strict style (pilot 2026-08-05): every object level
    needs additionalProperties:false AND every property listed in required."""
    root_props = {"candidates": {
        "type": "array",
        "items": {"type": "object", "additionalProperties": False,
                  "properties": item_props, "required": list(item_props)},
    }, "notes": {"type": "string"}}
    root_props.update(extra_root or {})
    return {"type": "object", "additionalProperties": False,
            "properties": root_props, "required": list(root_props)}


def research(key: str, prompt: str, effort: str, schema: dict) -> dict:
    body = json.dumps({
        "input": prompt, "research_effort": effort,
        "source_control": {"include_domains": DOMAINS},
        "output_schema": schema,
    }).encode()
    req = urllib.request.Request(
        API, data=body, method="POST",
        # WAF 403s the default Python-urllib UA (probed 2026-08-05)
        headers={"X-API-Key": key, "Content-Type": "application/json",
                 "User-Agent": "comic-scout/1.0"})
    with urllib.request.urlopen(req, timeout=1800) as r:
        return json.loads(r.read())


def offdomain(resp: dict) -> list[str]:
    return [str(s.get("url", "")) for s in (resp.get("output") or {}).get("sources") or []
            if s.get("url") and not any(d in str(s["url"]) for d in DOMAINS)]


# ─── our-side hard dedup (the prompt-side digest is advisory only) ───────────────

_STOP = frozenset("the a an of in on to who has have had what which why how his her "
                  "their and or for with actually can did does do that this these "
                  "is are was were be been it its not no cannot".split())

# Question-FORMAT words: shared listicle scaffolding, not subject matter. An overlap
# made only of these is the same TEMPLATE, not the same LANE ("Things Absolute
# Superman can do..." must not burn against the produced Absolute Batman video).
_FORMAT = frozenset("things times ways regular classic absolute modern comics comic "
                    "unbreakable famous new old three".split())


def _tokens(s: str) -> frozenset:
    # No apostrophes in the token class: "spider-man's" must yield {spider, man},
    # identical to "spider-man" in a re-skin question. 1-char leftovers ("s") dropped.
    s = re.sub(r"\(project: [^)]*\)", "", s.lower())
    return frozenset(w for w in re.findall(r"[a-z0-9]+", s)
                     if len(w) > 1 and w not in _STOP)


def is_burned(question: str, digest: str) -> str | None:
    """The digest line this question collides with, or None. Loose token overlap so
    re-skins match too ('Who beat the Hulk barehanded' vs 'hulk pure fistfight').
    # ponytail: >=60% token containment + >=2 non-format shared tokens, no embeddings.
    # KNOWN MISS: synonym re-skins ('beat' vs 'defeated Mephisto') slip through — by
    # choice: chasing synonyms would also bury legit siblings ('who LIFTED Mjolnir'
    # vs the produced 'who SHATTERED Mjolnir'). The Master-review step after discover
    # is the catch for those; this filter only has to stop the obvious repeats."""
    qt = _tokens(question)
    if not qt:
        return None
    for line in digest.splitlines():
        if not line.startswith("- "):
            continue
        lt = _tokens(line)
        # containment against the SHORTER side: a long re-skin question dilutes the
        # ratio against itself (real miss 2026-08-05: "Which Spider-Man villains or
        # symbiotes have bypassed or been immune to Spider-Man's spider-sense?" slid
        # past the produced "What has gotten past Spider-Man's spider-sense?").
        inter = qt & lt
        if (lt and len(inter) / min(len(qt), len(lt)) >= 0.6
                and len(inter - _FORMAT) >= 2):
            return line[2:]
    return None


def _issue_key(s: str) -> tuple:
    """(series tokens, issue number) for an issue string. The number is what actually
    discriminates — 'Venom #13' and 'Venom #1' share every series token."""
    num = re.search(r"#\s*(\d+)", s or "")
    toks = frozenset(w for w in _tokens(s) if not w.isdigit())
    return toks, (num.group(1) if num else "")


def _same_issue(a: str, b: str) -> bool:
    """Do two issue strings name the SAME comic? Tolerates the year being present on one
    side only ('Venom #13 (2019)' vs 'Venom #13') and word-order/punctuation drift.

    Needed because You.com IGNORES the --have list in the prompt: the 2026-08-05 micro run
    returned Immortal Hulk #1 and Venom #13 straight back despite both being listed as
    already-found. Soft constraints don't bind, so the filter lives here."""
    (at, an), (bt, bn) = _issue_key(a), _issue_key(b)
    if not at or not bt:
        return False
    if an and bn and an != bn:
        return False                     # same series, different issue — keep it
    # Jaccard, NOT containment-against-the-shorter: a shorter title is a SUBSET of a
    # longer one whenever the longer adds the very word that distinguishes them, so
    # containment scored "Batman Annual #1" == "Batman #1" at 1.0 and would have silently
    # dropped the Ace pick if plain Batman #1 were in the have-list. Union in the
    # denominator makes that extra word count against the match (0.5), as it must.
    return len(at & bt) / len(at | bt) >= 0.6


# ─── stages ──────────────────────────────────────────────────────────────────────

DISCOVER_ANGLES = [
    "famous weakness/immunity debates — who resisted or was immune to a famous power",
    "who broke a famously unbreakable object or rule",
    "who resisted famous mind control or telepathy",
    "fun-casual angle: who made a famously stoic character break composure",
    "the newest debates fans have about 2024-2026 comics",
]

_DISCOVER_PROPS = {
    "question": {"type": "string"}, "subject": {"type": "string"},
    "why_fans_ask_it": {"type": "string"},
    "evidence_urls": {"type": "array", "items": {"type": "string"}},
    "sample_answer_items": {"type": "array", "items": {"type": "string"}},
}

_ITEM_PROPS = {
    "character_or_thing": {"type": "string"},
    "series_issue_year": {"type": "string"},
    "what_visibly_happens": {"type": "string"},
    "source_urls": {"type": "array", "items": {"type": "string"}},
    "well_known_or_deep_cut": {"type": "string"},
}

_CONFIRM_PROPS = {
    "verdict": {"type": "string"},             # CONFIRMED / NOT CONFIRMED / CONFLICTING
    "verbatim_sentence": {"type": "string"},
    "source_url": {"type": "string"},
    "second_source_url": {"type": "string"},
    "volume_and_year": {"type": "string"},
    "comic_or_adaptation": {"type": "string"},
    "single_issue_or_multi": {"type": "string"},
    "subject_main_in_issue": {"type": "string"},
    "reprint_check": {"type": "string"},       # original US issue if a reprint magazine
}


def run_discover(key: str, outdir: Path, effort: str) -> None:
    digest = build_scouted_digest()
    rows, kept = [], []
    for i, angle in enumerate(DISCOVER_ANGLES, 1):
        prompt = (
            f"{digest}\n\n=== TASK ===\n"
            "Find ONE comic-book question fans genuinely and repeatedly ask, about an "
            "A-TIER famous character (Batman / Hulk / Thor / Spider-Man / Deadpool tier), "
            "whose answer is a LIST of 3 or more separate moments in DIFFERENT comics all "
            "published 2010 or later. Real fan debates and 'every character who…' articles "
            "only — never invent a question because it sounds viral. Cite the URL where "
            "fans actually ask or answer it.\n"
            f"Angle for THIS search: {angle}."
        )
        print(f"[discover {i}/{len(DISCOVER_ANGLES)}] {angle[:60]}…", flush=True)
        resp = _call_logged(key, prompt, effort, _schema(_DISCOVER_PROPS), outdir, f"discover{i}")
        for c in _cands(resp):
            c["_angle"] = angle
            rows.append(c)
    for c in rows:
        hit = is_burned(c.get("question", ""), digest)
        if hit:
            c["_dropped_as_burned"] = hit
        else:
            kept.append(c)
    report = outdir / "discover_report.md"
    lines = ["# DISCOVER — candidates for Master\n"]
    for c in kept:
        lines += [f"## {c.get('question')}",
                  f"- subject: {c.get('subject')} | angle: {c.get('_angle')}",
                  f"- why fans ask: {c.get('why_fans_ask_it','')[:300]}",
                  f"- sample items: {'; '.join(c.get('sample_answer_items') or [])[:400]}",
                  f"- evidence: {' '.join(c.get('evidence_urls') or [])}", ""]
    dropped = [c for c in rows if c.get("_dropped_as_burned")]
    if dropped:
        lines += ["## Dropped as burned (our hard filter, not You.com's)"]
        lines += [f"- {c.get('question')}  ⇒ collides with: {c['_dropped_as_burned']}"
                  for c in dropped]
    report.write_text("\n".join(lines))
    print(f"\n{len(kept)} candidate(s), {len(dropped)} dropped as burned → {report}")


# ─── MICRO — one scene, one issue (the other mode) ───────────────────────────────
# DISCOVER's whole premise is a question whose answer spans 3+ DIFFERENT comics. A micro
# moment is the exact opposite: ONE drawn beat inside ONE issue. So it needs its own plan —
# reusing discover's prompt returns listicles, which is what a one-off run outside the repo
# produced before this landed.
MICRO_ANGLES = [
    "an A-list hero doing something shockingly out of character in a single panel sequence "
    "fans keep sharing",
    "a famously unbeatable character humiliated or broken in one scene readers called the "
    "most brutal page of the year",
    "a villain doing something so unexpected that reviewers singled out that one page",
    "scenes fans call 'peak' or 'insane' — one specific issue, one specific page, never a "
    "whole storyline",
]

_MICRO_PROPS = {
    "moment": {"type": "string"},
    "character": {"type": "string"},
    "series_issue_year": {"type": "string"},
    "what_visibly_happens": {"type": "string"},
    "why_it_lands": {"type": "string"},
    "constant_broken": {"type": "string"},     # the thing everyone "knows" about them
    "evidence_urls": {"type": "array", "items": {"type": "string"}},
}


def _series_of(series_issue_year: str) -> str:
    """"Absolute Batman #11 (2025)" -> "absolute batman". Issue number and year removed."""
    s = re.split(r"#|\(", str(series_issue_year or ""))[0]
    return " ".join(w for w in re.findall(r"[a-z0-9]+", s.lower()) if len(w) > 1)


def _series_burned(series_issue_year: str, digest: str) -> str | None:
    """The digest line naming this SERIES, or None.

    is_burned() is not usable here: it needs >=60% token containment and >=2 non-format
    shared tokens, thresholds tuned for whole questions. "Absolute Batman #11 (2025)" carries
    four tokens, two of which ("absolute", "things") are format words — so a series already
    produced from scores 50% and slips through. A micro candidate identifies itself by SERIES,
    so match the series name directly: being in that lane at all is the signal, whichever
    issue it is.
    """
    words = _series_of(series_issue_year).split()
    if len(words) < 2:                   # a one-word series name is too generic to match on
        return None
    # The lane is the first two words, not the whole string. Annuals and specials fold the
    # year and "Annual" into the series field ("Absolute Batman 2025 Annual"), so matching
    # the full name misses the very siblings this is meant to catch.
    lane = " ".join(words[:2])
    for line in digest.splitlines():
        if line.startswith("- ") and lane in " ".join(
                re.findall(r"[a-z0-9]+", line.lower())):
            return line[2:][:120]
    return None


def run_micro(key: str, outdir: Path, effort: str, years: str) -> None:
    """Scout single MOMENTS for micro_moment mode. Same three-part shape as run_discover:
    our plan fans out, You.com digs, our filters decide."""
    digest = build_scouted_digest()
    rows, kept = [], []
    for i, angle in enumerate(MICRO_ANGLES, 1):
        prompt = (
            f"{digest}\n\n=== TASK ===\n"
            "Find ONE comic-book MICRO MOMENT: a single drawn beat inside a SINGLE issue — "
            "not a plot, not a crossover, not a character arc. It must be VISUALLY dramatic "
            "(something a reader SEES happen on the page), star a widely-known character, "
            f"and be published {years}.\n"
            "The strongest micro moment breaks a CONSTANT — the one thing everyone 'knows' "
            "about that character — inside that single scene. Name the constant.\n"
            "REJECT: talking-heads scenes, moments that need prior lore to follow, whole "
            "storylines, solicitations or previews for unpublished issues, and anything "
            "where you cannot give the exact series, issue number and year.\n"
            f"Angle for THIS search: {angle}."
        )
        print(f"[micro {i}/{len(MICRO_ANGLES)}] {angle[:60]}…", flush=True)
        resp = _call_logged(key, prompt, effort, _schema(_MICRO_PROPS), outdir, f"micro{i}")
        for c in _cands(resp):
            c["_angle"] = angle
            rows.append(c)
    for c in rows:
        # Burn-check on series+issue AND on the moment text: the same scene resurfaces
        # under a different phrasing across angles, and a sibling issue of an already-
        # produced series is the commonest false lead (measured: 3 of 7 candidates in the
        # first run were Absolute Batman, a series already produced from and noted in the
        # ban list as having 8 breakdowns in one week).
        hit = (_series_burned(c.get("series_issue_year", ""), digest)
               or is_burned(c.get("moment", ""), digest))
        if hit:
            c["_dropped_as_burned"] = hit
        else:
            kept.append(c)

    report = outdir / "micro_report.md"
    lines = ["# MICRO MOMENTS — candidates for Master\n"]
    for c in kept:
        lines += [f"## {c.get('character')} — {c.get('series_issue_year')}",
                  f"- moment: {str(c.get('moment', ''))[:300]}",
                  f"- constant broken: {c.get('constant_broken', '')}",
                  f"- what is SEEN: {str(c.get('what_visibly_happens', ''))[:300]}",
                  f"- why it lands: {str(c.get('why_it_lands', ''))[:300]}",
                  f"- evidence: {' '.join(c.get('evidence_urls') or [])}", ""]
    dropped = [c for c in rows if c.get("_dropped_as_burned")]
    if dropped:
        lines += ["## Dropped as burned (our hard filter, not You.com's)"]
        lines += [f"- {c.get('series_issue_year')}  ⇒ collides with: {c['_dropped_as_burned']}"
                  for c in dropped]
    lines += ["",
              "## STILL UNVERIFIED — do these before producing",
              "- on batcave.biz? (Cloudflare 403s plain HTTP; needs the nodriver scraper)",
              "- narration coverage: search AGAIN with different phrasing before trusting a "
              "clean verdict (see .claude/memory/scout_jeff_narration_missed.md)"]
    report.write_text("\n".join(lines), encoding="utf-8")
    print(f"\n{len(kept)} candidate(s), {len(dropped)} dropped as burned → {report}")


def run_enumerate(key: str, outdir: Path, question: str, have: list[str], effort: str) -> None:
    angles = [
        "in OTHER heroes' own comics (Iron Man, Avengers, X-Men, Fantastic Four books), "
        "not the subject's own series",                      # the Mysterium lesson
        "annuals, one-shots and backup stories",
        "published 2021-2026 only",
        "in villains' own series or villain one-shots",
        "events and crossovers 2010-2020",
        "praised on comicbookroundup.com with critic rating 8+",
    ]
    have_txt = "; ".join(have) if have else "(none yet)"
    all_items = []
    for i, angle in enumerate(angles, 1):
        prompt = (
            f"QUESTION: {question}\n"
            f"Answer items ALREADY FOUND — do NOT repeat these or reprints of them: {have_txt}.\n"
            "Find MORE real instances answering the question. Every item needs the exact "
            "series + issue number + year (2010 or later), what VISIBLY happens on the page "
            "(drawable, not narration-only), and the URL you found it at. If a series is a "
            "REPRINT magazine (UK Panini 'Astonishing/Essential' collections), name the "
            "original US issue instead.\n"
            f"Restrict THIS search to: {angle}."
        )
        print(f"[enumerate {i}/{len(angles)}] {angle[:60]}…", flush=True)
        resp = _call_logged(key, prompt, effort, _schema(_ITEM_PROPS), outdir, f"enum{i}")
        for c in _cands(resp):
            c["_angle"] = angle
            all_items.append(c)
    # Collapse duplicates across angles, then drop anything already in --have. Both use
    # _same_issue: exact token equality under-dedups ("Venom #13 (2019)" vs "Venom #13"
    # from two angles both survived), and the have-list needs the SAME tolerance because
    # You.com writes the issue back in whatever form its source used.
    uniq, dropped = [], []
    for c in all_items:
        sii = c.get("series_issue_year", "")
        if not sii:
            continue
        if any(_same_issue(sii, h) for h in have):
            dropped.append(sii)
            continue
        if any(_same_issue(sii, u["series_issue_year"]) for u in uniq):
            continue
        uniq.append(c)
    report = outdir / "enumerate_report.md"
    lines = [f"# ENUMERATE — {question}\n"]
    for c in uniq:
        lines += [f"- **{c.get('series_issue_year')}** [{c.get('well_known_or_deep_cut')}] "
                  f"{c.get('character_or_thing')} — {c.get('what_visibly_happens','')[:220]}",
                  f"  src: {' '.join(c.get('source_urls') or [])} (angle: {c.get('_angle','')[:40]})"]
    report.write_text("\n".join(lines))
    print(f"\n{len(uniq)} unique item(s) from {len(all_items)} raw → {report}")
    # Never drop silently — a filter that hides its own work reads as "nothing was found".
    if dropped:
        print(f"[have-filter] dropped {len(dropped)} already-found: {'; '.join(sorted(set(dropped)))}")


def run_confirm(key: str, outdir: Path, issue: str, claim: str, effort: str) -> None:
    prompt = (
        f"Verify this claim about {issue}: \"{claim}\".\n"
        "Find the synopsis or review TEXT describing this exact event. Return the VERBATIM "
        "sentence and its URL (plus a second independent source if one exists). State: is "
        "this from the COMIC or from a film/TV/game adaptation? Does the event span multiple "
        "issues? Is the subject the MAIN character of this specific issue, or a cameo? Is "
        "this series a REPRINT magazine — if yes, name the original US issue. Never use "
        "publisher solicitation text as evidence. If you cannot find a verbatim sentence, "
        "the verdict is NOT CONFIRMED — do not soften it."
    )
    print(f"[confirm] {issue}…", flush=True)
    resp = _call_logged(key, prompt, effort, _schema(_CONFIRM_PROPS), outdir, "confirm")
    for c in _cands(resp):
        print(json.dumps(c, indent=2, ensure_ascii=False))


# ─── plumbing ────────────────────────────────────────────────────────────────────

def _cands(resp: dict) -> list[dict]:
    c = (resp.get("output") or {}).get("content")
    return list(c.get("candidates") or []) if isinstance(c, dict) else []


def _append_search_log(tag: str, effort: str, prompt: str,
                        resp: dict, seconds: float, leaks: list[str]) -> None:
    """One JSON file per DAY under scout_runs/log/, keyed by the search's timestamp.
    A running index of every query fired — separate from the per-run raw dumps, so
    the whole history is greppable in one place (Master 2026-08-05)."""
    now = datetime.datetime.now()
    logdir = REPO / "scout_runs" / "log"
    logdir.mkdir(parents=True, exist_ok=True)
    day = logdir / f"{now:%Y-%m-%d}.json"
    try:
        book = json.loads(day.read_text()) if day.exists() else {}
    except json.JSONDecodeError:
        book = {}
    # timestamp key; suffix a counter if two searches land in the same second
    stamp = now.strftime("%H:%M:%S")
    k, i = stamp, 1
    while k in book:
        i += 1
        k = f"{stamp}#{i}"
    book[k] = {
        "stage": tag, "effort": effort, "seconds": round(seconds, 1),
        "prompt": prompt[:2000],
        "candidates": _cands(resp),
        "sources": [s.get("url") for s in (resp.get("output") or {}).get("sources") or []],
        "off_domain": leaks,
    }
    day.write_text(json.dumps(book, indent=2, ensure_ascii=False))


def _call_logged(key: str, prompt: str, effort: str, schema: dict,
                 outdir: Path, tag: str) -> dict:
    t0 = time.time()
    try:
        resp = research(key, prompt, effort, schema)
    except urllib.error.HTTPError as e:
        print(f"  [{tag}] HTTP {e.code}: {e.read().decode()[:300]}")
        return {}
    dt = time.time() - t0
    (outdir / f"{tag}.json").write_text(json.dumps(resp, indent=2, ensure_ascii=False))
    leaks = offdomain(resp)
    _append_search_log(tag, effort, prompt, resp, dt, leaks)
    print(f"  [{tag}] {dt:.0f}s, {len(_cands(resp))} candidate(s), "
          f"off-domain={len(leaks)}" + (f"  LEAKED: {leaks[:3]}" if leaks else ""))
    return resp


def _load_key() -> str:
    key = os.environ.get("YDC_API_KEY", "").strip()
    if not key:
        env = REPO / ".env"
        if env.exists():
            m = re.search(r"^YDC_API_KEY=(.+)$", env.read_text(), re.M)
            if m:
                key = m.group(1).strip()
    if not key:
        sys.exit("YDC_API_KEY not set (env or .env)")
    return key


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("digest")
    d = sub.add_parser("discover")
    d.add_argument("--effort", default="standard")
    m = sub.add_parser("micro", help="scout single MOMENTS for micro_moment mode")
    m.add_argument("--effort", default="deep")
    m.add_argument("--years", default="2010 or later, strongly preferring the last two years",
                   help='publication window phrasing, e.g. "in 2025 or 2026"')
    e = sub.add_parser("enumerate")
    e.add_argument("--question", required=True)
    e.add_argument("--have", default="", help="semicolon-separated items already found")
    e.add_argument("--effort", default="deep")
    c = sub.add_parser("confirm")
    c.add_argument("--issue", required=True)
    c.add_argument("--claim", required=True)
    c.add_argument("--effort", default="deep")
    args = ap.parse_args()

    if args.cmd == "digest":
        print(build_scouted_digest())
        return
    key = _load_key()
    outdir = REPO / "scout_runs" / datetime.datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    outdir.mkdir(parents=True, exist_ok=True)
    if args.cmd == "discover":
        run_discover(key, outdir, args.effort)
    elif args.cmd == "micro":
        run_micro(key, outdir, args.effort, args.years)
    elif args.cmd == "enumerate":
        have = [s.strip() for s in args.have.split(";") if s.strip()]
        run_enumerate(key, outdir, args.question, have, args.effort)
    elif args.cmd == "confirm":
        run_confirm(key, outdir, args.issue, args.claim, args.effort)


if __name__ == "__main__":
    main()
