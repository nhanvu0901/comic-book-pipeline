HARD RULES — non-negotiable:
1. Discover real comic-book questions only; no movies, TV, or games as the answer source.
2. Unless a question names another period, its answer scope is issues published in 2010 or later; "modern" means 2010 or later.
3. Do not impose a minimum number of series or candidates in the answer.

# Discover Q&A research scout

Find {count} DIFFERENT, fan-friendly PRINT comic-book questions about well-known
characters, powers, concepts, teams, or worlds. Favor broad What/Why/How/When
questions a newcomer can understand without knowing an issue number or event.
The question is the topic; cite exact comic issues later as ANSWER evidence.
Do not put an issue number, year, or narrow scene in a direct question.
Support two formats:
- 'direct': one grounded explanation built from comic story/evidence beats.
  Use this for What is/Why/How/When questions about a concept, character, or world.
- 'list': distinct times a character did something, or distinct abilities of a
  character, supported by separate comic issues. Keep this lane in the batch.
Set `proposed_format` to 'list' or 'direct'. Ground each question in a real fan
discussion or documented comics explanation; never invent a viral-sounding
question. Cite the URL where fans ask or answer it. Do not claim a topic is
"trending", "most talked about", or repeatedly asked based on a single hit.

For the Reddit discussion angle, seek a PRINT-comic lore question in a Reddit
post from the last 90 days. Exclude MCU, film, TV, games, release news, and
solicitation posts. It must be a direct What/Why/How/When question about ONE
recognizable concept, character, or world, not a broad trivia request covering
many unrelated subjects. Put the exact Reddit post URL in `reddit_discussion_url` and
`evidence_urls`; put the Research-reported ISO date (YYYY-MM-DD) in
`reddit_post_date`. If either is unavailable, do not return that Reddit-angle
candidate. Research-reported dates are unverified until the post is fetched.
Set both Reddit fields to empty strings for all other angles. Research snippets
do not reliably provide upvotes/comments, so do not score popularity here.
For the list lane, prefer examples supported by issues published in 2010 or
later unless the question explicitly asks about another period.

Work exactly ONE question per ANGLE below, in the order the angles are listed,
and return the candidates in that same order. Copy the angle you worked
verbatim into that candidate's `angle` field.

Two questions that differ only in wording are ONE question. Use different
subjects across the batch, and preserve both direct and list formats.

ANGLES:
{angles}

EXCLUDE — already offered to this user and turned down. Do not return any of
these, or a reworded or synonym version of one:
{exclude}

ALREADY DONE — avoid these relevant prior questions/issues:
{avoid}
