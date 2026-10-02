"""Sentence sub-shot matcher (Q&A / explore_answer mode).

Deterministic and model-free: Master locks the panels by hand, so a beat's sentences are handed
those panels ROUND-ROBIN. Covers: lock_panels normalises v1+v2 shapes, sentence splitting merges a
short countdown label, _match_sentences gives distinct panels across the first len(cands)
sentences then cycles, and build_sentence_panels writes that distribution (a beat with no
candidate panel → null page/panel, still timed).
"""
import json

import stages.review_gate as rg
import stages.sentence_match as sm


# ─── deliverable 1: lock_panels normalises both shapes ───────────────────────

def test_lock_panels_normalises_shapes():
    # v2 multi-panel
    v2 = {"panels": [{"page": 5, "panel": 0}, {"page": 5, "panel": 2}], "source": "batcave"}
    assert rg.lock_panels(v2) == [{"page": 5, "panel": 0}, {"page": 5, "panel": 2}]
    # old v1 single-panel → 1-item list
    v1 = {"page": 18, "panel": 1, "source": "batcave"}
    assert rg.lock_panels(v1) == [{"page": 18, "panel": 1}]
    # empty / malformed → []
    assert rg.lock_panels(None) == []
    assert rg.lock_panels({}) == []
    assert rg.lock_panels({"source": "batcave"}) == []


def test_split_sentences_merges_short_label():
    # "The Punisher." (2 words) folds forward into the next sentence, not its own shot.
    out = sm._split_sentences("The Punisher. In Thunderbolts he stood back up. He looked confused.")
    assert out == ["The Punisher. In Thunderbolts he stood back up.", "He looked confused."]
    # a lone trailing fragment folds backward
    assert sm._split_sentences("A full sentence here now. Ok.") == ["A full sentence here now. Ok."]


# ─── _match_sentences: round-robin over the candidate panels ─────────────────

def _cands(n, page=5):
    """Candidate 4-tuples (key, panel, src, page_tb). The panels are None on purpose: the
    distribution must never look at a panel's content."""
    return [((page, i), None, f"p{page}.png", None) for i in range(n)]


def test_match_sentences_round_robin_distinct_then_cycles():
    n_sent = 7
    sentences = [f"Sentence number {i} goes here." for i in range(n_sent)]
    spans = [(float(i), i + 0.9) for i in range(n_sent)]
    out = sm._match_sentences(sentences, spans, _cands(3))

    picks = [(r["page"], r["panel"]) for r in out]
    assert picks[:3] == [(5, 0), (5, 1), (5, 2)]            # distinct across the first len(cands)
    assert len(set(picks[:3])) == 3
    assert picks[3:] == [(5, 0), (5, 1), (5, 2), (5, 0)]    # then cycling in lock order
    # the sentences keep their text and time span regardless of which panel they got
    assert [r["text"] for r in out] == sentences
    assert [(r["start"], r["end"]) for r in out] == [(float(i), round(i + 0.9, 3)) for i in range(n_sent)]
    assert all(set(r) == {"text", "start", "end", "page", "panel"} for r in out)


def test_match_sentences_fewer_sentences_than_panels_uses_the_first_ones():
    out = sm._match_sentences(["One two three.", "Four five six."], [(0.0, 1.0), (1.0, 2.0)],
                              _cands(4))
    assert [(r["page"], r["panel"]) for r in out] == [(5, 0), (5, 1)]


def test_match_sentences_no_candidates_is_all_null_but_still_timed():
    out = sm._match_sentences(["One two three.", "Four five six."], [(0.0, 1.0), (None, None)], [])
    assert all(r["page"] is None and r["panel"] is None for r in out)
    assert (out[0]["start"], out[0]["end"]) == (0.0, 1.0)
    assert (out[1]["start"], out[1]["end"]) == (None, None)    # nothing to align to → no span
    assert sm._match_sentences([], [], _cands(2)) == []


# ─── fixture builder ─────────────────────────────────────────────────────────

