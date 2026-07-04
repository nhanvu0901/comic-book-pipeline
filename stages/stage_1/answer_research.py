"""Answer Research — Stage 1 mode `explore_answer` (Q&A video).

See EXPLORE_ANSWER_DESIGN.md (root, incl. ADDENDUM 2026-07-04). This is build
piece #1 of the "Explore Answer" mode: turn a QUESTION into a countdown listicle
of comic-grounded answers that the existing Stages 2->5 render as a ~60-76s Short.

Grounding INVERTS vs narrate mode (design "Core insights" #1): narrate has panels
and writes text; Q&A has FACTS (web research) and must FIND the panel per fact.
So this module's ONLY job is the FACTS half — research + verify N answer items,
then materialise them into the two project files Stages 2->5 already understand.

Anti-fabrication mirrors stages/stage_1/tools/gather_plot_sdk.py `_SYSTEM`: real
source URLs required, refuse-if-unsure (drop an item rather than invent it). A
fact -> WRONG issue is the #1 risk in the design; verification is not optional.
"""
import json
import re
from datetime import date
from pathlib import Path

from stages._claude_sdk import sdk_complete_web, sdk_available
from stages.stage_1.storage import save_comic_context, slugify
from config import get_project_dirs

# Items below this can't make a countdown listicle (design format spec: 3-6 items).
_MIN_ITEMS = 3
# Least->most shocking maps to presentation order; the shock is the finale (last).
# (design ADDENDUM "Order by SURPRISE ascending ... most shocking entry LAST").
_SURPRISE_RANK = {"low": 0, "medium": 1, "high": 2}
_ITEM_FIELDS = (
    "entity", "how_or_why", "source_comic", "source_year",
    "reader_url", "drawable_moment", "verification_note", "surprise_level",
)

_ANSWER_SYSTEM = """You are a comic-feats research agent. You are given a QUESTION \
about comics (e.g. "Who has survived Ghost Rider's Penance Stare?"). Use the \
WebSearch and WebFetch tools to find REAL, verifiable comic moments that answer it, \
then return them as a countdown listicle.

Anti-fabrication is the whole job — a wrong comic here becomes a wrong download and \
a wrong video downstream. Follow these rules exactly:

- ANSWER THE QUESTION with 3 to {max_items} items. Each item is one entity \
(character / being / object) that genuinely satisfies the question, grounded in a \
SPECIFIC published comic. Fewer real items is ALWAYS better than padding with \
invented ones — if you can only verify 3, return 3.

- PER ITEM, fill every field from what the sources actually say (never guess):
  * entity — the name as comics know it (e.g. "Deadpool", "Danny Ketch").
  * how_or_why — 1-2 PLAIN sentences of what HAPPENS (events, not opinions; drop \
"epic", "shocking", "underrated"). This is the fact the video narrates.
  * source_comic — series + issue, e.g. "Thanos" #13 or "Ghost Rider" (1990) #12.
  * source_year — the publication year of THAT issue (4 digits).
  * drawable_moment — ONE concrete visual a single panel would show (a pose, a face, \
an action). Downstream we must FIND a panel for this, so make it panel-sized and \
literal, not a whole scene.
  * verification_note — cite TWO OR MORE independent real sources that confirm the \
feat (name them / their URLs). If you found only ONE source, still include it but \
begin this note with "WEAK:" so the caller knows.
  * surprise_level — "low" | "medium" | "high": how shocking this entry is to a \
comics-literate viewer (a famous hero = low; an obscure/cross-universe/absurd answer \
= high).
  * reader_url — a batcave.biz reader URL for the source series, form \
"https://batcave.biz/reader/<news_id>/<chapter_id>" (two numeric ids). Search \
batcave.biz for the series and COPY a real reader URL. If you only find a series \
landing page ("...-<name>.html") and cannot open a real reader URL with both ids, \
set reader_url to "" — NEVER invent or guess the chapter id.

- ORDER the items by surprise ASCENDING: the most mainstream-recognisable, expected \
answers FIRST; the single most shocking / obscure / wildest answer LAST (it is the \
video's finale and retention payoff). Do not label them "number five/four".

- answer_summary — ONE sentence that restates the question as a promise and TEASES \
the final shock WITHOUT naming that entity (e.g. "...and one of them will surprise you").

- SOURCES to search and reconcile (use several): Marvel/DC Fandom \
(marvel.fandom.com, dc.fandom.com), Comic Vine (comicvine.gamespot.com), Wikipedia, \
CBR / ScreenRant feats lists, Reddit r/comicbooks / r/whowouldwin threads for leads \
(then CONFIRM the issue on a wiki — forum claims alone are WEAK). Open at least TWO \
independent sources per item with WebFetch before trusting it.

- YEAR/VOLUME MATCH (critical): many characters and titles repeat across years/volumes, \
and ambiguous names mis-resolve (a search for one issue can surface a different one). \
Pin the exact issue+year the feat happened in; if you cannot, mark the item WEAK.

- If you cannot verify an item from real sources, DROP it. Do not invent feats, \
issues, years, or reader URLs. Output STRICT JSON and nothing else."""


