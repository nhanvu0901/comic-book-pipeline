"""Stage 4/5 hash-guard: audio.wav is cached, so a narration.json edit without
--force must be caught instead of silently pairing new captions with old audio."""
import pytest

from stages.stage_4.pipeline import narration_hash, verify_narration_hash

SCENES = [{"text": "Hello world."}, {"text": "Goodbye."}]


def test_hash_write_then_match_passes(tmp_path):
    sidecar = tmp_path / "narration.tts.sha256"
    sidecar.write_text(narration_hash(SCENES))
    verify_narration_hash(sidecar, SCENES)  # no raise


def test_hash_mismatch_raises(tmp_path):
    sidecar = tmp_path / "narration.tts.sha256"
    sidecar.write_text(narration_hash(SCENES))
    edited = [{"text": "Hello world, changed."}, {"text": "Goodbye."}]
    with pytest.raises(RuntimeError, match="hash mismatch"):
        verify_narration_hash(sidecar, edited)


def test_missing_sidecar_warns_and_proceeds(tmp_path):
    sidecar = tmp_path / "narration.tts.sha256"  # never written
    warnings = []
    verify_narration_hash(sidecar, SCENES, log=warnings.append)
    assert len(warnings) == 1
    assert "missing" in warnings[0]
