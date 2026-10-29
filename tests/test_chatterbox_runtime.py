"""Reproducible, local packaging of the Chatterbox runtime used only by Stage 4."""
import hashlib

import pytest

from stages.stage_4 import chatterbox_runtime as runtime


def test_patch_upstream_pyproject_only_changes_version_and_safetensors_pin(tmp_path):
    pyproject = tmp_path / "pyproject.toml"
    original = (
        '[project]\nname = "chatterbox-tts"\nversion = "0.1.7"\n'
        'dependencies = ["torch==2.6.0", "safetensors==0.5.3"]\n'
    )
    pyproject.write_text(original)

    runtime.patch_upstream_pyproject(pyproject)

    patched = pyproject.read_text()
    assert 'version = "0.1.7+cbp.1"' in patched
    assert '"safetensors==0.8.0"' in patched
    assert patched.replace('version = "0.1.7+cbp.1"', 'version = "0.1.7"').replace(
        '"safetensors==0.8.0"', '"safetensors==0.5.3"') == original


def test_patch_rejects_unexpected_upstream_metadata(tmp_path):
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text('version = "0.1.8"\n"safetensors==0.5.3"\n')

    with pytest.raises(RuntimeError, match="expected upstream metadata"):
        runtime.patch_upstream_pyproject(pyproject)


def test_verify_sha256_rejects_a_different_source_archive(tmp_path):
    archive = tmp_path / "chatterbox.tar.gz"
    archive.write_bytes(b"known bytes")
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()

    runtime.verify_sha256(archive, digest)
    with pytest.raises(RuntimeError, match="SHA-256 mismatch"):
        runtime.verify_sha256(archive, "0" * 64)
