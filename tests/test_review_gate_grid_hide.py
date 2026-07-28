"""Review Beats UI — candidate GRID (fast picking) + project-wide HIDDEN-PANEL blacklist.

Pure helpers (hidden_set / filter_hidden / beats_locking / grid geometry) are tested against
the REAL psylocke-blood-hunt export, then the whole screen is built headless (FakePage) and a
✕ click is replayed end-to-end in a sandbox project copy.
"""
import json
import shutil

import pytest

import ui  # noqa: F401 — patches flet 0.85 compat (ft.padding.symmetric etc.) before screens
import flet as ft

from config import PROJECTS_ROOT
import ui.bridge as bridge
import ui.screens.s_review_gate as sg
from ui.state import AppState


PROJ = "psylocke-blood-hunt"
PROJ_DIR = PROJECTS_ROOT / PROJ
needs_project = pytest.mark.skipif(
    not (PROJ_DIR / "review" / "candidates.json").exists(),
    reason=f"{PROJ} export not present",
)


# Expectations are DERIVED from the live export, never frozen as literals. Master re-picks
# panels, hides tiles and deletes scenes in this very screen, so a snapshotted "18 beats,
# 68 tiles" turns his normal edits into red tests — which is exactly what happened before
# 2026-07-27 (the export still carried rows 5:* and 9:* for scenes narration no longer had).

def _expected_rows(project_dir) -> list[str]:
    """Row keys the screen actually renders. narration.json is the source of truth; the
    screen reconciles a stale candidates.json against it and drops rows whose scene is gone."""
    nar = json.loads((project_dir / "narration.json").read_text())
    keys: list[str] = []
    for s in nar.get("scenes") or []:
        if s.get("is_intro"):
            keys.append("intro")
        elif s.get("is_outro"):
            keys.append("outro")
        else:
            frags = s.get("visual_beats") or []
            sid = s["scene_id"]
            keys += [f"{sid}:{i}" for i in range(len(frags))] if frags else [str(sid)]
    return keys


def _hidden_list(project_dir) -> list[dict]:
    hp = project_dir / "review" / "hidden_panels.json"
    return (json.loads(hp.read_text()).get("hidden") or []) if hp.exists() else []


def _pool_size(project_dir) -> int:
    """Candidates per row in the export (every row shares one pool)."""
    beats = json.loads((project_dir / "review" / "candidates.json").read_text())["beats"]
    return max(len(b.get("candidates") or []) for b in beats)


def _expected_tiles(project_dir) -> int:
    """Pool minus the project-wide hidden blacklist."""
    hp = project_dir / "review" / "hidden_panels.json"
    hidden = sg.hidden_set(json.loads(hp.read_text())) if hp.exists() else set()
    return _pool_size(project_dir) - len(hidden)


def _a_locked_panel(locks: dict) -> tuple[tuple[int, int], list[str]]:
    """Pick any panel the live locks reference → ((page, panel), beat_keys locking it)."""
    for lk in locks.values():
        for p in sg._normalize_lock_panels(lk):
            victim = (int(p["page"]), int(p["panel"]))
            owners = sorted(
                k for k, other in locks.items()
                if victim in {(int(q["page"]), int(q["panel"]))
                              for q in sg._normalize_lock_panels(other)}
            )
            return victim, owners
    raise AssertionError("live locks.json has no locked panel to test against")


# ─── pure helpers ────────────────────────────────────────────────────────────

def test_hidden_set_skips_garbage_and_missing_file():
    assert sg.hidden_set(None) == set()
    assert sg.hidden_set({}) == set()
    doc = {"hidden": [{"page": 14, "panel": 0}, {"page": "3", "panel": "2"},
                      {"page": 9}, None, {"page": "x", "panel": 1}]}
    assert sg.hidden_set(doc) == {(14, 0), (3, 2)}


def test_hidden_panels_file_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setattr(bridge, "PROJECTS_ROOT", tmp_path)
    assert bridge.load_hidden_panels("p") == {"hidden": []}     # missing file
    bridge.save_hidden_panels("p", {"hidden": [{"page": 7, "panel": 3}]})
    assert sg.hidden_set(bridge.load_hidden_panels("p")) == {(7, 3)}


