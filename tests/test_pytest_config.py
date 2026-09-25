"""A bare `pytest` must collect only tests/.

With no pytest config it recursed into scratch/ and scripts/, whose test_*.py files are
manual scripts that act at import time: one re-ran a live URL-direct download into a real
project, others made paid You.com and OpenRouter calls — on every full run."""
import configparser
from pathlib import Path

_INI = Path(__file__).resolve().parents[1] / "pytest.ini"


def test_bare_pytest_collects_only_the_tests_folder():
    cfg = configparser.ConfigParser()
    cfg.read(_INI)
    assert cfg.get("pytest", "testpaths").split() == ["tests"]
    skipped = cfg.get("pytest", "norecursedirs").split()
    assert {"scratch", "scripts", "projects"} <= set(skipped)
