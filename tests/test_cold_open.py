"""Cold-open (#1): pick a striking STORY panel for frame 1, not the cover — and never
the final pages (no ending spoiler). Frame-1 evidence (2026-07-03): largest-area alone
opened on a WIDE bubble-heavy dinner-table splash (Doom) and a LANDSCAPE strip that
letterboxed AND rendered MIRRORED (spider-man 'ONE I'M SLYDE' backwards). The scorer
now prefers a portrait, clean, character panel; the cold-open is never mirrored."""
from stages.stage_5 import shots
from stages.stage_5.shots import _build_shots_per_chunk, _cold_open_panel


def _panel(w, h):
    return {"bbox": {"x": 0, "y": 0, "w": w, "h": h}}


def _pg(panels, src="p.png"):
    return {"panels": panels, "source_image": src,
            "image_dimensions": {"width": 1000, "height": 1500}}


def test_cold_open_picks_largest_non_ending_panel():
    pages = {
        1: _pg([_panel(400, 400)]),
        2: _pg([_panel(900, 1400), _panel(200, 200)]),   # biggest eligible panel
        3: _pg([_panel(500, 500)]),
        4: _pg([_panel(999, 1499)]),   # last 2 = ending → excluded even though huge
        5: _pg([_panel(999, 1499)]),
    }
    panel, src = _cold_open_panel(pages)
    assert panel is not None
    assert panel["_page_number"] == 2 and panel["bbox"]["w"] == 900
    assert src == "p.png"


def test_cold_open_skips_cover_and_ending():
    pages = {
        1: {"panels": [_panel(999, 1499)], "page_type": "cover", "source_image": "c.png"},
        2: _pg([_panel(400, 400)]),
        3: _pg([_panel(800, 800)]),   # biggest of the eligible (2,3)
        4: _pg([_panel(999, 1499)]),  # ending excluded
        5: _pg([_panel(999, 1499)]),  # ending excluded
    }
    panel, _ = _cold_open_panel(pages)
    assert panel is not None and panel["_page_number"] == 3   # cover skipped, ending excluded


def test_cold_open_empty_returns_none():
    assert _cold_open_panel({}) == (None, "")


def test_cold_open_prefers_portrait_clean_over_wide_bubble_heavy():
    """Frame-1 defect #1: the scorer must pick a PORTRAIT clean-character panel over a
    LARGER wide panel crammed with speech bubbles (the Doom dinner-table opener)."""
    wide_bubble = {"bbox": {"x": 0, "y": 0, "w": 1400, "h": 650},   # landscape, LARGER area
                   "characters": ["Doom"],
                   "dialog": [{"text": f"line {i}"} for i in range(12)]}  # ~12 bubbles
    portrait_clean = {"bbox": {"x": 0, "y": 0, "w": 700, "h": 1200},  # portrait, smaller area
                      "characters": ["Hero"], "dialog": []}
    pages = {
        1: {"panels": [wide_bubble, portrait_clean], "source_image": "p.png",
            "image_dimensions": {"width": 1500, "height": 1500}},
        2: _pg([_panel(100, 100)]),
        3: _pg([_panel(100, 100)]),
        4: _pg([_panel(100, 100)]),   # ending → excluded
        5: _pg([_panel(100, 100)]),   # ending → excluded
    }
    panel, _ = _cold_open_panel(pages)
    assert panel is not None
    # portrait clean wins DESPITE the wide panel having the larger raw area
    assert panel["bbox"]["w"] == 700 and panel["bbox"]["h"] == 1200


