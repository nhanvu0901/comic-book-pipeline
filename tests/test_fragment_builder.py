"""PLAN A — fragment-aligned scene builder for micro_moment (1:1 caption↔panel).

For a scene whose visual_beats PIN a panel per fragment (micro_moment) AND with
word_timestamps available, each fragment becomes its own render UNIT: panel = the pin,
caption = the verbatim fragment, span = the fragment's word-timed slice. This fixes the
old _split_members_by_clause path, which bucketed ~7-word caption CHUNKS (whose boundaries
never fall on a clause edge) and tore a pinned quote across two panels.

Covers: (a) one unit per fragment, caption verbatim, pin per fragment, total dur == scene
span; (b) a pinned quote lands WHOLE on its own panel (joker scene-3 shape, pin p2/6); (c)
recap-shape string beats stay byte-identical whether or not word_timestamps is passed;
(d) an unalignable fragment set falls back to an even span split without raising.
"""
import stages.stage_5.shots as shots


def _words(text: str, t0: float, t1: float) -> list[dict]:
    """Evenly-timed {word,start,end} tokens for a whitespace-split of `text` across [t0,t1]."""
    toks = text.split()
    step = (t1 - t0) / max(1, len(toks))
    return [{"word": tok, "start": round(t0 + i * step, 4), "end": round(t0 + (i + 1) * step, 4)}
            for i, tok in enumerate(toks)]


# ── (a) one unit per fragment: caption verbatim, pin per fragment, dur sums to scene ────
def test_fragment_units_one_unit_per_fragment_verbatim_and_pinned():
    frags = ["He wakes screaming,",
             "and she pulls him close —",
             '"I waited my whole life for that gag."']
    pins = [(1, 1), (1, 4), (2, 6)]
    words = _words(" ".join(frags), 10.0, 16.0)          # scene span 10.0 → 16.0
    members = [("whole scene text", 10.0, 6.0)]          # seg total 6.0s

    texts, slices, out_pins = shots._fragment_units(frags, pins, members, words)

    assert texts == frags
    assert len(slices) == 3
    assert [sl[0][0] for sl in slices] == frags          # caption verbatim, one member each
    assert out_pins == pins                              # pin preserved per fragment
    total = sum(sl[0][2] for sl in slices)
    assert abs(total - 6.0) < 1e-6                       # durations sum EXACTLY to scene span
    starts = [sl[0][1] for sl in slices]
    assert starts == sorted(starts)                      # monotonic, non-overlapping


# ── (b) a pinned quote lands WHOLE on its own panel (joker scene-3 shape) ───────────────
def test_pinned_quote_fragment_lands_whole_on_its_panel(monkeypatch):
    def _boom(*a, **k):
        raise AssertionError("_match_panels must NOT be called when every beat is pinned")
    monkeypatch.setattr(shots, "_match_panels", _boom)

    panels = [{"index": i, "bbox": {"x": 0, "y": i * 100, "w": 900 + i, "h": 90},
               "description": f"panel {i}"} for i in range(7)]
    pages = {2: {"page_number": 2, "is_story_page": True, "source_image": "/p2.png",
                 "image_dimensions": {"width": 1000, "height": 1500}, "panels": panels}}
    frags = [{"text": "In the dream he treats death row like a show,", "page": 2, "panel": 1},
             {"text": "and he uses his last meal to smash a pie into the priest's face —",
              "page": 2, "panel": 5},
             {"text": '"I waited my whole life for that gag."', "page": 2, "panel": 6}]
    joined = " ".join(b["text"] for b in frags)
    narr = {"mode": "micro_moment",
            "scenes": [{"scene_id": 3, "text": joined, "page_ref": 2, "visual_beats": frags}]}
    chunks = [{"text": joined, "start": 13.0, "end": 21.0}]      # ONE coarse caption chunk
    timings = [{"scene_id": 3, "start": 13.0, "end": 21.0}]
    words = _words(joined, 13.0, 21.0)

    built = shots._build_shots_per_chunk(narr, chunks, pages, timings,
                                         word_timestamps=words, project=None)

    # 3 fragments → 3 shots (a single coarse chunk would collapse to 1 on the old path)
    assert len(built) == 3
    quote = [s for s in built if "waited my whole life" in s.caption_text]
    assert len(quote) == 1                               # the quote is ONE shot, not torn
    q = quote[0]
    assert q.caption_text == '"I waited my whole life for that gag."'   # whole, verbatim
    assert q.panel_bbox["y"] == 600 and q.panel_bbox["w"] == 906        # panel index 6 on p2
    assert q.source_image == "/p2.png"
    assert q.fit_fill is True                            # micro_moment → fill the 9:16 frame


