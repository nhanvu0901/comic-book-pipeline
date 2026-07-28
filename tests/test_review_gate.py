"""Review gate — state, gate policy, candidates export, lock→anchor-bind, stage-4 auto-force.

The matcher-touching tests reuse test_panel_match's pattern: stub embed_batch (no network),
turn OFF the VLM rerank + size tie-break, and mock _panel_content_score so every decision is
deterministic. The stage-4 test stubs the TTS synth so it never hits the live endpoint.
"""
import json
from types import SimpleNamespace

import pytest

import stages.review_gate as rg
import stages.stage_5.shots as shots
import stages._embedding as _embedding


# ─── helpers (mirrors test_panel_match) ──────────────────────────────────────

def _no_network_embed(monkeypatch):
    monkeypatch.setattr(_embedding, "embed_batch", lambda texts: [None] * len(texts))
    monkeypatch.setattr(shots, "PANEL_RERANK", False)
    monkeypatch.setattr(shots, "PANEL_SIZE_TIE_MARGIN", 0.0)


def _page(tags):
    return {10: {
        "source_image": "p10.png",
        "image_dimensions": {"width": 600, "height": 2700},
        "page_type": "story",
        "panels": [
            {"index": i, "bbox": {"x": 0, "y": 900 * i, "w": 600, "h": 900},
             "description": t, "characters": []}
            for i, t in enumerate(tags)
        ],
        "text_blocks": [],
    }}


def _fake_score(panel, panel_vec, chunk_vec, scene_vec, page_tb, *, chunk_text, scene_text):
    tag = str(panel.get("description", "")).strip().lower()
    return (10.0, 0.9) if tag and tag in (chunk_text or "").lower() else (0.5, 0.1)


# ─── deliverable 1: state + gate ─────────────────────────────────────────────

def test_gate_blocks_then_approves_then_stale(tmp_path, monkeypatch):
    monkeypatch.setattr(rg, "REVIEW_GATE", True)
    monkeypatch.setattr(rg, "PROJECTS_ROOT", tmp_path)
    proj = tmp_path / "c"
    proj.mkdir()
    nar = proj / "narration.json"
    nar.write_text(json.dumps({"scenes": [{"scene_id": 1, "text": "x"}]}))

    # no locks.json → not approved → blocked
    with pytest.raises(SystemExit):
        rg.ensure_reviewed("c")

    # approve, pinned to the current narration sha → passes
    st = rg.load_state("c")
    st["approved"] = True
    st["narration_sha1"] = rg.narration_sha1("c")
    rg.save_state("c", st)
    rg.ensure_reviewed("c")  # no raise

    # edit narration after approval → stale approval → blocked again
    nar.write_text(json.dumps({"scenes": [{"scene_id": 1, "text": "CHANGED"}]}))
    with pytest.raises(SystemExit):
        rg.ensure_reviewed("c")


def test_state_roundtrip_defaults(tmp_path, monkeypatch):
    monkeypatch.setattr(rg, "PROJECTS_ROOT", tmp_path)
    (tmp_path / "p").mkdir()
    st = rg.load_state("p")  # missing file → fully-shaped default
    assert st == {"approved": False, "approved_at": None, "narration_sha1": None, "locks": {}}
    st["approved"] = True
    st["locks"]["3"] = {"page": 10, "panel": 2, "source": "batcave"}
    rg.save_state("p", st)
    assert rg.load_state("p")["locks"]["3"]["panel"] == 2


# ─── skip-flag policy incl. answer_research force ────────────────────────────

