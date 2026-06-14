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
    assert c.ART_LF_TARGET_WORDS_MAX == 1700
    assert c.ART_LF_CHAPTER_WORDS_MIN == 150
    assert c.ART_LF_CHAPTER_WORDS_MAX == 340
    assert c.ART_LF_CHAPTER_WORDS_BAND == (0.75, 1.5)
    assert c.ART_LF_TOTAL_WORDS_FLOOR == 1360
    assert c.ART_LF_SCENES_PER_CHAPTER_MIN == 14
    assert c.ART_LF_SCENES_PER_CHAPTER_MAX == 22
    assert c.ART_LF_SCENE_MAX_WORDS == 32
    assert c.ART_LF_CHAPTER_GAP_S == 1.0
    assert (c.ART_LF_OUTPUT_W, c.ART_LF_OUTPUT_H) == (1920, 1080)
    assert c.ART_LF_REHOOK_POSITIONS == (2, 3)
    assert c.ART_LF_REGION_REUSE_WINDOW == 6


def test_dedup_and_card_constants_exist():
    from art_pipeline import config as C
    assert C.ART_LF_SAID_LINES_MAX == 60
    assert C.ART_LF_DEDUP_THRESHOLD == 0.86
    assert C.ART_LF_DEDUP_MAX_PASSES == 2
    assert C.ART_LF_CHAPTER_CARDS is True
    assert C.ART_LF_CHAPTER_CARD_SEC == 2.6
    assert C.ART_CARD_BG == "#0d1b2a"
    assert C.ART_CARD_ACCENT == "#c9a44a"
    assert C.ART_CARD_FONT.endswith("Anton-Regular.ttf")


def test_art_voice_id_default_rupert():
    from art_pipeline import config as C
    assert C.ART_VOICE_ID == "0ad65e7f-006c-47cf-bd31-52279d487913"  # Rupert - Caring Dad
