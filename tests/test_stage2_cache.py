"""load_cached must survive a global page renumbering (earlier chapter's page count
changed) by finding the same content-hash under a different page_number and
re-keying the file — WITHOUT invalidating pages whose images didn't change.
See stages/stage_2/cache.py docstring."""
import json

from stages.stage_2.cache import cache_path, load_cached, save_cached


def _write(project_root, page_number, h, **extra):
    data = {"page_number": page_number, "source_image": "", "content_hash": h, **extra}
    p = cache_path(project_root, page_number, h)
    p.write_text(json.dumps(data))
    return p


def test_rekeys_on_page_number_shift(tmp_path):
    old = _write(tmp_path, 56, "abc123", source_image="/img/ch02_page_01.jpg")

    result = load_cached(tmp_path, 25, "abc123", "/img/ch02_page_01.jpg")

    assert result is not None
    assert result["page_number"] == 25          # in-memory field updated
    assert not old.exists()                      # old filename removed
    new = cache_path(tmp_path, 25, "abc123")
    assert new.exists()
    assert json.loads(new.read_text())["page_number"] == 25


def test_exact_match_is_fast_path_no_rekey(tmp_path):
    p = _write(tmp_path, 25, "abc123")
    result = load_cached(tmp_path, 25, "abc123")
    assert result is not None
    assert p.exists()  # untouched


def test_ambiguous_duplicate_hash_is_not_guessed(tmp_path):
    # Two distinct pages (e.g. blank pages) hash identically.
    _write(tmp_path, 10, "dead00", source_image="/img/ch01_page_10.jpg")
    _write(tmp_path, 40, "dead00", source_image="/img/ch02_page_05.jpg")

    # No image_path to disambiguate → treated as a miss, nothing is touched.
    assert load_cached(tmp_path, 41, "dead00") is None
    assert cache_path(tmp_path, 10, "dead00").exists()
    assert cache_path(tmp_path, 40, "dead00").exists()

    # With the matching source_image, the correct one is picked and re-keyed.
    result = load_cached(tmp_path, 41, "dead00", "/img/ch02_page_05.jpg")
    assert result is not None
    assert result["page_number"] == 41
    assert cache_path(tmp_path, 10, "dead00").exists()          # other page untouched
    assert not cache_path(tmp_path, 40, "dead00").exists()      # re-keyed away
    assert cache_path(tmp_path, 41, "dead00").exists()


def test_no_hash_match_is_plain_miss(tmp_path):
    assert load_cached(tmp_path, 1, "nope") is None


if __name__ == "__main__":
    import pytest, sys
    sys.exit(pytest.main([__file__, "-v"]))
