"""LM Studio sequencing (2026-07-10): on a 16GB Mac, LM Studio serves Qwen3-Embedding-8B
(:1234) as a SEPARATE OS process that release_model() can't touch. If it's still JIT-loaded
when Magi's batch-detect phase runs, both sit in RAM at once and push the box into swap.
_lms_unload_all() frees it before Magi; _ensure_embed_model_loaded() JIT-reloads it before
index_project() needs it. Both are best-effort (no LM Studio installed -> silent no-op) and
gated by LMS_AUTO_UNLOAD + the configured embedding backend actually being LM Studio's.
No real subprocess/HTTP call is made anywhere in this file."""
from unittest.mock import patch

import stages.stage_2.pipeline as pipeline


def _log(msgs):
    return lambda m: msgs.append(m)


# ─── _lms_unload_all ───────────────────────────────────────────────────────

def test_unload_skipped_when_knob_off():
    with patch.object(pipeline, "LMS_AUTO_UNLOAD", False), \
         patch.object(pipeline, "_lms_relevant", return_value=True), \
         patch("shutil.which", return_value="/usr/local/bin/lms") as which, \
         patch("subprocess.run") as run:
        pipeline._lms_unload_all(print)
    which.assert_not_called()
    run.assert_not_called()


def test_unload_skipped_when_backend_not_lm_studio():
    with patch.object(pipeline, "LMS_AUTO_UNLOAD", True), \
         patch.object(pipeline, "_lms_relevant", return_value=False), \
         patch("shutil.which", return_value="/usr/local/bin/lms") as which, \
         patch("subprocess.run") as run:
        pipeline._lms_unload_all(print)
    which.assert_not_called()
    run.assert_not_called()


def test_unload_no_binary_does_not_raise():
    msgs = []
    with patch.object(pipeline, "LMS_AUTO_UNLOAD", True), \
         patch.object(pipeline, "_lms_relevant", return_value=True), \
         patch("shutil.which", return_value=None), \
         patch.object(pipeline.Path, "exists", return_value=False), \
         patch("subprocess.run") as run:
        pipeline._lms_unload_all(_log(msgs))  # must not raise
    run.assert_not_called()


def test_unload_calls_lms_unload_all_when_relevant():
    with patch.object(pipeline, "LMS_AUTO_UNLOAD", True), \
         patch.object(pipeline, "_lms_relevant", return_value=True), \
         patch.object(pipeline, "_lms_kill_zombie_nodes"), \
         patch("shutil.which", return_value="/usr/local/bin/lms"), \
         patch("subprocess.run") as run:
        pipeline._lms_unload_all(print)
    run.assert_called_once()
    args = run.call_args[0][0]
    assert args == ["/usr/local/bin/lms", "unload", "--all"]
    assert run.call_args.kwargs.get("timeout") == 20


def test_unload_timeout_is_swallowed():
    msgs = []
    with patch.object(pipeline, "LMS_AUTO_UNLOAD", True), \
         patch.object(pipeline, "_lms_relevant", return_value=True), \
         patch("shutil.which", return_value="/usr/local/bin/lms"), \
         patch("subprocess.run", side_effect=pipeline.subprocess.TimeoutExpired(cmd="lms", timeout=20)):
        pipeline._lms_unload_all(_log(msgs))  # must not raise
    assert any("unload" in m for m in msgs)


def test_unload_all_triggers_zombie_sweep_after_success():
    with patch.object(pipeline, "LMS_AUTO_UNLOAD", True), \
         patch.object(pipeline, "_lms_relevant", return_value=True), \
         patch("shutil.which", return_value="/usr/local/bin/lms"), \
         patch("subprocess.run"), \
         patch.object(pipeline, "_lms_kill_zombie_nodes") as sweep:
        pipeline._lms_unload_all(print)
    sweep.assert_called_once()


def test_unload_all_skips_zombie_sweep_when_unload_raises():
    with patch.object(pipeline, "LMS_AUTO_UNLOAD", True), \
         patch.object(pipeline, "_lms_relevant", return_value=True), \
         patch("shutil.which", return_value="/usr/local/bin/lms"), \
         patch("subprocess.run", side_effect=pipeline.subprocess.TimeoutExpired(cmd="lms", timeout=20)), \
         patch.object(pipeline, "_lms_kill_zombie_nodes") as sweep:
        pipeline._lms_unload_all(print)
    sweep.assert_not_called()


# ─── _lms_kill_zombie_nodes ─────────────────────────────────────────────────

def _fake_ps_run(lms_ps_stdout, ps_axo_stdout):
    """subprocess.run stand-in: routes `[binary, "ps"]` vs `["ps", "-axo", ...]` to
    the two different canned outputs the real command would produce."""
    def _run(cmd, **kwargs):
        result = type("_Result", (), {})()
        result.stdout = ps_axo_stdout if cmd[0] == "ps" else lms_ps_stdout
        return result
    return _run


