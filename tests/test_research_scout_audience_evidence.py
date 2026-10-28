from datetime import datetime, timedelta, timezone
import sqlite3

from stages.research_scout.audience_evidence import AudienceEvidenceStore, PublicVideoMetadataAdapter
from stages.research_scout.opportunity import candidate_key
from stages.research_scout.storage import SessionStore
from ui.bridge import annotate_scout_entries, parse_public_video_id, update_scout_audience, update_scout_entry_evidence


def test_registry_persists_source_evidence_and_unknown_when_no_signals(tmp_path):
    store = AudienceEvidenceStore(tmp_path)
    store.add_demand_evidence(
        candidate_key="hero|rule|why question", query="why hero rule",
        url="https://forum.example/thread", quote="Fans ask why the rule changed.",
        source_type="community", confidence="direct", checked_at="2026-10-27T00:00:00+00:00",
    )

    reloaded = AudienceEvidenceStore(tmp_path)
    signals = reloaded.signals_for("missing")
    rows = reloaded.evidence_for("hero|rule|why question")

    assert signals["demand"]["status"] == "unknown"
    assert rows[0]["quote"] == "Fans ask why the rule changed."
    assert rows[0]["confidence"] == "direct"


def test_growth_requires_two_video_snapshots_and_keeps_public_views_separate(tmp_path):
    store = AudienceEvidenceStore(tmp_path)
    first = "2026-10-20T00:00:00+00:00"
    second = "2026-10-22T00:00:00+00:00"
    store.add_video_snapshot(video_id="abc", url="https://youtu.be/abc", channel="Comic Test",
                             published_at="2026-10-01T00:00:00+00:00", duration_seconds=45,
                             views=100, observed_at=first)
    one = store.video_signal("abc")
    store.add_video_snapshot(video_id="abc", url="https://youtu.be/abc", channel="Comic Test",
                             published_at="2026-10-01T00:00:00+00:00", duration_seconds=45,
                             views=160, observed_at=second)
    two = store.video_signal("abc")

    assert one["growth"]["status"] == "unknown"
    assert one["current_views"] == 100
    assert two["growth"] == {"status": "observed", "views_delta": 60, "seconds": 172800.0}
    assert "retention" not in two


def test_failed_refresh_preserves_old_snapshot_and_marks_stale(tmp_path):
    store = AudienceEvidenceStore(tmp_path)
    old = (datetime.now(timezone.utc) - timedelta(days=8)).isoformat()
    store.add_video_snapshot(video_id="abc", url="https://youtu.be/abc", channel="Comic Test",
                             published_at=None, duration_seconds=None, views=100, observed_at=old)
    store.record_refresh_failure("abc", "timeout", checked_at=datetime.now(timezone.utc).isoformat())

    signal = store.video_signal("abc", stale_after_seconds=3600)

    assert signal["current_views"] == 100
    assert signal["status"] == "stale"
    assert signal["last_refresh_error"] == "timeout"


def test_public_metadata_adapter_records_api_metadata_without_downloading_media(tmp_path):
    store = AudienceEvidenceStore(tmp_path)
    adapter = PublicVideoMetadataAdapter(
        api_key="present",
        fetch_json=lambda _url: {"items": [{"snippet": {"channelTitle": "Comic Test", "publishedAt": "2026-10-01T00:00:00Z"},
                                               "statistics": {"viewCount": "42"},
                                               "contentDetails": {"duration": "PT1M5S"}}]},
    )

    signal = adapter.refresh(store, "abc", candidate_key="hero|rule|question")

    assert signal["current_views"] == 42
    assert signal["channel"] == "Comic Test"
    assert signal["published_at"] == "2026-10-01T00:00:00Z"
    assert signal["duration_seconds"] == 65.0


def test_no_key_uses_injected_ytdlp_public_metadata_fallback_without_media(tmp_path):
    store = AudienceEvidenceStore(tmp_path)
    adapter = PublicVideoMetadataAdapter(api_key="", metadata_fetch=lambda _id: {
        "id": "abc", "webpage_url": "https://www.youtube.com/watch?v=abc", "channel": "Comic Test",
        "timestamp": 1790812800, "duration": 55, "view_count": 31,
    })

    signal = adapter.refresh(store, "abc", candidate_key="hero|rule|question")

    assert signal["current_views"] == 31
    assert signal["duration_seconds"] == 55
    assert signal["channel"] == "Comic Test"


