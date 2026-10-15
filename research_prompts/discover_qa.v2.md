HARD RULES — non-negotiable:
1. Discover real comic-book questions only; no movies, TV, or games as the answer source.
2. Unless a question names another period, its answer scope is issues published in 2010 or later. That scope is a search filter, not question text: never write scope words ("modern", "print", "original issues", a year) into a question.
3. Do not impose a minimum number of series or candidates in the answer.
4. The question MUST name the hero or team itself (the canonical_anchor, or the name fans use for them); a question that only says "he", a first name, a villain, a supporting character or "this power" is rejected before it is read.
5. The (YYYY) in every issue label is that issue's REAL original release year, 2010 or later, as the cited page states it: never the series launch year, a reprint's year, an article date or a guess. If the page does not show it, drop the candidate.
6. Copy each quote EXACTLY as ONE contiguous passage of the synopsis section of the cited page: no edits, no added names or words, no ellipsis, no joining of separate sentences.

# Discover Q&A research scout

Find {count} DIFFERENT, fan-friendly PRINT comic-book questions about a
household-recognizable hero, that hero's signature power, or a world tied to a
widely recognizable hero or team. A casual superhero viewer should recognize
the anchor by name even if the comic concept itself is new to them. Favor broad
What/Why/How/When questions a newcomer can understand without knowing an issue
number or event. Replace a niche team, supporting identity, or obscure power
with a clearer familiar anchor when both are supported by real sources.

Premise-first and source-first: read the retrieved issue-specific page BEFORE
wording each question. Ground each candidate in a concrete, source-supported
comic premise (an actual mechanism, counterintuitive condition, pivotal
conflict, or defining rule). Look for a COMPLETE ANSWER in an issue-specific
detailed comic synopsis or summary that includes spoilers (such as Fandom
Marvel/DC databases, Comic Vine, League of Comic Geeks, or detailed issue
breakdowns) alongside original issue release metadata. Prioritize sources that
actually state the complete outcome, mechanism, or resolution over promotional
blurbs that withhold the answer. Official publisher solicitation blurbs or
catalog teasers that merely tease an upcoming conflict without stating the
outcome are NOT evidence of an answer or payoff.

Identify why fans care or debate this: what makes it intriguing, surprising, or
counterintuitive? Identify the specific answer and viewer payoff: what concrete
revelation, explanation, or narrative resolution does the viewer actually learn
from the cited evidence? The viewer payoff must specifically answer the WHY or
HOW: name the exact biological consequence, specific mechanism used, actual
sacrifice made, or concrete resolution. Reject tautological or pseudo-payoffs
like "learns that the hero must prove themselves" or "stakes are tested". If
the retrieved source withholds the resolution or does not state the concrete
answer, DROP the candidate idea entirely — do NOT shrink it into a sterile, dry
question just to slip past the gate.

Give each direct question ONE answer target: a focused, coherent explanation
centered on one mechanism, contrast, conflict, rule, or explicitly stated
motive. A coherent answer target does not force an artificial single-clause
wording: compound phrasing that connects a concept to its specific tension,
counterintuitive condition, or tested limit is encouraged when the cited
evidence answers both. Keep the question evergreen and recognizable, with its full
answer inside the cited evidence. However, keep the scope focused: do not combine
unrelated storylines, do not ask for a whole multi-issue scene recap, and do
not append family/mission/stakes/ongoing consequences from a short blurb
or guess a motive the source does not state. If the issue source cannot
support the whole question, choose another source or drop the candidate.
Do not put an issue number, year, or narrow scene in a direct question.

In `why_fans_ask_it`, clearly state the core premise and why fans debate or care
about it (the curiosity trigger, counterintuitive contrast, or moral tension).
In `sample_answer_items` and `issue_evidence`, state the concrete viewer payoff:
the specific factual mechanism, revelation, or resolution that answers the
question, backed directly by the cited issue text. Never write mere promotional
hype, generic teasers, or unsubstantiated claims. Community discussion URLs
show fan demand and interest, but they are not proof of comic canon: ground the
factual answer and issue evidence in issue-specific synopsis pages with original
issue metadata.

