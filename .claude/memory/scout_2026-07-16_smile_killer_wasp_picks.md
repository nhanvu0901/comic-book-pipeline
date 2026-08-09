2026-07-16 moment-scout run (post session-limit restart). Reddit JSON confirmed still
network-blocked for this agent ("whoa there pardner" block page on old.reddit.com) — Step 1a
of the skill prompt is not executable here; relied on KnowYourMeme/CBR/ScreenRant
single-moment articles + comicbook.com sales-spike numbers instead (Step 1d), per existing
moment_scout_reddit_youtube_access_blocked.md.

## Winners this run (added to comic_candidates.csv as queued-micro-moment)
- **Batman: The Smile Killer (2020) #1** — top pick. batcave LIVE (news_id=691,
  chapter_id=3915, 35pp) confirmed by fetching real page images (not just series JSON).
  Real-page read found the actual comic is MORE layered than the old CSV hook suggested:
  it interweaves (a) young Bruce watching creepy kids'-show puppet "Mr. Smiles" grooming him
  ("fetch big sharp scissors"), (b) a present-day Arkham subplot where a psychiatrist insists
  Batman never existed, and (c) a Joker fight + a striking noir murder-scene splash (p.26: dead
  body on a rug + a child in clown facepaint holding a smoking gun) + a poster-quality Batman
  rooftop splash ending (p.28). Picked the puppet-show → murder-scene-reveal thread as the
  MICRO-MOMENT (skips the dialogue-heavy Arkham-interrogation pages, pp.18-25, which read as
  talking-heads on their own — same risk class as Immortal Hulk #13/Volstagg). Proof-of-interest
  = 2 independent dedicated single-moment articles (CBR + ScreenRant), no raw upvote/view number
  available but both outlets wrote a STORY-SPECIFIC piece, not a listicle.
- **Darkhold: Wasp (2021) #1** — batcave LIVE (news_id=5676, chapter_id=29564, 24pp). Real
  pages confirm strong stylized visual spectacle (NOT talking-heads): Hank Pym literally
  punches Janet Van Dyne (p.19), followed by a striking red/black silhouette monologue splash
  (p.21) building to her killing him. Proof-of-interest = ScreenRant ran a dedicated piece
  calling it "Marvel's Most Controversial Moment" (single-scene article, not listicle) + a
  second ScreenRant follow-up + active reader discussion. CONTENT NOTE flagged for Master:
  subject is domestic abuse + spousal killing — confirm tone fit before greenlight.

## Rejected this run (do not re-suggest without new info)
- **Amazing Spider-Man (2015-) #800 "Red Goblin"** — this was sitting in the CSV as
  "backlog-micro (thiếu proof number)". Found the missing proof this run (comicbook.com:
  >450,000 pre-orders, #797→#798 sales nearly doubled 128,189→233,235) — but a REAL PAGE
  READ (batcave news_id=5567/chapter_id=29062, confirmed = legacy #800 not #798) at pp.35 and
  42 shows the actual climax is a dense multi-character hostage plot: Norman Osborn planted
  kill-trigger shrapnel in FIVE named loved ones (Silk, Mary Jane, Aunt May, Harry Osborn, and
  Harry's son "Normie") and the fight involves Normie/Harry family drama a casual viewer has
  no way to parse. FAILS dead-simple/zero-lore despite having a real proof number and great
  visual spectacle — lesson: a proof-of-interest number does not waive the story-simplicity
  gate; always open the real pages before promoting a "missing proof" backlog row once the
  proof is found.
- **Predator vs. Black Panther (2024) #1** — confirmed HAS_NARRATION: TWO separate
  issue-by-issue narration videos ("PREDATOR VS BLACK PANTHER!! || issue 1/2, 2024") plus a
  dedicated "Full Story" video from channel "The Big Spill". Reject, don't re-check.
- **Godzilla vs. Hulk (2025) #1** — despite the title, the actual plot pulls in Thunderbolt
  Ross, Dr. Demonicus, Hedorah, and Mechagodzilla as named plot-relevant characters — too many
  moving parts for TELL-IN-TWO-SENTENCES even before checking coverage. Skip the line.
- **Batman/Deadpool crossovers (2025, both the DC Nov-2025 Grant Morrison one AND the Marvel
  Sept-2025 one)** — the DC one's "Deadbat" fusion money-shot already has a dedicated
  #shorts edit (youtube.com/shorts/LnpDgU4DB-s) live, AND the actual plot is extremely
  meta/self-referential (Cassandra Nova as villain — an X-Men lore character; Grant Morrison
  writes himself into the story with a "Cosmic Keyboard") — fails zero-lore hard even before
  the coverage check. Treat the whole 2025 Batman/Deadpool crossover event as burned: guaranteed
  pre-existing Shorts given how hard both companies marketed it, plus too meta for a casual
  viewer. Don't re-scout either version.
- **Absolute Carnage: Immortal Hulk (2019)** — CBR community reception describes the issue as
  centered on "a therapy session between Bruce, the Hulks, and the symbiote" — flagged as
  visual-spectacle risk (same talking-heads failure mode as Immortal Hulk #13) without
  spending a real-page-read check; deprioritize unless Master wants it re-verified by eye.
- **Wolverine Annual (2019) / Star Wars: Return of the Jedi – Lando (2023)** — both have decent
  reviews but NO measurable proof-of-interest signal found (no dedicated single-moment article,
  no quoted number, no meme entry) — fail Step 1 proof gate on evidence, not on story quality.

## Process note
batcave.biz Cloudflare challenge cannot be solved with plain curl (returns "Just a moment...").
Must go through the repo's own session solver: `utils.comic_scraper.readcomiconline._get_session()`
+ `_fetch_data(reader_url)` for series/chapter JSON, `_ajax_chapter_images(reader_url, news_id,
chapter_id)` for the real image URL list, then `sess.get(img_url, headers={"Referer": f"{SITE_BASE}/"})`
to actually download a page (a plain `sess.get` on the image CDN without the Referer header
returns 403). This is how "open the real page and eyeball it" should be done going forward
without needing Stage 2 to run first — much cheaper than a dry run.
