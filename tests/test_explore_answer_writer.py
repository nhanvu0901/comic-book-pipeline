"""write_explore_answer: no network — the LLM writer call and the outro/loop-
tease LLM helpers (reused from write_script.py) are monkeypatched. Verifies the
full contract from EXPLORE_ANSWER_DESIGN.md: 1:1 beat anchoring, deterministic
hook/banner, entity-named scenes, word band, and that write_script() (the
dispatch point) actually routes mode="explore_answer" here."""
import json
import shutil

import pytest

import stages.stage_3.explore_answer as ea
import stages.stage_3.write_script as ws
from config import PROJECTS_ROOT

_PROJECT = "_test_explore_answer_project"

_ANSWER_CONTEXT = {
    "items": [
        {"entity": "Wolverine", "how_or_why": "His healing factor closed every wound instantly.",
         "source_comic": "X-Men Legacy #1", "drawable_moment": "Wolverine walking through fire", "chapter_index": 1},
        {"entity": "Deadpool", "how_or_why": "His body regenerated faster than the stare could work.",
         "source_comic": "Deadpool #2", "drawable_moment": "Deadpool shrugging", "chapter_index": 2},
        {"entity": "Thanos", "how_or_why": "He simply endured the guilt without flinching.",
         "source_comic": "Thanos #3", "drawable_moment": "Thanos staring back", "chapter_index": 3},
    ]
}


def _page(page_number, chapter, page_in_chapter):
    return {
        "page_number": page_number,
        "source_image": f"ch{chapter:02d}_page{page_in_chapter:02d}.png",
        "is_story_page": True,
    }


def _fake_writer_call(*, system, user, models=None, max_tokens=2000, progress=None,
                      label="llm", validator=None):
    # _SCENES_PER_ITEM scenes per item: CONTEXT then MOMENT (2026-07-28). The old fixture
    # emitted one per item and now fails the module's own count gate — which is the gate
    # working, not the fixture being unlucky.
    raw = json.dumps({"scenes": [
        {"text": "Wolverine is the mutant whose healing factor never stops.",
         "connective": None, "beat_id": 1},
        {"text": "Wolverine healed through it in X-Men Legacy #1.", "connective": None, "beat_id": 1},
        {"text": "Deadpool is the mercenary who cannot stay dead.", "connective": None, "beat_id": 2},
        {"text": "Deadpool outran it entirely in Deadpool #2.", "connective": None, "beat_id": 2},
        {"text": "Thanos is the Titan who courts Death herself.", "connective": None, "beat_id": 3},
        {"text": "Thanos just endured it in Thanos #3.", "connective": None, "beat_id": 3},
    ]})
    if validator is not None:
        assert validator(raw), "fixture scenes must pass the module's own coarse validator"
    return raw, "fake-writer-model"


def _raising_call(*args, **kwargs):
    raise RuntimeError("no network in tests")


@pytest.fixture
def project_dir():
    root = PROJECTS_ROOT / _PROJECT
    root.mkdir(parents=True, exist_ok=True)
    (root / "answer_context.json").write_text(json.dumps(_ANSWER_CONTEXT))
    yield root
    shutil.rmtree(root, ignore_errors=True)