def test_zombie_sweep_skips_when_lms_ps_shows_loaded_model():
    with patch.object(pipeline, "_lms_bin", return_value="/usr/local/bin/lms"), \
         patch("time.sleep"), \
         patch("subprocess.run", side_effect=_fake_ps_run("Qwen3  LOADED  4.2GB", "")) as run, \
         patch("os.kill") as kill:
        pipeline._lms_kill_zombie_nodes(print)
    kill.assert_not_called()
    assert run.call_count == 1  # never even scanned `ps -axo` — a real model is up


def test_zombie_sweep_kills_fat_node_when_nothing_loaded():
    node_line = "  4242  6291456 /Users/x/.lmstudio/.internal/utils/node server.js"
    msgs = []
    with patch.object(pipeline, "_lms_bin", return_value="/usr/local/bin/lms"), \
         patch("time.sleep"), \
         patch("subprocess.run", side_effect=_fake_ps_run("", node_line)), \
         patch("os.kill") as kill:
        pipeline._lms_kill_zombie_nodes(_log(msgs))
    kill.assert_called_once_with(4242, pipeline.signal.SIGKILL)
    assert any("killed zombie node pid=4242" in m for m in msgs)


def test_zombie_sweep_leaves_small_node_alone():
    node_line = "  4242  512000 /Users/x/.lmstudio/.internal/utils/node server.js"
    with patch.object(pipeline, "_lms_bin", return_value="/usr/local/bin/lms"), \
         patch("time.sleep"), \
         patch("subprocess.run", side_effect=_fake_ps_run("", node_line)), \
         patch("os.kill") as kill:
        pipeline._lms_kill_zombie_nodes(print)
    kill.assert_not_called()


def test_zombie_sweep_no_binary_does_not_raise():
    with patch.object(pipeline, "_lms_bin", return_value=None), \
         patch("subprocess.run") as run, \
         patch("os.kill") as kill:
        pipeline._lms_kill_zombie_nodes(print)  # must not raise
    run.assert_not_called()
    kill.assert_not_called()


# ─── _ensure_embed_model_loaded ────────────────────────────────────────────

class _FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def read(self):
        import json
        return json.dumps(self._payload).encode()


def test_ensure_loaded_skipped_when_knob_off():
    with patch.object(pipeline, "LMS_AUTO_UNLOAD", False), \
         patch.object(pipeline, "_lms_relevant", return_value=True), \
         patch("shutil.which", return_value="/usr/local/bin/lms") as which, \
         patch("subprocess.run") as run:
        pipeline._ensure_embed_model_loaded(print)
    which.assert_not_called()
    run.assert_not_called()


def test_ensure_loaded_noop_when_model_already_loaded():
    with patch.object(pipeline, "LMS_AUTO_UNLOAD", True), \
         patch.object(pipeline, "_lms_relevant", return_value=True), \
         patch("shutil.which", return_value="/usr/local/bin/lms"), \
         patch("urllib.request.urlopen",
               return_value=_FakeResponse({"data": [{"id": "text-embedding-qwen3-embedding-8b"}]})), \
         patch("subprocess.run") as run:
        pipeline._ensure_embed_model_loaded(print)
    run.assert_not_called()


def test_ensure_loaded_loads_missing_model_by_configured_key():
    with patch.object(pipeline, "LMS_AUTO_UNLOAD", True), \
         patch.object(pipeline, "_lms_relevant", return_value=True), \
         patch("shutil.which", return_value="/usr/local/bin/lms"), \
         patch("urllib.request.urlopen",
               return_value=_FakeResponse({"data": [{"id": "some-other-model"}]})), \
         patch("subprocess.run") as run:
        pipeline._ensure_embed_model_loaded(print)
    run.assert_called_once()
    args = run.call_args[0][0]
    assert args[0] == "/usr/local/bin/lms"
    assert args[1] == "load"
    assert args[2] == "text-embedding-qwen3-embedding-8b"  # config.EMBED_OPENAI_MODEL default
    assert run.call_args.kwargs.get("timeout") == 120


def test_ensure_loaded_probe_failure_still_attempts_load():
    msgs = []
    with patch.object(pipeline, "LMS_AUTO_UNLOAD", True), \
         patch.object(pipeline, "_lms_relevant", return_value=True), \
         patch("shutil.which", return_value="/usr/local/bin/lms"), \
         patch("urllib.request.urlopen", side_effect=OSError("connection refused")), \
         patch("subprocess.run") as run:
        pipeline._ensure_embed_model_loaded(_log(msgs))
    run.assert_called_once()
    assert any("probe failed" in m for m in msgs)


def test_ensure_loaded_no_binary_does_not_raise():
    with patch.object(pipeline, "LMS_AUTO_UNLOAD", True), \
         patch.object(pipeline, "_lms_relevant", return_value=True), \
         patch("shutil.which", return_value=None), \
         patch.object(pipeline.Path, "exists", return_value=False), \
         patch("subprocess.run") as run:
        pipeline._ensure_embed_model_loaded(print)  # must not raise
    run.assert_not_called()
