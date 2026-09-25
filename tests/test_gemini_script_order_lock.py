"""The Gemini narration step maps script paragraphs to answer items BY POSITION.

The download order is the order of everything: answer item N was downloaded as chapter N,
so script paragraph N is item N. Nothing re-sorts, re-ranks or guesses by vocabulary, and a
script whose shape does not fit the item list is refused with a readable message instead of
being spread across the wrong comics. No network: a fake project on disk only."""
import json

import pytest

import stages.stage_3.gemini_prompt as gp
import stages.stage_3.pipeline as pl
from stages.user_errors import UserFacingError

URLS = [
    "https://batcave.biz/reader/1/11",
    "https://batcave.biz/reader/2/22",
    "https://batcave.biz/reader/3/33",
]
ITEMS = [
    {"entity": "Deadpool", "source_comic": "Deadpool: Invisible Touch #4 (2021)",
     "how_or_why": "every head wound wipes his memory", "reader_url": URLS[0]},
    {"entity": "Black Panther", "source_comic": "Black Panther vs. Deadpool #1 (2018)",
     "how_or_why": "his body regrows dead cancerous tissue", "reader_url": URLS[1]},
    {"entity": "Skrull", "source_comic": "Deadpool #3 (2008)",
     "how_or_why": "a Skrull copies the healing factor and explodes", "reader_url": URLS[2]},
]

# Item paragraphs sized like the template asks for (40-60 words): long enough that a
# hook or closing line (<=14 words) is never mistaken for one of them.
P1 = ("Deadpool heals from literally anything you throw at him. But there is a catch nobody "
      "mentions. Every time his brain takes a hit and grows back, a piece of his memory goes "
      "with it, so the man keeps forgetting who he even is.")
P2 = ("Black Panther's scientists finally scan him properly. What they find is worse than a "
      "curse. His body is not healing at all. It is regrowing dead, cancerous tissue over and "
      "over, which means the healing factor is the disease keeping him alive.")
P3 = ("Then a Skrull decides to copy the power for himself. Big mistake. His body starts "
      "healing wounds he does not even have, faster and faster, until the regeneration has "
      "nowhere left to go and he simply explodes on the spot.")
HOOK = "Nobody out-heals Deadpool. It costs him."
OUTRO = "He can't die. That's the problem."


def _project(tmp_path, monkeypatch, items=ITEMS, manifest_urls=None, chapters=(1, 2, 3),
             story=True):
    monkeypatch.setattr(gp, "PROJECTS_ROOT", tmp_path)
    monkeypatch.setattr(pl, "PROJECTS_ROOT", tmp_path)
    root = tmp_path / "proj"
    prep = root / "preprocessed"
    prep.mkdir(parents=True)
    (root / "raw_comic").mkdir()
    (root / "comic_context.json").write_text(json.dumps({"plot_source": "answer_research"}))
    (root / "answer_context.json").write_text(json.dumps({"question": "Q?", "items": items}))
    manifest_urls = manifest_urls or {ch: URLS[ch - 1] for ch in chapters}
    (root / "raw_comic" / "manifest.json").write_text(json.dumps([
        {"chapter_index": ch, "label": f"#{ch}", "reader_url": manifest_urls[ch],
         "pages": [f"raw_comic/ch{ch:02d}_page_{k:02d}.jpg" for k in range(1, 5)]}
        for ch in chapters
    ]))
    pn = 0
    for ch in chapters:
        for k in range(1, 5):
            pn += 1
            (prep / f"page_{pn:03d}_{pn:016x}.json").write_text(json.dumps({
                "page_number": pn, "is_story_page": story, "issue_label": f"#{ch}",
                "source_image": f"/x/raw_comic/ch{ch:02d}_page_{k:02d}.jpg"}))
    return "proj"


def _chapter_of_page(project_root, page_number):
    for p in (project_root / "preprocessed").glob("page_*.json"):
        d = json.loads(p.read_text())
        if d["page_number"] == page_number:
            return d["issue_label"]
    return None


def _body_chapters(tmp_path, narration):
    """issue label of each body paragraph, in order (one entry per beat)."""
    seen = []
    for s in narration["scenes"]:
        if s["is_intro"] or s["is_outro"]:
            continue
        lab = _chapter_of_page(tmp_path / "proj", s["page_ref"])
        if not seen or seen[-1][0] != s["beat_id"]:
            seen.append((s["beat_id"], lab))
    return seen


