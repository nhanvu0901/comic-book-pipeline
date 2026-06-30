---
name: comic-scout
description: >
  Discover NEW comics for the comic-book-pipeline to narrate. Use when the user
  wants to find more comics to produce / post — e.g. "find comics like our dark
  series", "what should we make next", "scout untapped comics", "find a thrilling
  story we can run". The agent deep-checks pipeline fit (scrapable on batcave.biz +
  has a Fandom synopsis + published 2010 or later + ONE-SHOT prioritized, ~22-45 pages,
  any genre with a decent fanbase), verifies each candidate LIVE against batcave.biz
  and the Fandom MediaWiki API, keeps only comics with NO full-story narration on
  YouTube in ANY format (Short OR long-form recap), dedups against already-produced
  projects, and returns a ranked candidate
  table with ready-to-run Stage 2 commands. It SEEDS discovery from "best-of" curation
  (ComicBookRoundup top-rated, "best one-shots" lists, Reddit recs) — not only batcave
  keyword guessing — EXHAUSTIVELY enumerates every one-shot in a discovered line/event,
  and RANKS by critical rating + character popularity so the best, most-viral-fit
  stories surface first (not just whatever passes the mechanical gates).
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

## WORK BUDGET — stay efficient, do NOT burn the session (hard caps)
Thoroughness comes from RANKING smartly, not from brute-forcing hundreds of calls.
A previous run hit ~170 tool calls and exhausted the account session — that is a
FAILURE, not thoroughness. Obey these caps:
- **≤ 6 discovery/curation WebSearches total** (Step 2a). Gather candidate TITLES cheaply
  first; you do not need to fetch every page.
- **Cheap-rank BEFORE deep-verify:** from the discovery hits, shortlist the **top ~8
  candidates** by rating/feat/mainstream signal, then LIVE-verify ONLY those.
- **Per shortlisted candidate: ≤ 2 batcave calls + ≤ 1 Fandom check + ≤ 1 Shorts check.**
- **STOP once you have 5–6 fully-verified strong candidates** — return them; do not keep
  hunting. Quality of the top 5 beats a long mediocre list.
- Never re-fetch the same URL. Batch related checks. If a source 403s twice, move on.
Total target: well under ~50 tool calls for a normal run.

## NO-BRAINER — THE DEFAULT #1 FILTER (every run, unless the user overrides)
The channel needs VIRAL, FAST, instantly-absorbable content. By DEFAULT, the single most
important property of any candidate is: **its WHOLE premise is graspable in ONE sentence,
with ZERO lore / ZERO continuity / ZERO setup** — a viewer who knows nothing "gets it" in
~2 seconds.
  ✓ "Joker poisons all of Gotham." / "Superman turns evil." / "Batman fights a perfect
    copy of himself." / "Spider-Man's first kiss kills the man he kisses."
  ✗ anything that needs explaining a prior event, an alternate timeline, a dead universe,
    a death/resurrection chain, or a niche concept (e.g. DCeased: A Good Day to Die — too
    much lore). LORE-HEAVY = OUT, no matter how high the rating.