def test_cold_open_penalizes_letterbox_landscape_even_when_clean():
    """Frame-1 letterbox guard: a LARGE clean landscape panel (character, NO dialogue) that
    _prepare_panel_frame would contain+blur into a thin band must LOSE to a smaller portrait
    panel — isolating the will_letterbox penalty from the dialogue confound."""
    wide_clean = {"bbox": {"x": 0, "y": 0, "w": 1400, "h": 500},   # aspect 2.8 → letterboxed
                  "characters": ["Hero"], "dialog": []}
    portrait = {"bbox": {"x": 0, "y": 0, "w": 700, "h": 1200},     # portrait, smaller area
                "characters": ["Hero"], "dialog": []}
    pages = {
        1: {"panels": [wide_clean, portrait], "source_image": "p.png",
            "image_dimensions": {"width": 1500, "height": 1500}},
        2: _pg([_panel(100, 100)]),
        3: _pg([_panel(100, 100)]),
        4: _pg([_panel(100, 100)]),   # ending → excluded
        5: _pg([_panel(100, 100)]),   # ending → excluded
    }
    panel, _ = _cold_open_panel(pages)
    assert panel is not None
    assert panel["bbox"]["w"] == 700 and panel["bbox"]["h"] == 1200   # portrait wins


def test_cold_open_penalizes_caption_dense_over_clean_single_subject():
    """RULE 3 (reads-instantly): a portrait panel packed with caption/dialogue boxes must LOSE to
    an identically-shaped clean single-subject panel — the caption-clutter penalty COLD_OPEN_W_DIALOG."""
    caption_dense = {"bbox": {"x": 0, "y": 0, "w": 700, "h": 1200},
                     "characters": ["Hero"], "dialog": [{"text": f"cap {i}"} for i in range(8)]}
    clean = {"bbox": {"x": 0, "y": 0, "w": 700, "h": 1200},
             "characters": ["Hero"], "dialog": []}
    pages = {
        1: {"panels": [caption_dense, clean], "source_image": "p.png",
            "image_dimensions": {"width": 1500, "height": 1500}},
        2: _pg([_panel(100, 100)]),
        3: _pg([_panel(100, 100)]),
        4: _pg([_panel(100, 100)]),   # ending → excluded
        5: _pg([_panel(100, 100)]),   # ending → excluded
    }
    panel, _ = _cold_open_panel(pages)
    assert panel is not None and panel["dialog"] == []   # clean single-subject wins


def test_cold_open_penalizes_crowded_establishing_over_single_subject():
    """RULE 3 (reads-instantly): a crowded panel (many named figures = no single readable subject,
    the busy-establishing-shot defect) loses to an identically-sized single-subject panel — the
    crowd penalty COLD_OPEN_W_CROWD."""
    crowd = {"bbox": {"x": 0, "y": 0, "w": 700, "h": 1200},
             "characters": [f"Char{i}" for i in range(7)], "dialog": []}
    single = {"bbox": {"x": 0, "y": 0, "w": 700, "h": 1200},
              "characters": ["Hero"], "dialog": []}
    pages = {
        1: {"panels": [crowd, single], "source_image": "p.png",
            "image_dimensions": {"width": 1500, "height": 1500}},
        2: _pg([_panel(100, 100)]),
        3: _pg([_panel(100, 100)]),
        4: _pg([_panel(100, 100)]),   # ending → excluded
        5: _pg([_panel(100, 100)]),   # ending → excluded
    }
    panel, _ = _cold_open_panel(pages)
    assert panel is not None and len(panel["characters"]) == 1   # single subject wins


def _intro_narration_inputs(intro_panel):
    """Minimal caption-chunk inputs with a single is_intro scene, plus a monkeypatch
    target that forces _match_panels to return `intro_panel` for the cold-open unit."""
    narration = {"scenes": [{"scene_id": 1, "is_intro": True, "text": "the hook"}]}
    caption_chunks = [{"text": "the hook", "start": 0.0, "end": 2.0}]
    scene_timings = [{"scene_id": 1, "start": 0.0, "end": 2.0}]
    pages = {1: {"panels": [intro_panel], "source_image": "s.png",
                 "image_dimensions": {"width": 1500, "height": 1500}}}
    return narration, caption_chunks, pages, scene_timings