def test_missing_views_from_either_provider_keeps_last_good_snapshot_without_fake_zero(tmp_path):
    store = AudienceEvidenceStore(tmp_path)
    store.add_video_snapshot(video_id="abc", url="https://youtu.be/abc", channel="Comic Test",
                             published_at=None, duration_seconds=45, views=99, origin="manual_import")
    api = PublicVideoMetadataAdapter(api_key="present", fetch_json=lambda _url: {"items": [{
        "snippet": {"channelTitle": "Comic Test"}, "statistics": {}, "contentDetails": {"duration": "PT45S"}}]})
    ytdlp = PublicVideoMetadataAdapter(api_key="", metadata_fetch=lambda _id: {
        "id": "abc", "channel": "Comic Test", "duration": 45})

    api_result = api.refresh(store, "abc")
    ytdlp_result = ytdlp.refresh(store, "abc")

    assert api_result["current_views"] == 99
    assert ytdlp_result["current_views"] == 99
    assert api_result["last_refresh_error"] == "metadata_missing_views"
    assert ytdlp_result["last_refresh_error"] == "metadata_missing_views"


def test_linked_competitor_is_related_observation_not_exact_coverage_and_refresh_is_bounded(tmp_path):
    store = AudienceEvidenceStore(tmp_path)
    store.link_video("hero|rule|question", "abc")
    store.add_video_snapshot(video_id="abc", url="https://youtu.be/abc", channel="Comic Test",
                             published_at=None, duration_seconds=None, views=42,
                             observed_at=datetime.now(timezone.utc).isoformat())
    calls = []
    adapter = PublicVideoMetadataAdapter(api_key="present", fetch_json=lambda url: calls.append(url) or {"items": []})

    result = adapter.refresh_many(store, "hero|rule|question", min_refresh_seconds=3600, limit=1)
    saturation = store.signals_for("hero|rule|question")["saturation"]

    assert result["skipped_fresh"] == ["abc"]
    assert calls == []
    assert saturation["videos"][0]["relation"] == "related"
    assert saturation["coverage_proof"] is False


def test_registry_migrates_v1_links_without_losing_existing_snapshot(tmp_path):
    path = tmp_path / "audience_evidence.sqlite3"
    with sqlite3.connect(path) as connection:
        connection.executescript("""
            CREATE TABLE candidate_videos (candidate_key TEXT NOT NULL, video_id TEXT NOT NULL,
                PRIMARY KEY(candidate_key, video_id));
            CREATE TABLE video_snapshots (id INTEGER PRIMARY KEY, video_id TEXT NOT NULL, url TEXT NOT NULL,
                channel TEXT NOT NULL, published_at TEXT, duration_seconds REAL, views INTEGER NOT NULL,
                observed_at TEXT NOT NULL);
            CREATE TABLE demand_evidence (id INTEGER PRIMARY KEY, candidate_key TEXT NOT NULL, query_text TEXT NOT NULL,
                url TEXT NOT NULL, quote TEXT NOT NULL, source_type TEXT NOT NULL, confidence TEXT NOT NULL,
                checked_at TEXT NOT NULL);
            CREATE TABLE video_refreshes (id INTEGER PRIMARY KEY, video_id TEXT NOT NULL, checked_at TEXT NOT NULL,
                error TEXT NOT NULL);
            INSERT INTO candidate_videos VALUES ('hero|rule|question', 'abc');
            INSERT INTO video_snapshots VALUES (1, 'abc', 'https://youtu.be/abc', 'Comic Test', NULL, NULL, 42,
                '2026-10-27T00:00:00+00:00');
            PRAGMA user_version=1;
        """)

    signal = AudienceEvidenceStore(tmp_path).signals_for("hero|rule|question")["saturation"]

    assert signal["videos"][0]["current_views"] == 42
    assert signal["videos"][0]["relation"] == "related"


def test_quality_assessment_persists_with_assessor_provenance(tmp_path):
    store = AudienceEvidenceStore(tmp_path)
    assessment = {"dimensions": {"curiosity": {"score": 0.7, "rationale": "Question.",
                                                "fact_cues": [{"url": "https://example.test", "quote": "Fact."}]}}}

    store.save_quality_assessment("hero|rule|question", assessment, assessor="model:test")

    saved = AudienceEvidenceStore(tmp_path).quality_for("hero|rule|question")
    assert saved["assessor"] == "model:test"
    assert saved["dimensions"]["curiosity"]["score"] == 0.7