def test_skip_flag_policy(tmp_path, monkeypatch):
    monkeypatch.setattr(rg, "REVIEW_GATE", True)
    monkeypatch.setattr(rg, "PROJECTS_ROOT", tmp_path)

    # HARD GATE all modes (Master 2026-07-14): --skip-review is accepted but IGNORED — an
    # unapproved project is blocked regardless of mode. Normal comic → NOW blocked too.
    a = tmp_path / "a"
    a.mkdir()
    (a / "narration.json").write_text("{}")
    with pytest.raises(SystemExit):
        rg.ensure_reviewed("a", skip_flag=True)

    # answer_research (Q&A) → still blocked.
    b = tmp_path / "b"
    b.mkdir()
    (b / "narration.json").write_text("{}")
    (b / "comic_context.json").write_text(json.dumps({"plot_source": "answer_research"}))
    with pytest.raises(SystemExit):
        rg.ensure_reviewed("b", skip_flag=True)

    # approving unblocks — skip_flag is now irrelevant either way.
    st = rg.load_state("a")
    st["approved"] = True
    rg.save_state("a", st)
    rg.ensure_reviewed("a", skip_flag=True)   # no raise


def test_gate_off_env(tmp_path, monkeypatch):
    monkeypatch.setattr(rg, "REVIEW_GATE", False)
    monkeypatch.setattr(rg, "PROJECTS_ROOT", tmp_path)
    (tmp_path / "z").mkdir()
    rg.ensure_reviewed("z")  # gate off → never blocks


# ─── deliverable 2: candidates.json schema ───────────────────────────────────

def test_build_candidates_schema(tmp_path, monkeypatch):
    monkeypatch.setattr(rg, "PROJECTS_ROOT", tmp_path)
    proj = tmp_path / "cand"
    proj.mkdir()
    (proj / "narration.json").write_text(json.dumps({"scenes": [
        {"scene_id": 1, "text": "hook", "is_intro": True},
        {"scene_id": 2, "text": "a splash panel", "page_ref": 10, "panel_ref": 0},
    ]}))

    # stub the matcher's ranked shortlist (no embeddings) + the thumb writer (no image file)
    def fake_match(units, pages, cluster, *, project=None, candidates_out=None, candidates_k=12):
        for _ in units:
            candidates_out.append([
                {"page": 10, "panel_idx": 0, "score": 9.5, "cosine": 0.83,
                 "panel": {"description": "a splash", "dialog": [{"ocr": "BOOM"}],
                           "bbox": {"x": 0, "y": 0, "w": 10, "h": 10}}, "src": "p10.png"},
            ])
        return []

    monkeypatch.setattr(shots, "_match_panels", fake_match)
    monkeypatch.setattr(rg, "_write_thumb", lambda *a, **k: True)

    out_path = rg.build_candidates("cand", k=5)
    data = json.loads(out_path.read_text())
    assert "generated_at" in data and isinstance(data["beats"], list)
    # HARD GATE all modes: recap now emits an INTRO row (cold-open reviewable) + the story beat.
    assert len(data["beats"]) == 2
    assert {b["unit"] for b in data["beats"]} == {"intro", "scene"}
    beat = next(b for b in data["beats"] if b["unit"] == "scene")
    assert set(beat) == {"scene_id", "narration_text", "page_ref", "panel_ref", "source",
                         "candidates", "beat_key", "pre_selected", "unit"}
    assert beat["scene_id"] == 2 and beat["page_ref"] == 10 and beat["panel_ref"] == 0
    assert beat["beat_key"] == "2" and beat["pre_selected"] == [{"page": 10, "panel": 0}]
    assert set(beat["source"]) == {"title", "issue", "url", "drawable_moment",
                                   "verified", "verify_note", "research_urls"}
    cand = beat["candidates"][0]
    assert set(cand) == {"page", "panel", "score", "thumb", "desc", "dialog"}
    assert cand["thumb"] == "review/thumbs/p010_0.jpg"
    assert cand["dialog"] == "BOOM"
    intro = next(b for b in data["beats"] if b["unit"] == "intro")
    assert intro["beat_key"] == "intro" and intro["narration_text"] == "hook"


