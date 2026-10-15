"""Round 3 of the QA discovery gate: the 13 FALSE REJECTS found by the round-2 live runs.

Round 2 removed every false accept but the gate could not survive ordinary model output:

1  canonical_anchor written as a SENTENCE ("Superman under red-sun suppression"), snake_case
   concept_key, and a top-up prompt that never defines either
2  a quote joined with "..." (each part verbatim in the same synopsis, the whole is not)
3  issue labels like "Immortal Hulk Vol 1 8 (2018)" (no "#")
4  lexical grounding that required SUBJECTIVE and FRAME words ("terrifying threat",
   "stop ... exposing") and could not see that "Punisher" is "Frank Castle"
5  smaller items: alias spellings, a wiki "stub" notice counted as a synopsis, a hook the
   top-up prompt never asked to be a question, and anchor_mismatch hiding the real reason

Pages here are small synthetic wiki pages in the reader's markdown form; the real ones are
replayed in tests/test_qa_gate_r3_replay.py.
"""

from __future__ import annotations

import pytest

from stages.research_scout.cited_sources import FetchedSource
from stages.research_scout.qa_discovery_gate import (
    GateResult,
    evaluate_issue_evidence,
    normalize_candidate,
)

DC_URL = "https://dc.fandom.com/wiki/Superman_Vol_3_38"
MV_URL = "https://marvel.fandom.com/wiki/Thor:_God_of_Thunder_Vol_1_11"

BURST = ("Superman unleashes the energy stored in every cell of his body in one burst. "
         "After that burst, the heat burns away his uniform and he must hide from the crowd, "
         "powerless until he recharges.")
FIRST, SECOND = (
    "Superman unleashes the energy stored in every cell of his body in one burst",
    "the heat burns away his uniform and he must hide from the crowd")


def _wiki(title, sections, *, url=DC_URL, intro="It was published on February 4, 2015."):
    head = (f"Title: {title}\n\nURL Source: {url}\n\nMarkdown Content:\n")
    body = f"****_{title}_**** is an issue of the series. {intro}\n\n"
    body += "".join(f"## {heading}\n\n{text}\n\n" for heading, text in sections)
    return head + body


def _syn(text=BURST, title="Powerless"):
    return (f'Synopsis for "{title}"', text)


def _candidate(*, question="How does Superman unleash the energy stored in his cells?",
               anchor="Superman", concept="energy burst", quote=FIRST,
               issue="Superman (2011) #38 (2015)", url=DC_URL, **extra):
    return {"question": question, "canonical_anchor": anchor, "concept_key": concept,
            "issue_evidence": [{"issue": issue, "source_url": url, "quote": quote}], **extra}


def _verdict(candidate, page, fmt="direct"):
    result = evaluate_issue_evidence(
        candidate, fmt, fetcher=lambda url: FetchedSource(url, text=page))
    return result.passed, result.reason


# --- 1. normalise the model's output before the gate -----------------------------------

@pytest.mark.parametrize("raw,clean", [
    ("Hulk regeneration and reassembly", "Hulk"),
    ("Batman spares the Joker to save Harley Quinn", "Batman"),
    ("Superman under red-sun suppression", "Superman"),
    ("Daredevil refuses lethal logic", "Daredevil"),
    ("Wonder Woman uses the Lasso of Truth", "Wonder Woman"),
    ("Hulk's strength is effectively beyond measurement", "Hulk"),
    ("Captain Marvel's Binary power is the decisive counter to symbiote possession",
     "Captain Marvel"),
    ("Wonder Woman's truth-based rule about Urzkartaga and the Lasso", "Wonder Woman"),
    ("Batman tricks Clayface by using DNA against DNA", "Batman"),
    ("The Lasso of Truth as a truth-exposure and restraint ability family", "Lasso of Truth"),
    ("Spider-Man swings through the city", "Spider-Man"),
])
def test_r3_1_a_sentence_anchor_is_reduced_to_its_leading_proper_name(raw, clean):
    assert normalize_candidate({"canonical_anchor": raw})["canonical_anchor"] == clean


@pytest.mark.parametrize("name", [
    "Spider-Man", "Black Panther", "Batman and Robin", "The Flash", "daredevil",
    "Captain America", "Iron Man and the Avengers", "X-Men", "Doctor Strange", "Ms. Marvel",
    "Speed Force", "hulk",
])
def test_r3_1_a_real_name_is_left_alone(name):
    assert normalize_candidate({"canonical_anchor": name})["canonical_anchor"] == name


