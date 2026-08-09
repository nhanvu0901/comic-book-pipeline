# Casual-tone DC lane is thin; two DC leads checked and rejected (2026-07-21)

Ran under the new "CHỈ Marvel & DC, casual/fun/heartfelt tone (not dark/twist)" mandate.
The comic_candidates.csv backlog has ZERO good casual DC one-shots — every queued DC row
is Batman: One Bad Day (dark/violent, 63-71pp, over page budget) or Tales from the Dark
Multiverse (dark, 54pp+). Checked two promising DC leads fresh this run, both rejected:

- **Superman's Pal Jimmy Olsen (2019-)** by Matt Fraction — batcave id 5371, confirmed
  live. Reputation is exactly the target tone (silver-age-throwback, zany, beloved,
  Eisner-nominated) but it's a 12-issue maxi-series telling ONE overarching mystery
  ("who keeps trying to kill Jimmy Olsen") — no single issue is a clean self-contained
  story. Worth a deeper look in a future run to find whether any ONE issue (e.g. a
  standalone flashback/gimmick issue) resolves cleanly enough to lift out; did not have
  budget to check per-issue this run.
- **Shazam! Fury of the Gods Special: Shazamily Matters (2023)** — batcave id 4754,
  confirmed live, 87 pages (single chapter) — a movie-tie-in "Special" that's almost
  certainly a multi-story anthology bundle (typical for these branded specials, see
  scout_marvels_voices_anthology.md), way over one-shot page budget regardless. Rejected
  on page count alone.
- Booster Gold's only fun crossover on batcave is "Booster Gold/The Flintstones Special"
  — excluded, Flintstones is licensed non-Marvel/DC IP (out of scope per 2026-07-21
  Marvel/DC-only mandate).

## Infra note: WebSearch quota exhausted mid-run
Session hit "200 of 200 WebSearch calls" (shared session-wide budget, not per-agent) with
zero searches left for this run — even the very first query failed. Fallback to curl on
comicbookroundup.com did NOT work as a substitute this time: guessing CBR's issue-page
URL slug (`/comic-books/reviews/<publisher>/<series-slug>-<n>`) returns HTTP 200 but is
just the generic "NEW COMICS" homepage, not a hit — CBR does not have a discoverable static
search endpoint via plain curl (their `/search?search=` path 404s). When WebSearch is
unavailable, the CSV backlog (already-recorded ratings + narration verdicts from past runs
that DID have WebSearch) is the only usable proof source — do not waste calls re-guessing
CBR URLs.