@needs_project
def test_filter_hidden_drops_panel_from_every_beat():
    beats = json.loads((PROJ_DIR / "review" / "candidates.json").read_text())["beats"]
    counts = {len(b.get("candidates") or []) for b in beats}
    assert len(beats) >= 2 and len(counts) == 1, (len(beats), counts)
    pool = counts.pop()

    victim = (int(beats[0]["candidates"][5]["page"]), int(beats[0]["candidates"][5]["panel"]))
    out = sg.filter_hidden(beats, {victim})
    assert len(out) == len(beats)
    for row, orig in zip(out, beats):
        keys = {(int(c["page"]), int(c["panel"])) for c in row["candidates"]}
        assert victim not in keys
        assert len(row["candidates"]) == pool - 1
        assert row["beat_key"] == orig["beat_key"]          # other fields untouched
        assert orig["candidates"] and len(orig["candidates"]) == pool  # input not mutated

    # candidates_all keeps the pool → un-hiding restores it without re-reading the export
    back = sg.filter_hidden(out, set())
    assert all(len(r["candidates"]) == pool for r in back)


@needs_project
def test_beats_locking_real_locks():
    locks = json.loads((PROJ_DIR / "review" / "locks.json").read_text())["locks"]
    (page, panel), owners = _a_locked_panel(locks)
    assert sg.beats_locking(locks, page, panel) == owners
    assert sg.beats_locking(locks, 9999, 0) == []
    assert sg.beats_locking({}, page, panel) == []
    # v1 single-panel lock shape still resolves
    assert sg.beats_locking({"7": {"page": 2, "panel": 1}}, 2, 1) == ["7"]


def test_reconcile_keeps_bookend_rows_every_mode_and_adds_missing_ones():
    """INTRO + OUTRO rows survive reconcile for EVERY mode (Q&A included — Master 2026-07-24),
    first and last, and a candidates.json exported before the bookend rows existed gets them
    back as empty rows (the "No candidates — Rebuild/Custom image" fallback) instead of losing
    the pick. Ghost-row dropping is unchanged."""
    narration = {"scenes": [
        {"scene_id": 1, "text": "hook", "is_intro": True},
        {"scene_id": 2, "text": "a b", "visual_beats": ["a", "b"]},
        {"scene_id": 3, "text": "bye", "is_outro": True},
    ]}
    old_export = [{"beat_key": "2:0", "narration_text": "stale", "candidates": [{"page": 1}],
                   "source": {"title": "t"}, "unit": "fragment", "scene_id": 2},
                  {"beat_key": "2:1", "candidates": [], "unit": "fragment", "scene_id": 2},
                  {"beat_key": "9:0", "candidates": [], "unit": "fragment", "scene_id": 9}]
    for qa in (True, False):
        rows = sg.reconcile_beats(old_export, narration, qa_mode=qa)
        assert [r["beat_key"] for r in rows] == ["intro", "2:0", "2:1", "outro"], qa
        assert [sg._beat_unit(r) for r in rows] == ["intro", "fragment", "fragment", "outro"]
        assert rows[0]["narration_text"] == "hook" and rows[-1]["narration_text"] == "bye"
        assert rows[0]["candidates"] == [] and rows[-1]["candidates"] == []   # empty-row fallback
        assert rows[1]["narration_text"] == "a"          # text refreshed from narration
        assert rows[1]["candidates"] == [{"page": 1}]    # matcher candidates preserved
    assert sg._row_label(rows[-1], "outro", "outro") == "OUTRO: bye"
    assert sg._row_label(rows[0], "intro", "intro") == "INTRO (cold-open): hook"