def test_write_explore_answer_end_to_end(monkeypatch, project_dir):
    # (explore_answer sizes its own budget from _EXP_* constants — recap's word band
    # is not involved, so there is nothing to shrink here.)
    monkeypatch.setattr(ea, "call_with_chain", _fake_writer_call)
    # generate_outro / generate_loop_tease live in write_script.py and call ITS
    # call_with_chain binding — patch that one too so no real LLM/network call
    # happens; both helpers already fall back to "" on any exception, which
    # keeps the outro on the deterministic factual-credit line regardless of
    # the 50/50 coin flip, making the assertion below deterministic.
    monkeypatch.setattr(ws, "call_with_chain", _raising_call)

    comic_context = {"title": "Who has survived Ghost Rider's Penance Stare?", "is_arc": True}
    story_pages = [
        _page(1, 1, 1), _page(2, 1, 2),
        _page(3, 2, 1), _page(4, 2, 2),
        _page(5, 3, 1), _page(6, 3, 2),
    ]
    debug_dump = {"project": _PROJECT}

    nar = ws.write_script(comic_context, story_pages, "explore_answer", debug_dump=debug_dump)

    # dispatch actually routed to explore_answer (not the narrate-mode path)
    assert nar.mode == "explore_answer"
    assert nar.banner_title == comic_context["title"]
    assert nar.title == comic_context["title"]

    # hook + (3 items x _SCENES_PER_ITEM) + outro
    assert len(nar.scenes) == 2 + 3 * ea._SCENES_PER_ITEM
    assert nar.scenes[0].is_intro
    assert nar.scenes[-1].is_outro
    # Q&A outro is meaning-first only — never a "sources linked in the description"
    # credit. With the LLM outro/tease helpers failing (stubbed to raise above), it
    # falls back to the generic meaning line, NOT a credit.
    assert "Full sources" not in nar.scenes[-1].text
    assert "linked in the description" not in nar.scenes[-1].text
    assert nar.scenes[-1].text.strip()  # never empty

    # one entity per PAIR — check the context scene of each item
    for scene, entity in zip(nar.scenes[1:7:2], ("Wolverine", "Deadpool", "Thanos")):
        assert entity.lower() in scene.text.lower()
        assert not scene.is_intro and not scene.is_outro
        assert scene.beat_id != 0

    body_words = sum(s.word_count for s in nar.scenes[1:7])
    assert 20 <= body_words <= 120


def test_write_explore_answer_explain_statement_lead_routes_to_explain_contract(monkeypatch, project_dir):
    """Explainer lane (2026-07-10) regression: a STATEMENT-lead title ("This is
    how...") used to be misclassified as "list" by question_archetype() — the
    whole EXPLAIN writer/validator/hook contract silently never fired for it.
    Asserts the writer is called with the EXPLAIN system prompt (not LIST) and
    the hook keeps the statement register (no forced "?")."""
    captured: dict = {}

    def _writer_call(*, system, user, models=None, max_tokens=2000, progress=None,
                      label="llm", validator=None):
        captured["system"] = system
        # CONTEXT + MOMENT per item, same as the LIST fixture above.
        raw = json.dumps({"scenes": [
            {"text": "Wolverine drills the same motion until it costs him nothing.",
             "connective": None, "beat_id": 1},
            {"text": "Wolverine trains until his body forgets pain, sealed shut before Legacy #1 ends.",
             "connective": None, "beat_id": 1},
            {"text": "Deadpool treats that same discipline as a punchline.",
             "connective": None, "beat_id": 2},
            {"text": "But that discipline turns cruel — Deadpool mocks the same ritual in Deadpool #2.",
             "connective": None, "beat_id": 2},
            {"text": "Thanos learned the lesson long before either of them.",
             "connective": None, "beat_id": 3},
            {"text": "That's why Thanos endures it without flinching in Thanos #3 — the training "
             "was never about pain.", "connective": None, "beat_id": 3},
        ]})
        if validator is not None:
            assert validator(raw), "fixture scenes must pass the module's own coarse validator"
        return raw, "fake-writer-model"

    monkeypatch.setattr(ea, "call_with_chain", _writer_call)
    monkeypatch.setattr(ws, "call_with_chain", _raising_call)

    comic_context = {"title": "This is how Batman trains himself", "is_arc": True}
    story_pages = [
        _page(1, 1, 1), _page(2, 1, 2),
        _page(3, 2, 1), _page(4, 2, 2),
        _page(5, 3, 1), _page(6, 3, 2),
    ]
    debug_dump = {"project": _PROJECT}

    nar = ws.write_script(comic_context, story_pages, "explore_answer", debug_dump=debug_dump)

    # root-bug regression: the EXPLAIN system prompt was used, not the LIST one.
    assert captured["system"] == ea._EXPLORE_WRITE_SYSTEM_EXPLAIN

    hook = nar.scenes[0].text
    assert hook.startswith("This is how Batman trains himself.")
    assert "himself?" not in hook
    assert any(hook.endswith(t) for t in ea._EXPLAIN_TEASE_POOL)


