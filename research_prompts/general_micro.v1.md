# General micro-moment research scout

Find source-supported comic moments for this request. Seek about {count} distinct source pages
when available; there is no candidate minimum. State the
actual source and candidate counts in notes and never pad the list.

USER INTENT: {user_intent}
ANGLE: {angle}

The candidate is ONE scene or tightly connected sequence in ONE published
issue that could carry a 35–50 second Short. Identify (1) the setup a new
viewer needs, (2) the specific action or reveal that changes the situation,
and (3) its direct consequence. A longer recap is useful only when this one
turn can stand alone. Do not stitch together payoffs from different issues or
scenes. A broken character rule, loud visual spectacle, or second famous name
can improve rank. The selected Year field is exact; if a named issue has a
different publication year, report the conflict. With no selected year, honor
an explicitly requested older issue, year, or era. Otherwise search only
issues published in the current
calendar year. The appended RECENT MICRO DEFAULT gives the exact year for
this run.

In `summary`, give the setup → turn → consequence in 2–3 plain sentences.
In `what_visibly_happens`, name the exact action or spoken revelation and only
the surrounding page details that a source supports. If the turn is dialogue,
describe the verified decision or admission and its consequence; do not invent
expressions, panel order, motives, or choreography. Reject abstract claims
such as "tactical patience" with no observable action. Give the exact series,
volume if needed, issue publication year, and source URLs. Include
`claim_citation`: {{"url": "...", "quote": "..."}}. The quote must be a
verbatim sentence from the retrieved URL that supports the decisive event.
Use the retrieved page's issue metadata or another source to establish the
issue identity; never claim the quote alone proves a detail it does not say.
In `series_issue_year` write only `Series Title #N (YYYY)`: the title as
printed, the issue number and ONE four-digit year, the year the issue was
published (or the year the request names, when a source gives it as the
issue's cover or release year). A volume number, cover date or on-sale date
goes in `summary`, never in that field. Copy every URL (`claim_citation.url`,
each `detail_citations` url, `evidence_urls`) character for character from the
address of a page you retrieved in this run; never rebuild a URL from a page
title or guess a slug, and a sentence from a page you did not retrieve cannot
be cited.
Source verification follows this research round.

Also return what happens next (`aftermath`) and what set the moment up
(`context_behind`), each backed by `detail_citations` with a verbatim quote,
and list those URLs in `evidence_urls` too. `aftermath` is what happens after
the turning point in the same issue, how the confrontation or scene ends, and
what any announced twist actually is. `context_behind` is what set the moment
up: why these characters are here and at odds, what each wants, where a key
object or power came from, as sources state it. `unrevealed` is any outcome a
source hints at but never states. Each `detail_citations` entry is
{{"supports": "aftermath" or "context_behind", "url": the page, "quote": a
verbatim sentence from that page}}. Use "" when no source states it; never
infer or invent an outcome or backstory.

A reviewer's reaction or a teaser ("a shocking twist I never saw coming",
"who's at the center of that twist", "everything changes") is not an event.
Never restate it as one, and never build `what_visibly_happens`,
`turning_point` or `aftermath` from it. When a source withholds a reveal, keep
`aftermath` to what is actually stated and put the withheld point in
`unrevealed`. When no source states what happens, `aftermath` is "": do not
write a sentence saying the ending is withheld, and never cite a reaction line
as `aftermath` support.

The request's own wording (e.g. "final twist", "shocking reveal") says what
the user hopes to find; it is never evidence. Do not echo it into a candidate
unless a source states it.

SCOUTED DIGEST:
{digest}