def test_manual_import_and_offline_refresh_keep_candidate_and_existing_snapshot(tmp_path, monkeypatch):
    monkeypatch.setattr(PublicVideoMetadataAdapter, "_ytdlp_metadata", staticmethod(
        lambda _video_id: (_ for _ in ()).throw(RuntimeError("offline"))))
    sessions = tmp_path / "research_sessions"
    store = SessionStore(sessions)
    session = store.create(mode="qa", user_intent="Why does Hero change?")
    candidate = {
        "id": "c1", "title": "Hero", "question": "Why does Hero change?",
        "canonical_anchor": "Hero", "concept_key": "change", "what_visibly_happens": "Hero changes course.",
        "claim_citation": {"url": "https://example.test", "quote": "Hero changes course."}, "evidence_urls": ["https://example.test"],
    }
    store.write_artifact(session.id, "general/candidates.v1.json", {"candidates": [candidate]})

    updated = update_scout_audience(session.id, "c1", root=sessions, manual_import={
        "demand": [{"query": "why hero change", "url": "https://forum.example/q", "quote": "Why did Hero change?", "source_type": "community", "confidence": "direct"}],
        "videos": [{"video_id": "abc", "url": "https://youtu.be/abc", "channel": "Comic Test", "published_at": "2026-10-01T00:00:00Z", "duration_seconds": 45, "views": 100, "observed_at": "2026-10-20T00:00:00+00:00"}],
    }, refresh=True)

    card = updated[0]
    assert card["id"] == "c1"
    assert card["audience_evidence"]["demand_evidence"][0]["url"] == "https://forum.example/q"
    assert card["audience_evidence"]["signals"]["saturation"]["videos"][0]["current_views"] == 100
    assert card["audience_evidence"]["signals"]["saturation"]["videos"][0]["last_refresh_error"] == "offline"


def test_manual_quality_updates_ranked_card_with_grounded_cue(tmp_path):
    sessions = tmp_path / "research_sessions"
    store = SessionStore(sessions)
    session = store.create(mode="qa", user_intent="Why does Hero change?")
    candidate = {"id": "c1", "title": "Hero", "question": "Why does Hero change?", "canonical_anchor": "Hero",
                 "concept_key": "change", "claim_citation": {"url": "https://example.test", "quote": "Hero changes course."}}
    store.write_artifact(session.id, "general/candidates.v1.json", {"candidates": [candidate]})

    updated = update_scout_audience(session.id, "c1", root=sessions, manual_quality={"dimensions": {
        "curiosity": {"score": 0.8, "rationale": "The choice poses a question.",
                      "fact_cues": [{"url": "https://example.test", "quote": "Hero changes course."}]},
        "stakes": {"score": None, "rationale": "Unknown.", "fact_cues": []},
        "payoff": {"score": None, "rationale": "Unknown.", "fact_cues": []},
        "clarity": {"score": None, "rationale": "Unknown.", "fact_cues": []},
    }})

    assert updated[0]["editorial_quality"]["status"] == "assessed"
    assert updated[0]["editorial_quality"]["dimensions"]["curiosity"]["score"] == 0.8


def test_session_free_partial_entries_keep_duplicate_ids_and_accept_link_url(tmp_path, monkeypatch):
    monkeypatch.setattr(PublicVideoMetadataAdapter, "_ytdlp_metadata", staticmethod(lambda _id: {
        "id": "PsoefS94HSY", "webpage_url": "https://youtube.com/shorts/PsoefS94HSY", "channel": "Seed",
        "timestamp": 1, "duration": 60, "view_count": 3}))
    entries = [{"id": "candidate-2", "question": f"Question {index}?", "issue_evidence": [{
        "source_url": "https://example.test", "quote": f"Fact {index}."}]} for index in range(3)]

    annotated = annotate_scout_entries(entries, root=tmp_path)
    updated = update_scout_entry_evidence(annotated[0], root=tmp_path,
        video_url="https://www.youtube.com/shorts/PsoefS94HSY", refresh=True)

    assert [entry["id"] for entry in annotated] == ["candidate-2"] * 3
    assert updated["audience_evidence"]["signals"]["saturation"]["videos"][0]["current_views"] == 3
    assert parse_public_video_id("https://youtu.be/PsoefS94HSY") == "PsoefS94HSY"
