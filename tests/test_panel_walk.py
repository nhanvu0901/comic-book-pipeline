"""Panel-walk narrator: the page↔prose binding holds, and the style guards bite.

The point of this mode is that no matcher runs, so the invariant worth locking is that every
narrated line carries the PAGE it was written from, in reading order. The old suite locked
one-sentence-per-panel too; that was dropped 2026-07-29 after measuring the reference channel
(they tell the story rather than describe drawings), so the tests now lock the page binding,
the tier-based visual anchor, and the guards that keep the writer out of image-description.
"""
import json
import re

import pytest

import stages.panel_walk.narrate as pw


def _panel(idx: int, desc: str, dialog=(), chars=(), y: int | None = None) -> dict:
    return {
        "index": idx,
        "bbox": {"x": 0, "y": idx * 100 if y is None else y, "w": 100, "h": 100},
        "description": desc,
        "characters": list(chars),
        "dialog": [{"text": d} for d in dialog],
    }


def _page(n: int, panels: list[dict], story: bool = True) -> dict:
    return {
        "page_number": n, "is_story_page": story, "issue_label": "#1",
        "source_image": f"/tmp/p{n}.jpg",
        "image_dimensions": {"width": 100, "height": 200},
        "panels": panels, "text_blocks": [], "page_summary": "",
    }


def _project(tmp_path, monkeypatch, pages: list[dict], slug: str = "pw",
             ctx: dict | None = None) -> str:
    monkeypatch.setattr(pw, "PROJECTS_ROOT", tmp_path)
    root = tmp_path / slug
    (root / "preprocessed").mkdir(parents=True)
    for pg in pages:
        (root / "preprocessed" / f"page_{pg['page_number']:03d}_x.json").write_text(
            json.dumps(pg))
    (root / "comic_context.json").write_text(json.dumps(ctx or {"title": "Test Comic"}))
    return slug


def _fake_llm(monkeypatch, *, fail_pages=(), page_text=None):
    """Stand in for the LLM. Emits prose sized to the band the prompt asked for, so the real
    validator runs against it — a fake that dodges the validator proves nothing."""
    calls = []

    def fake(*, system, user, max_tokens, progress, label, validator=None, models=None):
        if "cold-open" in label or "outro" in label:
            body = " ".join(["word"] * 130)
            out = json.dumps({"lines": [f"{body}."]})
            assert validator is None or validator(out), f"fake {label} must satisfy validator"
            return out, "fake-model"
        page = int(label.split("p")[-1])
        if page in fail_pages:
            raise RuntimeError("model down")
        n = user.count("PANEL ")
        calls.append((page, n, "LAST LINES" in user, "STORY SO FAR" in user, system))
        if page_text is not None:
            sents = page_text(page, n)
        else:
            # Read the band out of the prompt rather than hard-coding a length, so the fake
            # keeps satisfying the real validator when the band is retuned.
            lo, hi = (int(x) for x in re.search(r"Write (\d+)-(\d+) words", user).groups())
            sents, budget = [], max(lo, min(hi, n * 10))
            while budget > 0:
                take = min(budget, 10)
                budget -= take
                if budget and budget < 3:      # never leave a 1-2 word remainder
                    take, budget = take + budget, 0
                sents.append(f"p{page} line {len(sents)} " + " ".join(["w"] * (take - 3)) + ".")
        out = json.dumps({"sentences": sents, "gist": f"page {page} gist"})
        assert validator is None or validator(out), "fake output must satisfy the validator"
        return out, "fake-model"

    monkeypatch.setattr(pw, "call_with_chain", fake)
    return calls


def _body(nar):
    return [s for s in nar.scenes if not s.is_intro and not s.is_outro]


# ── tier grouping: the geometric claim the visual unit rests on ──────────────────────────────

def test_tiers_group_panels_that_share_a_row():
    row = [_panel(0, "a", y=0), _panel(1, "b", y=10)]      # overlap 90 > 0.5*100 → same tier
    row[1]["bbox"]["x"] = 200
    below = _panel(2, "c", y=400)                           # no overlap → its own tier
    rows = pw.tiers_of(row + [below])
    assert [[p["index"] for p in r] for r in rows] == [[0, 1], [2]]


