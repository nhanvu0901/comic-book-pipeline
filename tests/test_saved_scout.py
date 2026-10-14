"""Saved discovery batches remain available without changing legacy rejections."""

from stages.research_scout.saved_scout import SavedScoutStore


def test_batch_survives_reload_with_full_payload_mode_and_context(tmp_path):
    first = {
        "question": "What does a hero's sixth sense do?",
        "viewer_hook": "How does the hero know danger is coming?",
        "proposed_format": "direct",
        "angle": "Explain a familiar power",
        "source_evidence": "Several published stories explain its limits.",
        "evidence_urls": ["https://example.org/story"],
        "subject": "A hero",
    }
    store = SavedScoutStore(tmp_path)
    batch = store.save_batch("qa", [first], context={"publication_year": None})

    restored = SavedScoutStore(tmp_path).list_entries()
    assert batch is not None
    assert len(restored) == 1
    assert restored[0]["mode"] == "qa"
    assert restored[0]["status"] == "saved"
    assert restored[0]["entry"] == first
    assert restored[0]["context"] == {"publication_year": None}


def test_reroll_defers_batch_without_rejecting_and_saved_text_stays_excluded(tmp_path):
    store = SavedScoutStore(tmp_path)
    batch = store.save_batch("micro", [{
        "moment": "A hero opens the sealed door.",
        "series_issue_year": "Hero #2 (2025)",
        "turning_point": "The lock breaks.",
        "evidence_urls": ["https://example.org/hero"],
    }])

    store.defer_batch(batch["id"])

    assert SavedScoutStore(tmp_path).list_entries()[0]["status"] == "deferred"
    assert store.avoid_texts("micro") == ["A hero opens the sealed door."]
    assert store.avoid_texts("qa") == []


def test_same_text_is_saved_once_per_mode_and_fallback_is_not_a_saved_question(tmp_path):
    store = SavedScoutStore(tmp_path)
    store.save_batch("qa", [{"question": "What does the power do?", "angle": "A"}])
    store.save_batch("qa", [
        {"question": " WHAT does  the power do? ", "angle": "B"},
        {"question": "What is another power?", "angle": "C"},
    ])
    store.save_batch("micro", [{"moment": "What does the power do?", "angle": "M"}])
    assert store.save_batch("qa", [{"question": "raw angle", "fallback": True}]) is None

    rows = store.list_entries()
    assert len(rows) == 3
    assert len([row for row in rows if row["mode"] == "qa"]) == 2
    assert rows[-1]["entry"]["angle"] == "A"


def test_only_explicit_discard_changes_status_to_rejected(tmp_path):
    store = SavedScoutStore(tmp_path)
    batch = store.save_batch("qa", [{"question": "Why did the rule fail?"}])
    store.defer_batch(batch["id"])
    row = store.list_entries()[0]
    assert row["status"] == "deferred"

    discarded = store.discard_entry(row["id"])

    assert discarded["entry"]["question"] == "Why did the rule fail?"
    assert store.list_entries() == []
    assert store.list_entries(include_discarded=True)[0]["status"] == "rejected"
    assert store.avoid_texts("qa") == ["Why did the rule fail?"]