def _parse(tmp_path, monkeypatch, script, **kw):
    name = _project(tmp_path, monkeypatch, **kw)
    return gp.parse_and_save_script(name, script, log=lambda *_: None)


# ── position is the mapping ──────────────────────────────────────────────────


def test_paragraph_n_is_item_n(tmp_path, monkeypatch):
    nar = _parse(tmp_path, monkeypatch, f"{HOOK}\n\n{P1}\n\n{P2}\n\n{P3}\n\n{OUTRO}")
    assert _body_chapters(tmp_path, nar) == [(1, "#1"), (2, "#2"), (3, "#3")]
    assert [b["name"] for b in nar["beats"]] == ["Deadpool", "Black Panther", "Skrull"]


def test_paragraphs_are_never_resorted_by_their_wording(tmp_path, monkeypatch):
    """Even when paragraph 1 is obviously about item 3, it stays on item 1: the order is
    fixed upstream and the writer is told to keep it, so position wins over vocabulary."""
    nar = _parse(tmp_path, monkeypatch, f"{HOOK}\n\n{P3}\n\n{P1}\n\n{P2}\n\n{OUTRO}")
    assert _body_chapters(tmp_path, nar) == [(1, "#1"), (2, "#2"), (3, "#3")]
    first_body = next(s for s in nar["scenes"] if not s["is_intro"])
    assert first_body["text"].startswith("Then a Skrull")


def test_two_sentence_hook_and_closing_are_recognised(tmp_path, monkeypatch):
    nar = _parse(tmp_path, monkeypatch, f"{HOOK}\n\n{P1}\n\n{P2}\n\n{P3}\n\n{OUTRO}")
    intro = [s for s in nar["scenes"] if s["is_intro"]]
    outro = [s for s in nar["scenes"] if s["is_outro"]]
    assert nar["hook"] == HOOK
    assert [s["text"] for s in intro] == [HOOK]
    assert [s["text"] for s in outro] == [OUTRO]


def test_intro_and_outro_sit_on_the_first_and_last_item(tmp_path, monkeypatch):
    nar = _parse(tmp_path, monkeypatch, f"{HOOK}\n\n{P1}\n\n{P2}\n\n{P3}\n\n{OUTRO}")
    intro = next(s for s in nar["scenes"] if s["is_intro"])
    outro = next(s for s in nar["scenes"] if s["is_outro"])
    assert _chapter_of_page(tmp_path / "proj", intro["page_ref"]) == "#1"
    assert _chapter_of_page(tmp_path / "proj", outro["page_ref"]) == "#3"
    assert intro["beat_id"] == 0 and outro["beat_id"] == 3


def test_closing_line_merged_into_last_item_still_maps(tmp_path, monkeypatch):
    nar = _parse(tmp_path, monkeypatch, f"{HOOK}\n\n{P1}\n\n{P2}\n\n{P3} {OUTRO}")
    assert _body_chapters(tmp_path, nar) == [(1, "#1"), (2, "#2"), (3, "#3")]
    assert not [s for s in nar["scenes"] if s["is_outro"]]


# ── Gemini output shapes ─────────────────────────────────────────────────────


def test_final_script_marker_ignores_hook_brainstorm_and_tail(tmp_path, monkeypatch):
    script = (
        "Hook 1: Deadpool heals from anything.\n"
        "Hook 2: Nobody out-heals Deadpool. It costs him.\n"
        "Hook 3: Healing is Deadpool's curse.\n\n"
        "I pick Hook 2 because it states the constant.\n\n"
        f"FINAL SCRIPT\n\n{HOOK}\n\n{P1}\n\n{P2}\n\n{P3}\n\n{OUTRO}\n\n---\n**Word Count:** 170"
    )
    nar = _parse(tmp_path, monkeypatch, script)
    assert nar["hook"] == HOOK
    assert _body_chapters(tmp_path, nar) == [(1, "#1"), (2, "#2"), (3, "#3")]
    assert not any("Word" in s["text"] or "Hook 1" in s["text"] for s in nar["scenes"])


