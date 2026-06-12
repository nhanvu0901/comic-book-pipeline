from art_pipeline import scout_csv

ROW = {
    "title": "Wheat Field with Cypresses", "artist": "Vincent van Gogh",
    "year": "1889", "object_id": "436535", "department": "European Paintings",
    "image_url": "https://images.metmuseum.org/x.jpg",
    "wiki_grounding": "wiki:9000", "story_hook": "Painted from the asylum window",
    "yt_coverage": "no dedicated short", "date_added": "2026-06-10", "status": "queued",
    "longform_angle": "X-ray revealed a hidden first version under the wheat field",
}


def test_creates_file_with_header_and_appends(tmp_path):
    p = tmp_path / "art_candidates.csv"
    n = scout_csv.append_candidates([ROW], path=p)
    assert n == 1
    text = p.read_text()
    assert text.splitlines()[0] == ",".join(scout_csv.COLUMNS)
    assert "436535" in text


def test_dedups_by_object_id(tmp_path):
    p = tmp_path / "art_candidates.csv"
    scout_csv.append_candidates([ROW], path=p)
    n = scout_csv.append_candidates([ROW, dict(ROW, object_id="11417")], path=p)
    assert n == 1  # only the new id appended
    rows = scout_csv.read_candidates(path=p)
    assert [r["object_id"] for r in rows] == ["436535", "11417"]


def test_longform_angle_written_and_read_back(tmp_path):
    p = tmp_path / "art_candidates.csv"
    scout_csv.append_candidates([ROW], path=p)
    rows = scout_csv.read_candidates(path=p)
    assert rows[0]["longform_angle"] == ROW["longform_angle"]
    # Shorts-only candidate: empty angle survives the round trip too
    scout_csv.append_candidates([dict(ROW, object_id="11417", longform_angle="")], path=p)
    rows = scout_csv.read_candidates(path=p)
    assert rows[1]["longform_angle"] == ""


def test_append_never_rewrites_existing_lines(tmp_path):
    p = tmp_path / "art_candidates.csv"
    scout_csv.append_candidates([ROW], path=p)
    before = p.read_text().splitlines()[1]
    scout_csv.append_candidates([dict(ROW, object_id="11417", title="Changed")], path=p)
    assert p.read_text().splitlines()[1] == before
