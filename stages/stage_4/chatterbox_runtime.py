"""Build the isolated Windows Chatterbox runtime from a verified upstream source archive.

The upstream 0.1.7 package pins safetensors 0.5.3, whose mmap loader can crash native Torch on
Windows.  We retain upstream code and models verbatim, changing only the local wheel metadata to
declare the proven 0.8.0 runtime used by ``_chatterbox_worker``.  Nothing is uploaded or installed
outside ``.venv-chatterbox``.
"""
from __future__ import annotations

import argparse
import hashlib
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
import urllib.request
import venv
from pathlib import Path

_REPO = Path(__file__).resolve().parents[2]
UPSTREAM_VERSION = "0.1.7"
LOCAL_VERSION = "0.1.7+cbp.1"
UPSTREAM_SDIST_URL = (
    "https://files.pythonhosted.org/packages/source/c/chatterbox-tts/chatterbox_tts-0.1.7.tar.gz"
)
UPSTREAM_SDIST_SHA256 = "ed8afae83819b40a25927c2ef3bcc67f928bdfcf434c1376c972e6039252a187"


def verify_sha256(path: Path, expected: str) -> None:
    actual = hashlib.sha256(path.read_bytes()).hexdigest()
    if actual != expected:
        raise RuntimeError(f"SHA-256 mismatch for {path.name}: expected {expected}, got {actual}")


def patch_upstream_pyproject(path: Path) -> None:
    """Make the two declared metadata changes, refusing a source we have not audited."""
    original = path.read_text(encoding="utf-8")
    version_line = f'version = "{UPSTREAM_VERSION}"'
    safe_pin = '"safetensors==0.5.3"'
    if original.count(version_line) != 1 or original.count(safe_pin) != 1:
        raise RuntimeError("expected upstream metadata was not found exactly once; refusing to build")
    patched = original.replace(version_line, f'version = "{LOCAL_VERSION}"', 1)
    patched = patched.replace(safe_pin, '"safetensors==0.8.0"', 1)
    path.write_text(patched, encoding="utf-8")


def _venv_python(venv_dir: Path) -> Path:
    for rel in (("Scripts", "python.exe"), ("bin", "python")):
        candidate = venv_dir.joinpath(*rel)
        if candidate.exists():
            return candidate
    return venv_dir / "Scripts" / "python.exe"


def _run(command: list[str]) -> None:
    subprocess.run(command, check=True)


def _download_sdist(cache_dir: Path) -> Path:
    archive = cache_dir / "chatterbox_tts-0.1.7.tar.gz"
    if not archive.exists():
        partial = archive.with_suffix(".part")
        try:
            with urllib.request.urlopen(UPSTREAM_SDIST_URL) as response, partial.open("wb") as out:
                shutil.copyfileobj(response, out)
            os.replace(partial, archive)
        finally:
            partial.unlink(missing_ok=True)
    verify_sha256(archive, UPSTREAM_SDIST_SHA256)
    return archive


def _extract_sdist(archive: Path, destination: Path) -> Path:
    with tarfile.open(archive, "r:gz") as tar:
        members = tar.getmembers()
        if not members:
            raise RuntimeError("upstream Chatterbox source archive is empty")
        root = destination.resolve()
        for member in members:
            if not (root / member.name).resolve().is_relative_to(root):
                raise RuntimeError(f"unsafe path in upstream source archive: {member.name}")
        tar.extractall(destination, members=members)
    source = destination / f"chatterbox_tts-{UPSTREAM_VERSION}"
    if not source.is_dir():
        raise RuntimeError("upstream source archive has an unexpected top-level directory")
    return source


def install_runtime(*, venv_dir: Path, cache_dir: Path) -> Path:
    """Build and normally install the provenance-recorded local wheel into only ``venv_dir``."""
    cache_dir.mkdir(parents=True, exist_ok=True)
    if not _venv_python(venv_dir).exists():
        venv.EnvBuilder(with_pip=True).create(str(venv_dir))
    target_python = _venv_python(venv_dir)
    archive = _download_sdist(cache_dir)
    wheels = cache_dir / "wheels"
    wheels.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="chatterbox_build_") as temp:
        source = _extract_sdist(archive, Path(temp))
        patch_upstream_pyproject(source / "pyproject.toml")
        _run([str(target_python), "-m", "pip", "wheel", "--no-deps", "--wheel-dir", str(wheels), str(source)])
    matches = sorted(wheels.glob("chatterbox_tts-0.1.7+cbp.1-*.whl"))
    if not matches:
        raise RuntimeError("local Chatterbox wheel was not produced")
    wheel = matches[-1]
    _run([str(target_python), "-m", "pip", "install", "--upgrade", str(wheel)])
    _run([str(target_python), "-m", "pip", "check"])
    return wheel


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build/install the isolated, verified Chatterbox runtime")
    parser.add_argument("--venv", type=Path,
                        default=Path(os.getenv("CHATTERBOX_VENV", _REPO / ".venv-chatterbox")))
    parser.add_argument("--cache", type=Path,
                        default=Path(os.getenv("CHATTERBOX_RUNTIME_CACHE", _REPO / ".chatterbox-runtime-cache")))
    args = parser.parse_args(argv)
    wheel = install_runtime(venv_dir=args.venv, cache_dir=args.cache)
    print(f"installed local Chatterbox wheel: {wheel}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
