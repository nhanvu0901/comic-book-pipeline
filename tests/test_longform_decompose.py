"""Unit tests for stages.longform.decompose — NO network, NO Stage-2, NO render.

decompose_recap is pure file-ops over a fake saga project built in tmp_path.
decompose_qa is exercised with research_answer / build_contexts /
download_readers_only monkeypatched, so only the grouping + wiring is tested.
"""
import json

import pytest

import config
from stages.longform import decompose


# ─── decompose_recap ─────────────────────────────────────────────────────────


def _write_page(prep_dir, raw_dir, saga_root, fname, ch, page_no, page_number):
    """Write a fake raw page + its preprocessed JSON (source_image points into the
    saga's raw_comic, exactly like Stage 2 writes it)."""
    img = raw_dir / f"ch{ch:02d}_page_{page_no:02d}.jpg"
    img.write_bytes(b"\xff\xd8\xff\xe0fakejpeg")  # not a real image, just bytes to copy
    (prep_dir / fname).write_text(json.dumps({
        "page_number": page_number,
        "source_image": str(img),
        "issue_label": f"#{ch}",
        "is_story_page": True,
        "panels": [{"panel_bbox": [0, 0, 10, 10], "description": f"ch{ch} p{page_no}"}],
    }))


def _build_fake_saga(root):
    root.mkdir(parents=True, exist_ok=True)
    raw = root / "raw_comic"; raw.mkdir()
    prep = root / "preprocessed"; prep.mkdir()
    # issue 1: two pages, issue 2: one page (global page_number 1..3)
    _write_page(prep, raw, root, "page_001_aaa.json", ch=1, page_no=1, page_number=1)
    _write_page(prep, raw, root, "page_002_bbb.json", ch=1, page_no=2, page_number=2)
    _write_page(prep, raw, root, "page_003_ccc.json", ch=2, page_no=1, page_number=3)
    (root / "cluster_to_name.json").write_text(json.dumps({"0": "Doom"}))
    (root / "comic_context.json").write_text(json.dumps({
        "title": "Fake Series", "series": "Fake Series", "year": "2020",
        "publisher": "Marvel", "characters": ["Doom", "Kang"],
        "batcave_url": "https://batcave.biz/1-fake.html",
        "is_arc": True, "issue_count": 2,
        "plot_summary": "[#1] P1\n\n[#2] P2",
        "issues": [
            {"chapter_index": 1, "label": "#1", "plot_summary": "Plot one", "wiki_url": "w1"},
            {"chapter_index": 2, "label": "#2", "plot_summary": "Plot two", "wiki_url": ""},
        ],
    }))
    return root


def test_decompose_recap_splits_per_issue(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "PROJECTS_ROOT", tmp_path)
    saga = "fakesaga"
    _build_fake_saga(tmp_path / saga)

    slugs = decompose.decompose_recap(saga, log=lambda *_: None)

    # one sub-project per issue, in order
    assert slugs == [f"{saga}__seg01", f"{saga}__seg02"]

    seg1 = tmp_path / f"{saga}__seg01"
    seg2 = tmp_path / f"{saga}__seg02"

    # pages copied to the RIGHT issue's sub-project
    assert sorted(p.name for p in (seg1 / "raw_comic").glob("*.jpg")) == \
        ["ch01_page_01.jpg", "ch01_page_02.jpg"]
    assert sorted(p.name for p in (seg1 / "preprocessed").glob("*.json")) == \
        ["page_001_aaa.json", "page_002_bbb.json"]
    assert [p.name for p in (seg2 / "raw_comic").glob("*.jpg")] == ["ch02_page_01.jpg"]
    assert [p.name for p in (seg2 / "preprocessed").glob("*.json")] == ["page_003_ccc.json"]

    # source_image rewritten to the sub-project's own copy, and that file exists
    pj = json.loads((seg1 / "preprocessed" / "page_001_aaa.json").read_text())
    assert pj["source_image"] == str(seg1 / "raw_comic" / "ch01_page_01.jpg")
    assert (seg1 / "raw_comic" / "ch01_page_01.jpg").exists()

    # single-issue comic_context: NOT is_arc, NO issues[], plot_source=recap
    c1 = json.loads((seg1 / "comic_context.json").read_text())
    assert not c1.get("is_arc")
    assert "issues" not in c1
    assert c1["plot_source"] == "recap"
    assert c1["issue"] == "#1"
    assert c1["plot_summary"] == "Plot one"
    assert c1["series"] == "Fake Series"
    assert "#1" in c1["title"]
    assert c1["plot_status"] == "OK"
    assert c1["characters"] == ["Doom", "Kang"]

    c2 = json.loads((seg2 / "comic_context.json").read_text())
    assert c2["issue"] == "#2"
    assert c2["plot_summary"] == "Plot two"

    # cluster map copied into each segment
    assert (seg1 / "cluster_to_name.json").exists()
    assert (seg2 / "cluster_to_name.json").exists()


