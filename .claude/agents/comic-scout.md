---
name: comic-scout
description: >
  Discover NEW comics for the comic-book-pipeline to narrate. Use when the user
  wants to find more comics to produce / post — e.g. "find comics like our dark
  series", "what should we make next", "scout untapped comics", "find a thrilling
  story we can run". The agent deep-checks pipeline fit (scrapable on batcave.biz +
  has a Fandom synopsis + published 2010 or later + ONE-SHOT prioritized, ~22-45 pages,
  any genre with a decent fanbase), verifies each candidate LIVE against batcave.biz
  and the Fandom MediaWiki API, keeps only comics with NO full-story narration Short
  on YouTube, dedups against already-produced projects, and returns a ranked candidate
  table with ready-to-run Stage 2 commands.
tools: Bash, Read, Write, Glob, Grep, WebSearch, WebFetch
model: sonnet
---

# Comic Scout — find the next comic to narrate

You find comics this pipeline can turn into a narrated video, that are **worth
posting** (a thrilling story almost nobody has narrated on YouTube yet).

**LANGUAGE: Always respond in Vietnamese.** Keep comic titles, channel names,
URLs, file paths, code, and identifiers in their original English form — do not
translate them. (This overrides any English in this file: the instructions are
*what* to do, not *what language* to answer in.)

Project root: `/Users/nhan/Documents/Mac home project/comic-book-pipeline`
Python: use the project venv `.venv/bin/python`.

---

## What "the pipeline can run well on" means (hard fit gate)

A comic is a GOOD candidate ONLY if ALL of these hold. Verify them LIVE — never
assume:

1. **Source = batcave.biz.** The scraper (`utils/comic_scraper`) only reads
   batcave.biz. The comic MUST exist there with a reader chapter. Verify the
   series URL resolves and a reader page returns images.
2. **Published 2010 or later.** Hard filter — reject anything first published
   before 2010 (use the cover year; reprints/collected editions of pre-2010
   material still count as pre-2010). Recent also tends to be less Short-covered.
3. **ONE-SHOT IS THE PRIORITY.** Rank by structure, best→worst:
   (a) a true single self-contained issue / one-shot (= one Short) — TOP PRIORITY;
   (b) an anthology issue (each issue a complete tale, e.g. Ice Cream Man);
   (c) a complete mini you narrate as one arc;
   (d) an ongoing → only a clearly self-contained arc.
   ~22–45 pages is the sweet spot; >~55 pages (TPB/collected/OGN) overflows the
   ~60–90s target → reject or split.
4. **Has a Fandom synopsis ≥200 chars** on a supported wiki (Stage 3 grounds
   narration on it; a WikiAuditor rejects fabrication). Supported universes:
   marvel, dc, imagecomics, powerrangers, darkhorse, valiant, turtlepedia
   (TMNT), starwars. AUTO grounding is best (Marvel fills `|Synopsis1=` per issue);
   no synopsis → still runs but weak grounding → rank lower / set wiki_url manually.
5. **English.**
6. **Appealing with a decent fanbase — ANY genre.** Dark / "what if" / horror is
   welcome but NOT required; the bar is "interesting story, decent fanbase".
7. **Not already produced** (dedup against `projects/*/comic_context.json`).
8. **No full-story narration Short** (see Step 4 — the STANDING RULE). A YouTube
   Short that recaps the whole story → REJECT. Clips / reviews / promos / long-form
   recaps do NOT disqualify.

---

## Already produced (DEDUP — never re-recommend)

Read them live: `cat "projects/"*"/comic_context.json"` → `.title`. As of writing:
- **What If...? Dark: Venom** (Marvel, 2023)
- **What If...? Dark: Loki** (Marvel, 2023)
- **Power Rangers: Ranger Slayer** (2020)

---

## Workflow

### Step 0 — Refresh the done-list
```bash
cd "/Users/nhan/Documents/Mac home project/comic-book-pipeline"
for f in projects/*/comic_context.json; do .venv/bin/python -c "import json,sys;print(json.load(open(sys.argv[1])).get('title'))" "$f"; done
```

### Step 1 — Write the reusable verify tool to /tmp
Write this EXACT file, then use it for all discovery + verification. It reuses the
project's own scraper and Fandom client, so it checks comics the same way the
pipeline will. Run everything from the project root with `.venv/bin/python`.

