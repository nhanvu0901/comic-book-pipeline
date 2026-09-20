# Research evidence gate

Review the candidate below before it can enter the production pipeline.

USER INTENT: {user_intent}
ANGLE: {angle}
DIGEST:
{digest}

CANDIDATE:
{candidate}

RAW EVIDENCE:
{raw_evidence}

The raw evidence comes in two labelled sections.

CITED SOURCES holds the pages this candidate cited, already fetched for you and
quoted inline. You do not have to look them up, and you must not call a cited
URL unverifiable merely because it is missing from SEARCH RESULTS — those are a
separate, independent web search, not a list of what the citations say.

Weigh them like this:

- New candidates carry `claim_citation`, one URL and one verbatim quote bound
  to their exact claim. Treat that bound source as the primary evidence. The
  workflow has already checked the quote against retrieved text; still decide
  whether it supports the issue/year and visible event it is attached to.
- A cited source whose fetched text supports the claim IS support. Name it in
  evidence_urls.
- A cited source marked COULD NOT FETCH is UNVERIFIED, not contradicted. It is
  a gap in what we were able to read, and on its own it can never justify
  `rejected`.
- `rejected` is for a claim the evidence in front of you actively contradicts,
  or an issue number or year the evidence shows to be wrong.
- `inconclusive` is for evidence that neither supports nor contradicts. Say
  which it is: a source read and silent on the claim is a different finding
  from a source we never managed to open.
- `confirmed` needs the exact issue and year and the visible event both
  supported by the evidence shown here.

Report a concise verdict with reasons, the evidence URLs you actually relied
on, and any missing or conflicting claims.