def test_tiers_are_in_reading_order_regardless_of_input_order():
    a, b, c = _panel(2, "c", y=400), _panel(0, "a", y=0), _panel(1, "b", y=200)
    rows = pw.tiers_of([a, b, c])
    assert [r[0]["index"] for r in rows] == [0, 1, 2]


# ── page binding ─────────────────────────────────────────────────────────────────────────────

def test_every_line_carries_the_page_it_was_written_from(tmp_path, monkeypatch):
    pages = [
        _page(1, [_panel(0, "a hero stands"), _panel(1, "he turns")]),
        _page(2, [_panel(0, "a door opens"), _panel(1, "smoke"), _panel(2, "he runs")]),
    ]
    slug = _project(tmp_path, monkeypatch, pages)
    _fake_llm(monkeypatch)

    body = _body(pw.build_narration(slug))

    assert [s.page_ref for s in body] == [1, 1, 2, 2, 2], "pages in reading order, none dropped"
    assert all(s.word_count == len(s.text.split()) for s in body)


def test_line_count_per_page_is_free_not_pinned_to_panel_count(tmp_path, monkeypatch):
    """The old contract forced len(sentences) == len(panels). Two lines for a four-panel page
    must now be accepted — that freedom is what lets a long sentence span several drawings."""
    pages = [_page(1, [_panel(i, f"beat {i}") for i in range(4)])]
    slug = _project(tmp_path, monkeypatch, pages)
    _fake_llm(monkeypatch, page_text=lambda page, n: [
        "The lights go out and every door in the building locks at once.",
        "Nobody screams.",
    ])

    body = _body(pw.build_narration(slug))
    assert len(body) == 2, "prose length is the page's business, not the panel count's"


def test_lines_anchor_to_tier_leads_and_walk_down_the_page(tmp_path, monkeypatch):
    """Three tiers, three lines → one line per tier, anchored to each tier's first panel."""
    panels = [_panel(0, "top", y=0), _panel(1, "mid", y=300), _panel(2, "low", y=600)]
    slug = _project(tmp_path, monkeypatch, [_page(1, panels)])
    _fake_llm(monkeypatch)

    body = _body(pw.build_narration(slug))
    assert [s.panel_ref for s in body] == [0, 1, 2]


def test_extra_tiers_past_the_last_line_are_simply_not_shown(tmp_path, monkeypatch):
    """Panel-skipping must emerge from the prose being short, without a rule for it."""
    panels = [_panel(i, f"beat {i}", y=i * 300) for i in range(4)]
    slug = _project(tmp_path, monkeypatch, [_page(1, panels)])
    _fake_llm(monkeypatch, page_text=lambda page, n: [
        "He opens the box and the whole room goes quiet around him."])

    body = _body(pw.build_narration(slug))
    assert [s.panel_ref for s in body] == [0], "one line anchors one tier; the rest go unused"


# ── continuity: the page call must see both memories ─────────────────────────────────────────

def test_one_llm_call_per_page_with_running_context_and_gists(tmp_path, monkeypatch):
    pages = [_page(n, [_panel(0, "x")]) for n in (1, 2, 3)]
    slug = _project(tmp_path, monkeypatch, pages)
    calls = _fake_llm(monkeypatch)

    pw.build_narration(slug)

    assert [c[0] for c in calls] == [1, 2, 3], "one call per page, in page order"
    assert calls[0][2] is False and calls[0][3] is False, "page 1 has no memory to hand over"
    assert all(c[2] for c in calls[1:]), "later pages get the verbatim tail"
    assert all(c[3] for c in calls[1:]), "later pages get the running one-line-per-page summary"


# ── style guards ─────────────────────────────────────────────────────────────────────────────