def test_build_candidates_all_emits_full_ranked_pool(tmp_path, monkeypatch):
    """k<=0 → ALL panels of the beat's issue (>10), ranked score-descending — not capped at 10."""
    monkeypatch.setattr(rg, "PROJECTS_ROOT", tmp_path)
    proj = tmp_path / "all"
    proj.mkdir()
    (proj / "narration.json").write_text(json.dumps({"scenes": [
        {"scene_id": 2, "text": "a beat", "page_ref": 10, "panel_ref": 0},
    ]}))

    POOL = 15  # simulate an issue with 15 story panels (>10) for this beat

    # Honour candidates_k the way the real matcher does: top-candidates_k, score-descending.
    def fake_match(units, pages, cluster, *, project=None, candidates_out=None, candidates_k=12):
        take = min(candidates_k, POOL)
        for _ in units:
            candidates_out.append([
                {"page": 10, "panel_idx": i, "score": float(POOL - i), "cosine": 0.5,
                 "panel": {"description": f"panel {i}", "bbox": {"x": 0, "y": 0, "w": 10, "h": 10}},
                 "src": "p10.png"}
                for i in range(take)
            ])
        return []

    monkeypatch.setattr(shots, "_match_panels", fake_match)
    monkeypatch.setattr(rg, "_write_thumb", lambda *a, **k: True)

    cands = json.loads(rg.build_candidates("all", k=0).read_text())["beats"][0]["candidates"]
    assert len(cands) == POOL > 10, "k<=0 must emit ALL panels, not the old top-10 cap"
    scores = [c["score"] for c in cands]
    assert scores == sorted(scores, reverse=True), "candidates must be score-sorted descending"


# ─── FIX A: Q&A match query blends drawable_moment ───────────────────────────

def _capture_query_project(tmp_path, monkeypatch, *, qa: bool):
    """Build a 1-beat project (Q&A or recap) and return the query text build_candidates
    hands to the matcher for that beat."""
    monkeypatch.setattr(rg, "PROJECTS_ROOT", tmp_path)
    proj = tmp_path / ("qa" if qa else "recap")
    proj.mkdir()
    (proj / "narration.json").write_text(json.dumps({"scenes": [
        {"scene_id": 2, "text": "Frank felt zero remorse.", "page_ref": 10, "panel_ref": 0,
         "source_image": "ch01_page_03.jpg"},
    ]}))
    if qa:
        (proj / "comic_context.json").write_text(json.dumps({"plot_source": "answer_research"}))
        (proj / "answer_context.json").write_text(json.dumps({"items": [
            {"source_comic": "Thunderbolts", "source_year": "2014", "reader_url": "u",
             "drawable_moment": "Frank on his knees glaring up at a recoiling Ghost Rider",
             "verification_note": ""},
        ]}))

    seen: dict = {}

    def fake_match(units, pages, cluster, *, project=None, candidates_out=None, candidates_k=12):
        seen["queries"] = [t for _s, t in units]
        for _ in units:
            candidates_out.append([])
        return []

    monkeypatch.setattr(shots, "_match_panels", fake_match)
    monkeypatch.setattr(rg, "_write_thumb", lambda *a, **k: True)
    rg.build_candidates(proj.name, k=5)
    return seen["queries"][0]


def test_qa_query_blends_drawable_moment(tmp_path, monkeypatch):
    """A Q&A beat's match query LEADS with the NARRATION (what the audience hears, weighted
    by QA_NARR_WEIGHT repeats) and trails the drawable_moment to sharpen it (Master 2026-07-07:
    match the spoken beat, not the flashiest panel). A recap beat's query is narration UNCHANGED."""
    q = _capture_query_project(tmp_path, monkeypatch, qa=True)
    assert "Ghost Rider" in q and "glaring up" in q          # drawable_moment still reached the query
    assert "Frank felt zero remorse" in q                    # narration present
    assert q.startswith("Frank felt zero remorse")           # narration LEADS now
    # narration repeated ahead of the drawable_moment (up-weighted)
    assert q.count("Frank felt zero remorse") >= 2


def test_recap_query_is_narration_unchanged(tmp_path, monkeypatch):
    q = _capture_query_project(tmp_path, monkeypatch, qa=False)
    assert q == "Frank felt zero remorse."                   # no drawable_moment → narration only


# ─── FIX: Q&A build_candidates zeroes the page-anchored prior ────────────────

