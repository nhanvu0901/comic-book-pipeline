# Discover micro-moment research scout

Find {count} DIFFERENT comic-book MICRO MOMENTS. Each one is a single drawn
beat inside a SINGLE issue — not a plot, not a crossover, not a character arc.
Each must be VISUALLY dramatic (something a reader SEES happen on the page),
star a widely-known character, and be published in the CURRENT YEAR or
LATE PREVIOUS YEAR (the newest ongoing runs and freshly released single issues
around the time of this research). Strongly prioritize the latest releases.

The strongest micro moment breaks a CONSTANT — the one thing everyone 'knows'
about that character — inside that single scene. Name the constant for each.

REJECT: issues published before the current or previous year (strictly avoid
older back-issues), talking-heads scenes, moments that need prior lore to
follow, whole storylines, solicitations or previews for unpublished issues,
and anything where you cannot give the exact series, issue number and year.

DESCRIPTION & DETAIL REQUIREMENTS:
- Provide rich, thorough descriptions for every candidate so the user has full context to choose.
- In `moment`: Write a vivid, multi-sentence overview (2-3 sentences) detailing the situation, the shocking turn, and the final scene.
- In `what_visibly_happens`: Provide a detailed, step-by-step visual breakdown of the page (at least 3-5 sentences describing what characters are drawn doing, their physical actions, expressions, panels, and the visual payoff). Never write a brief one-line synopsis.
- In `why_it_lands`: Elaborate (2-3 sentences) on the emotional or shocking weight of the scene and why readers call it unforgettable.

Work exactly ONE moment per ANGLE below, in the order the angles are listed,
and return the candidates in that same order. Copy the angle you worked
verbatim into that candidate's `angle` field.

Two moments from the same scene are ONE moment — a whole batch about the same
character or the same lane is a failed batch.

ANGLES:
{angles}

EXCLUDE — already offered to this user and turned down. Do not return any of
these, or a reworded or synonym version of one:
{exclude}

Use the supplied digest as prior context.

SCOUTED DIGEST:
{digest}