def test_chosen_hook_marker_is_used(tmp_path, monkeypatch):
    script = (
        "**Hook 1:** Deadpool heals from anything.\n**Hook 2:** Nobody out-heals Deadpool.\n\n"
        "**[CHOSEN HOOK: Hook 2]** Nobody out-heals Deadpool.\n\n"
        f"{P1}\n\n{P2}\n\n{P3}\n\n{OUTRO}"
    )
    nar = _parse(tmp_path, monkeypatch, script)
    assert nar["hook"] == "Nobody out-heals Deadpool."
    assert _body_chapters(tmp_path, nar) == [(1, "#1"), (2, "#2"), (3, "#3")]


def test_chosen_hook_that_only_names_the_option_resolves_to_its_text(tmp_path, monkeypatch):
    script = (
        "Hook 1: Deadpool heals from anything.\nHook 2: Nobody out-heals Deadpool.\n\n"
        "Chosen hook: Hook 2\n\n"
        f"{P1}\n\n{P2}\n\n{P3}\n\n{OUTRO}"
    )
    nar = _parse(tmp_path, monkeypatch, script)
    assert nar["hook"] == "Nobody out-heals Deadpool."


def test_several_hook_options_with_no_choice_are_refused(tmp_path, monkeypatch):
    script = ("Hook 1: Deadpool heals from anything.\nHook 2: Nobody out-heals Deadpool.\n\n"
              f"{P1}\n\n{P2}\n\n{P3}\n\n{OUTRO}")
    with pytest.raises(UserFacingError, match="hook"):
        _parse(tmp_path, monkeypatch, script)


def test_markdown_headings_labels_and_emphasis_are_not_spoken(tmp_path, monkeypatch):
    script = (
        f"## Script\n\n{HOOK}\n\n**Item 1 — Deadpool**\n{P1}\n\n"
        f"### 2. Black Panther\n{P2.replace('worse', '**worse**')}\n\n"
        f"- {P3}\n\n{OUTRO}"
    )
    nar = _parse(tmp_path, monkeypatch, script)
    texts = " ".join(s["text"] for s in nar["scenes"])
    assert "#" not in texts and "*" not in texts and "Script" not in texts
    assert "Item 1" not in texts and not texts.startswith("-")
    assert _body_chapters(tmp_path, nar) == [(1, "#1"), (2, "#2"), (3, "#3")]


def test_single_newlines_between_parts_still_map(tmp_path, monkeypatch):
    nar = _parse(tmp_path, monkeypatch, f"{HOOK}\n{P1}\n{P2}\n{P3}\n{OUTRO}")
    assert nar["hook"] == HOOK
    assert _body_chapters(tmp_path, nar) == [(1, "#1"), (2, "#2"), (3, "#3")]


def test_hook_paragraph_then_single_newline_items_still_map(tmp_path, monkeypatch):
    nar = _parse(tmp_path, monkeypatch, f"{HOOK}\n\n{P1}\n{P2}\n{P3}\n\n{OUTRO}")
    assert nar["hook"] == HOOK
    assert _body_chapters(tmp_path, nar) == [(1, "#1"), (2, "#2"), (3, "#3")]


def test_bold_hook_without_punctuation_is_kept_as_the_hook(tmp_path, monkeypatch):
    nar = _parse(tmp_path, monkeypatch,
                 f"**Nobody out-heals Deadpool**\n\n{P1}\n\n{P2}\n\n{P3}\n\n{OUTRO}")
    assert nar["hook"] == "Nobody out-heals Deadpool"
    assert _body_chapters(tmp_path, nar) == [(1, "#1"), (2, "#2"), (3, "#3")]


def test_hard_wrapped_script_with_a_dropped_item_is_refused_not_split(tmp_path, monkeypatch):
    """Lines broken mid-sentence are one paragraph: reading them one per line would turn
    item 1's second half into 'item 2' and hide the dropped item."""
    wrapped_p1 = P1.replace("But there is a catch", "But there is\na catch")
    with pytest.raises(UserFacingError, match="3 answer item"):
        _parse(tmp_path, monkeypatch, f"{HOOK}\n\n{wrapped_p1}\n\n{P3}\n\n{OUTRO}")