def test_qa_zeros_fwd_bias_recap_keeps_it_and_restores(tmp_path, monkeypatch):
    """A Q&A beat's page_ref is the issue's FIRST page (drawable_moment has no page anchor of
    its own), not the page the moment happens on — so PANEL_FWD_BIAS's page-anchored prior in
    _match_panels would drag rank toward the issue opener/montage page. build_candidates must
    zero it for the Q&A matcher calls only; a recap build keeps the real value; both restore
    the module global afterward regardless of which path ran last."""
    monkeypatch.setattr(rg, "PROJECTS_ROOT", tmp_path)
    orig_bias = shots.PANEL_FWD_BIAS
    assert orig_bias != 0.0, "test needs a non-zero default to prove the override took effect"
    seen: dict = {}

    def fake_match(units, pages, cluster, *, project=None, candidates_out=None, candidates_k=12):
        seen["bias_during_call"] = shots.PANEL_FWD_BIAS
        for _ in units:
            candidates_out.append([])
        return []

    monkeypatch.setattr(shots, "_match_panels", fake_match)
    monkeypatch.setattr(rg, "_write_thumb", lambda *a, **k: True)

    qa = tmp_path / "qa_bias"
    qa.mkdir()
    (qa / "narration.json").write_text(json.dumps({"scenes": [
        {"scene_id": 2, "text": "beat", "page_ref": 3, "panel_ref": 0},
    ]}))
    (qa / "comic_context.json").write_text(json.dumps({"plot_source": "answer_research"}))
    (qa / "answer_context.json").write_text(json.dumps({"items": [
        {"source_comic": "X", "source_year": "2020", "reader_url": "u",
         "drawable_moment": "something", "verification_note": ""},
    ]}))
    rg.build_candidates("qa_bias", k=5)
    assert seen["bias_during_call"] == 0.0            # zeroed for Q&A
    assert shots.PANEL_FWD_BIAS == orig_bias          # restored after

    recap = tmp_path / "recap_bias"
    recap.mkdir()
    (recap / "narration.json").write_text(json.dumps({"scenes": [
        {"scene_id": 2, "text": "beat", "page_ref": 3, "panel_ref": 0},
    ]}))
    rg.build_candidates("recap_bias", k=5)
    assert seen["bias_during_call"] == orig_bias      # recap keeps the real prior
    assert shots.PANEL_FWD_BIAS == orig_bias          # still restored


# ─── deliverable 3: lock override reaches PANEL_ANCHOR_BIND ───────────────────

def test_lock_override_reaches_anchor_bind(tmp_path, monkeypatch):
    _no_network_embed(monkeypatch)
    monkeypatch.setattr(shots, "_panel_content_score", _fake_score)
    monkeypatch.setattr(shots, "PANEL_FWD_BIAS", 0.0)
    monkeypatch.setattr(rg, "PROJECTS_ROOT", tmp_path)

    proj = tmp_path / "p"
    proj.mkdir()
    # lock scene 1 → page 10, panel 2 (the content winner would be panel 0, "alpha")
    rg.save_state("p", {"approved": True,
                        "locks": {"1": {"page": 10, "panel": 2, "source": "batcave"}}})

    pages = _page(["alpha", "beta", "gamma"])
    narration = {"scenes": [{"scene_id": 1, "text": "alpha here"}]}
    shots._apply_review_locks(narration, "p")
    scene = narration["scenes"][0]
    assert scene["page_ref"] == 10 and scene["panel_ref"] == 2  # override applied

    out = shots._match_panels([(scene, "alpha here")], pages, {})
    panel, _src = out[0]
    assert panel["index"] == 2, "lock must bind panel 2, overriding the content winner (0)"


# ─── deliverable 4: stage-4 auto-force on narration hash change ───────────────

