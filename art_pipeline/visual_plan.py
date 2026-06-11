"""Visual plan sidecar (spec 2026-06-11 §A4b): per-scene declaration of what is
on screen. Lives next to narration.json so the comic Stage 3 schema (which
Stage 4 TTS reads) is never extended.

Plan entry: {"scene_id", "kind", "panel_ref", "subject", "motion", "fallback",
             optionally "page_ref" once hunt resolves a related image}.
Motion is DERIVED from kind (single source of truth: MOTION mapping here);
narrate writes it for transparency and hunt re-derives after fallbacks."""
import json
from pathlib import Path

KINDS = ("painting_region", "painting_full", "related")

# The "zoom with intent" requirement: real zoom only on painting regions.
_ZOOM_ALTERNATION = ("zoom_in", "zoom_out")
_FULL_INTRO_MOTION = "static"
_FULL_OTHER_MOTION = "zoom_out"   # subtle pull-back; never dead-static mid-video
_RELATED_MOTION = "pan_right"     # light drift, never zoom


def parse_visual(raw: dict, *, scene_id: int) -> dict:
    """Validate one LLM-supplied visual declaration. Raises ValueError with a
    scene-specific message so the narrate retry loop can feed it back."""
    kind = str((raw or {}).get("kind") or "").strip()
    if kind not in KINDS:
        raise ValueError(f"visual plan: scene {scene_id} unknown kind {kind!r} "
                         f"(allowed: {', '.join(KINDS)})")
    panel_ref = -1
    subject = ""
    if kind == "painting_region":
        try:
            panel_ref = int(raw.get("panel_ref"))
        except (TypeError, ValueError):
            panel_ref = -1
        if panel_ref < 0:
            raise ValueError(f"visual plan: scene {scene_id} painting_region "
                             f"needs a valid panel_ref (got {raw.get('panel_ref')!r})")
    elif kind == "related":
        subject = str(raw.get("subject") or "").strip()
        if not subject:
            raise ValueError(f"visual plan: scene {scene_id} related needs a "
                             "non-empty subject describing the image to find")
    return {"scene_id": scene_id, "kind": kind, "panel_ref": panel_ref,
            "subject": subject, "motion": "", "fallback": ""}


def assign_motions(plan: list[dict], *, intro_scene_id: int | None = None) -> None:
    """In-place: zoom_in/zoom_out alternate across painting_region scenes;
    painting_full is static at the intro, subtle zoom_out elsewhere; related
    always drifts (pan_right)."""
    zoom_i = 0
    for d in plan:
        if d["kind"] == "painting_region":
            d["motion"] = _ZOOM_ALTERNATION[zoom_i % len(_ZOOM_ALTERNATION)]
            zoom_i += 1
        elif d["kind"] == "painting_full":
            d["motion"] = (_FULL_INTRO_MOTION
                           if d["scene_id"] == intro_scene_id else _FULL_OTHER_MOTION)
        else:
            d["motion"] = _RELATED_MOTION


def visual_target(scene: dict, decl: dict):
    """Identity of what's on screen — used by the no-repeat rules."""
    if decl["kind"] == "painting_region":
        return ("r", scene.get("page_ref"), decl["panel_ref"])
    if decl["kind"] == "painting_full":
        return ("f", scene.get("page_ref"))
    return ("x", " ".join(decl["subject"].lower().split()))


def validate_variety(scenes: list[dict], plan_by_id: dict[int, dict]) -> None:
    """The 4 hard anti-repeat rules (spec §A4b). Raises ValueError naming the
    violated rule; the narrate retry loop feeds the message back to the LLM.

    Check order: global structural violations (3, 4, 2) before layout (1) so
    the error message names the real offending rule even when two rules fire."""
    targets = []
    for s in scenes:
        d = plan_by_id.get(s["scene_id"])
        if d is None:
            raise ValueError(f"visual plan: scene {s['scene_id']} has no visual declaration")
        targets.append((s, d, visual_target(s, d)))

    # 3. painting_full only at intro, outro, and at most once mid-video
    mid_fulls = [s["scene_id"] for s, d, _ in targets
                 if d["kind"] == "painting_full"
                 and not s.get("is_intro") and not s.get("is_outro")]
    if len(mid_fulls) > 1:
        raise ValueError(f"visual plan: painting_full used mid-video in scenes "
                         f"{mid_fulls} — allowed at intro, outro, and at most ONE mid scene")
    # 4. related subjects pairwise distinct
    seen_subjects: set = set()
    for s, d, t in targets:
        if d["kind"] == "related":
            if t in seen_subjects:
                raise ValueError(f"visual plan: scene {s['scene_id']} repeats related "
                                 f"subject {d['subject']!r} — subjects must differ")
            seen_subjects.add(t)
    # 1. no two consecutive scenes share a target (before region-reuse so
    #    consecutive identical regions surface as "consecutive", not "region")
    for (s1, _, t1), (s2, _, t2) in zip(targets, targets[1:]):
        if t1 == t2:
            raise ValueError(f"visual plan: scenes {s1['scene_id']} and "
                             f"{s2['scene_id']} show the same thing consecutive — vary them")
    # 2. each painting region used at most once (non-consecutive reuse)
    seen_regions: set = set()
    for s, d, t in targets:
        if d["kind"] == "painting_region":
            if t in seen_regions:
                raise ValueError(f"visual plan: scene {s['scene_id']} reuses painting "
                                 f"region {d['panel_ref']} — each region at most once")
            seen_regions.add(t)


def derive_trivial_plan(narration: dict) -> list[dict]:
    """All-painting plan for legacy projects with no visual_plan.json: scenes
    with a grounded panel_ref become painting_region, the rest painting_full."""
    plan: list[dict] = []
    intro_id = None
    for s in narration.get("scenes") or []:
        if s.get("is_intro"):
            intro_id = s["scene_id"]
        pr = int(s.get("panel_ref", -1))
        if pr >= 0:
            plan.append({"scene_id": s["scene_id"], "kind": "painting_region",
                         "panel_ref": pr, "subject": "", "motion": "", "fallback": ""})
        else:
            plan.append({"scene_id": s["scene_id"], "kind": "painting_full",
                         "panel_ref": -1, "subject": "", "motion": "", "fallback": ""})
    assign_motions(plan, intro_scene_id=intro_id)
    return plan


def save_plan(root: Path, plan: list[dict]) -> None:
    (root / "visual_plan.json").write_text(
        json.dumps(plan, indent=2, ensure_ascii=False))


def load_plan(root: Path) -> list[dict] | None:
    p = root / "visual_plan.json"
    if not p.exists():
        return None
    return json.loads(p.read_text())
