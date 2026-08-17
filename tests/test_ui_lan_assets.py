"""Panel thumbnails must load when the review UI is served over the LAN.

They did not. The screen passed ABSOLUTE LOCAL PATHS to ft.Image — fine for the desktop
client reading off its own disk, unreachable from a browser on another device, so the review
screen came up with every tile blank and no error anywhere.

Base64 is not the fix and must not become one: the comment at the thumb builder records that
embedding every tile up front (100+ per beat × 25 beats) killed the Flutter client. The
browser has to fetch them lazily over HTTP, which is what serving projects/ as flet's
assets_dir does.
"""
from pathlib import Path

import pytest

import ui.bridge as B


@pytest.fixture(autouse=True)
def _desktop_by_default():
    """Desktop is the default everywhere else; never leak web mode into another test."""
    B.set_web_mode(False)
    yield
    B.set_web_mode(False)


def _thumb(tmp_path, monkeypatch):
    monkeypatch.setattr(B, "PROJECTS_ROOT", tmp_path)
    d = tmp_path / "proj" / "review" / "thumbs"
    d.mkdir(parents=True)
    (d / "p008_0.jpg").write_bytes(b"\xff\xd8\xff")
    return d / "p008_0.jpg"


def test_desktop_still_gets_an_absolute_path(tmp_path, monkeypatch):
    """Unchanged behaviour: the desktop client loads each tile off disk as it scrolls."""
    _thumb(tmp_path, monkeypatch)
    got = B.review_thumb_path("proj", "review/thumbs/p008_0.jpg")
    assert Path(got).is_absolute() and Path(got).exists()


def test_web_gets_a_path_relative_to_the_asset_root(tmp_path, monkeypatch):
    """--lan hands projects/ to flet as assets_dir, so this is what the browser can GET."""
    _thumb(tmp_path, monkeypatch)
    B.set_web_mode(True)
    assert B.review_thumb_path("proj", "review/thumbs/p008_0.jpg") == \
        "/proj/review/thumbs/p008_0.jpg"


def test_a_missing_thumb_is_empty_in_both_modes(tmp_path, monkeypatch):
    _thumb(tmp_path, monkeypatch)
    for web in (False, True):
        B.set_web_mode(web)
        assert B.review_thumb_path("proj", "review/thumbs/nope.jpg") == ""
        assert B.review_thumb_path("proj", "") == ""


def test_a_file_outside_the_asset_root_falls_back_to_its_path(tmp_path, monkeypatch):
    """Nothing serves it, so a relative URL would 404 — hand back the path rather than a
    link that silently resolves to nothing."""
    monkeypatch.setattr(B, "PROJECTS_ROOT", tmp_path / "projects")
    (tmp_path / "projects").mkdir()
    outside = tmp_path / "elsewhere.jpg"
    outside.write_bytes(b"\xff\xd8\xff")
    B.set_web_mode(True)
    assert B.asset_src(outside) == str(outside)


def test_web_mode_is_off_unless_switched_on():
    """A desktop run must never emit URLs only a web server could resolve."""
    assert B.WEB_MODE is False
