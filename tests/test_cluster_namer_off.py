"""CLUSTER_NAMER is off by default — the VLM naming pass reaches no render decision.

Reference-counted by hand before switching it off: cluster_to_name.json is read in
review_gate and stage_5/pipeline, threaded through six signatures in stage_5/shots.py, and
ends at _match_panels(units, pages_by_number, cluster_to_name, ...) — which never reads the
argument. No other consumer maps a cluster id to a name either: speaker_cluster_id appears
only inside Stage 2, and Stage 3 never touches clusters.

Disabled, not deleted: money_shot.py imports a helper out of cluster_namer, and the module is
one env var away from coming back.
"""
import inspect
import re
from pathlib import Path

import config
from stages.stage_2 import pipeline as S2
from stages.stage_5 import shots as S5

REPO = Path(__file__).resolve().parent.parent


def test_off_by_default():
    assert config.CLUSTER_NAMER is False


def test_the_pass_is_skipped_and_writes_nothing(tmp_path, monkeypatch):
    monkeypatch.setattr(S2, "CLUSTER_NAMER", False)
    msgs = []
    pages = [{"panels": [{"cluster_ids": [1, 2]}]}]
    (tmp_path / "comic_context.json").write_text('{"title": "T"}')

    S2._resolve_clusters_after_preprocess(pages, tmp_path, msgs.append)

    assert not (tmp_path / "cluster_to_name.json").exists()
    assert any("SKIPPED" in m for m in msgs)


def test_the_matcher_still_ignores_the_argument():
    """The reason this is safe. If someone ever makes _match_panels USE cluster names, this
    fails and the switch has to be reconsidered rather than silently degrading picks."""
    src = inspect.getsource(S5._match_panels)
    body = src.split(")", 1)[1]                       # drop the signature line
    assert "cluster_to_name" not in body


def test_the_module_is_still_importable():
    """Disabled, not deleted — money_shot.py imports _parse_vlm_response from it, and that is
    exactly the kind of dangling import that left art_pipeline.assemble broken unnoticed."""
    from stages.stage_2 import cluster_namer
    assert callable(cluster_namer.resolve_cluster_names)
    from stages import money_shot                     # noqa: F401  — the borrower still loads


def test_the_knob_brings_it_back():
    """A disabled path has to be re-enablable, or it is deletion with extra steps."""
    src = (REPO / "stages" / "stage_2" / "pipeline.py").read_text(encoding="utf-8")
    assert re.search(r"if not CLUSTER_NAMER:", src)