def _prime_stage4(tmp_path, monkeypatch, sidecar_hash):
    import stages.stage_4.pipeline as s4
    # These tests exercise Stage-4 TTS caching, not the review gate — turn the (now hard) gate
    # off so an unapproved fixture project doesn't block before the logic under test runs.
    monkeypatch.setattr(rg, "REVIEW_GATE", False)
    monkeypatch.setattr(s4, "PROJECTS_ROOT", tmp_path)
    monkeypatch.setattr(s4, "TTS_PROVIDER", "cartesia")
    proj = tmp_path / "proj"
    proj.mkdir()
    scenes = [{"scene_id": 1, "text": "Hello world."}]
    (proj / "narration.json").write_text(json.dumps({"scenes": scenes}))
    (proj / "audio.wav").write_bytes(b"RIFFcached")
    (proj / "word_timestamps.json").write_text(json.dumps([{"word": "Hello", "start": 0.0, "end": 0.5}]))
    (proj / "narration.tts.sha256").write_text(sidecar_hash)

    calls = {"n": 0}

    def fake_synth(text, **kw):
        calls["n"] += 1
        return SimpleNamespace(wav_bytes=b"RIFFnew",
                               word_timestamps=[{"word": "Hello", "start": 0.0, "end": 0.5}])

    import stages.stage_4.cartesia_tts as cart
    monkeypatch.setattr(cart, "synthesize", fake_synth)
    monkeypatch.setattr(s4, "_wav_duration", lambda p: 5.0)
    monkeypatch.setattr(s4, "align_scenes_to_words", lambda s, w: [])
    monkeypatch.setattr(s4, "build_caption_chunks", lambda s, w: [])
    return s4, proj, scenes, calls


def test_stage4_auto_force_on_hash_change(tmp_path, monkeypatch):
    s4, proj, scenes, calls = _prime_stage4(tmp_path, monkeypatch, sidecar_hash="deadbeef_stale")
    # skip_review + non-answer project → gate off; stale sidecar → auto-regenerate
    s4.synthesize_project("proj", post_atempo=1.0, skip_review=True)
    assert calls["n"] == 1, "stale narration hash must trigger auto-regeneration"
    assert (proj / "narration.tts.sha256").read_text().strip() == s4.narration_hash(scenes)


def test_stage4_reuses_when_hash_matches(tmp_path, monkeypatch):
    import stages.stage_4.pipeline as s4pre
    good = s4pre.narration_hash([{"scene_id": 1, "text": "Hello world."}])
    s4, proj, scenes, calls = _prime_stage4(tmp_path, monkeypatch, sidecar_hash=good)
    s4.synthesize_project("proj", post_atempo=1.0, skip_review=True)
    assert calls["n"] == 0, "matching hash must reuse cached audio (no re-synth)"


if __name__ == "__main__":
    import sys
    sys.exit(pytest.main([__file__, "-q"]))


# ─── Candidate-ranking layer: dialog channel + vision judge ──────────────────

def _mk_cand(page, pidx, score, dialog=""):
    panel = {"bbox": {"x": 0, "y": 0, "w": 10, "h": 10}, "description": "d"}
    if dialog:
        panel["dialog"] = [{"text": dialog}]
    return {"page": page, "panel_idx": pidx, "panel": panel, "src": f"p{page}.jpg",
            "score": score}


def test_dialog_rescore_semantic_and_quote(monkeypatch):
    import stages.review_gate as rg
    # fake embeddings: query ↔ "resurrect" dialog aligned, other dialog orthogonal
    def fake_embed(texts):
        out = []
        for t in texts:
            if "resurrect" in t or "restore the child" in t:
                out.append([1.0, 0.0])
            elif t.strip():
                out.append([0.0, 1.0])
            else:
                out.append(None)
        return out
    import stages._embedding as emb
    monkeypatch.setattr(emb, "embed_batch", fake_embed)
    cands = [_mk_cand(1, 0, 0.50),                                   # no dialog, was top
             _mk_cand(2, 0, 0.48, dialog="I can restore the child to life"),
             _mk_cand(3, 0, 0.47, dialog="Kneel before Doom")]
    out = rg._dialog_rescore(cands, "Doom offers to resurrect her daughter", {})
    assert out[0]["page"] == 2        # semantic dialog match overtakes silent panel
    # quoted verbatim span → hard bonus beats everything
    cands = [_mk_cand(1, 0, 0.90),
             _mk_cand(2, 0, 0.10, dialog="LIVE, SCOTT.")]
    out = rg._dialog_rescore(cands, 'she whispers "Live, Scott" at his grave', {})
    assert out[0]["page"] == 2 and out[0]["score"] > 0.6