def _extract_json(text: str) -> dict | None:
    """Grab the first {...} object from the model text and parse it.

    Copied from gather_plot_sdk.py:54 (design says reuse that tolerant pattern):
    `\\{.*\\}` spans first "{" to last "}", so it survives ```json fences and any
    prose the model wraps around the object."""
    if not text:
        return None
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if not m:
        return None
    try:
        obj = json.loads(m.group(0))
        return obj if isinstance(obj, dict) else None
    except Exception:
        return None


def _clean_items(raw_items: list) -> list[dict]:
    """Keep only well-formed item dicts (all fields present) and normalise them.

    Refuse-if-unsure at the data layer: a partial item (missing how_or_why, no
    source, etc.) is a half-fabrication, so we drop it rather than ship a blank."""
    out: list[dict] = []
    for it in raw_items or []:
        if not isinstance(it, dict):
            continue
        item = {k: str(it.get(k, "") or "").strip() for k in _ITEM_FIELDS}
        lvl = item["surprise_level"].lower()
        item["surprise_level"] = lvl if lvl in _SURPRISE_RANK else "medium"
        # Every field except reader_url must be non-empty (reader_url "" is allowed
        # here — build_contexts is where an empty URL becomes a fail-loud error, so
        # the caller sees WHICH items lack a downloadable source, not a silent drop).
        if all(item[k] for k in _ITEM_FIELDS if k != "reader_url"):
            out.append(item)
    return out


def _order_by_surprise(items: list[dict]) -> list[dict]:
    """Stable sort ascending by surprise so the shock lands LAST (presentation order).

    The prompt already asks for this order; we enforce it defensively (cheap) because
    a mis-ordered finale kills the retention payoff — see design ADDENDUM ordering rule."""
    return sorted(items, key=lambda it: _SURPRISE_RANK.get(it["surprise_level"], 1))


def research_answer(question: str, *, max_items: int = 6, log=print) -> dict:
    """Research a comic Q&A question into ordered, verified answer items.

    Returns {"question", "answer_summary", "source_engine", "items": [...]} with
    items in PRESENTATION order (least surprising first, shock last). Raises
    RuntimeError/ValueError on unusable research — the whole video depends on this,
    so we fail loud rather than hand an empty/fabricated answer to the pipeline."""
    question = (question or "").strip()
    if not question:
        raise ValueError("research_answer: empty question")
    if not sdk_available():
        raise RuntimeError("research_answer: Claude SDK unavailable — cannot research")

    system = _ANSWER_SYSTEM.format(max_items=max_items)
    user = (
        f"QUESTION: {question}\n\n"
        f"Return 3 to {max_items} verified items answering it, ordered by surprise "
        "ascending (shock LAST). STRICT JSON only, no prose around it:\n"
        '{"answer_summary":"<one sentence teasing the shock, not naming it>",'
        '"items":[{"entity":"","how_or_why":"","source_comic":"","source_year":"",'
        '"drawable_moment":"","verification_note":"","surprise_level":"low|medium|high",'
        '"reader_url":"https://batcave.biz/reader/<news_id>/<chapter_id> or empty"}]}'
    )
    log(f"[answer-research] researching: {question!r} (<= {max_items} items) …")
    raw = sdk_complete_web(system, user, log=log)
    if not raw:
        raise RuntimeError("research_answer: SDK returned nothing")

    data = _extract_json(raw)
    if data is None:
        raise RuntimeError("research_answer: could not parse JSON from SDK output")

    items = _order_by_surprise(_clean_items(data.get("items")))[:max_items]
    if len(items) < _MIN_ITEMS:
        raise ValueError(
            f"research_answer: only {len(items)} verified item(s) — need >= {_MIN_ITEMS} "
            "for a listicle (research produced too few to trust)"
        )
    summary = (data.get("answer_summary") or "").strip()
    log(f"[answer-research] ✓ {len(items)} items (surprise: "
        f"{', '.join(i['surprise_level'] for i in items)})")
    return {
        "question": question,
        "answer_summary": summary,
        "source_engine": "claude-sdk-web",
        "items": items,
    }


