# Casual/fun-lane backlog: real CBR ratings attached + 1 title delisted (2026-07-21)

## Pivot context
Team-lead dropped the dark/hard-irony requirement (2026-07-21): channel now wants
CASUAL, INTERESTING, engaging storylines in ANY genre (fun/chaotic/heartfelt/weird/
cool-concept), not just dark moments. The existing `comic_candidates.csv` "queued"
backlog already had ~15 non-dark all-ages titles (Sonic 30th Anniversary line, Rick and
Morty vs the Universe, DuckTales, Power Rangers Unlimited, Transformers: Shattered
Glass, TMNT crossovers, Invincible Returns) sitting unused because past runs were
scored for dark/irony fit. These are a ready-made casual-lane goldmine — mine the CSV
backlog before any fresh discovery search.

## WebSearch budget note
This run hit the session's WebSearch cap (200/200, shared across all agents in the
team) almost immediately — 0 searches available. Fallback that still worked: direct
`curl -A "Mozilla/5.0" -L` against comicbookroundup.com (search-results?keyword=... and
the resulting /comic-books/reviews/<publisher>/<series-slug>/<issue-slug> pages) — this
is a plain HTTP fetch via Bash, NOT WebFetch/WebSearch, so it doesn't touch either
budget. Regex `Scored a ([0-9.]+) Rating Based On (\d+) Critic Reviews` in the page
HTML gives the exact score + sample size. Use this path whenever WebSearch is
exhausted and a ComicBookRoundup number is needed.

## Real CBR ratings pulled this run (score / #critic reviews / URL)
- Rick and Morty vs the Universe: Summer of Love (2025) — 8.5 / 1 review —
  comicbookroundup.com/comic-books/reviews/oni-press/rick-and-morty-vs-the-universe-(2025)/summer-of-love-1
- Rick and Morty vs the Universe: Beth 'Til Death (2025) — 7.5 / 1 review — same series, /beth-til-death-1
- Power Rangers Unlimited: Countdown to Ruin (2022) — 8.4 / 5 reviews (best sample
  size found this run) — .../boom-studios/power-rangers-unlimited/countdown-to-ruin-1
- Sonic the Hedgehog: Amy's 30th Anniversary Special (2023) — 9.0 / 1 review —
  .../idw-publishing/sonic-the-hedgehog-(2018)/amys-30th-anniversary
- Sonic the Hedgehog: Knuckles' 30th Anniversary Special (2024) — 8.0 / 1 review —
  .../sonic-the-hedgehog-(2018)/knuckles-30th-anniversary-1
- Sonic the Hedgehog: Tails' 30th Anniversary Special (2022) — 7.8 / 2 reviews —
  .../sonic-the-hedgehog-(2018)/tails-30th-anniversary-1
- Sonic the Hedgehog: Halloween Special (2023) — 7.0 / 3 reviews — .../halloween-special
- Sonic the Hedgehog: Chaotix's 30th Anniversary Special (2025) — N/A, ZERO reviews
  yet (too new/niche) — do not cite a rating for this one, no proof-of-interest signal.
- Transformers: Shattered Glass #1 (2021) — 8.8 / 8 reviews (best sample size overall) —
  .../idw-publishing/transformers-shattered-glass/1 — BUT structure risk: #1 of a
  5-issue mini, not self-contained (setup issue, doesn't resolve) — flag before using.
- DuckTales (2024) #1 — 9.0 / 2 reviews — .../dynamite/ducktales-(2024)/1 — same
  structure risk: ongoing #1, self-containment unverified.
- Invincible Returns (2010) — Critic 5.4/10 (4 reviews, mediocre) but User rating
  8.4/10 (readers like it much more than critics) — .../image-comics/invincible-returns
  — mixed signal, note both numbers honestly if used.

Sample sizes are small (1-8 reviews) because these are niche all-ages/licensed books —
CBR rating + URL is the best obtainable proof-of-interest for this lane; Reddit/YouTube
view numbers are not realistically findable for most all-ages one-shots (different
tier of virality than Marvel/DC event books). Don't hold this lane to the same
"≥5k upvotes / ≥100k views" bar as a mainstream Marvel/DC pick — CBR critic score is
the accepted substitute here (this is how the pre-existing CSV rows already treated it).

## IMPORTANT — title delisted from batcave.biz
**Teenage Mutant Ninja Turtles x Naruto #1** (IDW/Viz, 2024) was "queued/verified-clean"
in the CSV since 2026-06-23, but as of 2026-07-21 its stored reader URL
(`batcave.biz/reader/33163/234610`) returns **HTTP 410 Gone**, and a fresh batcave
search for "naruto" / "tmnt naruto" / "ninja turtles naruto" returns **zero hits** —
the whole series has been delisted from the site (likely a licensing takedown, Naruto/
Viz content is exactly the kind of thing that gets pulled). Appended a `rejected-banned`
CSV row. **Lesson: a backlog row's batcave availability can also go stale, not just its
narration verdict — always re-verify `_fetch_data(reader_url)` returns non-empty before
recommending a backlog pick that's more than a few weeks old, not just its narration
check.**