def test_r3_1_a_snake_case_concept_key_becomes_words_and_the_input_is_not_mutated():
    raw = {"concept_key": "hulk_regeneration_reassembly_limit", "canonical_anchor": "Hulk",
           "question": "q"}
    clean = normalize_candidate(raw)
    assert clean["concept_key"] == "hulk regeneration reassembly limit"
    assert raw["concept_key"] == "hulk_regeneration_reassembly_limit"
    assert normalize_candidate({"concept_key": "Speed Force"})["concept_key"] == "Speed Force"


def test_r3_1_the_gate_judges_the_normalised_candidate():
    candidate = _candidate(
        question="What happens to Superman under red-sun fire?",
        anchor="Superman under red-sun suppression",
        concept="superman_red_sun_power_suppression_rule",
        quote="Superman is bathed in red sun fire and his powers fail completely")
    page = _wiki("Superman Vol 3 38", [_syn(
        "Superman is bathed in red sun fire and his powers fail completely, so he can "
        "do nothing to save his friend as the minutes pass.")])
    assert _verdict(candidate, page) == (True, "")


def _client(*batches):
    class _Client:
        def __init__(self):
            self.batches = list(batches)

        def research(self, prompt, schema, profile, *, effort="standard"):
            batch = self.batches.pop(0) if self.batches else []
            return type("R", (), {"payload": {"output": {"content": {"candidates": batch}}},
                                  "error": None})()
    return _Client()


def _entry(question, anchor, concept):
    return {"question": question, "subject": anchor, "canonical_anchor": anchor,
            "concept_key": concept, "viewer_hook": "", "evidence_urls": [],
            "issue_evidence": []}


def _workflow(tmp_path, client):
    from stages.research_scout.storage import SessionStore
    from stages.research_scout.workflow import ScoutWorkflow
    return ScoutWorkflow(store=SessionStore(tmp_path), client=client,
                         qa_issue_verifier=lambda c, f: True)


def test_r3_1_the_workflow_returns_the_normalised_anchor_and_concept(tmp_path, monkeypatch):
    from stages.research_scout.models import ScoutMode
    monkeypatch.setattr("stages.youcom_scout.is_burned", lambda question, digest: False)
    entry = _entry("How does the Hulk's healing work when his body is cut apart?",
                   "Hulk regeneration and reassembly", "hulk_regeneration_reassembly_limit")
    batch = _workflow(tmp_path, _client([entry])).discover_questions(ScoutMode.QA, count=1)
    assert batch[0]["canonical_anchor"] == "Hulk"
    assert batch[0]["concept_key"] == "hulk regeneration reassembly limit"


def test_r3_1_two_sentence_anchors_for_one_hero_count_as_one_anchor(tmp_path, monkeypatch):
    from stages.research_scout.models import ScoutMode
    monkeypatch.setattr("stages.youcom_scout.is_burned", lambda question, digest: False)
    batch = _workflow(tmp_path, _client([
        _entry("How does the Hulk's healing work?", "Hulk regeneration", "hulk_healing"),
        _entry("What happens when someone measures the Hulk's strength?",
               "Hulk's strength is beyond measurement", "hulk_strength"),
    ], [])).discover_questions(ScoutMode.QA, count=5)
    assert [e["canonical_anchor"] for e in batch] == ["Hulk"]


def test_r3_1_the_topup_prompt_defines_anchor_concept_hook_and_quote():
    from stages.research_scout.workflow import _qa_discovery_topup_prompt
    prompt = " ".join(_qa_discovery_topup_prompt(
        missing_direct=1, missing_list=1, angles=["a"], excluded=[], anchors=set(),
        concepts=set()).split()).casefold()
    assert "canonical_anchor is only the" in prompt and "hero or team name" in prompt
    assert "never a sentence" in prompt
    assert "concept_key is a short" in prompt and "no underscores" in prompt
    assert "viewer_hook is a short curiosity question" in prompt and "ends with ?" in prompt
    assert "one contiguous passage" in prompt and "no ellipsis" in prompt


# --- 2. a quote joined with an ellipsis ----------------------------------------------------------