def test_dialog_rescore_embed_failure_is_noop(monkeypatch):
    import stages.review_gate as rg
    import stages._embedding as emb
    monkeypatch.setattr(emb, "embed_batch", lambda t: (_ for _ in ()).throw(RuntimeError("down")))
    cands = [_mk_cand(1, 0, 0.5), _mk_cand(2, 0, 0.4, dialog="hi there")]
    out = rg._dialog_rescore(cands, "query", {})
    assert [c["page"] for c in out] == [1, 2]     # unchanged order


def test_vlm_rank_top_reorders_and_survives_failure(monkeypatch, tmp_path):
    import stages.review_gate as rg
    import stages._claude_sdk as sdk
    (tmp_path / "review" / "thumbs").mkdir(parents=True)
    monkeypatch.setattr(rg, "_write_thumb", lambda src, bbox, out: (out.write_bytes(b"x") or True))
    monkeypatch.setattr(sdk, "sdk_available", lambda: True)
    monkeypatch.setattr(sdk, "sdk_complete_vision",
                        lambda s, u, log=None: '{"scores": [2, 9, 5]}')
    cands = [_mk_cand(1, 0, 0.9), _mk_cand(2, 0, 0.8), _mk_cand(3, 0, 0.7),
             _mk_cand(4, 0, 0.6)]
    monkeypatch.setattr(rg, "QA_CAND_VLM_K", 3)
    out = rg._vlm_rank_top(list(cands), "the moment", tmp_path, log=lambda m: None)
    assert [c["page"] for c in out] == [2, 3, 1, 4]   # judged slice reordered, tail intact
    # judge failure → order preserved
    monkeypatch.setattr(sdk, "sdk_complete_vision", lambda s, u, log=None: None)
    out2 = rg._vlm_rank_top(list(cands), "the moment", tmp_path, log=lambda m: None)
    assert [c["page"] for c in out2] == [1, 2, 3, 4]


def test_moment_present_flag_when_all_vision_low(monkeypatch, tmp_path):
    """Vision judge ran but best score < floor → beat.source gets moment_warn
    (the CC #9-vs-#8 wrong-issue class the resolver can't catch)."""
    import stages.review_gate as rg
    monkeypatch.setattr(rg, "PROJECTS_ROOT", tmp_path)
    proj = tmp_path / "qa"; proj.mkdir()
    (proj / "narration.json").write_text(json.dumps({"scenes": [
        {"scene_id": 2, "text": "beat", "page_ref": 10, "panel_ref": 0}]}))
    (proj / "comic_context.json").write_text(json.dumps({"plot_source": "answer_research"}))
    (proj / "answer_context.json").write_text(json.dumps({"items": [
        {"rank": 1, "source_comic": "Wrong Issue #9", "reader_url": "u", "drawable_moment": "x"}]}))

    def fake_match(units, pages, cluster, *, project=None, candidates_out=None, candidates_k=12):
        for _ in units:
            candidates_out.append([{"page": 10, "panel_idx": 0, "score": 5.0,
                "panel": {"description": "d", "bbox": {"x": 0, "y": 0, "w": 10, "h": 10}},
                "src": "p10.png", "_vlm": 2.0}])   # judged low
        return []
    monkeypatch.setattr(shots, "_match_panels", fake_match)
    monkeypatch.setattr(rg, "_write_thumb", lambda *a, **k: True)
    # neutralise the ranking helpers (their own network); keep the injected _vlm
    monkeypatch.setattr(rg, "_dialog_rescore", lambda cl, q, pg: cl)
    monkeypatch.setattr(rg, "_vlm_rank_top", lambda cl, q, root, log=print: cl)
    monkeypatch.setattr(rg, "_issue_of", lambda s: "", raising=False)

    data = json.loads(rg.build_candidates("qa", k=5).read_text())
    assert "moment_warn" in data["beats"][0]["source"]