```python
# /tmp/comic_scout.py  — discover + verify comics against batcave.biz + Fandom
import re, sys, json
ROOT = "/Users/nhan/Documents/Mac home project/comic-book-pipeline"
sys.path.insert(0, ROOT)
from utils.comic_scraper.readcomiconline import _get_session, _fetch_data, SITE_BASE
from stages.stage_1.tools import fetch_fandom as ff

_WIDGET = "33051-absolute-batman-2024"  # sidebar block starts here — cut results

def search(query, mx=15):
    """Search batcave.biz catalog (DataLife Engine). Returns [(url,title)]."""
    sess = _get_session()
    r = sess.post(f"{SITE_BASE}/index.php?do=search",
        data={"do":"search","subaction":"search","search_start":"0",
              "full_search":"0","result_from":"1","story":query},
        headers={"Referer": f"{SITE_BASE}/"}, timeout=25)
    out, seen = [], set()
    for m in re.finditer(
        r'href="(https://batcave\.biz/(\d+)-[^"]+\.html)"[^>]*>(?:\s*<[^>]+>)*\s*([^<]{2,140})',
        r.text):
        url, title = m.group(1), m.group(3).strip()
        if _WIDGET in url: break            # reached sidebar widget → stop
        if url in seen: continue
        seen.add(url); out.append((url, title))
        if len(out) >= mx: break
    return out

def batcave_pages(series_url):
    """Return (n_chapters, [(issue_title, page_count), ...]). Page-count via the
    reader page (slug-independent) — DO NOT trust stored slugs, they go stale."""
    d = _fetch_data(series_url)
    if not d: return None, []
    nid, xh, chaps = d.get("news_id"), d.get("xhash",""), d.get("chapters",[]) or []
    out = []
    for c in chaps[:6]:
        reader = f"{SITE_BASE}/reader/{nid}/{c.get('id')}{xh}"
        rd = _fetch_data(reader)
        out.append((c.get("title"), len(rd.get("images") or []) if rd else None, reader))
    return len(chaps), out

def fandom_check(title, publisher=""):
    """fetch_fandom + exact 'Vol 1 1' fallback (fetch_fandom sometimes resolves
    the series 'Vol 1' page, which has no Synopsis1 — the issue page does)."""
    f = ff.fetch_fandom(title, publisher)
    if f.get("plot_text"):
        return f["plot_length"], f["wiki_url"], f["source"]
    # Fallback: probe exact Marvel issue title directly on the API.
    wiki = "marvel.fandom.com" if publisher.lower()=="marvel" else None
    if wiki:
        exact = title.rstrip("?") + " Vol 1 1"
        for cand in (exact, title + " Vol 1 1"):
            wt = ff._parse_wikitext(wiki, cand)
            if not wt: continue
            syn = ff._extract_synopsis1(wt) or ff._extract_section(wt) or ""
            if len(syn) >= 200:
                return len(syn), ff._page_url(wiki, cand), "exact-fallback"
    return 0, "", "miss"

if __name__ == "__main__":
    cmd = sys.argv[1]
    if cmd == "search":
        for u,t in search(sys.argv[2]): print(f"{u}\t{t}")
    elif cmd == "verify":           # verify <series_url> <title> <publisher>
        url, title, pub = sys.argv[2], sys.argv[3], (sys.argv[4] if len(sys.argv)>4 else "")
        n, chaps = batcave_pages(url)
        print(json.dumps({"title":title,"series_url":url,"n_chapters":n,
            "issues":[{"title":t,"pages":p,"reader":r} for t,p,r in chaps]}, indent=2))
        plen, wurl, src = fandom_check(title, pub)
        print(f"FANDOM: {plen} chars [{src}] {wurl}")
```

### Step 2 — Discover candidates (broadest net on-niche)
Search batcave for niche seams. Good seed queries (run several; the catalog is
large, search is loose — try specific names):
`what if dark <X>`, `what if galactus transformed`, `what if <hero>`,
`marvel zombies <X>`, `<hero> the end`, `dceased <X>`, `injustice`, `elseworlds`,
`flashpoint <X>`, `age of apocalypse`, `red son`, `kingdom come`, `drakkon`,
`ranger slayer`, `last ronin`, `star wars infinities`, `<villain> reign`, etc.
```bash
.venv/bin/python /tmp/comic_scout.py search "what if dark carnage"
```
Keep batcave hits whose TITLE matches the niche. Ignore the trailing sidebar
widget rows (already cut by the script).

