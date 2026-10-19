"""QA Discover requires issue-level, fetched print-comic evidence."""

import json
from pathlib import Path

from stages.research_scout.cited_sources import FetchedSource
from stages.research_scout.qa_discovery_gate import (
    evaluate_issue_evidence,
    canonical_topic,
    verified_issue_evidence,
    _publication_years,
    _cover_publication_years,
)
from stages.research_scout.models import ScoutMode
from stages.research_scout.policies import PolicyBundle
from stages.research_scout.storage import SessionStore
from stages.research_scout.workflow import ScoutWorkflow
from stages.youcom_scout import _DISCOVER_PROPS, _schema


FIXTURES = Path(__file__).parent / "fixtures" / "qa_discover_live"


def _candidate(*, year=2025, quote=None, issue=None,
               url="https://www.dc.com/comics/batman-2016/batman-161"):
    issue = issue or f"Batman #161 ({year})"
    quote = quote or f"{issue} tests Batman's no-kill rule when he faces the Joker."
    return {
        "question": "Why does Batman refuse to kill the Joker?",
        "canonical_anchor": "Batman",
        "concept_key": "Batman no-kill rule",
        "issue_evidence": [{"issue": issue, "source_url": url, "quote": quote}],
    }


def _page(quote, *, number=161, year=2025):
    return f"Title: Batman (2016) #{number} | DC Comics\nPublished\nJuly 23, {year}\n{quote}"


def test_issue_gate_requires_fetched_exact_issue_year_and_relevant_quote():
    candidate = _candidate()
    source = FetchedSource(candidate["issue_evidence"][0]["source_url"], text=_page(
        candidate["issue_evidence"][0]["quote"]
    ))
    assert verified_issue_evidence(candidate, "direct", fetcher=lambda url: source)


def test_issue_gate_rejects_unfetched_wrong_year_and_off_topic_urls():
    candidate = _candidate()
    assert not verified_issue_evidence(
        candidate, "direct", fetcher=lambda url: FetchedSource(url, error="HTTP 403")
    )
    old = _candidate(year=1995)
    assert not verified_issue_evidence(
        old, "direct", fetcher=lambda url: FetchedSource(url, text=_page(old["issue_evidence"][0]["quote"], year=1995))
    )
    # round 4 (A2): a label year the page contradicts is corrected when the page says 2010+ ...
    wrong_year = _candidate()
    corrected = evaluate_issue_evidence(
        wrong_year, "direct", fetcher=lambda url: FetchedSource(url, text=_page(wrong_year["issue_evidence"][0]["quote"], year=2024)))
    assert corrected.passed and "(2024)" in corrected.claims[0]["issue"]
    assert any("year_corrected" in line for line in corrected.details)
    off_topic = _candidate(quote="Batman #161 (2025) has a new cover by its artist.")
    assert not verified_issue_evidence(
        off_topic, "direct", fetcher=lambda url: FetchedSource(url, text=_page(off_topic["issue_evidence"][0]["quote"]))
    )
    adjacent = _candidate(quote="Batman #161 (2025) has the Joker on its cover.")
    assert not verified_issue_evidence(
        adjacent, "direct", fetcher=lambda url: FetchedSource(url, text=_page(adjacent["issue_evidence"][0]["quote"]))
    )


def test_issue_gate_rejects_article_publish_date_as_issue_year():
    candidate = _candidate(url="https://aiptcomics.com/2025/07/23/batman-161-review/")
    source = FetchedSource(candidate["issue_evidence"][0]["source_url"], text=_page(
        candidate["issue_evidence"][0]["quote"]
    ))
    assert not verified_issue_evidence(candidate, "direct", fetcher=lambda url: source)