# ── (c) recap-shape string beats: byte-identical with or without word_timestamps ────────
def test_recap_string_beats_byte_identical_with_or_without_word_timestamps(monkeypatch):
    panel_a = {"index": 0, "bbox": {"x": 0, "y": 0, "w": 700, "h": 900}, "_page_number": 3}
    panel_b = {"index": 1, "bbox": {"x": 0, "y": 0, "w": 800, "h": 900}, "_page_number": 3}

    def _fake_match(units_arg, pbn, c2n, *, project=None, narration=None):
        return [(panel_a, "/pA.png"), (panel_b, "/pB.png")][:len(units_arg)]
    monkeypatch.setattr(shots, "_match_panels", _fake_match)

    pages = {3: {"page_number": 3, "is_story_page": True, "source_image": "/p3.png",
                 "image_dimensions": {"width": 1000, "height": 1500},
                 "panels": [{"index": 0, "bbox": {"x": 0, "y": 0, "w": 700, "h": 900}, "description": "a"},
                            {"index": 1, "bbox": {"x": 0, "y": 0, "w": 800, "h": 900}, "description": "b"}]}}
    chunks = [{"text": "A big fight", "start": 0.0, "end": 1.5},
              {"text": "Frank wins", "start": 1.5, "end": 3.0}]
    timings = [{"scene_id": 1, "start": 0.0, "end": 3.0}]
    narr = {"scenes": [{"scene_id": 1, "text": "A big fight Frank wins", "page_ref": 3,
                        "visual_beats": ["A big fight", "Frank wins"]}]}   # string beats = recap
    words = _words("A big fight Frank wins", 0.0, 3.0)

    without = shots._build_shots_per_chunk(narr, chunks, pages, timings, project=None)
    with_wt = shots._build_shots_per_chunk(narr, chunks, pages, timings,
                                           word_timestamps=words, project=None)

    def _fp(built):
        return [(s.panel_bbox, s.caption_text, round(s.duration_seconds, 4), s.fit_fill)
                for s in built]
    assert _fp(without) == _fp(with_wt)                  # old path untouched by word_timestamps
    assert len(without) == 2
    assert all(s.fit_fill is False for s in without)     # non-micro → no fill


# ── (d) aligner robustness: exact match normalizes punctuation; mismatch → even split ───
def test_align_exact_match_drops_stray_punctuation():
    frags = ["Hello there,", '"quote here."']
    words = [{"word": "Hello", "start": 0.0, "end": 0.5},
             {"word": "there", "start": 0.5, "end": 1.0},
             {"word": ",", "start": 1.0, "end": 1.05},           # stray punctuation-only token
             {"word": '"quote', "start": 1.1, "end": 1.6},
             {"word": 'here."', "start": 1.6, "end": 2.0}]
    assert shots._align_fragments_to_words(frags, words) == [(0.0, 1.0), (1.1, 2.0)]


def test_align_fallback_even_split_on_mismatch_no_raise():
    frags = ["alpha beta", "gamma delta"]
    words = [{"word": "totally", "start": 0.0, "end": 1.0},
             {"word": "different", "start": 1.0, "end": 2.0},
             {"word": "words", "start": 2.0, "end": 4.0}]
    spans = shots._align_fragments_to_words(frags, words)        # must not raise
    assert spans == [(0.0, 2.0), (2.0, 4.0)]                     # even split over [0.0, 4.0]