def test_grid_geometry_and_offset():
    cols, pitch = sg.grid_geometry(756.0)          # 1400px window
    assert cols == 6 and 170 < pitch < 200
    assert sg.GRID_H / pitch >= 3.0                # ≥3 rows × 6 cols = ≥18 tiles visible
    assert sg.grid_scroll_offset(0, 756.0) == 0.0
    assert sg.grid_scroll_offset(5, 756.0) == 0.0           # still row 0
    assert sg.grid_scroll_offset(6, 756.0) == pytest.approx(pitch)
    assert sg.grid_scroll_offset(13, 756.0) == pytest.approx(2 * pitch)
    assert sg.grid_geometry(1.0)[0] == 1 and sg.grid_scroll_offset(-3, 100.0) == 0.0


# ─── headless build ──────────────────────────────────────────────────────────

class FakePage:
    """Minimal stand-in: records dialogs so a confirm can be replayed, no-ops the rest."""

    def __init__(self, width: float = 1400):
        self.width = width
        self.overlay: list = []
        self.services: list = []
        self.dialogs: list = []

    def show_dialog(self, d):
        self.dialogs.append(d)

    def pop_dialog(self, *a):
        pass

    def update(self, *a):
        pass

    def run_task(self, *a, **k):
        pass

    def __getattr__(self, name):
        return lambda *a, **k: None


def _walk(control, depth: int = 0):
    """Yield every control in the tree (controls / content / actions children)."""
    if control is None or depth > 60:
        return
    yield control
    for attr in ("controls", "actions"):
        for child in (getattr(control, attr, None) or []):
            yield from _walk(child, depth + 1)
    content = getattr(control, "content", None)
    if isinstance(content, ft.Control):
        yield from _walk(content, depth + 1)


def _build(project: str, monkeypatch) -> tuple[FakePage, ft.Control]:
    monkeypatch.setattr(ft.BaseControl, "update", lambda self: None)   # not mounted
    monkeypatch.setattr(sg, "save_state", lambda s: None)              # don't touch state.json
    page = FakePage()
    state = AppState(project_name=project, current_stage=5)
    root = sg.build(page, state, on_go=lambda s: None, on_state_change=lambda: None)
    return page, root


def _hide_buttons(root) -> dict[str, ft.IconButton]:
    """{"p14·0": ✕ button} — the meta row renders the label right before the ✕."""
    out: dict[str, ft.IconButton] = {}
    label = ""
    for c in _walk(root):
        if isinstance(c, ft.Text) and isinstance(c.value, str) and c.value.startswith("p"):
            label = c.value
        elif isinstance(c, ft.IconButton) and c.tooltip == "Ẩn panel này khỏi MỌI beat":
            out.setdefault(label, c)
    return out


@needs_project
def test_headless_build_has_grid_jump_and_hide(monkeypatch, capsys):
    _page, root = _build(PROJ, monkeypatch)
    nodes = list(_walk(root))
    grids = [c for c in nodes if isinstance(c, ft.GridView)]
    jumps = [c for c in nodes if isinstance(c, ft.TextField) and c.hint_text == "trang"]
    hides = [c for c in nodes if isinstance(c, ft.IconButton)
             and c.tooltip == "Ẩn panel này khỏi MỌI beat"]
    narration_fields = [c for c in nodes if isinstance(c, ft.TextField) and c.multiline]
    unhide = [c for c in nodes if isinstance(c, ft.OutlinedButton)
              and "Hiện lại tất cả" in str(c.content or "")]

    rows, tiles = _expected_rows(PROJ_DIR), _expected_tiles(PROJ_DIR)
    assert len(grids) == len(rows)                             # one per RECONCILED beat card
    assert all(len(g.controls) == tiles for g in grids)         # tiles live in the grid
    assert len(jumps) == len(rows)                             # page-jump field per card
    assert len(hides) == len(rows) * tiles                     # ✕ on every tile
    assert len(narration_fields) > 0                           # editable narration intact
    assert len(unhide) == 1                                    # header un-hide affordance
    with capsys.disabled():
        print(f"\n  grids={len(grids)} tiles/grid={len(grids[0].controls)} "
              f"jump_fields={len(jumps)} hide_buttons={len(hides)} "
              f"narration_textfields={len(narration_fields)} unhide_btn={len(unhide)}")


# ─── sandbox: ✕ end-to-end (locked panel → confirm → lock dropped) ───────────

