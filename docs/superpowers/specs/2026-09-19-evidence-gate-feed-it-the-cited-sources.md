# Feed the evidence gate the sources the candidate actually cited

Date: 2026-09-19
Status: approved, ready for implementation
Branch: `feat/scout-single-selection-flow`

## Problem

Every candidate in session `f56a9bc0` ("What is the hidden cost of Deadpool's healing
factor?") came back `inconclusive` or `rejected`. The gate's own reasons all say the same
thing:

> The raw evidence does not contain ... the cited CBR article
> The provided search results are all from Reddit and do not support ...
> this URL is not present in the provided evidence list, making it unverifiable

The gate is not too strict. It is being asked a question it cannot answer.

### Defect 1 — the search query is a truncated JSON dump

`ScoutWorkflow._gate_one` searches with the candidate serialised whole:

```python
raw = self.client.search(
    json.dumps(candidate, ensure_ascii=False),
    bundle.source_profiles.get("specific_web_search"),
)
```

`compact_search_query` then keeps the first 45 words / 360 characters. The string that
actually reaches the API, reconstructed from the session on disk:

```
{"evidence_urls": ["https://www.cbr.com/black-panther-deadpool-solve-death/",
"https://www.cbr.com/deadpools-healing-factor-redefined/"], "series_issue_year":
"Black Panther vs. Deadpool #2 (2018)", "source_issue": "Black Panther vs.
Deadpool #2", "summary": "Wade's body is revealed to be regenerating dead
tissue, not healthy tissue. The "dying factor"
```

JSON braces, quote marks and field names, cut off mid-sentence — 446 of 800 characters
discarded. It reads as forum chatter, so forum chatter is what comes back. Domain counts
from the stored payloads:

| candidate | Reddit hosts | everything else |
|---|---|---|
| candidate-1 | 67 | 1 screenrant, 1 marvel.fandom, 0 CBR |
| candidate-11 | 83 | 1 marvel.fandom, 0 CBR |

### Defect 2 — the cited URLs are never fetched

`evidence_gate.v1.md` instructs:

> Check that the exact issue and year, visible event, and **every cited URL** are
> supported by the raw evidence.

Nothing ever retrieves those URLs. They appear in the query string as search *text* and
nowhere else. The gate is required to verify sources that the pipeline never supplies, so
any candidate citing CBR or Fandom fails by construction.

### Consequence

Nothing ever reaches `confirmed`, so `Re-scout, keep confirmed` never appears and there is
never anything to keep — the second half of the reported symptom.

## Design

### 1. A real search query

Build it from the candidate's own fields in plain words — series, issue, year, the
character, and what visibly happens — never `json.dumps`. `compact_search_query` keeps
enforcing the 45-word / 360-character limit; the point is that the words it keeps are
words, not punctuation.

### 2. Fetch what the candidate cited

Retrieve each cited URL (`evidence_urls`, plus `source_urls` when present) through the
`r.jina.ai` reader, the pattern already proven in
`stages/stage_1/tools/fetch_fandom.py::_fetch_via_jina` — Cloudflare blocks direct reads
of several of these sites. That helper parses MediaWiki JSON, so it is the pattern to
follow, not the function to call: this needs a text-returning sibling.

Limits, all of them firm:

- At most 3 cited URLs per candidate.
- 10-second timeout per fetch.
- Each fetched page truncated to 6000 characters.
- Sequential within one candidate. Gating already runs candidates in parallel; a nested
  pool multiplies out.
- **Never raises.** A fetch that fails becomes a note in the evidence, not an exception.

### 3. Say which sources were actually read

`RAW EVIDENCE` becomes two labelled sections rather than one blob:

```
CITED SOURCES (fetched from the candidate's own citations)
  [1] https://www.cbr.com/... — fetched, 5,800 chars
      <text>
  [2] https://www.cbr.com/... — COULD NOT FETCH (timeout)

SEARCH RESULTS
  <the search payload as today>
```

This distinction is the point of the whole change. "Fetched and it does not support the
claim" and "we never managed to look" are different findings, and today they collapse
into the same `inconclusive`.

No new placeholder: both sections go inside the existing `raw_evidence` value, so
`_ALLOWED_PLACEHOLDERS` is untouched.

### 4. Gate prompt

Bump to `research_prompts/evidence_gate.v2.md` and repoint `_TEMPLATE_FILES["evidence_gate"]`
in `stages/research_scout/policies.py`. It must state:

- The cited sources are provided inline, already fetched.
- A cited URL whose text is present and supports the claim is support.
- A cited URL marked COULD NOT FETCH is **unverified, not contradicted** — it may not on
  its own produce `rejected`.
- `rejected` is for a claim the fetched evidence actively contradicts, or an issue/year
  the evidence shows to be wrong.

### 5. Reddit leaves the verification profile

Drop `reddit.com` from `specific_web_search` in the source policy. It stays in the
discovery profile, where knowing what fans argue about is the job. For confirming an issue
number and year it is not a primary source, and with `count=8` it was crowding out every
other domain. A Reddit URL a candidate cites itself still gets fetched by §2.

## Testing

Test-driven throughout. No network in tests — stub the fetcher and the search client.

- The query sent for a candidate contains its series/issue/year as words and no `{` or `"`.
- Cited URLs are fetched and their text reaches `raw_evidence`, labelled with the URL.
- A failing fetch is recorded as COULD NOT FETCH and does **not** raise.
- The 3-URL, 10-second, 6000-character limits all hold.
- A candidate with no cited URLs still gates, on search results alone.
- `specific_web_search` no longer includes reddit.com; the discovery profile still does.
- `evidence_gate.v2` renders with the same placeholder set as v1.

## Out of scope

The gate model choice; general research; Stage 2+.