def test_image_description_language_is_rejected(tmp_path, monkeypatch):
    """'panel' appears 5 times in 60k words of the reference. A response leaning on it has
    missed the brief, so the validator must refuse it rather than ship it."""
    band = pw._valid_page(1, 100)
    assert band(json.dumps({"sentences": ["He runs for the door."], "gist": "g"}))
    for bad in ("In this panel he runs.", "We see him run.", "The page shows him running.",
                "The artwork goes dark here."):
        assert not band(json.dumps({"sentences": [bad], "gist": "g"})), bad


def test_a_too_short_page_is_rejected():
    """Under-length can only be fixed by re-asking, so it stays a validator reject."""
    band = pw._valid_page(10, 20)
    assert band(json.dumps({"sentences": [" ".join(["w"] * 15)], "gist": "g"}))
    assert not band(json.dumps({"sentences": [" ".join(["w"] * 4)], "gist": "g"}))


def test_over_length_is_not_a_validator_reject():
    """Measured on the-autumnal: the writer returns GOOD prose at ~28 words/panel against a
    16-word cap, so rejecting on length skipped every page — and all three retries resent an
    identical prompt, so they failed identically. Length is narrate_page's job now."""
    band = pw._valid_page(10, 20)
    assert band(json.dumps({"sentences": [" ".join(["w"] * 40)], "gist": "g"}))


def test_trim_keeps_whole_sentences_and_never_empties_a_page():
    sents = ["one two three.", "four five six.", "seven eight nine."]
    assert pw._trim_to_budget(sents, 7) == sents[:2]
    assert pw._trim_to_budget(sents, 1) == sents[:1], "a page must never trim to nothing"
    assert pw._trim_to_budget(sents, 99) == sents


def test_missing_gist_is_rejected():
    """The gist is not decoration — the cold open and outro are written from nothing else."""
    band = pw._valid_page(1, 100)
    assert not band(json.dumps({"sentences": ["He runs."]}))
    assert not band(json.dumps({"sentences": ["He runs."], "gist": "  "}))


def test_roster_is_a_closed_list_so_the_writer_cannot_guess_a_name(tmp_path, monkeypatch):
    ctx = {"title": "T", "summary": {"characters": [{"name": "Jean Grey"}, "Logan"]},
           "issues": [{"characters": ["Logan", {"name": "Scott Summers"}]}]}
    _project(tmp_path, monkeypatch, [_page(1, [_panel(0, "x")])], ctx=ctx)
    line = pw._roster_line("pw")
    assert "Jean Grey" in line and "Logan" in line and "Scott Summers" in line
    assert line.count("Logan") == 1, "deduped"
    assert "do not use a name outside this list" in line


def test_roster_is_empty_when_the_context_has_no_characters(tmp_path, monkeypatch):
    _project(tmp_path, monkeypatch, [_page(1, [_panel(0, "x")])], ctx={"title": "T"})
    assert pw._roster_line("pw") == ""


# ── cold open + outro ────────────────────────────────────────────────────────────────────────

def test_cold_open_and_outro_are_written_and_flagged(tmp_path, monkeypatch):
    pages = [_page(n, [_panel(0, "x")]) for n in (1, 2)]
    slug = _project(tmp_path, monkeypatch, pages)
    _fake_llm(monkeypatch)

    nar = pw.build_narration(slug)

    assert any(s.is_intro for s in nar.scenes), "the reference opens cold in 5/5 videos"
    assert any(s.is_outro for s in nar.scenes), "and closes with a fixed block in 5/5"
    assert nar.scenes[0].is_intro and nar.scenes[-1].is_outro, "and they bracket the body"
    assert [s.scene_id for s in nar.scenes] == list(range(1, len(nar.scenes) + 1))
    assert nar.hook == nar.scenes[0].text


def test_an_explicit_hook_replaces_the_generated_cold_open(tmp_path, monkeypatch):
    slug = _project(tmp_path, monkeypatch, [_page(1, [_panel(0, "x")])])
    _fake_llm(monkeypatch)

    nar = pw.build_narration(slug, hook="One line, mine.")
    intros = [s for s in nar.scenes if s.is_intro]
    assert [s.text for s in intros] == ["One line, mine."]