def test_align_empty_words_no_raise():
    assert shots._align_fragments_to_words(["a", "b"], []) == [(0.0, 0.0), (0.0, 0.0)]


# ── LỖI 1/2 (Master 2026-07-11, ComicCut autopsy): 1 beat = 1 panel = 1 CONTINUOUS shot ───────
# Micro fragments are already at clause granularity, so time-split (SHOT_MAX_SECONDS) must NOT
# re-chop a held fragment into duplicate-caption sub-shots; and two CONSECUTIVE fragments the
# writer pinned to the SAME panel must merge into one shot (no repeated panel back-to-back).
def _mpage(pn: int, n_panels: int) -> dict:
    """A story page with `n_panels` panels (index 0..n-1), each a small distinct crop."""
    return {pn: {"page_number": pn, "is_story_page": True, "source_image": f"/p{pn}.png",
                 "image_dimensions": {"width": 1000, "height": 1500},
                 "panels": [{"index": i, "bbox": {"x": 0, "y": i * 100, "w": 900, "h": 90},
                             "description": f"p{pn} panel {i}"} for i in range(n_panels)]}}


# ── (a) a long micro fragment is held as ONE shot even past the SHOT_MAX_SECONDS cap ──────────
def test_micro_fragment_not_time_split_even_over_cap(monkeypatch):
    monkeypatch.setattr(shots, "SHOT_MAX_SECONDS", 3.5)
    monkeypatch.setattr(shots, "SEAMLESS_LOOP", False)   # isolate from the loop-tail carve
    frags = [{"text": "the clown steps onto the gallows grinning wide", "page": 2, "panel": 1},
             {"text": "and he smashes a pie into the priest face", "page": 2, "panel": 4}]
    joined = " ".join(b["text"] for b in frags)
    narr = {"mode": "micro_moment",
            "scenes": [{"scene_id": 1, "text": joined, "page_ref": 2, "visual_beats": frags}]}
    chunks = [{"text": joined, "start": 0.0, "end": 8.0}]        # 2 frags → ~4s each (> 3.5 cap)
    timings = [{"scene_id": 1, "start": 0.0, "end": 8.0}]
    words = _words(joined, 0.0, 8.0)

    built = shots.build_shots(narr, scene_timings=timings, caption_chunks=chunks,
                              pages_by_number=_mpage(2, 6), word_timestamps=words, project=None)

    assert len(built) == 2                               # one shot per fragment, NOT re-chopped
    assert all(s.duration_seconds > 3.5 for s in built)  # each held past the cap → no time-split
    assert built[0].caption_text != built[1].caption_text


# ── (b) two CONSECUTIVE same-pin fragments merge into one held shot ───────────────────────────
def test_micro_consecutive_same_pin_fragments_merge(monkeypatch):
    monkeypatch.setattr(shots, "SHOT_MAX_SECONDS", 3.5)
    monkeypatch.setattr(shots, "SEAMLESS_LOOP", False)   # isolate from the loop-tail carve

    def _boom(*a, **k):
        raise AssertionError("_match_panels must NOT run — every beat is pinned")
    monkeypatch.setattr(shots, "_match_panels", _boom)

    frags = [{"text": "he lifts the blade over the priest", "page": 2, "panel": 1},
             {"text": "and drives it down with a laugh", "page": 2, "panel": 1}]   # SAME pin
    joined = " ".join(b["text"] for b in frags)
    narr = {"mode": "micro_moment",
            "scenes": [{"scene_id": 1, "text": joined, "page_ref": 2, "visual_beats": frags}]}
    chunks = [{"text": joined, "start": 0.0, "end": 6.0}]
    timings = [{"scene_id": 1, "start": 0.0, "end": 6.0}]
    words = _words(joined, 0.0, 6.0)

    built = shots.build_shots(narr, scene_timings=timings, caption_chunks=chunks,
                              pages_by_number=_mpage(2, 6), word_timestamps=words, project=None)

    assert len(built) == 1                               # merged, not two identical crops in a row
    assert built[0].caption_text == joined               # both clauses ride the one shot
    assert abs(built[0].duration_seconds - 6.0) < 1e-6   # durations summed (audio sync kept)
    assert built[0].panel_bbox["y"] == 100               # panel index 1 on p2