def test_missing_answer_context_raises(monkeypatch):
    monkeypatch.setattr(ea, "call_with_chain", _fake_writer_call)
    monkeypatch.setattr(ws, "call_with_chain", _raising_call)
    with pytest.raises(FileNotFoundError):
        ws.write_script({"title": "Q?"}, [], "explore_answer",
                        debug_dump={"project": "_no_such_project_xyz"})


def test_missing_project_name_raises(monkeypatch):
    monkeypatch.setattr(ea, "call_with_chain", _fake_writer_call)
    with pytest.raises(RuntimeError):
        ws.write_script({"title": "Q?"}, [], "explore_answer", debug_dump={})


if __name__ == "__main__":
    import types
    mp = pytest.MonkeyPatch()
    try:
        d = PROJECTS_ROOT / _PROJECT
        d.mkdir(parents=True, exist_ok=True)
        (d / "answer_context.json").write_text(json.dumps(_ANSWER_CONTEXT))
        test_write_explore_answer_end_to_end(mp, d)
        test_missing_answer_context_raises(mp)
        test_missing_project_name_raises(mp)
        print("ok")
    finally:
        shutil.rmtree(d, ignore_errors=True)
        mp.undo()


# ─── Archetype dispatch (Why/How → explain mode) ────────────────────────────

def test_question_archetype_detection():
    from stages.question_archetype import question_archetype as qa
    # explain family (Master's 2026-07-06 archetype)
    assert qa("Why the Phoenix Force chose a broken host to rebuild the entire multiverse") == "explain"
    assert qa("How Magneto built a mutant utopia on the ruins of a dead world") == "explain"
    assert qa("The day Joker finally went sane and realized the horror he created") == "explain"
    assert qa("The tragic reason Silver Surfer must keep his memories hidden") == "explain"
    assert qa("The time Superman realized his greatest power was a curse") == "explain"
    assert qa("What made Doctor Doom give up absolute godhood?") == "explain"
    # explainer lane (2026-07-10): statement-lead titles, not real questions
    assert qa("This is how Batman trains himself") == "explain"
    assert qa("This is why Deadpool always breaks the fourth wall") == "explain"
    assert qa("Here's how Wolverine really heals") == "explain"
    assert qa("Here's why Joker never kills Batman") == "explain"
    assert qa("Why does Batman always work alone") == "explain"
    # list family stays list
    assert qa("Who has survived Ghost Rider's Penance Stare?") == "list"
    assert qa("4 villains who broke a hero's body") == "list"
    assert qa("") == "list"


def test_is_statement_lead():
    from stages.question_archetype import is_statement_lead as isl
    # statement register (explainer lane) — no "?" belongs at the end
    assert isl("This is how Batman trains himself")
    assert isl("Here's why Deadpool always breaks the fourth wall")
    assert isl("The day Joker finally went sane")
    assert isl("The tragic reason Silver Surfer hides his memories")
    # real interrogatives are NOT statement leads
    assert not isl("Why does Batman always work alone")
    assert not isl("How did Magneto build a mutant utopia")
    assert not isl("Who has survived Ghost Rider's Penance Stare?")
    assert not isl("")


def test_validator_explain_requires_answer_in_final_scene():
    beats = [ea.Beat(id=1, function="COLD_OPEN", name="Jean", page_refs=[1]),
             ea.Beat(id=2, function="LANDING", name="Scott", page_refs=[2])]
    # final scene narrates an event but never answers → flagged
    scenes = [{"text": "Jean hatches from the egg, broken and blank. " + "word " * 18},
              {"text": "Scott hears her whisper and walks back into the school. " + "word " * 18}]
    issues = ea._validate_explore_scenes(scenes, beats, "explain")
    assert any("never states the ANSWER" in i for i in issues)
    # same scenes pass in list mode (rule is explain-only)
    assert not any("ANSWER" in i for i in ea._validate_explore_scenes(scenes, beats, "list"))
    # causal marker in the final scene satisfies the guard
    scenes[1]["text"] = ("That's why it had to be Scott — only a broken host could let him go. "
                         + "word " * 12)
    assert not any("ANSWER" in i for i in ea._validate_explore_scenes(scenes, beats, "explain"))


