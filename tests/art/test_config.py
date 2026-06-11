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


def test_visuals_constants():
    from art_pipeline import config as c
    assert "by-sa" in c.VISUAL_LICENSE_WHITELIST and "pd" in c.VISUAL_LICENSE_WHITELIST
    assert 0 < c.VISUAL_MATCH_MIN < c.VISUAL_KEEP_THRESHOLD <= 1
    assert c.VISUAL_KEEP_THRESHOLD > 0 and c.VISUAL_MAX_PER_VIDEO >= 1
    assert c.COMMONS_API.startswith("https://commons.wikimedia.org")
    assert c.OPENVERSE_API.startswith("https://api.openverse.org")
