"""Narration-driven panel matcher (final update of beat) — _match_panels_forward.

Mocks _panel_content_score so the FORWARD-ONLY / NO-REUSE / HOLD-while-same-subject /
ADVANCE-on-new-subject control flow is asserted deterministically (no embeddings,
offline). The scorer returns (10.0, 0.9) when a panel's tag word appears in the
narration text, else (0.5, 0.1) — the 2nd value is the raw cosine the floor checks,
so 0.1 < PANEL_COS_FLOOR drives the weak-hold path — and we control every decision."""
import stages.stage_5.shots as shots


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


def _idx_seq(monkeypatch):
    monkeypatch.setattr(shots, "_panel_content_score", _fake_score)
    pages = _page(["alpha", "beta", "gamma"])
    scene = {"scene_id": 1, "text": "x"}
    units = [
        (scene, "alpha scene"),     # u0 cold-start → OPEN alpha (p0)
        (scene, "alpha again"),     # u1 same subject → HOLD p0
        (scene, "beta now"),        # u2 new subject forward → ADVANCE p1
        (scene, "alpha returns"),   # u3 best match is BEHIND cursor → forward-only → HOLD p1
        (scene, "gamma end"),       # u4 forward → ADVANCE p2
    ]
    out = shots._match_panels_forward(units, pages, {})
    return [(p["index"] if p else None) for p, _src in out]


def test_forward_only_hold_advance(monkeypatch):
    seq = _idx_seq(monkeypatch)
    # cold-open, hold, advance, forward-only-hold, advance
    assert seq == [0, 0, 1, 1, 2]
    # FORWARD-ONLY: panel index never decreases
    assert all(b >= a for a, b in zip(seq, seq[1:]))


def test_no_reuse(monkeypatch):
    seq = _idx_seq(monkeypatch)
    # each DISTINCT panel is claimed at most once (holds repeat the current one, but
    # the matcher never RE-CLAIMS a panel it already advanced past)
    claimed_order = [seq[0]] + [b for a, b in zip(seq, seq[1:]) if b != a]
    assert claimed_order == sorted(set(claimed_order))   # strictly increasing, no repeats


def test_weak_match_holds_not_force(monkeypatch):
    # Every forward panel scores low for the unit → matcher HOLDS the current panel
    # instead of forcing an unrelated one (Master's "giữ panel hiện tại" rule).
    monkeypatch.setattr(shots, "_panel_content_score", _fake_score)
    pages = _page(["alpha", "beta"])
    scene = {"scene_id": 1, "text": "x"}
    units = [(scene, "alpha here"), (scene, "something off-panel nobody drew")]
    out = shots._match_panels_forward(units, pages, {})
    idx = [p["index"] if p else None for p, _ in out]
    assert idx == [0, 0]   # opened on alpha, then HELD it (no forced beta)