@pytest.mark.parametrize("joiner", [" ... ", " … ", "... ", " [...] ", " (...) "])
def test_r3_2_fragments_joined_by_an_ellipsis_are_checked_one_by_one(joiner):
    quote = f"{FIRST.rstrip('.')}.{joiner}{SECOND}."
    assert _verdict(_candidate(quote=quote), _wiki("Superman Vol 3 38", [_syn()])) == (True, "")


def test_r3_2_leading_and_trailing_ellipses_are_ignored():
    quote = f"... {FIRST} ..."
    assert _verdict(_candidate(quote=quote), _wiki("Superman Vol 3 38", [_syn()])) == (True, "")


def test_r3_2_fragments_must_keep_the_pages_order():
    quote = f"{SECOND}. ... {FIRST}."
    assert _verdict(_candidate(quote=quote), _wiki("Superman Vol 3 38", [_syn()])) == (
        False, "quote_not_found")


def test_r3_2_every_fragment_must_be_on_the_page():
    quote = f"{FIRST}. ... Then he flies to Gotham and rescues the whole city from the flood."
    assert _verdict(_candidate(quote=quote), _wiki("Superman Vol 3 38", [_syn()])) == (
        False, "quote_not_found")


def test_r3_2_fragments_from_two_different_sections_are_a_stitched_quote():
    page = _wiki("Superman Vol 3 38", [
        _syn(f"{FIRST}. Nothing else is told here, only a long fall into the dark.", "One"),
        _syn(f"Much later, {SECOND}.", "Two"),
    ])
    quote = f"{FIRST}. ... {SECOND}."
    assert _verdict(_candidate(quote=quote), page) == (False, "quote_not_found")


def test_r3_2_fragments_of_a_reprinted_story_are_still_a_reprint():
    page = _wiki("Superman Vol 3 38", [
        ('Synopsis for "Old Story"',
         f"Reprint of the 1st story from Some Title #5\n\n{BURST}")])
    quote = f"{FIRST}. ... {SECOND}."
    assert _verdict(_candidate(quote=quote), page) == (False, "reprint_source")


def test_r3_2_fragments_found_only_in_a_solicit_blurb_are_a_teaser():
    page = _wiki("Superman Vol 3 38", [
        _syn("Superman fights somebody else entirely, for pages and pages of the story."),
        ("Solicit Synopsis", BURST)])
    quote = f"{FIRST}. ... {SECOND}."
    assert _verdict(_candidate(quote=quote), page) == (False, "teaser_only_source")


def test_r3_2_fragments_too_short_to_prove_anything_do_not_count():
    quote = "Superman unleashes ... burst"
    assert _verdict(_candidate(quote=quote), _wiki("Superman Vol 3 38", [_syn()])) == (
        False, "quote_not_found")


def test_r3_2_an_ellipsis_that_the_page_itself_contains_still_matches_whole():
    text = "If someone were to shoot him in the head... all he would do is die. Until night falls."
    page = _wiki("Superman Vol 3 38", [_syn(text)])
    candidate = _candidate(
        question="What happens when someone shoots Superman in the head?",
        quote="If someone were to shoot him in the head... all he would do is die.")
    assert _verdict(candidate, page) == (True, "")


def test_r3_2_a_stitched_quote_without_an_ellipsis_is_still_rejected():
    quote = f"{FIRST}. {SECOND}."
    assert _verdict(_candidate(quote=quote), _wiki("Superman Vol 3 38", [_syn(
        f"{FIRST}. Many pages later, nothing else matters. {SECOND}.")])) == (
        False, "quote_not_found")


def test_r3_2_wiki_footnote_markers_inside_a_sentence_do_not_break_a_verbatim_quote():
    text = ("Superman unleashes the energy stored in every cell of his body in one burst."
            "[[Continuity 2]](https://dc.fandom.com/wiki/Superman_Vol_3_38#cite_note-2) After "
            "that burst, the heat burns away his uniform.[7] He must hide from the crowd.")
    quote = ("Superman unleashes the energy stored in every cell of his body in one burst. "
             "After that burst, the heat burns away his uniform. He must hide from the crowd.")
    assert _verdict(_candidate(quote=quote), _wiki("Superman Vol 3 38", [_syn(text)])) == (
        True, "")


# --- 3. 'Series Vol N M (YYYY)' labels --------------------------------------------------------------

