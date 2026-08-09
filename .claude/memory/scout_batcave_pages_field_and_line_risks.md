# Scout tool fix + infra/line risk notes (2026-07-09)

## batcave reader JSON bug (fixed in /tmp/comic_scout.py this run)
`_fetch_data(reader_url)["images"]` is an EMPTY list by default (0 items) even for
real, scrapable issues — do NOT use `len(images)` to count pages, it always
returns 0 and looks like a 404/broken chapter.
The real page count is `chapters[].pages` (an int) already present on the
SERIES page's `_fetch_data(series_url)["chapters"]` list — no need to even
fetch the reader page separately for a page-count check. Example:
`_fetch_data(series_url)["chapters"][0]["pages"]` → 34 for Hellboy: Krampusnacht.

## Fandom fetch was fully Cloudflare-blocked (2026-07-09)
`fetch_fandom` returned "miss" for EVERY title tested this run, including a
known-good title with confirmed prior synopsis ("What If...? Dark: Venom").
This is the Cloudflare managed-challenge block (see
project_fandom_fetch_pitfalls), not a reflection of candidate quality — when
this happens, do NOT down-rank candidates for "miss"; recommend a manual
`wiki_url` (the Fandom/Wikipedia page usually exists, e.g. confirmed via
WebSearch: marvel.fandom.com/wiki/Hulk:_Blood_Hunt_Vol_1_1,
dc.fandom.com/wiki/Batman:_The_Smile_Killer_Vol_1_1,
hellboy.fandom.com/wiki/Krampusnacht — that last one is on a wiki
(`hellboy.fandom.com`) NOT in the scout's supported-wiki list
(`darkhorse.fandom.com` is checked instead) — set wiki_url manually for any
Hellboy pick.

## Line-level risk notes
- **Immortal Hulk one-shots (Time of Monsters, Flatline, Threshing Place)**:
  each has at least one dedicated YouTube narration video under its own exact
  title (e.g. "IMMORTAL HULK TIME OF MONSTERS | COMICBOOK UNIVERSE", "The
  Immortal Hulk: FLATLINE (one-shot, 2021-)"), on top of Comicstorian's
  flagship "Immortal Hulk - Full Story". Treat the whole spinoff line as
  HAS_NARRATION risk — skip.
- **Batman: One Bad Day line**: page counts run 63-71pp for the ones still
  queued (Clayface 63, Ra's al Ghul 66, Catwoman 70, Penguin 71 per CSV) —
  confirmed via the reader's real page count, not a slug artifact. Over the
  pipeline's ~45pp sweet spot; reject remaining entries on page count unless
  future entries come in shorter.
- **Darkhold line remaining entries (Blade, Wasp)**: Blade rated ~6.7 CBR
  ("easily missable", "rushed") — below the ≥8.0 acclaim bar; usable as a
  fallback pick (same-line-as-produced bonus, mainstream Blade via movies)
  but not a top recommendation.