# ── (c) a NON-micro (recap) build STILL time-splits when the cap is set (old behavior kept) ───
def test_recap_still_time_splits_when_cap_set(monkeypatch):
    monkeypatch.setattr(shots, "SHOT_MAX_SECONDS", 3.5)
    panel_a = {"index": 0, "bbox": {"x": 0, "y": 0, "w": 700, "h": 900}, "_page_number": 3}
    monkeypatch.setattr(shots, "_match_panels",
                        lambda u, *a, **k: [(panel_a, "/pA.png")] * len(u))
    text = "a long held recap beat about a very big fight"
    chunks = [{"text": text, "start": 0.0, "end": 9.0}]
    timings = [{"scene_id": 1, "start": 0.0, "end": 9.0}]
    narr = {"scenes": [{"scene_id": 1, "text": text, "page_ref": 3,
                        "visual_beats": [text]}]}          # string beat = recap (no "mode")

    built = shots.build_shots(narr, scene_timings=timings, caption_chunks=chunks,
                              pages_by_number=_mpage(3, 2), word_timestamps=None, project=None)

    assert len(built) >= 3                               # 9s split into ≤3.5s fragments
    assert all(s.duration_seconds <= 3.5 + 1e-6 for s in built)
    assert abs(sum(s.duration_seconds for s in built) - 9.0) < 1e-3


# ── (d) joker-shape: 18 distinctly-pinned fragments → 18 held shots, no adjacent dup captions ─
def test_micro_joker_shape_18_fragments_18_shots_no_dup_captions(monkeypatch):
    monkeypatch.setattr(shots, "SHOT_MAX_SECONDS", 3.5)
    monkeypatch.setattr(shots, "SEAMLESS_LOOP", False)   # isolate from the loop-tail carve

    def _boom(*a, **k):
        raise AssertionError("_match_panels must NOT run — every beat is pinned")
    monkeypatch.setattr(shots, "_match_panels", _boom)

    scenes, chunks, timings, words, pages = [], [], [], [], {}
    gid = 0
    for i in range(3):                                   # 3 body scenes × 6 fragments = 18
        pn = 2 + i
        frags = [{"text": f"beat number {gid + j} shows the clown", "page": pn, "panel": j}
                 for j in range(6)]                      # distinct pins 0..5
        joined = " ".join(b["text"] for b in frags)
        t0, t1 = i * 24.0, (i + 1) * 24.0                # ~4s per fragment (> 3.5 cap)
        scenes.append({"scene_id": i + 1, "text": joined, "page_ref": pn, "visual_beats": frags})
        chunks.append({"text": joined, "start": t0, "end": t1})
        timings.append({"scene_id": i + 1, "start": t0, "end": t1})
        words.extend(_words(joined, t0, t1))
        pages.update(_mpage(pn, 6))
        gid += 6
    narr = {"mode": "micro_moment", "scenes": scenes}

    built = shots.build_shots(narr, scene_timings=timings, caption_chunks=chunks,
                              pages_by_number=pages, word_timestamps=words, project=None)

    assert len(built) == 18                              # one held shot per fragment, none split
    assert all(s.duration_seconds > 3.5 for s in built)  # each past the 3.5 cap → proves no split
    caps = [s.caption_text for s in built]
    assert all(caps[k] != caps[k + 1] for k in range(len(caps) - 1)), "adjacent duplicate caption"
    assert len(set(caps)) == 18                          # all 18 captions distinct