def _lock_pages():
    """(page, panel) pool where the scorer would normally pick page 2's big portrait panel
    (see test_cold_open_picks_largest_non_ending_panel) — locking onto page 3's small panel
    proves the lock bypasses the scorer rather than just agreeing with it."""
    return {
        1: _pg([_panel(400, 400)]),
        2: _pg([_panel(900, 1400), _panel(200, 200)]),   # scorer's pick, index 0
        3: _pg([_panel(500, 500)]),                       # lock target: p3/0
        4: _pg([_panel(999, 1499)]),
        5: _pg([_panel(999, 1499)]),
    }


def test_cold_open_lock_env_honored(monkeypatch):
    """COLD_OPEN_LOCK env wins and bypasses the scorer entirely."""
    monkeypatch.setenv("COLD_OPEN_LOCK", "3,0")
    panel, src = _cold_open_panel(_lock_pages())
    assert panel is not None
    assert panel["_page_number"] == 3 and panel["index"] == 0
    assert src == "p.png"


def test_cold_open_lock_narration_field_honored():
    """No env → falls back to narration.json's optional `cold_open_lock` field."""
    narration = {"cold_open_lock": [3, 0]}
    panel, _ = _cold_open_panel(_lock_pages(), narration=narration)
    assert panel is not None
    assert panel["_page_number"] == 3 and panel["index"] == 0


def test_cold_open_lock_string_form_honored():
    """narration field also accepts the "page,panel" string form (same as the env var)."""
    narration = {"cold_open_lock": "3,0"}
    panel, _ = _cold_open_panel(_lock_pages(), narration=narration)
    assert panel is not None
    assert panel["_page_number"] == 3 and panel["index"] == 0


def test_cold_open_lock_invalid_falls_back_to_scorer():
    """A lock pointing at a panel that doesn't exist logs a warning and falls back to the
    normal scorer — same pick as test_cold_open_picks_largest_non_ending_panel."""
    narration = {"cold_open_lock": [99, 0]}
    panel, src = _cold_open_panel(_lock_pages(), narration=narration)
    assert panel is not None
    assert panel["_page_number"] == 2 and panel["bbox"]["w"] == 900   # scorer's pick, unchanged
    assert src == "p.png"


def test_cold_open_no_lock_unchanged():
    """No env, no narration field (or narration omitted entirely) → old behavior, byte-identical."""
    panel, src = _cold_open_panel(_lock_pages())
    assert panel is not None
    assert panel["_page_number"] == 2 and panel["bbox"]["w"] == 900
    panel2, src2 = _cold_open_panel(_lock_pages(), narration={})
    assert (panel2["_page_number"], panel2["bbox"]["w"]) == (panel["_page_number"], panel["bbox"]["w"])
    assert src2 == src


def test_cold_open_hard_gate_drops_tiny_even_when_it_scores_higher():
    """R2 tiny gate (HARD, not a penalty): a tiny clean portrait that OUT-SCORES a big panel on
    the soft scorer (small area, perfect aspect, a character, no bubbles) is still REMOVED from
    contention when a gate-passing bigger panel exists — proving the gate eliminates, not merely
    down-weights. Old soft scorer picked the tiny one (0.57 > 0.38); the gate flips it to big."""
    tiny_clean = {"bbox": {"x": 0, "y": 0, "w": 300, "h": 300},        # af 0.04 < 0.12 → gated out
                  "characters": ["Hero"], "dialog": []}
    big_ok = {"bbox": {"x": 0, "y": 0, "w": 1300, "h": 1000},          # af 0.58, aspect 1.3, clean
              "characters": ["Hero"], "dialog": [{"text": f"l{i}"} for i in range(8)]}
    pages = {
        1: {"panels": [tiny_clean, big_ok], "source_image": "p.png",
            "image_dimensions": {"width": 1500, "height": 1500}},
        2: _pg([_panel(100, 100)]),
        3: _pg([_panel(100, 100)]),
        4: _pg([_panel(100, 100)]),   # ending → excluded
        5: _pg([_panel(100, 100)]),   # ending → excluded
    }
    panel, _ = _cold_open_panel(pages)
    assert panel is not None
    assert panel["bbox"]["w"] == 1300 and panel["bbox"]["h"] == 1000   # tiny gated out, big wins