def test_issue_gate_ignores_reader_published_time_and_accepts_marvel_published_field():
    candidate = _candidate(year=2026)
    quote = candidate["issue_evidence"][0]["quote"]
    stale = FetchedSource(candidate["issue_evidence"][0]["source_url"], text=(
        "Title: Batman (2016) #161 | DC\nPublished Time: Wed, 14 Oct 2026 GMT\n"
        "On Sale Date: July 23, 2025\n" + quote
    ))
    # the reader's Published Time (2026) never vouches for the label; the page's own On Sale
    # Date (2025) is what corrects it (round 4, A2)
    result = evaluate_issue_evidence(candidate, "direct", fetcher=lambda url: stale)
    assert result.passed and "(2025)" in result.claims[0]["issue"] and "2026" not in result.claims[0]["issue"]

    marvel = _candidate(year=2018, issue="Amazing Spider-Man Annual (2018) #1 (2018)",
                        url="https://www.marvel.com/comics/issue/71372/amazing_spider-man_annual_2018_1")
    marvel["question"] = (
        "What is Spider-Man's black symbiote suit, how does it work, "
        "and what limit on Peter's control does it reveal?"
    )
    marvel["canonical_anchor"] = "Spider-Man"
    marvel["concept_key"] = "symbiote suit"
    marvel["issue_evidence"][0]["quote"] = (
        "the symbiote would creep onto Peter, and unbeknownst to its host, "
        "take them out into the night!"
    )
    source = FetchedSource(
        marvel["issue_evidence"][0]["source_url"],
        text=(Path(__file__).parent / "fixtures" / "qa_discover_issue" /
              "marvel_annual_2018_1.txt").read_text(encoding="utf-8"),
    )
    assert verified_issue_evidence(marvel, "direct", fetcher=lambda url: source)


def test_issue_release_date_takes_precedence_over_later_cover_date():
    candidate = _candidate(year=2010)
    quote = candidate["issue_evidence"][0]["quote"]
    for release_line in (
        "Release Date: December 2009",
        "On Sale Date: December 2009",
        "It was published on December 2, 2009.",
    ):
        page = f"Title: Batman #161 | DC Comics\n{release_line}\nCover Date: March 2010\n{quote}"
        assert _publication_years(page) == {2009}
        result = evaluate_issue_evidence(
            candidate, "direct", fetcher=lambda url: FetchedSource(url, text=page), semantic=None)
        assert (result.passed, result.reason) == (False, "year_out_of_range")


def test_issue_release_year_is_exact_even_when_cover_year_matches_claim():
    candidate = _candidate(year=2011)
    quote = candidate["issue_evidence"][0]["quote"]
    page = f"Title: Batman #161 | DC Comics\nRelease Date: December 2010\nCover Date: March 2011\n{quote}"
    assert _publication_years(page) == {2010}
    result = evaluate_issue_evidence(
        candidate, "direct", fetcher=lambda url: FetchedSource(url, text=page), semantic=None)
    assert result.passed
    assert result.claims[0]["issue"] == "Batman #161 (2010)"
    assert result.claims[0]["year_corrected"] == {"from": 2011, "to": 2010}


def test_issue_cover_date_is_used_only_when_release_date_is_absent():
    candidate = _candidate(year=2010)
    quote = candidate["issue_evidence"][0]["quote"]
    page = f"Title: Batman #161 | DC Comics\nCover Date: March 2010\n{quote}"
    assert _publication_years(page) == {2010}
    assert evaluate_issue_evidence(
        candidate, "direct", fetcher=lambda url: FetchedSource(url, text=page), semantic=None,
    ).passed


def test_real_wiki_pages_do_not_launder_2009_releases_through_2010_cover_dates():
    fixture_dir = Path(__file__).parent / "fixtures" / "qa_issue_dates"
    candidates = json.loads((fixture_dir / "candidates.json").read_text(encoding="utf-8"))
    for candidate, filename in zip(
        candidates, ("captain_america_who_will_wield.txt", "wonder_woman_39.txt"), strict=True,
    ):
        page = (fixture_dir / filename).read_text(encoding="utf-8")
        assert _publication_years(page) == {2009}
        assert _cover_publication_years(page) == {2010}
        result = evaluate_issue_evidence(
            candidate, "direct", semantic=None,
            fetcher=lambda url: FetchedSource(url, text=page),
        )
        assert (result.passed, result.reason) == (False, "year_out_of_range")


def test_list_requires_two_verified_distinct_issues():
    first = _candidate()
    first["question"] = "What are some times Batman spared the Joker?"
    first["issue_evidence"][0]["quote"] = "Batman #161 (2025) explains why Batman spared the Joker."
    first["issue_evidence"][0]["issue"] = "Batman #161 (2025)"
    sources = {first["issue_evidence"][0]["source_url"]: _page(first["issue_evidence"][0]["quote"])}
    assert not verified_issue_evidence(
        first, "list", fetcher=lambda url: FetchedSource(url, text=sources[url])
    )
    second = {"issue": "Batman #162 (2025)", "source_url": "https://www.dc.com/comics/batman-2016/batman-162",
              "quote": "Batman #162 (2025) explains why Batman spared the Joker again."}
    first["issue_evidence"].append(second)
    sources[second["source_url"]] = _page(second["quote"], number=162)
    assert verified_issue_evidence(
        first, "list", fetcher=lambda url: FetchedSource(url, text=sources[url])
    )


