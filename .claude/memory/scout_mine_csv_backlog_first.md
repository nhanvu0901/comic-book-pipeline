2026-07-07: comic_candidates.csv accumulates "queued" candidates across many past scout runs that
were never actually produced by Master. On a fresh "find more comics" request, query the CSV first:

    python -c "
    import csv
    with open('comic_candidates.csv') as f:
        rows=[r for r in csv.DictReader(f) if r['status'].startswith('queued')]
    for r in rows:
        pub = r['publisher']
        pg = int(''.join(c for c in r['pages'] if c.isdigit()) or 0)
        if pub in ('Marvel','DC') and 15<=pg<=50:
            print(r['title'], r['year'], pub, r['structure'], pg, r['hook'][:80])
    "

Filter for mainstream lead + one-shot + right page range, then only LIVE re-verify the shortlist
(1 batcave chapter-list call gives page count instantly — no need to fetch the reader page image
count; 1 fresh WebSearch narration check per pick). This found 5 solid picks in one run with zero
"seed discovery" WebSearches needed. Still spend 1-2 discovery searches for genuinely new 2025
material the backlog wouldn't have.

CAUTION: a backlog row's "verified-clean" narration verdict can go stale or be a past miss — a
2026-06 pass on "Superman (2016) #39 - Goodnight Moon" said clean, but a deeper 2026-07-07 check
found an English narration video (channel "Comics Explored", ~176s, #short-tagged) that a shallow
"comicstorian/complete story" query didn't surface because it doesn't use those exact words. When
a story is famous/acclaimed enough to be a top pick, always do one more oembed/plain-title search
pass before finalizing, even if the CSV already says "clean".
