def test_art_modes_registry():
    from art_pipeline.config import ART_MODES_BY_KEY
    assert set(ART_MODES_BY_KEY) == {"painting_deep_dive", "themed_listicle", "artist_journey"}
    assert ART_MODES_BY_KEY["painting_deep_dive"].label


def test_roots_are_separate_from_comic():
    from art_pipeline.config import ART_PROJECTS_ROOT
    from config import PROJECTS_ROOT
    assert ART_PROJECTS_ROOT.name == "art_projects"
    assert ART_PROJECTS_ROOT != PROJECTS_ROOT


def test_region_constants_sane():
    from art_pipeline import config as c
    assert 0 < c.REGION_MIN_AREA_PCT < c.REGION_MAX_AREA_PCT <= 100
    assert c.REGION_MIN_COUNT >= 3


def test_pacing_constants():
    from art_pipeline import config
    assert config.ART_MIN_SCENES == 10
    assert config.ART_SHOT_SPLIT_SEC == 5.0
    assert config.ART_MAX_STATIC_SEC == 4.0
    assert config.VISUAL_MIN_SHORT_SIDE == 600


def test_embedding_visuals_constants_removed():
    from art_pipeline import config
    for name in ("VISUAL_KEEP_THRESHOLD", "VISUAL_MATCH_MIN",
                 "VISUAL_LICENSE_WHITELIST", "VISUAL_MAX_PER_VIDEO",
                 "COMMONS_API", "OPENVERSE_API"):
        assert not hasattr(config, name), name


def test_longform_constants():
    from art_pipeline import config as c
    assert c.ART_LF_MODES == ("painting_story", "artist_journey")
    assert c.ART_LF_CHAPTER_ROLES_5 == (
        "cold_open", "backfill", "evidence", "twist", "resolution")
    assert c.ART_LF_CHAPTER_ROLES_4 == (
        "cold_open", "backfill_evidence", "twist", "resolution")
    assert c.ART_LF_TARGET_WORDS_MIN == 1600
    assert c.ART_LF_TARGET_WORDS_MAX == 1900
    assert c.ART_LF_CHAPTER_WORDS_MIN == 150
    assert c.ART_LF_CHAPTER_WORDS_MAX == 380
    assert c.ART_LF_CHAPTER_WORDS_BAND == (0.85, 1.5)
    assert c.ART_LF_SCENES_PER_CHAPTER_MIN == 14
    assert c.ART_LF_SCENES_PER_CHAPTER_MAX == 22
    assert c.ART_LF_SCENE_MAX_WORDS == 32
    assert c.ART_LF_CHAPTER_GAP_S == 1.0
    assert (c.ART_LF_OUTPUT_W, c.ART_LF_OUTPUT_H) == (1920, 1080)
    assert c.ART_LF_REHOOK_POSITIONS == (2, 3)
    assert c.ART_LF_REGION_REUSE_WINDOW == 6
