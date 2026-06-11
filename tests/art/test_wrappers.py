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
    assert fp == "painting_deep_dive|4|intro|outro"
    log_path = tmp_path / "_variety_log.csv"
    w1 = append_variety_log("p1", n, path=log_path)
    w2 = append_variety_log("p2", n, path=log_path)
    w3 = append_variety_log("p3", n, path=log_path)
    assert w1 == "" and w2 == ""
    assert "same structure" in w3  # 3rd identical fingerprint in a row → warn


def test_video_wrapper_disables_mirror_and_inpaint(monkeypatch, tmp_path):
    from art_pipeline import video
    from art_pipeline.config import ART_PROJECTS_ROOT
    import stages.stage_5.pipeline as s5
    import stages.stage_5.shots as shots

    proj = tmp_path / "wrap-test"
    proj.mkdir(parents=True, exist_ok=True)
    (proj / "art_context.json").write_text('{"title":"T","artworks":[],"sources":[]}')
    (proj / "narration.json").write_text('{"mode":"painting_deep_dive","scenes":[{}]}')
    monkeypatch.setattr(video, "get_art_project_path", lambda name: (tmp_path / name))
    monkeypatch.setattr(video, "VARIETY_LOG", tmp_path / "_variety_log.csv")

    seen = {}

    def fake_assemble(name, **kw):
        seen.update(mirror=shots.MIRROR_PANELS, inpaint=shots.INPAINT_BUBBLE_TEXT,
                    root=s5.PROJECTS_ROOT)
        return "RESULT"

    monkeypatch.setattr(s5, "assemble_project", fake_assemble)

    out = video.assemble_art("wrap-test")
    assert out == "RESULT"
    # flags must be False AT RENDER TIME (assemble_art restores them afterwards)
    assert seen["mirror"] is False             # famous artworks must not be mirrored
    assert seen["inpaint"] is False            # do not erase signatures
    assert seen["root"] == ART_PROJECTS_ROOT
    assert (tmp_path / "wrap-test" / "youtube_description.txt").exists()


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
    assert "“Old map” (pd), http://src/2" in d   # no author → no dash segment