def test_cta_language_is_rejected_in_the_outro():
    """subscribe/like appear zero times in 60k words of the reference."""
    band = pw._valid_block(1, 100)
    assert band(json.dumps({"lines": ["That is the end of it."]}))
    assert not band(json.dumps({"lines": ["We see the end here."]}))


# ── pacing constant must be longform's own ───────────────────────────────────────────────────

def test_pacing_is_longform_specific_not_borrowed_from_a_short_mode(tmp_path, monkeypatch):
    """Regression guard for the bug this rewrite fixed: panel_walk fell through
    write_script._wps_for into _RECAP_WORDS_PER_SEC, a rate measured on a 60-second Short."""
    from stages.stage_3 import write_script

    slug = _project(tmp_path, monkeypatch, [_page(1, [_panel(0, "x")])])
    _fake_llm(monkeypatch)

    nar = pw.build_narration(slug)
    assert nar.words_per_second == pw._WORDS_PER_SEC
    assert nar.words_per_second != write_script._wps_for("panel_walk")
    assert all(s.target_seconds == round(s.word_count / pw._WORDS_PER_SEC, 2)
               for s in nar.scenes)


def test_page_budget_holds_the_reference_seconds_per_panel():
    """The reference's one invariant across both materials is ~1.7-2.1 SECONDS of screen time
    per panel. The word cap is just that number expressed in words at our render pace, so the
    two constants must stay consistent — at 16 words/panel the first the-autumnal pass held
    every panel 4.2s and played as a slideshow."""
    sec_per_panel = pw._WORDS_PER_PANEL_MAX / pw._WORDS_PER_SEC
    assert 1.7 <= sec_per_panel <= 2.8, sec_per_panel


# ── unchanged guards ─────────────────────────────────────────────────────────────────────────

def test_non_story_pages_are_skipped(tmp_path, monkeypatch):
    pages = [_page(1, [_panel(0, "cover art")], story=False), _page(2, [_panel(0, "story")])]
    slug = _project(tmp_path, monkeypatch, pages)
    _fake_llm(monkeypatch)

    assert [s.page_ref for s in _body(pw.build_narration(slug))] == [2]


def test_a_failed_page_is_skipped_not_fatal(tmp_path, monkeypatch):
    pages = [_page(n, [_panel(0, "x")]) for n in (1, 2, 3)]
    slug = _project(tmp_path, monkeypatch, pages)
    _fake_llm(monkeypatch, fail_pages=(2,))

    body = _body(pw.build_narration(slug))
    assert [s.page_ref for s in body] == [1, 3], "one dead page must not kill a 90-page walk"


def test_magi_only_descriptions_are_refused(tmp_path, monkeypatch):
    """VLM_EXTRACT=0 leaves dialogue OCR in `description` — narrating it ships nonsense.
    Stage 2's own preprocessing_method is the tell, so that is what the guard reads."""
    pages = [_page(1, [_panel(0, "a hero stands")]),
             _page(2, [_panel(0, "Wordless transition/SFX panel")], story=False)]
    for pg in pages:
        pg["preprocessing_method"] = "magi"          # no VLM leg
    slug = _project(tmp_path, monkeypatch, pages)
    _fake_llm(monkeypatch)

    with pytest.raises(RuntimeError, match="VLM_EXTRACT=1"):
        pw.build_narration(slug)


def test_a_few_undescribed_panels_do_not_block_a_good_vlm_run(tmp_path, monkeypatch):
    """The placeholder is a BACKFILL for any panel the VLM skipped, so it survives a clean
    run too (8/325 on the-autumnal). Blocking on it made a good run unnarratable."""
    pages = [_page(1, [_panel(0, "a hero stands"),
                       _panel(1, "Wordless transition/SFX panel")])]
    for pg in pages:
        pg["preprocessing_method"] = "magi+vlm"
    slug = _project(tmp_path, monkeypatch, pages)
    _fake_llm(monkeypatch)

    assert _body(pw.build_narration(slug)), "a 1-in-2 backfill must still narrate"