def test_cold_open_hard_gate_drops_letterbox_when_portrait_exists():
    """R2 letterbox gate: a large would-letterbox landscape is dropped in favor of any clean
    portrait, even a much smaller one at the tiny-gate floor."""
    wide_lb = {"bbox": {"x": 0, "y": 0, "w": 1400, "h": 700},          # aspect 2.0 → letterbox
               "characters": ["Hero"], "dialog": []}
    portrait_small = {"bbox": {"x": 0, "y": 0, "w": 480, "h": 900},    # af 0.19 (> 0.12), clean
                      "characters": ["Hero"], "dialog": []}
    pages = {
        1: {"panels": [wide_lb, portrait_small], "source_image": "p.png",
            "image_dimensions": {"width": 1500, "height": 1500}},
        2: _pg([_panel(100, 100)]),
        3: _pg([_panel(100, 100)]),
        4: _pg([_panel(100, 100)]),   # ending → excluded
        5: _pg([_panel(100, 100)]),   # ending → excluded
    }
    panel, _ = _cold_open_panel(pages)
    assert panel is not None
    assert panel["bbox"]["w"] == 480 and panel["bbox"]["h"] == 900     # letterbox dropped


def test_cold_open_gate_falls_back_when_no_candidate_passes():
    """R2 fallback: when EVERY opening panel would letterbox / is tiny, the gate finds no clean
    candidate and the OLD full-pool scorer runs unchanged — a panel is still returned (no crash,
    never None when panels exist)."""
    wide_a = {"bbox": {"x": 0, "y": 0, "w": 1400, "h": 600},   # aspect 2.33 → letterbox, bigger
              "characters": ["Hero"], "dialog": []}
    wide_b = {"bbox": {"x": 0, "y": 0, "w": 1200, "h": 600},   # aspect 2.0 → letterbox, smaller
              "characters": ["Hero"], "dialog": []}
    pages = {
        1: {"panels": [wide_a, wide_b], "source_image": "p.png",
            "image_dimensions": {"width": 1500, "height": 1500}},
        2: _pg([_panel(90, 90)]),     # tiny → gated
        3: _pg([_panel(90, 90)]),
        4: _pg([_panel(90, 90)]),     # ending → excluded
        5: _pg([_panel(90, 90)]),     # ending → excluded
    }
    panel, _ = _cold_open_panel(pages)
    assert panel is not None
    assert panel["bbox"]["w"] == 1400   # old largest-area fallback (no clean candidate)


def _money_pages():
    """Same pool as _lock_pages(): the scorer would pick page-2's big portrait (p2/0)."""
    return _lock_pages()


def test_cold_open_money_bind_wins_over_scorer(monkeypatch):
    """R2 money-bind: a Q&A project whose subject_panels.json carries a VLM-confirmed money panel
    (`force_intro`) opens on THAT panel, overriding the scorer's own pick (p2/0)."""
    import stages.subject_panels as sp
    monkeypatch.setattr(sp, "load_subject_panels",
                        lambda project: {"panels": [{"page": 3, "panel": 0, "force_intro": True, "money": True}]})
    panel, src = _cold_open_panel(_money_pages(), project="proj")
    assert panel is not None
    assert panel["_page_number"] == 3 and panel["index"] == 0   # money panel, not the scorer's p2/0


