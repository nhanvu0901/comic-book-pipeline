"""Narration carries banner_title (Stage 3); Stage 5 exports it to title.txt
instead of burning it (2026-07-13, banner burn removed — Master titles in CapCut)."""
from stages.stage_3.schema import Narration
from stages.stage_5 import pipeline as P


def test_narration_serializes_banner_title():
    n = Narration(mode="panel_walk", title="Rogue Becomes Herald", hook="...",
                  banner_title="Rogue Kissed A God")
    d = n.to_dict()
    assert d["banner_title"] == "Rogue Kissed A God"


def test_narration_banner_title_defaults_empty():
    n = Narration(mode="m", title="t", hook="h")
    assert n.banner_title == "" and n.to_dict()["banner_title"] == ""


def test_write_title_file_writes_when_present(tmp_path):
    P._write_title_file(tmp_path, {"banner_title": "Rogue Kissed A God"})
    assert (tmp_path / "title.txt").read_text().strip() == "Rogue Kissed A God"


def test_write_title_file_skips_when_blank(tmp_path):
    P._write_title_file(tmp_path, {"banner_title": "   "})
    assert not (tmp_path / "title.txt").exists()


def test_write_title_file_skips_when_missing(tmp_path):
    P._write_title_file(tmp_path, {})
    assert not (tmp_path / "title.txt").exists()