def test_canonical_topic_folds_articles_and_generic_world_suffixes():
    assert canonical_topic("The Flash") == canonical_topic("Flash")
    assert canonical_topic("Age of Apocalypse timeline") == canonical_topic("Age of Apocalypse world")
    assert canonical_topic("Speed Force abilities") == canonical_topic("Speed Force")


def test_three_saved_live_batches_cannot_pass_with_bare_urls_only():
    for index in (1, 2, 3):
        payload = json.loads((FIXTURES / f"batch_{index}.json").read_text(encoding="utf-8"))
        for candidate in payload["raw"]:
            assert not verified_issue_evidence(
                candidate, candidate.get("proposed_format", "direct"),
                fetcher=lambda url: (_ for _ in ()).throw(AssertionError("bare URL fetched")),
            )


def test_saved_live_payloads_report_zero_verified_questions_without_source_fetch(tmp_path, monkeypatch):
    monkeypatch.setattr("stages.youcom_scout.is_burned", lambda question, digest: False)
    for index in (1, 2, 3):
        payload = json.loads((FIXTURES / f"batch_{index}.json").read_text(encoding="utf-8"))
        client = _BatchClient(payload["raw"], [])
        workflow = ScoutWorkflow(store=SessionStore(tmp_path / str(index)), client=client)
        batch = workflow.discover_questions(ScoutMode.QA)
        assert len(client.prompts) == 2
        assert len(batch) == 1
        assert batch[0]["fallback"] is True
        assert batch[0]["verification_shortage"] is True


def test_qa_discover_schema_requires_issue_metadata_at_every_object_level():
    schema = _schema(_DISCOVER_PROPS)
    item = schema["properties"]["candidates"]["items"]
    assert {"canonical_anchor", "concept_key", "issue_evidence"} <= set(item["required"])
    issue = item["properties"]["issue_evidence"]["items"]
    assert issue["additionalProperties"] is False
    assert set(issue["required"]) == {"issue", "source_url", "quote"}


class _BatchClient:
    def __init__(self, *batches):
        self.batches = list(batches)
        self.prompts = []

    def research(self, prompt, schema, profile, *, effort="standard"):
        self.prompts.append(prompt)
        batch = self.batches.pop(0) if self.batches else []
        return type("Result", (), {"payload": {"output": {"content": {"candidates": batch}}},
                                   "error": None})()


def _entry(question, anchor, concept):
    return {"question": question, "subject": anchor, "canonical_anchor": anchor,
            "concept_key": concept, "viewer_hook": "", "evidence_urls": [],
            "issue_evidence": []}


def _valid_five():
    return [
        _entry("Why does Batman refuse to kill?", "Batman", "no kill rule"),
        _entry("How does Spider-Man's Spider-Sense work?", "Spider-Man", "Spider-Sense"),
        _entry("What does Superman's heat vision do?", "Superman", "heat vision"),
        _entry("Why does Wonder Woman use the Lasso of Truth?", "Wonder Woman", "Lasso of Truth"),
        _entry("What are some abilities of the Flash?", "Flash", "Flash abilities"),
    ]


def test_valid_five_use_one_research_call_and_keep_four_plus_one(tmp_path, monkeypatch):
    monkeypatch.setattr("stages.youcom_scout.is_burned", lambda question, digest: False)
    client = _BatchClient(_valid_five())
    workflow = ScoutWorkflow(store=SessionStore(tmp_path), client=client,
                             qa_issue_verifier=lambda candidate, fmt: True)
    batch = workflow.discover_questions(ScoutMode.QA)
    assert len(client.prompts) == 1
    assert [entry["proposed_format"] for entry in batch] == ["direct"] * 4 + ["list"]
    assert "issue_evidence" in client.prompts[0]


def test_duplicate_anchor_and_concept_top_up_only_missing_lanes(tmp_path, monkeypatch):
    monkeypatch.setattr("stages.youcom_scout.is_burned", lambda question, digest: False)
    initial = _valid_five()
    initial[-1] = _entry("What are some Speed Force abilities of the Flash?", "Flash", "Speed Force")
    initial[0] = _entry("How does the Flash use the Speed Force?", "The Flash", "Speed Force")
    replacement = _entry("What are some abilities of Thor?", "Thor", "Thor abilities")
    client = _BatchClient(initial, [replacement])
    workflow = ScoutWorkflow(store=SessionStore(tmp_path), client=client,
                             qa_issue_verifier=lambda candidate, fmt: True)
    batch = workflow.discover_questions(ScoutMode.QA)
    assert len(client.prompts) == 2
    assert len(batch) == 5
    assert [entry["proposed_format"] for entry in batch].count("list") == 1
    assert "Return exactly 1 list" in client.prompts[1]
    assert "Return exactly 1 direct" not in client.prompts[1]
    assert "Thor" in batch[-1]["question"]


