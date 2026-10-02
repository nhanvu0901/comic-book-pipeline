"""Word-level text similarity: no model, no network, the same answer on every run.

"Are these two texts about the same thing?" does not need an embedding model. The model
has to load, and when it cannot every score silently becomes 0.0 and the check turns into
a no-op; which backend answers changes the numbers; and it is far more than two sentences
need. Comparing the WORDS needs none of that.

Two measures, for two jobs:
  seq_ratio      the texts as word SEQUENCES (difflib), so word order counts. A near-verbatim
                 repeat (the same sentence with a word or two changed) scores high; texts
                 that only share a topic score low. For near-duplicate detection.
  token_jaccard  the overlap of the two texts' distinct CONTENT words (stopwords dropped),
                 word order ignored. For "how much of what these two differently worded
                 texts say is the same", e.g. a narration beat against an image description.

Both return 0.0-1.0, and 0.0 when either text has nothing to compare, so two empty texts
never read as duplicates of each other. Standard library only.
"""
from __future__ import annotations

import difflib
import re

# Letters and digits of any script; "_" is a separator like punctuation.
_WORD_RE = re.compile(r"[^\W_]+")

# Function words: they say nothing about the topic, so sharing them is not overlap. `words`
# splits at the apostrophe, so a contraction leaves its stem behind ("didn't" -> "didn" +
# "t"); the stems are listed too. "won" is left out on purpose: it is the verb far more often
# than the stem of "won't". One-character leftovers ("t", "s", "d", "m") are dropped by
# length in `content_words`.
STOPWORDS = frozenset(
    # articles, quantifiers
    "a an the this that these those some any each both all such no not only own same "
    "other more most "
    # adverbs
    "very too so than then there here also just now "
    # pronouns
    "i me my mine myself you your yours yourself yourselves he him his himself she her "
    "hers herself it its itself we us our ours ourselves they them their theirs "
    "themselves who whom whose which what "
    # auxiliaries, modals
    "am is are was were be been being do does did doing have has had having will would "
    "shall should can cannot could may might must "
    # prepositions
    "of to in on at by for with from into onto about above across after against along "
    "among around as before behind below beside between beyond during inside near off "
    "out outside over through toward towards under until up upon within without down "
    # conjunctions, wh-words
    "and or but nor yet if because while when where why how though although unless "
    "whether since once "
    # contraction stems
    "don didn doesn isn wasn weren aren hasn haven hadn wouldn couldn shouldn mustn "
    "needn ain ll re ve".split()
)


def words(text: str) -> list[str]:
    """Lowercased word tokens of `text`, in order, punctuation stripped. Accented letters
    and digits are word characters ("Pokémon" -> "pokémon"); apostrophes, hyphens and
    underscores split ("don't" -> "don", "t"). None counts as empty."""
    return _WORD_RE.findall(str(text or "").lower())


def seq_ratio(a: str, b: str) -> float:
    """How alike two texts are as word sequences, 0.0-1.0; word order matters.
    0.0 when either side has no words: difflib scores two empty lists as identical (1.0),
    and two blank texts must not count as a duplicate. autojunk is off because it would
    treat words that repeat in a long text as noise. Near-duplicates score the same either
    way round; texts in very different word order may not (difflib is not symmetric), so
    keep the argument order fixed when ranking many pairs."""
    wa, wb = words(a), words(b)
    if not wa or not wb:
        return 0.0
    return difflib.SequenceMatcher(None, wa, wb, autojunk=False).ratio()


def content_words(text: str) -> frozenset[str]:
    """The distinct words of `text` that carry meaning: `words` minus STOPWORDS and
    one-character tokens."""
    return frozenset(w for w in words(text) if len(w) > 1 and w not in STOPWORDS)


def token_jaccard(a: str, b: str) -> float:
    """Shared content words over all distinct content words of both texts, 0.0-1.0; word
    order ignored. 0.0 when either side has no content words, so two texts made only of
    stopwords do not count as identical."""
    ca, cb = content_words(a), content_words(b)
    if not ca or not cb:
        return 0.0
    return len(ca & cb) / len(ca | cb)
