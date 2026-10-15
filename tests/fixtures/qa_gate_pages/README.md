Trimmed copies of the fandom issue pages cited by the 2026-10-15 live QA-discovery run
(read through the free r.jina.ai reader): reader header + the article's intro, `Synopsis for`
and `Solicit Synopsis` sections. The ~14k-char site navigation that precedes the article on the
real pages is NOT stored; the tests re-insert a synthetic prefix of that size.

- `live_candidates.json` — the 10 candidates that run produced (gate-relevant fields only).
- `marvel_release_categories.json` — release/cover-date categories from the free MediaWiki
  `prop=categories` API; Marvel Database pages carry no date in their body text.
