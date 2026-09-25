import pytest

from art_ui import state as st


def test_slugify():
    assert st.slugify("Wheat Field with Cypresses!") == "wheat-field-with-cypresses"


def test_state_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setattr(st, "ART_ROOT", tmp_path)
    s = st.ArtAppState(project_name="p1", object_ids=[436535], mode="artist_journey")
    s.mark_approved(2)
    st.save_state(s)
    loaded = st.load_state("p1")
    assert loaded.object_ids == [436535]
    assert loaded.mode == "artist_journey"
    assert loaded.is_approved(2)


def test_mark_dirty_cascades_to_stage_6(tmp_path, monkeypatch):
    monkeypatch.setattr(st, "ART_ROOT", tmp_path)
    s = st.ArtAppState(project_name="p2")
    for n in (3, 4, 5, 6):
        s.mark_approved(n)
    s.mark_dirty(3)
    assert s.is_dirty(4) and s.is_dirty(5) and s.is_dirty(6)


def test_list_art_projects_keys_on_state_or_selection(tmp_path, monkeypatch):
    monkeypatch.setattr(st, "ART_ROOT", tmp_path)
    (tmp_path / "a").mkdir(); (tmp_path / "a" / "state.json").write_text("{}")
    (tmp_path / "b").mkdir(); (tmp_path / "b" / "selection.json").write_text("{}")
    (tmp_path / "c").mkdir()  # junk dir — ignored
    assert st.list_art_projects() == ["a", "b"]


def test_save_state_writes_atomically_on_json_dump_failure(tmp_path, monkeypatch):
    """save_state must go through write_json_atomic like the rest of the app: a plain
    write_text truncates the target immediately, so a failure mid-write (a bad field, a
    kill) would leave a corrupt state.json instead of the previous good one. A plain
    write_text never calls json.dump at all (it calls json.dumps in memory first), so
    this only exercises the real code path once save_state goes through the atomic
    writer — it can't pass by accident the way an unserialisable-value test could."""
    monkeypatch.setattr(st, "ART_ROOT", tmp_path)
    good = st.ArtAppState(project_name="p1", mode="artist_journey")
    st.save_state(good)
    before = st.state_path("p1").read_bytes()

    import utils.atomic_json as atomic_json
    monkeypatch.setattr(atomic_json.json, "dump",
                        lambda *a, **k: (_ for _ in ()).throw(TypeError("mid-write failure")))

    with pytest.raises(TypeError):
        st.save_state(st.ArtAppState(project_name="p1", mode="short-trip"))

    assert st.state_path("p1").read_bytes() == before
    assert not st.state_path("p1").with_name("state.json.tmp").exists()