def test_horizontal_rule_between_parts_is_a_separator_not_the_end(tmp_path, monkeypatch):
    nar = _parse(tmp_path, monkeypatch, f"{HOOK}\n\n---\n\n{P1}\n\n{P2}\n\n{P3}\n\n{OUTRO}")
    assert _body_chapters(tmp_path, nar) == [(1, "#1"), (2, "#2"), (3, "#3")]


# ── shapes that cannot be mapped are refused, never guessed ─────────────────


def test_a_dropped_item_is_refused(tmp_path, monkeypatch):
    with pytest.raises(UserFacingError, match="3 answer item"):
        _parse(tmp_path, monkeypatch, f"{HOOK}\n\n{P1}\n\n{P3}\n\n{OUTRO}")


def test_an_item_split_in_two_is_refused(tmp_path, monkeypatch):
    with pytest.raises(UserFacingError, match="3 answer item"):
        _parse(tmp_path, monkeypatch,
               f"{HOOK}\n\n{P1}\n\n{P2}\n\nNow the worst one.\n\n{P3}\n\n{OUTRO}")


def test_a_transition_line_posing_as_an_item_is_refused(tmp_path, monkeypatch):
    """hook + item + 'Now the worst one.' + item + closing has the right COUNT for three
    items, but the middle 'paragraph' is not an item — mapping it would shift item 3."""
    with pytest.raises(UserFacingError):
        _parse(tmp_path, monkeypatch,
               f"{HOOK}\n\n{P1}\n\nNow the worst one.\n\n{P3}\n\n{OUTRO}")


def test_no_info_from_the_writer_is_reported(tmp_path, monkeypatch):
    with pytest.raises(UserFacingError, match="NO INFO"):
        _parse(tmp_path, monkeypatch, "NO INFO — item 2 has only one sourced beat.")


# ── the downloaded comics must be the ones the items cite ────────────────────


def test_item_whose_chapter_was_not_downloaded_is_named(tmp_path, monkeypatch):
    with pytest.raises(UserFacingError, match="#2 Black Panther"):
        _parse(tmp_path, monkeypatch, f"{HOOK}\n\n{P1}\n\n{P2}\n\n{P3}\n\n{OUTRO}",
               chapters=(1, 3))


def test_item_whose_url_changed_since_download_is_refused(tmp_path, monkeypatch):
    stale = {1: URLS[0], 2: "https://batcave.biz/reader/9/99", 3: URLS[2]}
    with pytest.raises(UserFacingError, match="re-download"):
        _parse(tmp_path, monkeypatch, f"{HOOK}\n\n{P1}\n\n{P2}\n\n{P3}\n\n{OUTRO}",
               manifest_urls=stale)


def test_items_citing_the_same_issue_share_its_chapter(tmp_path, monkeypatch):
    items = [ITEMS[0], ITEMS[1], dict(ITEMS[2], reader_url=URLS[0])]
    nar = _parse(tmp_path, monkeypatch, f"{HOOK}\n\n{P1}\n\n{P2}\n\n{P3}\n\n{OUTRO}",
                 items=items, chapters=(1, 2))
    assert _body_chapters(tmp_path, nar) == [(1, "#1"), (2, "#2"), (3, "#1")]
    assert [b["id"] for b in nar["beats"]] == [1, 2, 3]


def test_chapter_without_story_pages_anchors_to_its_own_first_page(tmp_path, monkeypatch):
    nar = _parse(tmp_path, monkeypatch, f"{HOOK}\n\n{P1}\n\n{P2}\n\n{P3}\n\n{OUTRO}",
                 story=False)
    assert _body_chapters(tmp_path, nar) == [(1, "#1"), (2, "#2"), (3, "#3")]


# ── the editor never offers a script written for a different item list ───────


def test_editor_reopens_the_script_one_paragraph_per_item(tmp_path, monkeypatch):
    script = f"{HOOK}\n\n{P1}\n\n{P2}\n\n{P3}\n\n{OUTRO}"
    name = _project(tmp_path, monkeypatch)
    gp.parse_and_save_script(name, script, log=lambda *_: None)
    text, note = gp.saved_script_for_editor(name)
    assert note == ""
    # Re-approving the reopened text must give the same mapping, not one item per sentence.
    again = gp.parse_and_save_script(name, text, log=lambda *_: None)
    assert _body_chapters(tmp_path, again) == [(1, "#1"), (2, "#2"), (3, "#3")]