def _answer_digest(question: str, items: list[dict]) -> str:
    """One-line-per-item plot_summary Stages 2->5 read as the 'story'.

    Q&A has no single plot; the digest IS the arc — question + each answer's fact and
    source, in presentation order — so the writer/matcher have grounding text to work
    from (design: reuse saga machinery; plot_summary drives Stage 3)."""
    lines = [f"Q&A: {question}"]
    for i, it in enumerate(items, 1):
        lines.append(
            f"{i}. {it['entity']} — {it['how_or_why']} "
            f"(source: {it['source_comic']}, {it['source_year']})"
        )
    return "\n".join(lines)


def build_contexts(
    question: str, research: dict, project_name: str,
    *, researched_at: str = "", log=print,
) -> tuple[Path, Path]:
    """Materialise research into answer_context.json + comic_context.json.

    - answer_context.json: human-readable record for Master (design constraint #2) —
      question, summary, and each item's sources/whys/URLs, in presentation order.
    - comic_context.json: SAGA-ARC shape so Stages 2->5 run unchanged. Each answer
      item becomes one "issue" of the saga (reuse the multi-issue machinery, design
      "Core insights" #2).

    Fail-loud: raises ValueError naming the offending items if ANY reader_url is empty
    — the pipeline cannot download a source with no reader URL, so the caller must
    decide (re-research / hand-fill) rather than silently ship a listicle with holes.
    Returns (answer_context_path, comic_context_path)."""
    items = research.get("items") or []
    researched_at = researched_at or date.today().isoformat()
    year = (researched_at[:4] if researched_at[:4].isdigit() else str(date.today().year))
    slug = slugify(project_name)

    # --- fail-loud on undownloadable items (the design's #1-risk mitigation (b)) ---
    missing = [it["entity"] for it in items if not (it.get("reader_url") or "").strip()]
    if missing:
        raise ValueError(
            "build_contexts: empty reader_url for item(s): "
            + ", ".join(missing)
            + " — pipeline cannot download these sources (re-research or hand-fill the URL)"
        )

    # --- answer_context.json (presentation order; rank 1 first, shock last) ---
    answer_ctx = {
        "question": question,
        "answer_summary": research.get("answer_summary", ""),
        "researched_at": researched_at,
        "source_engine": research.get("source_engine", ""),
        "items": [
            {
                "rank": i,
                "entity": it["entity"],
                "how_or_why": it["how_or_why"],
                "source_comic": it["source_comic"],
                "source_year": it["source_year"],
                "reader_url": it["reader_url"],
                "drawable_moment": it["drawable_moment"],
                "verification_note": it["verification_note"],
                "surprise_level": it["surprise_level"],
            }
            for i, it in enumerate(items, 1)
        ],
    }
    root = get_project_dirs(slug)["root"]
    answer_path = root / "answer_context.json"
    answer_path.write_text(json.dumps(answer_ctx, indent=2, ensure_ascii=False))
    log(f"[answer-research] wrote {answer_path}")

    # --- comic_context.json (saga-arc shape) ---
    # `issues` (list) is load-bearing: Stage 3's arc path iterates it as per-issue
    # dicts (write_script.py:1340) and the tests assert it. The design's "issues=Q&A"
    # label collides with that key, so the human "Q&A" marker goes on `issue`
    # (singular) — the conventional issue-number field (verify_plot.py:61 reads it).
    comic_ctx = {
        "title": question,
        "series": question,
        "issue": "Q&A",
        "year": year,
        "publisher": "Marvel/DC (mixed)",
        "characters": [it["entity"] for it in items],
        "plot_summary": _answer_digest(question, items),
        "plot_status": "OK",
        # plot_source guards the identity-repair hook (worker C skips rebuild for
        # "answer_research"): a Q&A plot is web-verified facts, NOT one comic's story,
        # so panel-rebuild would destroy it.
        "plot_source": "answer_research",
        "is_arc": True,
        "issue_count": len(items),
        "issues": [
            {
                "label": it["source_comic"],
                "chapter_index": i,
                "plot_summary": it["how_or_why"],
                "wiki_url": "",
            }
            for i, it in enumerate(items, 1)
        ],
        "reader_urls": [it["reader_url"] for it in items],
    }
    # Deliberately NO "summary" key (Stage 2 VLM cold-read fills it from panels) and
    # NO "user_prompt" key (identity Hook 0 no-ops without it) — design map, item 2.
    comic_path_str = save_comic_context(comic_ctx, slug, get_project_dirs)
    return answer_path, Path(comic_path_str)


