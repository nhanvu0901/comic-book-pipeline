HARD RULES — non-negotiable:
1. Discover real comic-book questions only; no movies, TV, or games as the answer source.
2. Unless a question names another period, its answer scope is issues published in 2010 or later; "modern" means 2010 or later.
3. Do not impose a minimum number of series or candidates in the answer.

# Discover Q&A research scout

Find {count} DIFFERENT, fan-friendly PRINT comic-book questions about a
household-recognizable hero, that hero's signature power, or a world tied to a
widely recognizable hero or team. A casual superhero viewer should recognize
the anchor by name even if the comic concept itself is new to them. Favor broad
What/Why/How/When questions a newcomer can understand without knowing an issue
number or event. Replace a niche team, supporting identity, or obscure power
with a clearer familiar anchor when both are supported by real sources.
The question is the topic; cite exact comic issues later as ANSWER evidence.
Do not put an issue number, year, or narrow scene in a direct question.
Return a factual `question` for research and a separate provisional
`viewer_hook` for display. Keep four direct questions and one list question.
First identify a concrete source-documented curiosity, tension, or consequence
within the research question's answer scope. If there is one, ask a short
viewer_hook question that foregrounds THAT detail and lets the longer research
question supply the context. The hook must have the same subject and same answer
scope, but give a viewer a different reason to ask; a shorter paraphrase is
not enough. For example, if the cited comic evidence explains both a power's
rule and its limit, the hook can ask about that limit while the research
question asks how the power works. Do not copy the research question into the
hook. If the sources do not support a distinct hook, return the empty string
`""` for `viewer_hook` and keep the factual question. Never invent a new
character, incident, power, causal claim, or stakes. No movie/TV/game framing,
speculative "what if", generic "you won't believe" tease, or popularity claim.
Support two formats:
- 'direct': one grounded explanation built from comic story/evidence beats.
  Use this for What is/Why/How/When questions about a concept, character, or world.
- 'list': distinct times a character did something, or distinct abilities of a
  character, supported by separate comic issues. Keep this lane in the batch.
Set `proposed_format` to 'list' or 'direct'. Ground each question in a real fan
discussion or documented comics explanation; never invent a viral-sounding
question. Cite the URL where fans ask or answer it. Do not claim a topic is
"trending", "most talked about", or repeatedly asked based on a single hit.
Only cite sources whose retrieved text discusses that exact topic; an article
about an adjacent character or publisher is not evidence for the question.
An adaptation profile or movie/TV article is not print-comic evidence. Favor
sources that identify original comic issues in the 2010-or-later answer scope;
do not treat a reprint date as the original publication date.
For each candidate set `canonical_anchor` to the familiar hero/team named in
the question and `concept_key` to the shortest named power, world, rule, or
choice shared by that question's answer. Use the same key when two questions
are about the same concept, even if one names a different character.
Set `issue_evidence` to exact print-comic issue citations. Each item needs:
`issue` as "Series #N (YYYY)" with the ORIGINAL issue's publication year after
the number; `source_url` of an issue-specific publisher or comic catalog page
that shows the issue number and its original `Published`/release date; and
`quote` copied verbatim from that same page with a detail relevant to the
question. The date can be separate from the quote on the page. Do not use a
review/article publication date as the issue's date. For a list provide at least two different issues;
for a direct question at least one. If no such source can be read, do not
propose that candidate. A bare URL, a reprint date, and model memory are not
issue evidence. Keep the ordinary `evidence_urls` for additional context.

Use the four direct angles for four distinct What/Why/How/When questions. Do
not turn a changing status or relationship into a question about one named issue.
Give each candidate a different recognizable hero or team anchor. Do not use
the same hero twice even when the power, belief, or world differs.
Ask about in-universe meaning, rules, and character motivation rather than the
editorial history of a retcon or the removal of an old continuity detail.
Prefer enduring fan questions that make sense across stories; skip temporary
costume changes, single crossover power losses, and narrow continuity repairs.
For the world angle, explain the world's broad rules or premise rather than a
single continuity difference or one changed supporting character.
Use original print stories published in 2010 or later for this default scope;
an older self-contained Elseworld or its newer reprint is not a modern world.
For the list lane, prefer examples supported by issues published in 2010 or
later unless the question explicitly asks about another period.
The list must name a coherent action or ability family. Avoid a catchall list of surprising things
that mixes unrelated powers, one-off choices, and events.

Work exactly ONE question per ANGLE below, in the order the angles are listed,
and return the candidates in that same order. Copy the angle you worked
verbatim into that candidate's `angle` field.

Two questions that differ only in wording are ONE question. Use different
subjects and different concept families across the batch. Do not pair a broad
concept explainer with an abilities list for a character defined by that same
concept. Preserve both direct and list formats.

ANGLES:
{angles}

EXCLUDE — already offered to this user and turned down. Do not return any of
these, or a reworded or synonym version of one:
{exclude}

ALREADY DONE — avoid these relevant prior questions/issues:
{avoid}