def test_cold_open_money_bind_off_without_project(monkeypatch):
    """No project → money-bind cannot fire (needs the project's subject_panels.json) → scorer pick."""
    import stages.subject_panels as sp
    monkeypatch.setattr(sp, "load_subject_panels",
                        lambda project: {"panels": [{"page": 3, "panel": 0, "force_intro": True}]})
    panel, _ = _cold_open_panel(_money_pages())   # no project passed
    assert panel is not None and panel["_page_number"] == 2   # scorer's pick, money-bind skipped


def test_cold_open_lock_beats_money_bind(monkeypatch):
    """R2 precedence: COLD_OPEN_LOCK (Master's hand-pin) wins even over a money-bind panel."""
    import stages.subject_panels as sp
    monkeypatch.setattr(sp, "load_subject_panels",
                        lambda project: {"panels": [{"page": 3, "panel": 0, "force_intro": True}]})
    monkeypatch.setenv("COLD_OPEN_LOCK", "2,0")
    panel, _ = _cold_open_panel(_money_pages(), project="proj")
    assert panel is not None
    assert panel["_page_number"] == 2 and panel["index"] == 0   # lock wins over money-bind


def test_qa_subject_sequence_gate_orders_clean_frame1_first(monkeypatch):
    """R2 Q&A intro gate (the batcave fix): _qa_subject_sequence returns clean subject panels before
    a would-letterbox one, so intro_panels[0] (frame 1) is never a wide blur strip — even when the
    letterbox panel is ranked FIRST in a manual subject_panels.json (batcave's p86/0, aspect 2.23)."""
    letterbox_first = {"bbox": {"x": 0, "y": 0, "w": 1920, "h": 860},   # aspect 2.23 → letterbox
                       "characters": ["Batman"], "dialog": []}
    portrait_second = {"bbox": {"x": 0, "y": 0, "w": 962, "h": 1578},   # clean portrait
                       "characters": ["Batman"], "dialog": []}
    pages = {
        10: {"panels": [letterbox_first], "source_image": "a.png",
             "image_dimensions": {"width": 1920, "height": 1478}},
        20: {"panels": [portrait_second], "source_image": "b.png",
             "image_dimensions": {"width": 1058, "height": 1600}},
    }
    pool = shots._panel_pool(pages)
    entry_by_key = {k: (p, s) for (k, p, s, _t) in pool}
    import stages.subject_panels as sp
    monkeypatch.setattr(sp, "load_subject_panels",
                        lambda project: {"subject": "Batman", "manual": True,
                                         "panels": [{"page": 10, "panel": 0, "score": 100},   # ranked first
                                                    {"page": 20, "panel": 0, "score": 5.0}]})
    seq = shots._qa_subject_sequence("proj", entry_by_key, exclude=set())
    assert seq, "subject sequence should not be empty"
    assert seq[0][0]["_page_number"] == 20   # clean portrait leads; letterbox p10 demoted to the tail


def test_cold_open_shot_is_never_mirrored(monkeypatch):
    """Frame-1 defect #2: the intro/cold-open shot must be un-mirrored UNCONDITIONALLY,
    even for a panel that would otherwise be flipped — a landscape dialogue strip with no
    critical-text hint (the spider-man 'ONE I'M SLYDE' opener that rendered backwards)."""
    intro_panel = {"bbox": {"x": 0, "y": 0, "w": 1400, "h": 500},   # landscape, has dialog
                   "description": "a wide action panel",             # no critical-text hint
                   "dialog": [{"text": "ONE I'M SLYDE"}]}
    # Sanity: nothing but is_intro should suppress the mirror for this panel.
    assert shots._panel_has_critical_text(intro_panel) is False
    assert not intro_panel.get("_whole_page")
    narration, chunks, pages, timings = _intro_narration_inputs(intro_panel)
    monkeypatch.setattr(shots, "_match_panels", lambda *a, **k: [(intro_panel, "s.png")])
    built = _build_shots_per_chunk(narration, chunks, pages, timings)
    assert built and built[0].is_intro is True
    assert built[0].no_mirror is True