def test_validator_explain_bans_list_language():
    beats = [ea.Beat(id=1, function="COLD_OPEN", name="Jean", page_refs=[1])]
    scenes = [{"text": "Jean is the last one on this list, because reasons. " + "word " * 15}]
    issues = ea._validate_explore_scenes(scenes, beats, "explain")
    assert any("list language" in i for i in issues)
    assert not any("list language" in i for i in ea._validate_explore_scenes(scenes, beats, "list"))


# ─── No bare reveal fragments (2026-07-10 Master feedback: "'I AM BANE'
# written across their chests. They are alive." read as a floating quote +
# an unexplained one-line fragment) — the writer prompt now bans standalone
# reveal fragments in BOTH archetype variants; consequence must be spelled
# out in the same/next sentence, quoted in-art text introduced naturally.

def test_writer_system_prompts_ban_bare_reveal_fragments():
    for system in (ea._EXPLORE_WRITE_SYSTEM_LIST, ea._EXPLORE_WRITE_SYSTEM_EXPLAIN):
        assert "complete subject-verb-object clause" in system
        assert "fragment" in system
        assert "They are alive." in system  # the banned-example is named, not just described


# ─── Length-based fragment granularity (todo #10) + per-item mini-arc (todo #7):
# the old fixed "TARGET 3 fragments" rule made the first fragment carry connective
# +citation+event (~29w ≈ 8.5s hanging on one static panel). Replaced by a
# length-based split (a citation/connective head is its own establishing shot) and
# a mini-arc rule so each item is a setup→turn→payoff, not a flattened wiki fact.

def test_writer_system_prompts_have_length_based_fragment_and_miniarc_rules():
    for system in (ea._EXPLORE_WRITE_SYSTEM_LIST, ea._EXPLORE_WRITE_SYSTEM_EXPLAIN):
        assert "TARGET 3 fragments" not in system            # the old fixed count is gone
        assert "35+ words gives 4-5 fragments" in system      # length-based granularity
        assert "establishing shot" in system                  # citation/connective head split
        assert "MINI-ARC PER ITEM" in system                  # setup -> visual turn -> payoff
        assert "wiki-style" in system                          # no flattening to one summary line
        # the verbatim guarantee (Stage 5 word-position bucketing) must survive the rewrite
        assert "VERBATIM ONLY" in system
        assert "concatenate back to" in system


def test_validate_explore_scenes_is_fragment_count_agnostic(monkeypatch):
    """Trace lock (todo #10): the Q&A validator gates scene count, per-scene word
    cap, band, and entity-naming — it NEVER inspects visual_beats. Splitting a
    scene into 5 fragments vs 2 changes NOTHING it reports, which is why the finer-
    granularity change needs no downstream validator edit. A future regression that
    hard-codes a fragment count would break this."""
    beat = ea.Beat(id=1, function="COLD_OPEN", name="Zarathos", page_refs=[1])
    text = ("In Ghost Rider #35, the demon Zarathos finally cornered the boy, but the "
            "Spirit of Vengeance rose up through the flames, seized the blade from his "
            "hand, and turned the stroke back onto the master who had summoned it.")
    five = {"text": text, "visual_beats": [
        "In Ghost Rider #35,",
        "the demon Zarathos finally cornered the boy,",
        "but the Spirit of Vengeance rose up through the flames,",
        "seized the blade from his hand,",
        "and turned the stroke back onto the master who had summoned it."]}
    two = {"text": text, "visual_beats": [
        "In Ghost Rider #35, the demon Zarathos finally cornered the boy,",
        "but the Spirit of Vengeance rose up through the flames, seized the blade "
        "from his hand, and turned the stroke back onto the master who had summoned it."]}
    # An item is _SCENES_PER_ITEM scenes now (context + moment), so feed a full pair —
    # otherwise the scene-count gate fires and masks what this test is actually about.
    five_pair = [five, five]
    two_pair = [two, two]
    # identical issues regardless of fragment count (validator ignores visual_beats)
    assert (ea._validate_explore_scenes(five_pair, [beat], "list")
            == ea._validate_explore_scenes(two_pair, [beat], "list"))
    # with the band widened, a 5-fragment scene passes clean — there is no fragment gate
    monkeypatch.setattr(ea, "_exp_band", lambda n: (0, 500))
    assert ea._validate_explore_scenes(five_pair, [beat], "list") == []
    assert ea._validate_explore_scenes(two_pair, [beat], "list") == []