if __name__ == "__main__":
    # Self-check: no network. Stub the SDK, run the real research+build path into a
    # temp project, and assert the two files come out with the promised shape.
    import tempfile

    _FIXTURE = (
        "```json\n"  # markdown fence — _extract_json must tolerate it
        + json.dumps({
            "answer_summary": "A few heroes shrugged it off — and the last one shouldn't have.",
            "items": [
                {"entity": "Ghost Rider", "how_or_why": "Danny Ketch turns the Stare on "
                 "himself and feels nothing, having no innocent blood on his soul.",
                 "source_comic": '"Ghost Rider" (1990) #12', "source_year": "1991",
                 "drawable_moment": "Ghost Rider's flaming skull staring into a mirror",
                 "verification_note": "marvel.fandom.com + Comic Vine issue page",
                 "surprise_level": "low",
                 "reader_url": "https://batcave.biz/reader/111/222"},
                {"entity": "Deadpool", "how_or_why": "His scrambled mind gives the Stare no "
                 "coherent guilt to burn, so he just laughs it off.",
                 "source_comic": '"Deadpool" #...', "source_year": "2014",
                 "drawable_moment": "Deadpool grinning as hellfire washes over him",
                 "verification_note": "CBR feats list + marvel.fandom.com",
                 "surprise_level": "medium",
                 "reader_url": "https://batcave.biz/reader/333/444"},
                {"entity": "Man-Thing", "how_or_why": "Having no soul to judge, the empathic "
                 "swamp creature is simply unaffected by the Penance Stare.",
                 "source_comic": '"Marvel Comics Presents" #...', "source_year": "1990",
                 "drawable_moment": "Man-Thing looming unmoved before Ghost Rider",
                 "verification_note": "WEAK: single Reddit r/comicbooks thread",
                 "surprise_level": "high",
                 "reader_url": "https://batcave.biz/reader/555/666"},
            ],
        })
        + "\n```"
    )

    # Run via `python -m ...` executes this file as __main__; research_answer/
    # build_contexts look up these names in THIS namespace, so rebind the globals
    # here directly (an `import ... as mod` would patch a second module object).
    sdk_complete_web = lambda *a, **k: _FIXTURE           # noqa: F811,E731
    sdk_available = lambda: True                          # noqa: F811,E731

    q = "Who has survived Ghost Rider's Penance Stare?"
    res = research_answer(q, log=lambda _m: None)
    assert res["source_engine"] == "claude-sdk-web"
    assert [i["surprise_level"] for i in res["items"]] == ["low", "medium", "high"], \
        "items must be surprise-ascending (shock last)"

    with tempfile.TemporaryDirectory() as d:
        get_project_dirs = lambda name: {"root": Path(d)}  # noqa: F811,E731
        a_path, c_path = build_contexts(q, res, "gr_penance", researched_at="2026-07-04",
                                        log=lambda _m: None)
        a = json.loads(a_path.read_text())
        c = json.loads(c_path.read_text())

    assert [it["rank"] for it in a["items"]] == [1, 2, 3]
    assert a["items"][-1]["entity"] == "Man-Thing"  # shock stays last
    assert a["researched_at"] == "2026-07-04"
    assert c["is_arc"] is True and c["issue_count"] == 3
    assert c["plot_source"] == "answer_research"
    assert "summary" not in c and "user_prompt" not in c
    assert c["reader_urls"] == [it["reader_url"] for it in res["items"]]
    assert isinstance(c["issues"], list) and c["issues"][0]["chapter_index"] == 1

    # fail-loud on an empty reader_url
    res["items"][1]["reader_url"] = ""
    try:
        build_contexts(q, res, "gr_penance", log=lambda _m: None)
        raise AssertionError("expected ValueError for empty reader_url")
    except ValueError as e:
        assert "Deadpool" in str(e)

    print("answer_research self-check OK")