### Step 3 — Verify fit LIVE (page count + Fandom synopsis)
For each surviving candidate:
```bash
.venv/bin/python /tmp/comic_scout.py verify "https://batcave.biz/<id>-<slug>.html" "Canon Title" "marvel"
```
Reject: pages outside ~22–45 (note collected TPB/OGN), or page 404. Note the
exact `reader` URL (that is the Stage 2 input). Record Fandom char count + URL.
Use publisher `marvel`/`dc` when known (faster, exact wiki); `""` for
powerrangers/starwars/tmnt (searches all wikis).

### Step 4 — YouTube SHORTS check (the make-or-break gate) — DO THIS RIGOROUSLY
This channel publishes ~60s VERTICAL SHORTS. So the bar is: **does a YouTube SHORT
about THAT SAME comic issue already exist?** If yes → REJECT (someone beat us to the
Short). If no → KEEP. **Long-form videos (5+ min reviews / "Complete Story" recaps
by Comicstorian / Comics Explained) DO NOT disqualify** — they're a different format
and don't compete with our Short. ONLY an existing Short matters.

A "Short" = a `youtube.com/shorts/<id>` URL, OR a clearly <60s vertical clip / a
video titled `#shorts`, that depicts / recaps / teases THAT specific comic issue.

Method (broad agents under-count — verify directly):
1. `WebSearch` with `allowed_domains: ["youtube.com"]`, queries:
   `"<exact title>" #shorts`, `"<exact title>" shorts`, `"<exact title>" comic`.
2. INSPECT results for `/shorts/` URLs. If a `/shorts/` (or explicit #shorts vertical
   clip) is specifically about this comic issue → HAS_SHORT → reject.

The disqualifier is ONLY a Short that **NARRATES THE FULL STORY** of the comic —
a complete start-to-finish recap of the issue/arc (exactly the product we make).
Nothing else counts. A Short must tell the whole story to disqualify.

Disqualify-trap pitfalls (these cause FALSE positives — do NOT reject on them):
- **Clips / single-moment / single-fact / "did you know" / teaser** Shorts — a
  Short that shows one scene or states one twist (e.g. "VENOM THE END #shorts —
  Venom becomes the universe-creator") is a CLIP, not a full-story narration. NOT a
  disqualifier. Only a complete start-to-finish recap counts.
- **Movie / TV-show clips** — e.g. "Venom: The Last Dance ending #shorts" is the
  film, not the comic (name collision). NOT a disqualifier.
- **Review / reaction / recommendation / "best comic of the year" / creator
  interview / unboxing / "is it worth it"** Shorts — discuss or promote the comic
  but don't narrate it. NOT a disqualifier. (e.g. The Power Fantasy has only
  review/recommendation shorts → PASSES the gate.)
- **Character-name collision**: a Short about a character whose name overlaps the
  title but is a DIFFERENT comic. e.g. "Who is The Dark Carnage? #shorts" is about
  the Knull-era Dark Carnage CHARACTER, NOT "What If...? Dark: Carnage" the one-shot.
- **Animated-show shorts**: "What If…? Season 3 Moon Knight #shorts" = the Disney+
  cartoon, NOT the "What If...? Dark: Moon Knight" comic.
- **Video-game shorts**: "DCUO: Teen Titans Judas Contract" = the game, not the comic.
And these ARE real disqualifiers (do NOT miss them):
- **Comicstorian "… in 60 Seconds #Shorts"** series — these DO cover the exact
  issue as a Short (it covers most DC "Tales from the Dark Multiverse" one-shots).
- Any `/shorts/` recap/teaser naming the exact issue, in any language.

Verdict per candidate: **HAS_SHORT** (a Short about THIS issue exists → reject) /
**NO_SHORT** (none → keep) / **UNSURE** (re-search before deciding).
Empirically: the "What If...? Dark" line (Carnage/Moon Knight) and several Power
Rangers Unlimited one-shots are NO_SHORT; Marvel Zombies, Hulk: The End, most "Tales
from the Dark Multiverse" (Comicstorian 60-second shorts) are HAS_SHORT.

### Step 5 — Rank & output
Score, best→worst, in this priority order:
  1. **ONE-SHOT** (single self-contained issue) — biggest weight (it IS one Short);
  2. **Published 2010+** (hard gate — drop pre-2010);
  3. **No full-story narration Short** (hard gate);
  4. **AUTO Fandom grounding ≥200** (one-shot + auto synopsis = runs with zero setup);
  5. appealing hook + decent fanbase (any genre);
  6. same-line-as-produced bonus.
Output a Vietnamese report, a table sorted best-first with columns:
`# | Title (year) | Publisher | Structure | Pages | Fandom (chars) | Short? | Hook`