def test_build_hook_by_archetype():
    ctx = {"answer_summary": "Because only the dead woman could let him go."}
    h_list = ea._build_hook("Who survived X", ctx, "list", "proj-a")
    h_explain = ea._build_hook("Why did the Phoenix choose a broken host", ctx, "explain", "proj-a")
    # tease is one of the pool's rotated variants, not a fixed sentence
    assert any(h_list.endswith(t) for t in ea._LIST_TEASE_POOL)
    assert any(h_explain.endswith(t) for t in ea._EXPLAIN_TEASE_POOL)
    assert "list" not in h_explain  # no list language on explain hooks
    # explain hook must NOT spoil the thesis (the answer lands at the END)
    assert "dead woman" not in h_explain
    assert h_explain.startswith("Why did the Phoenix choose a broken host?")


def test_build_hook_statement_lead_keeps_statement_register():
    # explainer lane (2026-07-10): "This is how X" is a STATEMENT, not a real
    # question — forcing a "?" onto it ("...himself?") reads wrong.
    ctx = {"answer_summary": "Because the ritual is really a punishment."}
    h = ea._build_hook("This is how Batman trains himself", ctx, "explain", "proj-a")
    assert h.startswith("This is how Batman trains himself.")
    assert "himself?" not in h
    assert any(h.endswith(t) for t in ea._EXPLAIN_TEASE_POOL)
    # thesis never leaks into the hook (same guarantee as the interrogative case)
    assert "punishment" not in h

    # a real interrogative explain question is untouched by the statement branch
    h2 = ea._build_hook("Why does Batman always work alone", ctx, "explain", "proj-a")
    assert h2.startswith("Why does Batman always work alone?")


def test_build_hook_tease_rotation_is_deterministic_per_project():
    ctx = {"answer_summary": "x"}
    # same project slug -> same tease, every call (retries must reproduce it)
    a1 = ea._build_hook("Who survived X", ctx, "list", "project-alpha")
    a2 = ea._build_hook("Who survived X", ctx, "list", "project-alpha")
    assert a1 == a2
    # across many different slugs, more than one pool variant gets picked
    # (pool has 6 entries; a run of 12 distinct slugs landing on just 1 is
    # astronomically unlikely with a uniform hash)
    picks = {ea._pick_tease(ea._LIST_TEASE_POOL, f"project-{i}") for i in range(12)}
    assert len(picks) > 1


def test_build_hook_comparison_shape_skips_list_language():
    # "X things A can do that B can't" is a capability comparison, not a
    # ranked list of people — it must not get the "who"/"list"/"name"/
    # "Here's the answer" list tease (bug: comparison questions were routed
    # to _LIST_TEASE_POOL just because question_archetype() calls them "list").
    h = ea._build_hook("Things Carnage Can Do That Venom Can't", {}, "list", "carnage-x")
    low = h.lower()
    assert "who" not in low
    assert "list" not in low
    assert "name" not in low
    assert "here's the answer" not in low
    assert any(h.endswith(t) for t in ea._COMPARISON_TEASE_POOL)

    # ordinary list questions are untouched (same tease pool, same behavior)
    h2 = ea._build_hook("Which Villains Have Actually Defeated Mephisto",
                         {"answer_summary": "three villains"}, "list", "meph-x")
    assert any(h2.endswith(t) for t in ea._LIST_TEASE_POOL)
    assert "three villains" in h2
    picks_explain = {ea._pick_tease(ea._EXPLAIN_TEASE_POOL, f"project-{i}") for i in range(12)}
    assert len(picks_explain) > 1