# ─── INTRO + OUTRO review rows, every mode (Master 2026-07-24) ───────────────────

def _bookend_project(tmp_path, monkeypatch, slug: str, *, qa: bool, mode: str = ""):
    """1 intro + 1 body + 1 outro narration; Q&A variant also gets 2 answer items so the
    bookend citation borrow (intro → item 1, outro → last body beat's item) is exercised."""
    monkeypatch.setattr(rg, "PROJECTS_ROOT", tmp_path)
    proj = tmp_path / slug
    proj.mkdir()
    (proj / "narration.json").write_text(json.dumps({"mode": mode, "scenes": [
        {"scene_id": 1, "text": "hook line", "is_intro": True, "page_ref": 10, "panel_ref": -1},
        {"scene_id": 2, "text": "body line", "page_ref": 10, "panel_ref": 0},
        {"scene_id": 3, "text": "closing line", "is_outro": True, "page_ref": 10, "panel_ref": 1},
    ]}))
    if qa:
        (proj / "comic_context.json").write_text(json.dumps({"plot_source": "answer_research"}))
        (proj / "answer_context.json").write_text(json.dumps({"items": [
            {"source_comic": "First Item #1", "source_year": "2020", "reader_url": "u1",
             "drawable_moment": "dm1"},
            {"source_comic": "Second Item #2", "source_year": "2021", "reader_url": "u2",
             "drawable_moment": "dm2"},
        ]}))

    def fake_match(units, pages, cluster, *, project=None, candidates_out=None, candidates_k=12):
        for _ in units:
            candidates_out.append([
                {"page": 10, "panel_idx": 0, "score": 1.0, "cosine": 0.5,
                 "panel": {"description": "d", "bbox": {"x": 0, "y": 0, "w": 10, "h": 10}},
                 "src": "p10.png"}])
        return []

    monkeypatch.setattr(shots, "_match_panels", fake_match)
    monkeypatch.setattr(rg, "_write_thumb", lambda *a, **k: True)
    monkeypatch.setattr(rg, "_dialog_rescore", lambda cl, q, pg: cl)
    monkeypatch.setattr(rg, "_vlm_rank_top", lambda cl, q, root, log=print: cl)
    return json.loads(rg.build_candidates(slug, k=5).read_text())["beats"]


@pytest.mark.parametrize("qa,mode", [(True, "explore_answer"), (False, "micro_moment"),
                                     (False, "")])
def test_bookend_rows_every_mode_in_video_order(tmp_path, monkeypatch, qa, mode):
    """Q&A used to emit NO intro row and NO mode emitted an outro row — Master could not hand-pick
    frame 1 / the last frame. Both rows now exist for every mode, first and LAST (video order),
    single-select, with candidates and the scene's own anchor pre-selected."""
    beats = _bookend_project(tmp_path, monkeypatch, f"bk_{qa}_{mode or 'recap'}", qa=qa, mode=mode)
    assert [b["beat_key"] for b in beats] == ["intro", "2", "outro"]
    intro, outro = beats[0], beats[-1]
    assert (intro["unit"], outro["unit"]) == ("intro", "outro")
    assert intro["narration_text"] == "hook line" and outro["narration_text"] == "closing line"
    assert intro["candidates"] and outro["candidates"]
    assert intro["pre_selected"] == []                            # panel_ref -1 → nothing anchored
    assert outro["pre_selected"] == [{"page": 10, "panel": 1}]     # scene's own anchor


def test_intro_row_prefers_cold_open_lock_over_scene_anchor(tmp_path, monkeypatch):
    monkeypatch.setattr(rg, "PROJECTS_ROOT", tmp_path)
    proj = tmp_path / "cok"
    proj.mkdir()
    (proj / "narration.json").write_text(json.dumps({"cold_open_lock": "7,3", "scenes": [
        {"scene_id": 1, "text": "hook", "is_intro": True, "page_ref": 10, "panel_ref": 0},
        {"scene_id": 2, "text": "body", "page_ref": 10, "panel_ref": 0}]}))
    monkeypatch.setattr(shots, "_match_panels",
                        lambda units, *a, candidates_out=None, **k:
                        [candidates_out.append([]) for _ in units] and [])
    beats = json.loads(rg.build_candidates("cok", k=5).read_text())["beats"]
    assert beats[0]["pre_selected"] == [{"page": 7, "panel": 3}]