@needs_project
def test_hide_locked_panel_end_to_end(monkeypatch, capsys):
    sandbox = PROJECTS_ROOT / "_uitest_hide"
    shutil.rmtree(sandbox, ignore_errors=True)
    shutil.copytree(PROJ_DIR, sandbox,
                    ignore=shutil.ignore_patterns("raw_comic", "panel_viz", "*.mp4", "*.wav"))
    try:
        page, root = _build("_uitest_hide", monkeypatch)
        locks_before = json.loads((sandbox / "review" / "locks.json").read_text())
        assert locks_before["approved"] is True
        (vpage, vpanel), owners = _a_locked_panel(locks_before["locks"])
        # the copied project may already have panels hidden — measure, don't assume empty
        pre_hidden = _hidden_list(sandbox)

        btn = _hide_buttons(root)[f"p{vpage}·{vpanel}"]      # a LOCKED panel → must confirm
        btn.on_click(None)
        assert len(page.dialogs) == 1, "a locked panel must confirm first"
        confirm = [a for a in page.dialogs[0].actions if str(getattr(a, "content", "")) == "Ẩn"][0]
        confirm.on_click(None)

        hidden = json.loads((sandbox / "review" / "hidden_panels.json").read_text())
        assert hidden["hidden"] == pre_hidden + [{"page": vpage, "panel": vpanel}]
        locks_doc = json.loads((sandbox / "review" / "locks.json").read_text())
        assert locks_doc["approved"] is False and locks_doc["approved_at"] is None
        # hiding must strip the panel from EVERY lock that referenced it; a lock left with
        # no panels at all is dropped outright
        assert (vpage, vpanel) not in {(p["page"], p["panel"])
                                       for lk in locks_doc["locks"].values()
                                       for p in sg._normalize_lock_panels(lk)}
        # every beat that had ONLY this panel loses its lock entirely
        for key in owners:
            before = sg._normalize_lock_panels(locks_before["locks"][key])
            if len(before) == 1:
                assert key not in locks_doc["locks"], f"{key} was {vpage}/{vpanel}-only"

        # rebuild from DISK: the panel is gone from every beat, everything else survives
        page2, root2 = _build("_uitest_hide", monkeypatch)
        grids = [c for c in _walk(root2) if isinstance(c, ft.GridView)]
        pool = _pool_size(sandbox)
        assert len(grids) == len(_expected_rows(sandbox))
        assert all(len(g.controls) == pool - len(pre_hidden) - 1 for g in grids)
        assert f"p{vpage}·{vpanel}" not in _hide_buttons(root2)
        with capsys.disabled():
            print(f"  after hide: grids={len(grids)} tiles/grid={len(grids[0].controls)} "
                  f"locks={len(locks_doc['locks'])}")

        # an UNLOCKED panel hides with NO dialog (that's the speed) …
        free = next(lbl for lbl in _hide_buttons(root2)
                    if not sg.beats_locking(locks_doc["locks"],
                                            int(lbl[1:3]), int(lbl.split("·")[1])))
        _hide_buttons(root2)[free].on_click(None)
        assert page2.dialogs == []
        on_disk = json.loads((sandbox / "review" / "hidden_panels.json").read_text())
        assert len(on_disk["hidden"]) == len(pre_hidden) + 2
        assert {"page": vpage, "panel": vpanel} in on_disk["hidden"]

        # … and un-hide all clears the blacklist
        unhide = [c for c in _walk(root2) if isinstance(c, ft.OutlinedButton)
                  and "Hiện lại tất cả" in str(c.content or "")][0]
        unhide.on_click(None)
        assert json.loads((sandbox / "review" / "hidden_panels.json").read_text()) == {"hidden": []}
        page3, root3 = _build("_uitest_hide", monkeypatch)
        grids3 = [c for c in _walk(root3) if isinstance(c, ft.GridView)]
        assert all(len(g.controls) == pool for g in grids3) and page3.dialogs == []
    finally:
        shutil.rmtree(sandbox, ignore_errors=True)
