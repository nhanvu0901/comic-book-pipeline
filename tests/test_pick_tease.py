"""_pick_tease: rotates the hook-ending tease line per project, skipping teases
another project's narration.json already closed on, so consecutive channel
videos don't collide on the same line."""
import json

from stages.stage_3.explore_answer import _pick_tease

_POOL = ("tease-a", "tease-b", "tease-c")


def _write_narration(projects_dir, slug, hook):
    project = projects_dir / slug
    project.mkdir(parents=True, exist_ok=True)
    (project / "narration.json").write_text(json.dumps({"hook": hook}))


def test_excludes_tease_used_by_other_project(tmp_path):
    _write_narration(tmp_path, "mephisto-defeated", "Who beat him? Mephisto did. tease-a")
    picked = _pick_tease(_POOL, "carnage-venom-cant", projects_dir=tmp_path)
    assert picked != "tease-a"


def test_same_slug_is_deterministic(tmp_path):
    _write_narration(tmp_path, "other-project", "Some hook. tease-b")
    first = _pick_tease(_POOL, "carnage-venom-cant", projects_dir=tmp_path)
    second = _pick_tease(_POOL, "carnage-venom-cant", projects_dir=tmp_path)
    assert first == second


def test_own_prior_hook_not_excluded(tmp_path):
    # A retry on the SAME project must not starve its own candidate pool
    # against its own previous hook.
    _write_narration(tmp_path, "carnage-venom-cant", "Some hook. tease-a")
    picked = _pick_tease(_POOL, "carnage-venom-cant", projects_dir=tmp_path)
    assert picked in _POOL  # unrestricted — own project excluded from "used" scan


def test_falls_back_to_full_pool_when_all_used(tmp_path):
    for i, tease in enumerate(_POOL):
        _write_narration(tmp_path, f"proj-{i}", f"Some hook. {tease}")
    picked = _pick_tease(_POOL, "carnage-venom-cant", projects_dir=tmp_path)
    assert picked in _POOL


def test_corrupt_narration_json_does_not_crash(tmp_path):
    project = tmp_path / "broken-project"
    project.mkdir(parents=True, exist_ok=True)
    (project / "narration.json").write_text("{not valid json")
    picked = _pick_tease(_POOL, "carnage-venom-cant", projects_dir=tmp_path)
    assert picked in _POOL


def test_missing_hook_field_does_not_crash(tmp_path):
    project = tmp_path / "no-hook-project"
    project.mkdir(parents=True, exist_ok=True)
    (project / "narration.json").write_text(json.dumps({"scenes": []}))
    picked = _pick_tease(_POOL, "carnage-venom-cant", projects_dir=tmp_path)
    assert picked in _POOL
