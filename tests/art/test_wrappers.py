def test_tts_wrapper_points_stage4_at_art_root(monkeypatch):
    from art_pipeline import tts
    from art_pipeline.config import ART_PROJECTS_ROOT
    import stages.stage_4.pipeline as s4

    calls = {}
    monkeypatch.setattr(s4, "synthesize_project",
                        lambda name, **kw: calls.update(name=name, kw=kw) or "RESULT")
    out = tts.synthesize_art("proj-x", emotion="calm")
    assert out == "RESULT"
    assert calls["name"] == "proj-x" and calls["kw"]["emotion"] == "calm"
    assert s4.PROJECTS_ROOT == ART_PROJECTS_ROOT


def test_build_youtube_description_has_credit_url_and_cc0():
    from art_pipeline.video import build_youtube_description
    ctx = {"title": "T", "wiki_url": "http://wiki/T",
           "sources": ["http://wiki/T"],
           "artworks": [{"title": "Wheat Field with Cypresses",
                         "artist": "Vincent van Gogh", "year": "1889",
                         "credit_line": "Purchase, 1993",
                         "object_url": "https://www.metmuseum.org/art/collection/search/436535"}]}
    d = build_youtube_description(ctx)
    assert "Purchase, 1993" in d
    assert "metmuseum.org/art/collection/search/436535" in d
    assert "public domain (CC0)" in d
    assert "http://wiki/T" in d


def test_variety_log_appends_and_warns_on_repetition(tmp_path):
    from art_pipeline.video import append_variety_log, structure_fingerprint
    n = {"mode": "painting_deep_dive",
         "scenes": [{"is_intro": True}, {}, {}, {"is_outro": True}]}
    fp = structure_fingerprint(n)
    # fingerprint is now 5 fields: mode|count|short/longform|intro/cold|outro/hard-end
    assert fp == "painting_deep_dive|4|short|intro|outro"
    log_path = tmp_path / "_variety_log.csv"
    w1 = append_variety_log("p1", n, path=log_path)
    w2 = append_variety_log("p2", n, path=log_path)
    w3 = append_variety_log("p3", n, path=log_path)
    assert w1 == "" and w2 == ""
    assert "same structure" in w3  # 3rd identical fingerprint in a row -> warn


def test_assemble_art_uses_art_assembler_and_restores_flags(monkeypatch, tmp_path):
    import stages.stage_5.shots as shots
    import art_pipeline.video as video

    seen = {}
    def fake_assemble(project, **kw):
        seen["mirror"] = shots.MIRROR_PANELS
        seen["inpaint"] = shots.INPAINT_BUBBLE_TEXT
        return "RESULT"
    monkeypatch.setattr("art_pipeline.assemble.assemble_art_video", fake_assemble)
    root = tmp_path / "proj"; root.mkdir()
    monkeypatch.setattr(video, "get_art_project_path", lambda n: root)
    monkeypatch.setattr(video, "VARIETY_LOG", tmp_path / "_variety_log.csv")
    (root / "art_context.json").write_text('{"title": "T", "artworks": []}')
    (root / "narration.json").write_text('{"mode": "painting_deep_dive", "scenes": []}')

    before = (shots.MIRROR_PANELS, shots.INPAINT_BUBBLE_TEXT,
              shots.OUTPUT_W, shots.OUTPUT_H, shots.TARGET_ASPECT)
    out = video.assemble_art("proj")
    assert out == "RESULT"
    assert seen == {"mirror": False, "inpaint": False}
    assert (shots.MIRROR_PANELS, shots.INPAINT_BUBBLE_TEXT,
            shots.OUTPUT_W, shots.OUTPUT_H, shots.TARGET_ASPECT) == before
    assert (root / "youtube_description.txt").exists()


def test_description_includes_additional_image_credits():
    from art_pipeline.video import build_youtube_description
    ctx = {"title": "T", "artworks": [], "sources": [],
           "extra_image_credits": [
               {"title": "Asylum photo", "author": "Jane Doe",
                "license": "by-sa", "source_url": "http://src/1"},
               {"title": "Old map", "author": "", "license": "pd",
                "source_url": "http://src/2"},
           ]}
    d = build_youtube_description(ctx)
    assert "Additional images:" in d
    assert "“Asylum photo” — Jane Doe (by-sa), http://src/1" in d
    assert "“Old map” (pd), http://src/2" in d   # no author -> no dash segment


def test_youtube_chapters_text():
    from art_pipeline.video import build_youtube_chapters
    chapters = [{"chapter_id": 1, "title": "The Storm", "start": 0.0},
                {"chapter_id": 2, "title": "The Painter", "start": 95.4}]
    txt = build_youtube_chapters(chapters)
    assert txt.splitlines() == ["00:00 The Storm", "01:35 The Painter"]


def test_discover_bgm(tmp_path):
    from art_pipeline.video import discover_bgm
    assert discover_bgm(tmp_path) is None
    (tmp_path / "bgm.mp3").write_bytes(b"x")
    assert discover_bgm(tmp_path).name == "bgm.mp3"


def test_fingerprint_includes_length():
    from art_pipeline.video import structure_fingerprint
    n = {"mode": "painting_story", "scenes": [
        {"is_intro": True, "is_outro": False, "chapter_id": 1},
        {"is_intro": False, "is_outro": True, "chapter_id": 5}]}
    assert structure_fingerprint(n).startswith("painting_story|2|")
    assert "longform" in structure_fingerprint(n)