def _hulk(issue, title="Immortal Hulk Vol 1 8", **kw):
    page = _wiki(title, [_syn(BURST)], url="https://marvel.fandom.com/wiki/Immortal_Hulk_Vol_1_8",
                 intro="\nPublished: November 7, 2018")
    candidate = _candidate(issue=issue, url="https://marvel.fandom.com/wiki/Immortal_Hulk_Vol_1_8",
                           anchor="Hulk", **kw)
    candidate["question"] = "How does Hulk unleash the energy stored in his cells?"
    return _verdict(candidate, page)


@pytest.mark.parametrize("label", [
    "Immortal Hulk Vol 1 8 (2018)",
    "Immortal Hulk Vol. 1 #8 (2018)",
    "Immortal Hulk (2018) Vol 1 8",
    "Immortal Hulk Volume 1 8 (2018)",
])
def test_r3_3_labels_with_vol_n_m_and_a_trailing_year_are_read(label):
    assert _hulk(label) == (True, "")


def test_r3_3_the_page_still_confirms_the_number_and_year_of_such_a_label():
    assert _hulk("Immortal Hulk Vol 1 9 (2018)") == (False, "issue_number_not_found")
    assert _hulk("Immortal Hulk Vol 1 8 (2019)") == (False, "year_not_found")
    assert _hulk("Immortal Hulk Vol 1 8") == (False, "issue_year_unresolved")
    assert _hulk("Immortal Hulk Vol 1 8 (2005)") == (False, "year_out_of_range")


# --- 4. lexical grounding ---------------------------------------------------------------------------------

GODBOMB = ("The Godbomb will kill any and all gods that have ever existed, consuming them from "
           "the inside out.")


def _godbomb(question="What makes the Godbomb such a terrifying threat in Thor's world?",
             quote=GODBOMB, body=None):
    page = _wiki("Thor: God of Thunder Vol 1 11", [_syn(
        body or f"Gorr unleashes the Godbomb across the cosmos. {GODBOMB} Thor is powerless "
                "to stop it as the gods of every age begin to fall.")],
        url=MV_URL, intro="\nPublished: August 14, 2013")
    candidate = _candidate(
        question=question, anchor="Thor", concept="world_rule_godbomb", quote=quote,
        issue="Thor: God of Thunder #11 (2013)", url=MV_URL)
    return _verdict(candidate, page)


def test_r3_4_subjective_and_frame_words_are_not_required():
    assert _godbomb() == (True, "")
    assert _godbomb("Why is the Godbomb so scary and deadly in Thor's world?") == (True, "")


def test_r3_4_but_the_topic_noun_itself_still_has_to_be_in_the_source():
    other = "Gorr plans something terrible for every god in the universe, and nobody can stop him."
    assert _godbomb(quote=other, body=other + " Thor listens in horror.") == (
        False, "topic_overlap")


def test_r3_4_light_verbs_of_a_question_frame_are_not_required():
    page = _wiki("Batman Vol 2 20", [_syn(
        "Clayface has cornered Bruce Wayne in the lab. Bruce reveals that he put a film "
        "containing his DNA over his skin to trick Clayface, and the plan works.")],
        url="https://dc.fandom.com/wiki/Batman_Vol_2_20",
        intro="It was published on July 8, 2013.")
    candidate = _candidate(
        question="How does Batman stop Clayface from exposing him?", anchor="Batman",
        concept="batman_clayface_dna_trick", issue="Batman #20 (2013)",
        url="https://dc.fandom.com/wiki/Batman_Vol_2_20",
        quote="Bruce reveals that he put a film containing his DNA over his skin to trick "
              "Clayface")
    assert _verdict(candidate, page) == (True, "")


def test_r3_4_reflexive_and_indefinite_words_are_not_required():
    page = _wiki("Superman Vol 3 38", [_syn(
        "A spatial anomaly separates him into two beings: a completely Kryptonian Superman "
        "and a completely human Clark Kent, who begins to suffocate.")])
    candidate = _candidate(
        question="What happens when Superman is split into his human and Kryptonian selves?",
        concept="human-Kryptonian split",
        quote="A spatial anomaly separates him into two beings: a completely Kryptonian "
              "Superman and a completely human Clark Kent")
    assert _verdict(candidate, page) == (True, "")