def _write_project(root):
    """2 story scenes. Scene 10 locks 3 panels on page 5; its 3 sentences take them in lock
    order. Scene 11 has no lock and no panel anchor → all sparse."""
    (root / "review").mkdir(parents=True)
    (root / "preprocessed").mkdir()

    s10 = "Frank Castle vomited on the floor. The hero punched Johnny Blaze hard. Nothing here otherwise."
    s11 = "An unrelated closing thought entirely."
    (root / "narration.json").write_text(json.dumps({"scenes": [
        {"scene_id": 10, "text": s10, "page_ref": 5, "panel_ref": -1},
        {"scene_id": 11, "text": s11, "page_ref": 9, "panel_ref": -1},
    ]}))

    # word_timestamps aligned to the concatenated scene words, 0.1s per word.
    words, t = [], 0.0
    for w in (s10 + " " + s11).split():
        words.append({"word": w, "start": round(t, 3), "end": round(t + 0.09, 3)})
        t += 0.1
    (root / "word_timestamps.json").write_text(json.dumps(words))

    # locks.json — scene 10 locks 3 panels (v2 shape); scene 11 has none.
    (root / "review" / "locks.json").write_text(json.dumps({
        "approved": False, "approved_at": None, "narration_sha1": None,
        "locks": {"10": {"panels": [{"page": 5, "panel": 0}, {"page": 5, "panel": 1},
                                    {"page": 5, "panel": 2}], "source": "batcave"}},
    }))

    descs = ["frank castle vomited floor", "hero punched johnny blaze", "unrelated background rubble"]
    (root / "preprocessed" / "page_005.json").write_text(json.dumps({
        "page_number": 5, "page_type": "story", "source_image": "p5.png",
        "image_dimensions": {"width": 600, "height": 2700},
        "panels": [{"index": i, "bbox": {"x": 0, "y": 900 * i, "w": 600, "h": 900},
                    "description": d, "characters": []} for i, d in enumerate(descs)],
        "text_blocks": [],
    }))


# ─── deliverable 2 + 3: build_sentence_panels ────────────────────────────────

def test_build_sentence_panels_spreads_sentences_over_locked_panels(tmp_path):
    root = tmp_path / "qa"
    _write_project(root)

    out_path = sm.build_sentence_panels(root)          # path arg → no PROJECTS_ROOT needed
    doc = json.loads(out_path.read_text())
    scenes = {s["scene_id"]: s["sentences"] for s in doc["scenes"]}
    assert set(scenes) == {10, 11}

    s10 = scenes[10]
    assert len(s10) == 3
    # every sentence has a valid time span
    for sent in s10:
        assert sent["start"] is not None and sent["end"] is not None
        assert sent["start"] < sent["end"]
    # 3 sentences over the 3 locked panels → one distinct panel each, in lock order
    assert [(s["page"], s["panel"]) for s in s10] == [(5, 0), (5, 1), (5, 2)]

    # scene 11 has no lock and no panel anchor → every sentence sparse (null), still timed
    s11 = scenes[11]
    assert s11 and all(x["page"] is None and x["panel"] is None for x in s11)
    assert all(x["start"] < x["end"] for x in s11)


def test_no_lock_falls_back_to_scene_anchor(tmp_path):
    """When a scene has NO lock but a real (page_ref, panel_ref) anchor, that panel is the
    single candidate (backward-compatible with the pre-multi-select world)."""
    root = tmp_path / "qa2"
    _write_project(root)
    # drop the lock and give scene 10 a resolvable panel anchor instead
    (root / "review" / "locks.json").write_text(json.dumps({
        "approved": False, "locks": {}}))
    nar = json.loads((root / "narration.json").read_text())
    nar["scenes"][0]["panel_ref"] = 0          # page_ref 5, panel 0
    (root / "narration.json").write_text(json.dumps(nar))

    doc = json.loads(sm.build_sentence_panels(root).read_text())
    s10 = {s["scene_id"]: s["sentences"] for s in doc["scenes"]}[10]
    # only (5,0) is a candidate → every sentence takes it (the panel repeats once they outnumber it)
    assert [(x["page"], x["panel"]) for x in s10] == [(5, 0)] * 3