def test_decompose_recap_rejects_non_saga(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "PROJECTS_ROOT", tmp_path)
    root = tmp_path / "single"
    root.mkdir(parents=True)
    (root / "comic_context.json").write_text(json.dumps({"title": "X", "plot_summary": "y"}))
    with pytest.raises(ValueError, match="no issues"):
        decompose.decompose_recap("single", log=lambda *_: None)


# ─── decompose_qa ────────────────────────────────────────────────────────────


def _fake_items(n):
    return [
        {"entity": f"E{i}", "how_or_why": f"why {i}", "source_comic": f"C{i} #{i}",
         "source_year": "2020", "reader_url": f"https://batcave.biz/reader/{i}/{i}",
         "drawable_moment": "m", "verification_note": "v", "surprise_level": "low"}
        for i in range(1, n + 1)
    ]


def test_decompose_qa_groups_and_wires(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "PROJECTS_ROOT", tmp_path)

    research = {"question": "Q?", "answer_summary": "s", "source_engine": "stub",
                "items": _fake_items(15)}
    monkeypatch.setattr(
        "stages.stage_1.answer_research.research_answer",
        lambda q, *, max_items=6, hint="", log=print: research,
    )

    built, downloaded = {}, {}

    def fake_build(question, res, project_name, *, researched_at="", log=print):
        # what would become answer_context.json for this segment
        root = config.get_project_dirs(project_name)["root"]
        root.mkdir(parents=True, exist_ok=True)
        (root / "answer_context.json").write_text(json.dumps({
            "question": question,
            "items": [{"rank": i, "entity": it["entity"]} for i, it in enumerate(res["items"], 1)],
        }))
        built[project_name] = [it["entity"] for it in res["items"]]
        return root / "answer_context.json", root / "comic_context.json"

    def fake_download(project_name, reader_urls, *, progress=print):
        downloaded[project_name] = list(reader_urls)
        return {"total_pages": len(reader_urls)}

    monkeypatch.setattr("stages.stage_1.answer_research.build_contexts", fake_build)
    monkeypatch.setattr("stages.stage_2.url_mode.download_readers_only", fake_download)

    slugs = decompose.decompose_qa("Q?", "myq", max_items=15, log=lambda *_: None)

    # 15 items, target 4 → ceil(15/4)=4 groups, even split [4,4,4,3]
    assert slugs == [f"myq__seg{k:02d}" for k in range(1, 5)]
    assert [len(built[s]) for s in slugs] == [4, 4, 4, 3]

    # segments partition all 15 entities IN ORDER (no overlap, no loss)
    flat = [e for s in slugs for e in built[s]]
    assert flat == [f"E{i}" for i in range(1, 16)]

    # answer_context.json per segment holds exactly that group's items
    seg1_ctx = json.loads((tmp_path / "myq__seg01" / "answer_context.json").read_text())
    assert [it["entity"] for it in seg1_ctx["items"]] == ["E1", "E2", "E3", "E4"]

    # reader_urls handed to download match each group, per segment
    assert downloaded[slugs[0]] == [f"https://batcave.biz/reader/{i}/{i}" for i in range(1, 5)]
    assert downloaded[slugs[-1]] == [f"https://batcave.biz/reader/{i}/{i}" for i in range(13, 16)]


def test_chunk_items_floor_and_sizes():
    # small set → single group
    assert decompose._chunk_items(list(range(3))) == [[0, 1, 2]]
    # 6 → [3,3];  10 → [4,3,3];  12 → [4,4,4]
    assert [len(g) for g in decompose._chunk_items(list(range(6)))] == [3, 3]
    assert [len(g) for g in decompose._chunk_items(list(range(10)))] == [4, 3, 3]
    assert [len(g) for g in decompose._chunk_items(list(range(12)))] == [4, 4, 4]
    # every group meets the listicle floor of 3 for any n >= 3
    for n in range(3, 30):
        assert min(len(g) for g in decompose._chunk_items(list(range(n)))) >= 3