def test_editor_drops_a_script_written_for_other_items(tmp_path, monkeypatch):
    name = _project(tmp_path, monkeypatch)
    gp.parse_and_save_script(name, f"{HOOK}\n\n{P1}\n\n{P2}\n\n{P3}\n\n{OUTRO}",
                             log=lambda *_: None)
    answer = tmp_path / name / "answer_context.json"
    ctx = json.loads(answer.read_text())
    ctx["items"][1]["source_comic"] = "Something else #7 (2020)"
    answer.write_text(json.dumps(ctx))
    text, note = gp.saved_script_for_editor(name)
    assert text == ""
    assert "changed" in note


# ── prompt carries the order ─────────────────────────────────────────────────


def test_prompt_numbers_items_in_download_order_and_forbids_reordering(tmp_path, monkeypatch):
    name = _project(tmp_path, monkeypatch)
    prompt, _path = gp.generate_gemini_writer_prompt(name)
    assert prompt.index('"item_number": 1') < prompt.index('"item_number": 2') \
        < prompt.index('"item_number": 3')
    assert "most surprising answer goes LAST" not in prompt
    assert "skip any item" not in prompt
    assert "FINAL SCRIPT" in prompt
    stored = json.loads((tmp_path / name / "answer_context.json").read_text())
    assert "item_number" not in stored["items"][0]   # the prompt copy only


# ── items with no reader URL take the chapter downloaded for them ────────────
# Seen 2026-09-24: the evidence gate returned no reader URL (OpenRouter failed), so two
# items were created with reader_url "" and the user downloaded all three chapters with
# Stage 2's "Download from URL(s)" — which fills comic_context.json and the manifest,
# not answer_context.json. The narration step then refused chapters 2 and 3 ("the item
# now cites ") although they were exactly the comics of items 2 and 3.

def _unresolved_items():
    items = [dict(it) for it in ITEMS]
    items[1]["reader_url"] = ""
    items[2]["reader_url"] = ""
    return items


def test_script_places_on_chapters_downloaded_for_items_with_no_url(tmp_path, monkeypatch):
    narration = _parse(tmp_path, monkeypatch, f"{HOOK}\n\n{P1}\n\n{P2}\n\n{P3}\n\n{OUTRO}",
                       items=_unresolved_items())

    assert [lab for _beat, lab in _body_chapters(tmp_path, narration)] == ["#1", "#2", "#3"]
    answer = json.loads((tmp_path / "proj" / "answer_context.json").read_text())
    assert [it["reader_url"] for it in answer["items"]] == URLS
    assert answer["unresolved_reader_urls"] == []


def test_adopting_download_urls_fills_only_empty_items_and_both_contexts(tmp_path, monkeypatch):
    from stages.stage_1 import answer_research

    _project(tmp_path, monkeypatch, items=_unresolved_items())
    root = tmp_path / "proj"

    assert answer_research.adopt_downloaded_reader_urls(root) == [2, 3]

    answer = json.loads((root / "answer_context.json").read_text())
    comic = json.loads((root / "comic_context.json").read_text())
    assert [it["reader_url_status"] for it in answer["items"]] == ["ready"] * 3
    assert comic["reader_urls"] == URLS and comic["unresolved_reader_urls"] == []
    assert answer_research.adopt_downloaded_reader_urls(root) == []


def test_a_download_that_does_not_match_the_item_count_is_not_adopted(tmp_path, monkeypatch):
    """With fewer or more chapters than items, position no longer says which is which."""
    from stages.stage_1 import answer_research

    _project(tmp_path, monkeypatch, items=_unresolved_items(), chapters=(1, 2))

    assert answer_research.adopt_downloaded_reader_urls(tmp_path / "proj") == []
    answer = json.loads((tmp_path / "proj" / "answer_context.json").read_text())
    assert [it["reader_url"] for it in answer["items"]][1:] == ["", ""]


def test_an_item_citing_a_different_comic_is_still_refused(tmp_path, monkeypatch):
    other = {1: URLS[0], 2: "https://batcave.biz/reader/9/99", 3: URLS[2]}
    with pytest.raises(UserFacingError, match="item #2"):
        _parse(tmp_path, monkeypatch, f"{HOOK}\n\n{P1}\n\n{P2}\n\n{P3}\n\n{OUTRO}",
               manifest_urls=other)