def test_duplicate_concept_across_different_anchors_triggers_direct_top_up(tmp_path, monkeypatch):
    monkeypatch.setattr("stages.youcom_scout.is_burned", lambda question, digest: False)
    initial = _valid_five()
    initial[0] = _entry("Why did Magneto lead the X-Men in Age of Apocalypse?", "Magneto", "Age of Apocalypse")
    initial[1] = _entry("What is the Age of Apocalypse world for the X-Men?", "X-Men", "Age of Apocalypse world")
    replacement = _entry("How does Spider-Man's Spider-Sense work?", "Spider-Man", "Spider-Sense")
    client = _BatchClient(initial, [replacement])
    workflow = ScoutWorkflow(store=SessionStore(tmp_path), client=client,
                             qa_issue_verifier=lambda candidate, fmt: True)
    batch = workflow.discover_questions(ScoutMode.QA)
    assert len(client.prompts) == 2
    assert len(batch) == 5
    assert [entry["proposed_format"] for entry in batch].count("direct") == 4
    assert sum("Age of Apocalypse" in entry["question"] for entry in batch) == 1
    assert "Return exactly 1 direct" in client.prompts[1]


def test_shortage_after_top_up_is_marked_instead_of_filling_with_weak_candidates(tmp_path, monkeypatch):
    monkeypatch.setattr("stages.youcom_scout.is_burned", lambda question, digest: False)
    client = _BatchClient(_valid_five()[:3], [])
    workflow = ScoutWorkflow(store=SessionStore(tmp_path), client=client,
                             qa_issue_verifier=lambda candidate, fmt: True)
    batch = workflow.discover_questions(ScoutMode.QA)
    assert len(client.prompts) == 2
    assert len(batch) == 3
    assert all(entry["batch_incomplete"] is True for entry in batch)
    assert all(entry["batch_expected"] == 5 for entry in batch)


def test_initial_and_topup_qa_prompts_choose_one_answer_target_after_reading_sources(tmp_path, monkeypatch):
    monkeypatch.setattr("stages.youcom_scout.is_burned", lambda question, digest: False)
    client = _BatchClient(_valid_five()[:3], [])
    workflow = ScoutWorkflow(store=SessionStore(tmp_path), client=client,
                             qa_issue_verifier=lambda candidate, fmt: True)
    workflow.discover_questions(ScoutMode.QA)
    assert len(client.prompts) == 2
    for prompt in client.prompts:
        lowered = " ".join(prompt.casefold().split())
        assert "one answer target" in lowered
        assert "read" in lowered and "issue" in lowered and "before" in lowered
        assert "same answer scope" in lowered
        assert "empty string" in lowered
        assert "canonical_anchor" in lowered and "concept_key" in lowered
        assert "family/mission" in lowered
    first = client.prompts[0].casefold()
    assert "how does it work, and what" not in first
    assert "tension follows from its rules" not in first


def test_qa_scope_instruction_does_not_enter_micro_discovery_prompt():
    bundle = PolicyBundle.load(ScoutMode.MICRO)
    prompt = bundle.render(
        "discover", angles="\n".join(bundle.general_angles["micro"]),
        count="5", exclude="- none", avoid="- none",
    ).text.casefold()
    assert "micro moments" in prompt
    assert "one answer target" not in prompt
    assert "family/mission" not in prompt


def test_legacy_qa_cli_uses_same_narrow_source_first_scope(tmp_path, monkeypatch):
    from stages import youcom_scout

    prompts = []
    monkeypatch.setattr(youcom_scout, "_scouted_context_text", lambda: "")
    monkeypatch.setattr(youcom_scout, "_call_logged", lambda _key, prompt, *_args: (
        prompts.append(prompt) or {"output": {"content": {"candidates": []}}}
    ))
    youcom_scout.run_discover("test-key", tmp_path, "standard")
    assert len(prompts) == 5
    assert all("one answer target" in prompt.casefold() for prompt in prompts)
    assert all("read" in prompt.casefold() and "before" in prompt.casefold()
               for prompt in prompts)
    assert all("same answer scope" in prompt.casefold() for prompt in prompts)
    assert all("curiosity, tension, or consequence" not in prompt.casefold()
               for prompt in prompts)