def test_r3_4_a_name_the_question_misses_in_the_source_still_fails():
    # "Dark Gods": only "gods" is on the page, so the named thing is not.
    page = _wiki("Wonder Woman Vol 5 36", [_syn(
        "The gods of Olympus gather while Diana fights the Furies. The gods are afraid of "
        "the coming war and say so loudly.")], url="https://dc.fandom.com/wiki/Wonder_Woman_Vol_5_36",
        intro="It was published on March 7, 2018.")
    candidate = _candidate(
        question="What is the Dark Gods problem in Wonder Woman's world?", anchor="Wonder Woman",
        concept="Dark Gods", issue="Wonder Woman #36 (2018)",
        url="https://dc.fandom.com/wiki/Wonder_Woman_Vol_5_36",
        quote="The gods are afraid of the coming war and say so loudly.")
    assert _verdict(candidate, page) == (False, "topic_overlap")


PUNISHER = (
    '**Featured Characters:**\n\n*   [Daredevil (Matt Murdock)](https://x.fandom.com/wiki/'
    'Matthew_Murdock_(Earth-616) "Matthew Murdock (Earth-616)")\n'
    '*   [Punisher (Frank Castle)](https://x.fandom.com/wiki/Frank_Castle_(Earth-616) '
    '"Frank Castle (Earth-616)")')
CASTLE = ("Castle argues that killing one man saves many others, but Matt refuses, believing "
          "there is always hope for change.")


def _punisher(links=True):
    sections = [('Appearing in "Know Fear"', PUNISHER if links else "*   Castle"), _syn(CASTLE)]
    page = _wiki("Daredevil Vol 6 4", sections,
                 url="https://marvel.fandom.com/wiki/Daredevil_Vol_6_4",
                 intro="\nPublished: April 17, 2019")
    candidate = _candidate(
        question="How does Daredevil respond when Punisher pushes him toward killing?",
        anchor="Daredevil", concept="daredevil_refuses_killing_under_pressure",
        issue="Daredevil Vol 6 4 (2019)", url="https://marvel.fandom.com/wiki/Daredevil_Vol_6_4",
        quote=CASTLE)
    return _verdict(candidate, page)


def test_r3_4_a_page_alias_counts_for_content_words_not_only_for_the_anchor():
    assert _punisher(links=True) == (True, "")


def test_r3_4_without_the_page_tying_the_names_the_words_are_not_matched():
    assert _punisher(links=False)[0] is False


BAR = ("Batman walks into the bar and orders a drink while the Joker laughs in the corner. "
       "Later, Batman leaves the bar and goes home to sleep.")


def _bar(quote, concept="no kill rule"):
    page = _wiki("Superman Vol 3 38", [_syn(BAR)])
    candidate = _candidate(question="Why does Batman refuse to kill the Joker?", anchor="Batman",
                           concept=concept, quote=quote)
    return _verdict(candidate, page)


def test_r3_4_merely_mentioning_the_hero_does_not_make_a_quote_relevant():
    assert _bar("Later, Batman leaves the bar and goes home to sleep.") == (
        False, "topic_overlap")


def test_r3_4_a_concept_word_in_the_quote_is_a_second_way_in_but_the_asked_word_is_still_needed():
    nano = ("Sharon explains that the reason Steve has been regressing is a nanovirus built to "
            "mimic red blood cells. Tony cures the nanovirus and Steve recovers his superhuman "
            "physique.")
    page = _wiki("Captain America Vol 6 10", [_syn(nano)],
                 url="https://marvel.fandom.com/wiki/Captain_America_Vol_6_10",
                 intro="\nPublished: April 25, 2012")
    candidate = _candidate(
        question="Why does Steve Rogers keep losing access to his super-soldier physique?",
        anchor="Captain America", concept="mechanism_strength_loss_nanovirus",
        issue="Captain America Vol 6 #10 (2012)",
        url="https://marvel.fandom.com/wiki/Captain_America_Vol_6_10",
        quote="the reason Steve has been regressing is a nanovirus built to mimic red blood cells")
    page_with_link = page.replace(
        "## Synopsis",
        '## Appearing in "X"\n\n**Featured Characters:**\n\n*   [Captain America (Steve Rogers)]'
        '(https://x/Steven_Rogers "Steven Rogers (Earth-616)")\n\n## Synopsis', 1)
    assert _verdict(candidate, page_with_link) == (True, "")
    # the same quote, but the synopsis never says what the question asks about
    bare = page_with_link.replace("physique", "strength").replace("superhuman", "great")
    assert _verdict(candidate, bare)[0] is False


