"""utils.lexical_sim — word-level similarity with no model and no network.

seq_ratio compares two texts as word SEQUENCES, so word order counts and a near-verbatim
repeat scores high. token_jaccard compares their distinct CONTENT words (stopwords dropped)
and ignores order. Both are 0.0 when a side has nothing to compare, so a blank text never
looks like a duplicate of another blank text.
"""
from utils.lexical_sim import STOPWORDS, content_words, seq_ratio, token_jaccard, words

SENTENCE = "The old detective walked slowly into the abandoned warehouse at midnight alone"
REVERSED = " ".join(reversed(SENTENCE.split()))


# --- words -------------------------------------------------------------------------------

def test_words_are_lowercase_in_order_without_punctuation():
    assert words("Hello, World! It's Batman...") == ["hello", "world", "it", "s", "batman"]


def test_words_split_on_underscore():
    assert words("a_b") == ["a", "b"]


def test_words_keep_accented_letters_and_digits():
    assert words("Pokémon #12") == ["pokémon", "12"]


def test_words_of_none_or_blank_text_is_empty():
    assert words(None) == []
    assert words("") == []
    assert words("  ...  ?! ") == []


def test_every_stopword_is_a_bare_lowercase_token():
    # words() lowercases and splits, so any other entry could never match a token.
    assert all(words(w) == [w] for w in STOPWORDS)


# --- seq_ratio ---------------------------------------------------------------------------

def test_seq_ratio_of_identical_text_is_one():
    assert seq_ratio(SENTENCE, SENTENCE) == 1.0


def test_seq_ratio_ignores_case_and_punctuation():
    assert seq_ratio("Hello, World!", "hello world") == 1.0


def test_seq_ratio_one_swapped_word_is_still_a_near_duplicate():
    swapped = SENTENCE.replace("midnight", "dawn")
    assert len(words(SENTENCE)) == 12
    assert swapped != SENTENCE
    assert seq_ratio(SENTENCE, swapped) >= 0.85


def test_seq_ratio_of_unrelated_sentences_is_low():
    other = "Dragons breathe fire into the burning kingdom while villagers flee"
    # They share the bigram "into the"; that alone must not look like a repeat.
    assert seq_ratio(SENTENCE, other) < 0.3


def test_seq_ratio_word_order_matters():
    assert seq_ratio(SENTENCE, SENTENCE) == 1.0
    assert seq_ratio(SENTENCE, REVERSED) < 0.5     # same words, scrambled order


def test_seq_ratio_is_zero_when_either_side_has_no_words():
    # difflib scores two empty lists as identical (1.0); blank texts must not be duplicates.
    assert seq_ratio("", "") == 0.0
    assert seq_ratio("", SENTENCE) == 0.0
    assert seq_ratio(SENTENCE, "") == 0.0
    assert seq_ratio("...", SENTENCE) == 0.0
    assert seq_ratio(SENTENCE, "?! --") == 0.0
    assert seq_ratio("...", "?!") == 0.0


def test_seq_ratio_treats_none_as_empty():
    assert seq_ratio(None, SENTENCE) == 0.0
    assert seq_ratio(SENTENCE, None) == 0.0
    assert seq_ratio(None, None) == 0.0


def test_seq_ratio_is_not_thrown_off_by_words_that_repeat_in_a_long_text():
    # difflib's default autojunk drops a word that repeats often in a 200+ word text from the
    # matching, so a long scene built from a few repeated words scored 0.0 against a copy
    # that differed only in its first word.
    body = " ".join(["the cat sat on the mat"] * 50)           # 300 words, 5 distinct
    assert seq_ratio("one " + body, "two " + body) > 0.99


# --- content_words -----------------------------------------------------------------------

def test_content_words_are_distinct_and_drop_stopwords_and_single_characters():
    found = content_words("The cat sat on a mat. The CAT sat! I x")
    assert isinstance(found, frozenset)
    assert found == frozenset({"cat", "sat", "mat"})


def test_content_words_drop_contraction_stems():
    # "didn't" splits into "didn" + "t", "we're" into "we" + "re": none of those is content.
    # "won" stays: it is the verb far more often than the stem of "won't".
    found = content_words("They didn't know we're here, you'll see, I've won")
    assert found == frozenset({"know", "see", "won"})


def test_content_words_keep_accented_tokens():
    assert "pokémon" in content_words("Pokémon battle")


def test_content_words_of_none_is_empty():
    assert content_words(None) == frozenset()


# --- token_jaccard -----------------------------------------------------------------------

def test_token_jaccard_ignores_stopwords_and_case():
    assert token_jaccard("the cat sat", "a cat sat") == 1.0
    assert token_jaccard("The Cat SAT!", "a cat, sat") == 1.0


def test_token_jaccard_of_disjoint_texts_is_zero():
    assert token_jaccard("red dragon attacks", "quiet library closes") == 0.0


def test_token_jaccard_partial_overlap_is_shared_over_distinct_content_words():
    # {red, dragon, attacks, village} vs {red, dragon, flees}: 2 shared of 5 distinct.
    assert token_jaccard("red dragon attacks village", "red dragon flees") == 2 / 5
    assert token_jaccard("red dragon flees", "red dragon attacks village") == 2 / 5


def test_token_jaccard_ignores_word_order():
    assert token_jaccard(SENTENCE, REVERSED) == 1.0


def test_token_jaccard_is_zero_when_a_side_has_no_content_words():
    assert token_jaccard("the and of to", "cat sat") == 0.0       # only stopwords
    assert token_jaccard("cat sat", "the and of to") == 0.0
    # Two texts with nothing to compare are not "identical".
    assert token_jaccard("the and of to", "of the and") == 0.0
    assert token_jaccard("a I x", "a I x") == 0.0                  # only 1-char tokens
    assert token_jaccard("", "") == 0.0
    assert token_jaccard("cat sat", "") == 0.0


def test_token_jaccard_treats_none_as_empty():
    assert token_jaccard(None, "cat sat") == 0.0
    assert token_jaccard("cat sat", None) == 0.0
    assert token_jaccard(None, None) == 0.0