This is a HARD default gate AND the top ranking factor (above rating). When you write each
candidate's hook, it MUST be that one-sentence no-brainer premise; if you can't, reject it.
Only relax this if the user explicitly asks for a lore-heavy / deep-cut pick.

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
3. **Structure — TWO request MODES (read which one the user asked for):**
   • DEFAULT ("find the next comic"): a self-contained unit = one Short. Best→worst:
     (a) standalone one-shot / special; (b) anthology issue; (c) complete mini as
     one arc; (d) ongoing → a clearly self-contained arc. Branded specials
     (What If...? Dark, etc.) ARE valid here — they are the channel's staple.
   • "ICONIC SELF-CONTAINED MOMENT" MODE (the user's niche — confirmed 2026-06-15
     via example Shorts "Galactus at Silver Surfer's funeral" and "The time Odin
     fought Galactus", in the style of channels Mr Pool & StraightToFacts): find a
     comic that tells ONE self-contained, CANONICAL, iconic-or-deeply-emotional
     story/moment about MAJOR established characters — an epic confrontation, a
     heroic sacrifice, a tragic death/funeral, a profound character beat — readable
     with zero continuity, that NOBODY has made a narration Short of yet.
       ✓ canonical (really happened), self-contained, emotionally OR epically
         memorable, about well-known characters; 2010+ on batcave; wiki-grounded.
       ❌ NOT a "What If...?"/alternate-universe premise (user rejected these).
       ❌ NOT an event tie-in / anniversary / branded special, NOT an anthology,
         NOT a multi-issue continuity saga.
     A self-contained MINI that is one complete elegy/story (Silver Surfer: Requiem
     style) counts — pick the issue(s) on batcave that contain the moment.
   BOTH modes: ❌ no anthologies (unrelated characters per issue), ❌ no multi-issue
   sagas needing continuity. ~22–45 pages sweet spot; >~55 (TPB/OGN) → reject/split.
4. **Has a Fandom synopsis ≥200 chars** on a supported wiki (Stage 3 grounds
   narration on it; a WikiAuditor rejects fabrication). Supported universes:
   marvel, dc, imagecomics, powerrangers, darkhorse, valiant, turtlepedia
   (TMNT), starwars. AUTO grounding is best (Marvel fills `|Synopsis1=` per issue);
   no synopsis → still runs but weak grounding → rank lower / set wiki_url manually.
5. **English.**
6. **Appealing with a decent fanbase — ANY genre.** Dark / "what if" / horror is
   welcome but NOT required; the bar is "interesting story, decent fanbase".
7. **Not already produced** (dedup against `projects/*/comic_context.json`).
8. **No full-story narration on YouTube — ANY format** (see Step 4 — the STANDING
   RULE). A #shorts recap OR a long-form "Complete Story" / "Full Story" from a
   comic-recap channel (Comicstorian, Comics Explained, Variant Comics…) that narrates
   the whole issue start-to-finish → REJECT (the story is already tapped). Single-moment
   clips / reviews / reactions / promos do NOT disqualify.
9. **Story quality / acclaim — a GOOD story, not just a FIT one (NEW, high weight).**
   Check the comic's critical rating: WebSearch ComicBookRoundup (`comicbookroundup.com
   "<title>" reviews`), Goodreads, or critic/Reddit "best of" acclaim. STRONGLY prefer
   ≥8.0/10 or clearly-acclaimed stories. A pipeline-fit but mediocre/forgettable comic
   is NOT a good candidate. When several issues in a LINE qualify, rate ALL of them and
   surface the BEST — e.g. of the Darkhold one-shots, Iron Man 8.5 + Spider-Man 8.3 beat
   Blade 6.8 / Black Bolt 7.6; do NOT stop at the first few that pass the gates.
10. **Mainstream-character bias (virality lever).** All else equal, PREFER comics about
    widely-recognized characters (Spider-Man, Iron Man, Batman, Wolverine, Hulk, X-Men,
    Joker, Venom, Deadpool…) over obscure ones — a known character widens the YouTube
    seed pool + search demand (the documented reason our obscure picks plateau). Niche/
    obscure is acceptable ONLY when the story itself is exceptional (acclaim ≥8).
11. **POWER FEATS readers love (virality lever — high weight).** STRONGLY prefer stories
    built around a memorable POWER FEAT — a character using their abilities in an
    awe-inspiring, overwhelming, "how is that even possible" way that the powerscaling
    fandom obsesses over (the "<hero> solos <villain>", "<character> is way stronger than
    you think", "this version of X moves like Kratos" energy). These feats drive comments,
    debate, and shares — exactly the engagement that breaks the seed-pool ceiling. Hunt for:
    a hero/villain unleashing FULL power, a god-tier display, a shocking power upgrade or
    awakening, an underdog pulling off the impossible, a feat that re-ranks a character's
    strength. When you find one, NAME the specific feat in the hook (so Stage 3 + the banner
    lead with it). A story with a jaw-dropping feat beats an equally-rated story without one.

---

## Already produced (DEDUP — never re-recommend) — PERSISTENT, AUTHORITATIVE

`projects/` gets wiped/cleaned, so do NOT rely only on `projects/*/comic_context.json`
for dedup — projects can disappear after a video is made. THIS list is the source of
truth; union it with whatever `projects/` still has. NEVER recommend any title here again:
- **What If...? Dark: Venom** (Marvel, 2023)
- **What If...? Dark: Loki** (Marvel, 2023)
- **Power Rangers: Ranger Slayer** (2020)
- **Batman - One Bad Day: Bane** (DC, 2022)
- **Captain Marvel: The End** (Marvel, 2020)
- **Thor Annual** (Marvel, 2023) #1 — MODOK/Yggdrasil
- **Annihilation - Scourge: Silver Surfer** (Marvel, 2019)
- **What If...? Galactus Transformed Gambit** (Marvel, 2025)
- **Ghost Rider vs. Galactus** (Marvel, 2025) — Johnny Blaze (NOT Cosmic GR/Punisher)
- **Weapon VIII** (Marvel) — Edge of Spider-Verse (2024) #1, "New Toys" (Earth-72 Peter Parker weapon)
- **Edge of Spider-Verse (2014) #4** — Patton Parnell, the horror "monster" Spider-Man
- **Shadowland: Spider-Man** (Marvel, 2010) — Spider-Man + Shang-Chi vs Mr. Negative
- **What If...? Galactus Transformed Hulk** (Marvel) — "hulk suffer"
- **Godzilla vs. Hulk** (Marvel) — "hulk vs godzilla"
- **The Darkhold: Iron Man** (Marvel, 2021) — "ironman"
- **What If...? Galactus Transformed Moon Knight** (Marvel) — "moonknight suffer"
- **What If...? Galactus Transformed Rogue** (Marvel, 2025) — "rouge suffer"
- **The Darkhold: Spider-Man** (Marvel, 2021) — "spider-man darkhold"

When a new comic is produced, ADD it here immediately (don't wait — projects/ is volatile).

---

## User-rejected — NEVER recommend again

The user reviewed these and rejected them; do NOT surface them in ANY future run,
even though they pass the mechanical gates. Dedup against this list by title:
- **What If...? Dark: Spider-Gwen** (Marvel, 2023)
- **What If...? Dark: Moon Knight** (Marvel, 2023)
- **Web of Venom: Wraith** (Marvel, 2020)
- **Web of Venom: The Good Son** (Marvel, 2020)
- **X-Men: Marvels Snapshots** (Marvel, 2020)
- **Power Rangers Unlimited: Heir to Darkness** (BOOM!, 2021)
- **DCeased: A Good Day to Die** (DC, 2019) — too much lore, NOT no-brainer (2026-06-24)
- **Batman: One Bad Day: Mr. Freeze** (DC, 2022) (2026-06-24)
- **Venom: The End** (Marvel, 2020) — future-setting lore, not instantly absorbable (2026-06-24)
- **Batman: The Red Death** (DC, 2017) — HAS_SHORT: Comicstorian full-story origin #Shorts (2026-06-25)
- **Batman: The Murder Machine** (DC, 2017) — HAS_SHORT: Comicstorian full-story origin #Shorts (2026-06-25)
- **Batman: The Dawnbreaker** (DC, 2017) — HAS_SHORT: Comicstorian full-story origin #Shorts (2026-06-25)
- **Batman: The Drowned** (DC, 2017) — HAS_SHORT: Comicstorian full-story origin #Shorts (2026-06-25)
- **Batman: The Merciless** (DC, 2017) — HAS_SHORT: Comicstorian full-story origin #Shorts (2026-06-25)
- **Batman: The Devastator** (DC, 2017) — HAS_SHORT: Comicstorian full-story origin #Shorts (2026-06-25)
- **Tales from the Dark Multiverse: Batman Knightfall** (DC, 2019) — HAS_SHORT: Comicstorian "In 60 Seconds #Shorts" (2026-06-25)
- **Tales from the Dark Multiverse: Batman Hush** (DC, 2020) — HAS_SHORT: Comicstorian "In 60 Seconds #Shorts" (2026-06-25)
- **Web of Venom: Ve'Nam** (Marvel, 2018) — HAS_NARRATION: Comicstorian "Venom Origins in Vietnam Vs Wolverine 'Ve-Nam' — Complete Story" /watch?v=5AJdtQeMDBM (scout missed it on the apostrophe-vs-hyphen exact-quote bug) (2026-06-25)
- **Web of Venom: Carnage Born** (Marvel, 2018) — HAS_NARRATION: Comicstorian long-form full-story /watch?v=A1tzIjLkqro + fails NO-BRAINER (needs Knull "symbiote god" intro) (2026-06-25)
- **Tales from the Dark Multiverse: Flashpoint** (DC, 2020) — HAS_NARRATION: Comics Explained full-story /watch?v=G2hTGgp265s + lore-heavy alt-Flashpoint (2026-06-25)
- **Web of Venom (2026) #1** (Marvel) — user-rejected after full produce + review (Spider-Venom/Boomerang; #1 of "Death Spiral" ongoing); reader/34183/247060 (2026-06-26)

NO-BRAINER BAR (user, 2026-06-24): the channel needs VIRAL, FAST, instantly-absorbable
content. A candidate's WHOLE premise must be graspable in ONE sentence with ZERO lore /
ZERO continuity / ZERO setup — a viewer who knows nothing must "get it" in ~2 seconds
(e.g. "Joker poisons Gotham", "Superman goes evil"). REJECT anything that needs
explaining a universe, a prior event, a death/timeline, or a niche concept — even if
acclaimed. Lore-heavy = OUT, no matter the rating.

(Pattern to be cautious of: marketed "What If...?" / event-tie-in / alternate-universe
one-shot SPECIALS. The target is a self-contained DONE-IN-ONE story that reads as a
side-quest, not a branded special — confirm with the user if unsure.)

---

## Workflow

### Step 0 — Refresh the done-list and build the exclusion set

**MANDATORY — do this BEFORE any discovery search.** Build a complete exclusion set from
three sources, then skip ANY candidate whose title appears in it:

```bash
cd "/Users/nhan/Documents/Mac home project/comic-book-pipeline"
# Source 1: active projects (may be wiped, so union with CSV)
for f in projects/*/comic_context.json; do .venv/bin/python -c "import json,sys;print(json.load(open(sys.argv[1])).get('title',''))" "$f" 2>/dev/null; done
# Source 2: CSV — all produced + rejected-banned titles (the authoritative live record)
.venv/bin/python -c "
import csv
with open('comic_candidates.csv') as f:
    for r in csv.DictReader(f):
        if r['status'].startswith('rejected-banned') or r['status'].startswith('produced'):
            print(r['title'])
"
```

Source 3 (static fallback for titles produced BEFORE the CSV existed): the
"Already produced" and "User-rejected" lists hardcoded below in this file.

Union all three → **exclusion set**. Any candidate whose title is in this set → SKIP
immediately, do NOT search/verify/mention it. This is the single most important
efficiency rule: burned tool calls on banned titles are wasted budget.

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

### Step 2 — Discover candidates: SEED FROM CURATION FIRST, then enumerate, then batcave
Do NOT rely only on guessing batcave keywords (that is why good stories get missed).
Discover in this order:

**2a. Seed from "best-of" curation (WebSearch) — find the GOOD stories first.**
Run searches like: `best Marvel one-shot comics`, `best self-contained <character> comic`,
`most acclaimed What-If comics`, `comicbookroundup highest rated <line/character>`,
`reddit best standalone comic <character>`, `best <event> tie-in one-shots ranked`.
Collect the highly-rated / most-recommended titles → these become candidates to verify.
This surfaces the acclaimed stories the keyword-spelunk below would never hit.

**2b. EXHAUSTIVELY enumerate a discovered LINE/EVENT.** When you find a multi-one-shot
line or event (Darkhold, "What If...? Dark", Tales from the Dark Multiverse, "<X>: The
End", Absolute Carnage tie-ins, etc.), LIST EVERY one-shot in it (WebSearch the line +
"all issues" / check the publisher's reading guide), then rate + verify EACH — never
stop after the first 2-3. (The Darkhold line has Iron Man, Spider-Man, Blade, Black
Bolt, Wasp, Alpha, Omega — miss none.)

**2c. THEN keyword-search batcave** for the curated/enumerated titles to confirm they
exist + get the reader URL. Good seed queries (run several; the catalog is
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

**2d. Hunt self-contained issues inside long-running ONGOING series** — this is an
untapped vein. Famous ongoing series (Amazing Spider-Man, Absolute Batman, Wolverine,
X-Men, Iron Man, etc.) regularly publish issues designed as **done-in-one** stories,
identifiable by the words **"The End"** (or "Fin") on the FINAL PAGE vs. **"To Be
Continued…"** (skip). Method:
1. WebSearch: `"Amazing Spider-Man" OR "Wolverine" OR "Iron Man" self-contained done-in-one
   issue "the end" NOT "to be continued" 2020 2021 2022 2023 2024 site:comicbookroundup.com`
   or `reddit best standalone issue Amazing Spider-Man done in one`.
2. For a promising series on batcave, use `verify` to check chapter count and page count
   of a specific issue (each chapter = one issue; 20–45 pages = sweet spot).
3. CONFIRM it ends with "The End" not "Continued" — check via Fandom synopsis or a
   brief mention in a review. If ambiguous, it's UNSURE → do not add to CSV.
Ongoing issues that qualify: self-contained within ~22-45 pages, resolves fully, no
prior-issue knowledge needed (no-brainer gate still applies).

### Step 3 — Verify fit LIVE (page count + Fandom synopsis)
For each surviving candidate:
```bash
.venv/bin/python /tmp/comic_scout.py verify "https://batcave.biz/<id>-<slug>.html" "Canon Title" "marvel"
```
Reject: pages outside ~22–45 (note collected TPB/OGN), or page 404. Note the
exact `reader` URL (that is the Stage 2 input). Record Fandom char count + URL.
Use publisher `marvel`/`dc` when known (faster, exact wiki); `""` for
powerrangers/starwars/tmnt (searches all wikis).

### Step 4 — YouTube NARRATION check (the make-or-break gate) — DO THIS RIGOROUSLY
The North Star (top of this file): a story **almost nobody has narrated on YouTube yet**.
So the gate is broader than "is there a Short?": **does a dedicated full-story NARRATION
of this exact issue/arc already exist on YouTube — in ANY format?** A <60s `/shorts/`
recap AND a 5–20 min "Complete Story" / "Full Story" video BOTH count. If yes →
HAS_NARRATION → REJECT. The big comic-recap channels (Comicstorian, Comics Explained,
Variant Comics, ComicVerse…) ARE our competitors — if they already told this story
start-to-finish, it is TAPPED, no matter the runtime. (This is the rule the scout used
to get WRONG: it let long-form "Complete Story" recaps pass. They do NOT pass anymore.)

What DISQUALIFIES (→ HAS_NARRATION → reject):
- A `youtube.com/shorts/<id>` (or #shorts vertical clip) that recaps the whole issue.
- A long-form **full-story narration** of the issue/arc from a recap channel —
  Comicstorian ("… Complete Story", "… Full Story", "… in 60 Seconds"), Comics Explained
  ("… Full Story"), Variant, ComicVerse, or any channel narrating the plot start-to-finish.
- A **"… IN MINUTES" / "… in X minutes" / "the entire … " / multi-part "(Part 1)(Part 2)…"
  recap series** — these narrate the whole story across one or more videos → HAS_NARRATION.
- A **FOREIGN-LANGUAGE full-story narration** (e.g. Spanish "comics narrados", "historia
  completa", "resumen"; Portuguese, Hindi "in hindi") — coverage in ANY language counts.

⚠️ RECENT FAILURE (2026-06-27): a run marked 7 of 8 candidates "clean" that ALL had coverage
(Iron Man/Hellcat Annual "WEDDING FROM HELL" recap; the whole What If…?: Venom 2024 line via a
"What If Venom 2024 Part N" series + Comics Explained; Absolute Carnage: Symbiote of Vengeance
via Comicstorian; King in Black: Ghost Rider "Return of the King" one-shot recap). The gate was
NOT executed — do not repeat. A FAMOUS Marvel/DC one-shot is almost always already covered;
default to REJECT unless the searches below genuinely come up empty.

What does NOT disqualify (→ still KEEP):
- **Review / reaction / "is it worth it" / unboxing / interview / "best comic of the year"**
  — discusses the comic, does NOT narrate the plot.
- **Single-moment / single-fact / "did you know" / teaser** clip — shows ONE scene or
  states ONE twist, not the whole story.
- **Movie / TV / game / animated-show clip** with a name collision (e.g. "Venom: The Last
  Dance ending" is the film; "What If…? S3 Moon Knight" is the Disney+ cartoon).
- **Character-name collision** — a clip about a CHARACTER whose name overlaps the title
  but is a DIFFERENT comic (e.g. "Who is Dark Carnage?" ≠ "What If...? Dark: Carnage").

Method — SEARCH TITLE VARIANTS, never one exact quote (this is exactly why Ve'Nam slipped
through: Comicstorian titled it "Ve-Nam" with a HYPHEN, so an exact `"Ve'Nam"` search never
matched the video that was sitting right there):
1. Build spelling variants of the title — swap the apostrophe / hyphen / space and the
   removed form. e.g. "Ve'Nam" → also "Ve-Nam", "Ve Nam", "VeNam"; "What If...?" →
   "What If", "What If Dark". Drop subtitles to the core distinctive words.
2. For EACH variant, `WebSearch` `allowed_domains: ["youtube.com"]`:
   `<variant> comicstorian`, `<variant> "complete story"`, `<variant> "full story"`,
   `<variant> comics explained`, `<variant> #shorts`.
3. Run ONE plain WebSearch (NO domain filter): `<title> comicstorian full story` — the big
   channels rank on Google even when the youtube-domain search under-counts them.
4. INSPECT the hits: a `/shorts/` recap OR a recap-channel "Complete/Full Story" of THIS
   issue → HAS_NARRATION → reject. Reviews / single-clips / collisions → ignore (list above).

Verdict per candidate: **HAS_NARRATION** (a full-story narration exists, any format →
reject) / **NO_NARRATION** (none → keep) / **UNSURE** (re-run the variant searches before
deciding). When UNSURE on a FAMOUS mainstream story, lean REJECT — almost all are already
covered; only KEEP after the variant searches genuinely come up empty.
Record the verdict's EVIDENCE honestly: if you find a Comicstorian/Comics Explained
full-story video, that is HAS_NARRATION → REJECT — do NOT write "long-form NOT a Short →
verified-clean" (that was the bug).

**MANDATORY EVIDENCE PER CANDIDATE (no "clean" without it):** for EVERY candidate you keep,
the output MUST show the actual searches you ran AND the top hits you inspected, e.g.
`NO_NARRATION — ran: "<title> comicstorian", "<title> complete story", "<title> in minutes",
"<title> comics explained", "<title> #shorts" → only reviews/previews found (list 2-3 hit
titles)`. If you cannot list the searches + hits, the candidate is UNSURE, not clean. A bare
"clean"/"verified" with no shown searches is INVALID and must not be appended to the CSV.

### Step 5 — Rank & output
**HARD GATES (must pass — these are pass/fail, NOT ranking factors):** on batcave,
2010+, ONE-SHOT/self-contained, NO full-story narration (ANY format — Short OR long-form
recap), not produced/rejected, **NO-BRAINER** (one-sentence premise, zero lore/continuity).
If a candidate needs ANY one-line lore intro to land — a prior event, a character's
backstory, a cosmic concept (e.g. Knull) or a bloodline/family tree — it FAILS the
no-brainer gate → REJECT it outright. Do NOT surface it as a "RISK" / "maybe" candidate
and do NOT add it to the CSV. "RISK: lore needs a brief intro" is a REJECT, not a caveat.
**Then RANK the survivors best→worst by (in priority order):**
  1. **NO-BRAINER** — premise graspable in ONE sentence, zero lore/continuity (DEFAULT #1, see above);
  2. **Critical rating / acclaim** — ComicBookRoundup ≥8.0 or clearly acclaimed;
  3. **POWER FEAT readers love** — a jaw-dropping powerscaling/feat moment (drives debate + shares);
  4. **Mainstream character** — recognizable hero/villain → wider seed pool + search demand;
  5. **Strong self-contained HOOK** — epic / tragic / twist / feat that lands in ONE Short;
  6. **AUTO Fandom grounding ≥200** — runs with zero setup — TIEBREAK only;
  7. same-line-as-produced bonus.
GROUNDING IS A TIEBREAK, NOT A GATE: an acclaimed, mainstream story with weak/blocked
Fandom is still a TOP pick — keep it, flag a manual `wiki_url` (Wikipedia works). NEVER
drop or down-rank a great story just because Fandom auto-grounding missed.
Output a Vietnamese report, a table sorted best-first with columns:
`# | Title (year) | Publisher | Structure | Pages | Rating | Fandom (chars) | Short? | Hook`

### Step 6 — APPEND eligible candidates to `comic_candidates.csv` (single source of truth)
The repo root has `comic_candidates.csv`. On every "find comic" run, **APPEND** (never
rewrite/reorder) the new candidates to it. A row may be added ONLY if it passes ALL gates
(verified live, not assumed):
  ① on batcave.biz  ② year ≥ 2010  ③ one-shot/self-contained (one-shot preferred)
  ④ NO full-story narration in ANY format (Short OR long-form recap; verified by Step 4's
     TITLE-VARIANT search — apostrophe/hyphen/space variants + the recap channels by name)
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
