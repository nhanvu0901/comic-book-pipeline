# General Q&A research scout

USER INTENT: {user_intent}
ANGLE: {angle}

Format guidance:
- For list questions ("who has...", "times X happened", independent cases): treat the intent as an
  EXHAUSTIVE ENUMERATION task across different comics. One candidate per distinct character/thing/moment.
- For direct questions (Why/How, What-is/concept): develop ONE grounded answer thesis in `answer_thesis`
  and return candidates as the sequential story/evidence beats that establish or demonstrate the answer.
  Do NOT force separate comics or independent cases for direct questions.

Rules:
- Sweep EVERY retrieved source. Seek coverage from about {count} distinct source pages
  supports. Full, partial, assisted, and temporary cases all count — state the
  difference in the summary. Seek coverage from about {count} distinct source pages
  when the research supports it, but there is no candidate minimum: return only
  genuinely supported entries and never pad the list. State the actual
  distinct-source and candidate counts in notes.
- Each candidate needs: the exact series + issue number + year when a source
  names them (do not invent missing issue details or guess issue numbers from
  listicles or multi-issue storylines; prefer issue synopses from comic fandom
  wikis or comics.org), what VISIBLY happens on the page, a concise summary,
  the source URLs you actually used, and `claim_citation`: {{"url": "...", "quote": "..."}}.
  The quote must be a verbatim sentence from that retrieved URL that directly
  supports this candidate's exact issue/year and visible feat.
- Real published canonical comics only. Exclude film, MCU, and TV adaptations.
  Widely-debated hypothetical picks ("could resist", "would win") go LAST and
  must say "hypothetical" in the summary.

Use the supplied digest as prior context.

SCOUTED DIGEST:
{digest}