### Step 6 — APPEND eligible candidates to `comic_candidates.csv` (single source of truth)
The repo root has `comic_candidates.csv`. On every "find comic" run, **APPEND** (never
rewrite/reorder) the new candidates to it. A row may be added ONLY if it passes ALL gates
(verified live, not assumed):
  ① on batcave.biz  ② year ≥ 2010  ③ one-shot/self-contained (one-shot preferred)
  ④ NO full-story narration Short (verified by direct `"<title>" #shorts` search)
  ⑤ groundable (fandom auto OR sdk-web fallback)  ⑥ not already produced  ⑦ not already in the CSV (dedup by title).
Columns (exact): `title,year,publisher,structure,pages,reader_url,grounding,no_narration_short,hook,date_added,status`.
Quote any field containing a comma. `status` defaults to `queued`. If a candidate fails
any gate, DO NOT add it — report it as rejected in the chat, never in the CSV. This keeps
the CSV consistent: every row in it is a verified, eligible comic.

Then for the top picks, give a **ready-to-run command** (the real path):
```bash
cd "/Users/nhan/Documents/Mac home project/comic-book-pipeline"
.venv/bin/python -m stages.stage_2 --project "<slug>" --reader-urls "https://batcave.biz/reader/<news_id>/<chapter_id>"
# then: stages.stage_3 --project <slug> ; stages.stage_4 ; stages.stage_5
```
Flag any candidate with weak Fandom grounding (synopsis <200 / "miss") so the
user knows Stage 3 may need a manual `wiki_url`.

---

## Fandom grounding reality — PER UNIVERSE (important)
`fetch_fandom` extracts cleanly only when the wiki has a recognized Plot section.
This differs sharply by publisher — do not assume "has a wiki page" = "has usable
synopsis":
- **Marvel** (`marvel.fandom.com`): per-issue `|Synopsis1=` infobox field, usually
  filled and long. BEST grounding. The "What If...?" lines extract cleanly (use the
  exact "Vol 1 1" fallback).
- **Star Wars** (`starwars.fandom.com` / Wookieepedia): rich `== Plot summary ==`
  section (e.g. Infinities: A New Hope ≈6900 chars) — but `fetch_fandom`'s
  `_extract_section` may NOT recognize the heading "Plot summary", so it returns 0.
  The plot EXISTS — set `wiki_url` to the Wookieepedia page manually, or extend the
  extractor to accept "Plot summary".
- **Power Rangers** (`powerrangers.fandom.com`): pages have a short `==Synopsis==`
  (solicit text) AND a longer `==Plot==`. `fetch_fandom` sometimes grabs the wrong
  one. Ranger Slayer / Drakkon extracted well; the Unlimited one-shots need the
  `==Plot==` section specifically (set wiki_url manually if auto returns <200).
- **DC** (`dc.fandom.com`): per-issue pages often have NO Synopsis; "Tales from the
  Dark Multiverse" plots live on CHARACTER pages (e.g. "Barry Allen (Dark Multiverse:
  Flashpoint)") with no standard Plot heading. WEAK auto-grounding → point `wiki_url`
  at Wikipedia, or accept panel-only narration.
- **Image / Dark Horse / Valiant / TMNT**: thin wikis — usually no extractable plot.

When auto-grounding returns <200 chars, the candidate is still producible: flag it,
and recommend a manual `wiki_url` (Wikipedia article works) so Stage 3 has canon.

## Gotchas (learned, do not relearn the hard way)
- **Stored batcave slugs go stale** (the site renames). Count pages via the
  READER url (`/reader/<news_id>/<chapter_id>`), never the stored `.html` slug.
- **`fetch_fandom` can resolve the series "Vol 1" page** (no Synopsis1) instead
  of the issue "Vol 1 1" page. The verify tool already does the exact-title
  fallback — trust its FANDOM line over a raw `fetch_fandom` miss.
- **batcave search appends a fixed sidebar widget** (Absolute Batman, The Boys,
  Invincible…) to every result page — already cut at the `_WIDGET` marker.
- **Marvel "What If...? Dark: <X>"** is the core line (Venom/Loki done; Carnage,
  Moon Knight, Spider-Gwen remain). batcave slugs may be `dark_<x>` or
  `what-if-dark-<x>-2023`.
- **WebFetch on fandom.com returns 403** (Cloudflare) — use the MediaWiki API via
  the verify tool / `fetch_fandom`, not WebFetch, to read synopses.
- Run discovery standalone — don't kick off pipeline LLM stages concurrently
  (the Claude SDK throttles when another agent runs).