def test_r3_4_flat_catalog_pages_keep_the_strict_two_word_rule():
    page = ("Title: Batman (2016) #161 | DC Comics\nPublished\nJuly 23, 2025\n"
            "Batman #161 (2025) has the Joker on its cover.")
    candidate = _candidate(
        question="Why does Batman refuse to kill the Joker?", anchor="Batman",
        concept="Batman no-kill rule", issue="Batman (2016) #161 (2025)",
        url="https://www.dc.com/comics/batman-2016/batman-161",
        quote="Batman #161 (2025) has the Joker on its cover.")
    assert _verdict(candidate, page) == (False, "topic_overlap")


# --- 5. smaller items -------------------------------------------------------------------------------------------

def _alias_page(featured):
    return _wiki("Flash Forward Vol 1 6", [
        ('Appearing in "Finish"', f"**Featured Characters:**\n\n*   {featured}"), _syn()],
        url="https://dc.fandom.com/wiki/Flash_Forward_Vol_1_6")


def _wally(featured, question="Why does Wally West unleash the energy stored in his cells?"):
    candidate = _candidate(
        question=question, anchor="Flash", concept="energy burst", issue="Flash Forward #6 (2015)",
        url="https://dc.fandom.com/wiki/Flash_Forward_Vol_1_6")
    return _verdict(candidate, _alias_page(featured))


@pytest.mark.parametrize("featured", [
    # slash-joined names with a universe tag
    '[The Flash/Wally West (Earth 0)](https://x.fandom.com/wiki/Wally_West "Wally West")',
    # the link TITLE carries the real name, the display text is a first name
    '[Wally](https://x.fandom.com/wiki/Flash_(Wally_West) "Flash (Wally West)")',
])
def test_r3_5_alias_spellings_beyond_hero_in_brackets_are_read(featured):
    assert _wally(featured) == (True, "")


def test_r3_5_universe_and_volume_tags_are_not_aliases():
    junk = ('[Flash Forward (Volume 1)](https://x/Flash_Forward "Flash Forward (Volume 1)") '
            '[Earth 0 (Earth 0)](https://x/Earth_0 "Earth 0 (Earth 0)")')
    assert _wally(junk, "Why does the volume of Earth unleash the energy stored in cells?") == (
        False, "anchor_mismatch")


def test_r3_5_a_wiki_stub_notice_is_not_a_synopsis():
    page = _wiki("Superman Vol 3 38", [
        ('Synopsis for "Finish Line"',
         "This article is a stub. You can help the wiki by expanding it with more detail."),
        ("Notes", FIRST + ".")])
    assert _verdict(_candidate(quote=FIRST), page) == (False, "no_synopsis")


def test_r3_5_the_most_informative_reason_wins_over_a_model_format_one():
    # anchor not named AND the quote is not on the page: the quote is the real problem
    candidate = _candidate(
        question="How does Clark unleash the energy stored in his cells?",
        quote="Superman flies to Gotham and rescues the whole city from a flood of water")
    result = evaluate_issue_evidence(
        candidate, "direct",
        fetcher=lambda url: FetchedSource(url, text=_wiki("Superman Vol 3 38", [_syn()])))
    assert (result.passed, result.reason) == (False, "quote_not_found")
    assert any("anchor_mismatch" in line for line in result.details)
    assert any("quote_not_found" in line for line in result.details)


def test_r3_5_a_reprint_is_reported_even_when_the_anchor_is_unnamed():
    page = _wiki("Superman Vol 3 38", [
        ('Synopsis for "Old Story"', f"Reprint of the 1st story from Some Title #5\n\n{BURST}")])
    candidate = _candidate(question="How does Clark unleash the energy stored in his cells?")
    result = evaluate_issue_evidence(
        candidate, "direct", fetcher=lambda url: FetchedSource(url, text=page))
    assert (result.passed, result.reason) == (False, "reprint_source")


def test_r3_5_gate_result_is_still_a_bool_like_verdict():
    assert not GateResult(False, "x") and GateResult(True)
