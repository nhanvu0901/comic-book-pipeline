from stages._arc import issue_index_of_page, allocate_beats_across_issues


def test_outline_inputs_group_pages_by_issue():
    pages = [
        {"page_number": 1, "source_image": "/r/ch01_page_01.jpg"},
        {"page_number": 2, "source_image": "/r/ch01_page_02.jpg"},
        {"page_number": 3, "source_image": "/r/ch02_page_01.jpg"},
    ]
    by_issue = {}
    for p in pages:
        by_issue.setdefault(issue_index_of_page(p), []).append(p["page_number"])
    assert by_issue == {1: [1, 2], 2: [3]}
    alloc = allocate_beats_across_issues(20, 2, [2, 1])
    assert sum(alloc.values()) == 20 and alloc[1] >= 2 and alloc[2] >= 2