def test_qa_bookends_borrow_first_and_last_item_citation(tmp_path, monkeypatch):
    """A bookend has no answer item of its own; resolving it directly fell through to the
    whole-comic citation. It cites the FIRST (intro) / LAST (outro) body beat's item instead.
    Two issues → the real per-issue map decides, so this also covers the multi-issue Q&A."""
    monkeypatch.setattr(rg, "PROJECTS_ROOT", tmp_path)
    proj = tmp_path / "qa_cite"
    (proj / "preprocessed").mkdir(parents=True)
    for pn, issue in ((10, "#1"), (20, "#2")):
        (proj / "preprocessed" / f"page_{pn:03d}.json").write_text(json.dumps({
            "page_number": pn, "issue_label": issue, "source_image": f"p{pn}.png",
            "page_type": "story", "image_dimensions": {"width": 600, "height": 900},
            "panels": [{"index": 0, "bbox": {"x": 0, "y": 0, "w": 600, "h": 900},
                        "description": "d", "characters": []}], "text_blocks": []}))
    (proj / "narration.json").write_text(json.dumps({"mode": "explore_answer", "scenes": [
        {"scene_id": 1, "text": "hook", "is_intro": True, "page_ref": 10, "panel_ref": -1},
        {"scene_id": 2, "text": "item one", "page_ref": 10, "panel_ref": 0},
        {"scene_id": 3, "text": "item two", "page_ref": 20, "panel_ref": 0},
        {"scene_id": 4, "text": "closing", "is_outro": True, "page_ref": 20, "panel_ref": 0},
    ]}))
    (proj / "comic_context.json").write_text(json.dumps({"plot_source": "answer_research"}))
    (proj / "answer_context.json").write_text(json.dumps({"items": [
        {"source_comic": "First Item #1", "source_year": "2020", "reader_url": "u1",
         "drawable_moment": "dm1"},
        {"source_comic": "Second Item #2", "source_year": "2021", "reader_url": "u2",
         "drawable_moment": "dm2"}]}))
    monkeypatch.setattr(shots, "_match_panels",
                        lambda units, *a, candidates_out=None, **k:
                        [candidates_out.append([]) for _ in units] and [])
    monkeypatch.setattr(rg, "_dialog_rescore", lambda cl, q, pg: cl)
    monkeypatch.setattr(rg, "_vlm_rank_top", lambda cl, q, root, log=print: cl)

    beats = json.loads(rg.build_candidates("qa_cite", k=5).read_text())["beats"]
    assert [b["beat_key"] for b in beats] == ["intro", "2", "3", "outro"]
    assert beats[0]["source"]["title"] == "First Item #1"      # intro ← first body beat's item
    assert beats[-1]["source"]["title"] == "Second Item #2"    # outro ← last body beat's item


# ─── custom-image lock (v3 additive shape) ───────────────────────────────────────

def test_lock_custom_image_reads_v3_shape_only():
    assert rg.lock_custom_image({"custom_image": "review/custom/x.jpg"}) == "review/custom/x.jpg"
    assert rg.lock_custom_image(None) is None
    assert rg.lock_custom_image({}) is None
    assert rg.lock_custom_image({"custom_image": ""}) is None
    # v1/v2 page-panel shapes are NOT a custom-image lock.
    assert rg.lock_custom_image({"page": 1, "panel": 0}) is None
    assert rg.lock_custom_image({"panels": [{"page": 1, "panel": 0}]}) is None


def test_lock_panels_is_noop_for_custom_image_shape():
    """lock_panels() must return [] for a v3 custom-image lock — every existing page/panel
    reader (_apply_review_locks, _review_locks's any() gate, ...) has to no-op on it, not
    crash on a missing "page" key."""
    assert rg.lock_panels({"custom_image": "review/custom/x.jpg"}) == []