# ─── Seconds-targeted band (2026-07-08: was a flat n_items*(22..46) multiply
# with no runtime ceiling — a 3-item Q&A measured ~35.6s, too short of the
# 45-65s Paddy Galloway completion sweet spot, while 6 items could run 81s+ of
# body alone) ─────────────────────────────────────────────────────────────

def test_exp_band_targets_seconds_not_flat_per_item():
    overhead = ea._QA_INTRO_OUTRO_SEC
    for n in (3, 4, 5, 6):
        lo, hi = ea._exp_band(n)
        assert lo <= hi
        lo_total_sec = lo / ea._WORDS_PER_SEC + overhead
        hi_total_sec = hi / ea._WORDS_PER_SEC + overhead
        # floor is reached (within rounding slack) and ceiling is respected
        # for every realistic item count, not just the old n=3 case. The band
        # targets the seconds floor UNLESS n readable items (each at the
        # _EXP_WORDS_PER_ITEM_MIN sanity floor) physically take longer — then that
        # floor wins, so respect whichever is higher.
        per_item_floor_sec = n * ea._EXP_WORDS_PER_ITEM_MIN / ea._WORDS_PER_SEC + overhead
        assert lo_total_sec <= max(ea._QA_TARGET_MIN_SEC, per_item_floor_sec) + 5
        assert hi_total_sec <= ea._QA_TARGET_MAX_SEC + 1
    # Since each item became _SCENES_PER_ITEM scenes, even a 2-item question can now
    # physically reach the seconds floor (4 scenes x up to the per-scene cap), so the
    # band is a real range rather than the collapsed single value it used to be. The
    # degenerate guard in _exp_band stays as a safety net; nothing realistic trips it.
    lo2, hi2 = ea._exp_band(2)
    assert lo2 < hi2 <= 2 * ea._SCENES_PER_ITEM * ea._EXP_SCENE_MAX_WORDS
    assert lo2 / ea._WORDS_PER_SEC + overhead >= ea._QA_TARGET_MIN_SEC


# ─── ADDITIVE story-context: relationships / stakes_why per item + question-level
# viewer_context / constant_broken. Old answer_context.json WITHOUT them must produce
# a byte-identical writer prompt (the additive contract Master relies on). ──────────

def test_items_block_includes_relationships_and_stakes_when_present():
    beats = [ea.Beat(id=1, function="COLD_OPEN", name="Blackheart", page_refs=[1])]
    # No apostrophes in the values so repr() uses single quotes (matches entity='...' style).
    items = [{"source_comic": "Ghost Rider #1", "drawable_moment": "a stab",
              "relationships": "Blackheart is the son of Mephisto",
              "stakes_why": "a father falls to his own child"}]
    block = ea._items_block(beats, items)
    assert "relationships='Blackheart is the son of Mephisto'" in block
    assert "stakes_why='a father falls to his own child'" in block


def test_items_block_omits_missing_story_context_fields():
    # An old item with neither field -> block has no relationships= / stakes_why= at all.
    beats = [ea.Beat(id=1, function="COLD_OPEN", name="Wolverine", page_refs=[1])]
    items = [{"source_comic": "X-Men #1", "drawable_moment": "walks through fire"}]
    block = ea._items_block(beats, items)
    assert "relationships=" not in block and "stakes_why=" not in block
    assert "entity='Wolverine'" in block


def test_viewer_context_block_present_and_empty():
    ctx = {"viewer_context": "Nobody has ever survived the stare.",
           "constant_broken": "The Penance Stare always kills."}
    blk = ea._viewer_context_block(ctx)
    assert "VIEWER CONTEXT" in blk and "Nobody has ever survived" in blk
    assert "THE CONSTANT BEING BROKEN" in blk
    # Old context with neither field -> empty string (byte-identical prompt path).
    assert ea._viewer_context_block({}) == ""