Return a factual `question` for research and a separate provisional
`viewer_hook` for display. viewer_hook is a short curiosity question about the SAME answer, or the empty string. The viewer_hook must name the same hero or team as the question, in words (never a pronoun such as "this power" or "him"), reuse at least one key noun of the question, and be the same kind of question: a Why/How/What-is question gets a Why/How/What/Can question, a list question (What are some ...) gets a list-shaped one. It ends with ? and never copies or merely shortens the question. If no distinct hook fits, return the empty string. The angle set provides four direct angles and one
list angle as diverse inspiration lanes to guide the {count} candidates across
different narrative lanes (power contrasts, turning points, world tensions,
tested principles, and coherent lists). Select candidates based on premise
quality and verifiable evidence rather than forcing a rigid quota; each
candidate's `proposed_format` follows the idea's natural scope: 'direct' for
focused explanation or 'list' for distinct examples. Return an honest shortage
if fewer than {count} candidates have complete, source-supported answers.
If the cited excerpt gives a distinct concrete detail within that SAME answer,
ask a short viewer_hook question that foregrounds it. The hook must have the
same subject and same answer scope as the research question; it cannot add a
second answer target or new stakes. A shorter paraphrase is not enough. Do not
copy the research question into the hook. If the sources do not support a
distinct hook within the same answer, return the empty string
`""` for `viewer_hook` and keep the factual question. Never invent a new
character, incident, power, causal claim, or stakes. No movie/TV/game framing,
speculative "what if" fan fiction (invented hypothetical scenarios that never
occurred in print), generic "you won't believe" tease, or popularity claim. A
canon "what happens when..." grounded in an actual published comic scene or event
is valid, but imaginary scenarios with no comic basis are forbidden.

Support two formats:
- 'direct': one grounded explanation built from comic story/evidence beats.
  Use this for What is/Why/How/When questions about a concept, character, or world.
- 'list': distinct times a character did one named kind of action, or distinct
  abilities within one named family. Each item needs its own issue source
  showing that same action or ability family with concrete outcomes.
Set `proposed_format` to 'list' or 'direct'. Ground each question in a real fan
discussion or documented comics explanation; never invent a viral-sounding
question. Cite the URL where fans ask or answer it. Do not claim a topic is
"trending", "most talked about", or repeatedly asked based on a single hit.
Only cite sources whose retrieved text discusses that exact topic; an article
about an adjacent character or publisher is not evidence for the question.
An adaptation profile or movie/TV article is not print-comic evidence. Favor
sources that identify original comic issues in the 2010-or-later answer scope;
do not treat a reprint date as the original publication date.
For each candidate set `canonical_anchor` to ONLY the name of the familiar
hero/team (1-3 words, never a sentence) named in both the question and its issue evidence. Set `concept_key` to the shortest
named power, world, rule, or choice shared by that question and its cited
text; never invent a key just to make two candidates appear different.
Use the same key when two questions are about the same concept, even if one
names a different character.
Set `core_premise` to the underlying comic contrast, conflict, or premise supported by the excerpt.
Set `viewer_payoff` to the concrete comic revelation, choice, or consequence in that scene delivering on the premise.
Set `issue_evidence` to exact print-comic issue citations. Each item needs:
`issue` as "Series #N (YYYY)" with the ORIGINAL issue's publication year after
the number; `source_url` of an issue-specific comic page (synopsis or database)
that shows the issue number and its original `Published`/release date; and
`quote` copied verbatim from that same page as ONE contiguous passage (no
ellipsis, no joining of separate sentences) containing the concrete factual
detail or resolution relevant to the question. The date can be separate from the
quote on the page. Do not use a review/article publication date as the issue's date.
For a list provide at least two different issues; for a direct question at least one.
If no such source can be read, do not propose that candidate. A bare URL, a reprint
date, and model memory are not issue evidence. Keep the ordinary `evidence_urls`
for additional context.

Use the angles as inspiration for distinct What/Why/How/When questions and list
lanes. Copy the inspiring angle into that candidate's `angle` field. Do
not turn a changing status or relationship into a question about one named issue.
Give each candidate a different recognizable hero or team anchor. Do not use
the same hero twice even when the power, belief, or world differs.
Ask about one in-universe meaning, rule, or explicitly stated motivation rather than the
editorial history of a retcon or the removal of an old continuity detail.
Prefer enduring fan questions that make sense across stories; skip temporary
costume changes, single crossover power losses, and narrow continuity repairs.
For the world angle, ask about one defining premise or rule supported by an
issue source rather than a single continuity difference or one changed
supporting character.
Use original print stories published in 2010 or later for this default scope;
an older self-contained Elseworld or its newer reprint is outside this scope.
For the list lane, prefer examples supported by issues published in 2010 or
later unless the question explicitly asks about another period.
The list must name a coherent action or ability family, and every example's
issue source must demonstrate that family. Avoid a catchall list of unrelated
powers, one-off choices, and events.

Two questions that differ only in wording are ONE question. Use different
subjects and different concept families across the batch. Do not pair a broad
concept explainer with an abilities list for a character defined by that same
concept.

ANGLES:
{angles}

EXCLUDE — already offered to this user and turned down. Do not return any of
these, or a reworded or synonym version of one:
{exclude}

ALREADY DONE — avoid these relevant prior questions/issues:
{avoid}