def test_unknown_preprocessing_method_is_not_treated_as_guilty(tmp_path, monkeypatch):
    """An export recording no method (older projects) is unknown, not Magi-only."""
    pages = [_page(1, [_panel(0, "a hero stands")])]
    slug = _project(tmp_path, monkeypatch, pages)
    _fake_llm(monkeypatch)

    assert _body(pw.build_narration(slug))


def test_all_pages_failing_raises(tmp_path, monkeypatch):
    pages = [_page(n, [_panel(0, "x")]) for n in (1, 2)]
    slug = _project(tmp_path, monkeypatch, pages)
    _fake_llm(monkeypatch, fail_pages=(1, 2))

    with pytest.raises(RuntimeError, match="no narration"):
        pw.build_narration(slug)


# ─── comic_context shape variance ────────────────────────────────────────────
# Three shapes exist in the wild. A URL-direct project stores `issues` as a RANGE
# STRING ("#1-3"); iterating it yielded one character per loop and crashed on .get.

def test_roster_reads_url_direct_shape(tmp_path, monkeypatch):
    slug = _project(tmp_path, monkeypatch, [_page(1, [_panel(0, "x")])],
                    ctx={"title": "T", "issues": "#1-3", "characters": ["Kat", "Sybil"]})
    line = pw._roster_line(slug)
    assert "Kat" in line and "Sybil" in line


def test_roster_survives_a_string_issues_field(tmp_path, monkeypatch):
    slug = _project(tmp_path, monkeypatch, [_page(1, [_panel(0, "x")])],
                    ctx={"issues": "#1-3", "summary": "a plain string too"})
    assert pw._roster_line(slug) == ""          # no names, but no crash


def test_roster_still_reads_stage1_and_anthology_shapes(tmp_path, monkeypatch):
    slug = _project(tmp_path, monkeypatch, [_page(1, [_panel(0, "x")])],
                    ctx={"summary": {"characters": [{"name": "Alpha"}]},
                         "issues": [{"characters": ["Beta"]}]})
    line = pw._roster_line(slug)
    assert "Alpha" in line and "Beta" in line


def test_bookend_retry_quotes_the_miss_instead_of_resending_the_same_prompt():
    """A 74-page run once died at the OUTRO: the model wrote ~70 words against a 90 floor and
    call_with_chain's retries resent an identical prompt, so it wrote 70 three times. The
    length check lives in _write_block now, and each retry states the actual miss."""
    seen: list[str] = []

    def fake(*, system, user, max_tokens, progress, label, validator=None, models=None):
        seen.append(user)
        n = 70 if len(seen) == 1 else 100          # too short, then correct
        out = json.dumps({"lines": [" ".join(["w"] * n)]})
        assert validator is None or validator(out), "shape-only validator must accept both"
        return out, "fake"

    import stages.panel_walk.narrate as m
    real, m.call_with_chain = m.call_with_chain, fake
    try:
        lines = m._write_block("sys {wmin} {wmax}", ["g1"], "T", 90, 150, "outro", None)
    finally:
        m.call_with_chain = real

    assert len(seen) == 2, "it must re-ask, not give up and not loop forever"
    assert "70 words" in seen[1], "the retry has to tell the model what it actually wrote"
    assert seen[0] in seen[1], "the retry keeps the original brief and appends the correction"
    assert sum(len(l.split()) for l in lines) == 100


def test_bookend_ships_rather_than_losing_the_whole_run():
    """Three informed tries and still off-band: return the text. Losing 74 good pages to a
    bookend is the worse failure, and the count is logged so it stays visible."""
    def fake(*, system, user, max_tokens, progress, label, validator=None, models=None):
        return json.dumps({"lines": [" ".join(["w"] * 20)]}), "fake"

    import stages.panel_walk.narrate as m
    real, m.call_with_chain = m.call_with_chain, fake
    logged: list[str] = []
    try:
        lines = m._write_block("sys {wmin} {wmax}", ["g"], "T", 90, 150, "outro", logged.append)
    finally:
        m.call_with_chain = real
    assert lines and sum(len(l.split()) for l in lines) == 20
    assert any("still" in x for x in logged), "the overrun must be reported, not hidden"
