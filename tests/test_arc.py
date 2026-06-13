from stages._arc import issue_index_of_page, allocate_beats_across_issues


def test_issue_index_parses_chapter_prefix():
    # source_image carries the ch{NN}_page path written by scrape_issue_pages
    assert issue_index_of_page({"source_image": "/p/raw_comic/ch01_page_05.jpg"}) == 1
    assert issue_index_of_page({"source_image": "/p/raw_comic/ch03_page_12.jpg"}) == 3


def test_issue_index_unknown_returns_zero():
    assert issue_index_of_page({"source_image": "/p/raw_comic/cover.jpg"}) == 0
    assert issue_index_of_page({}) == 0


def test_allocate_even_split_gives_each_issue_a_floor():
    alloc = allocate_beats_across_issues(total=20, n_issues=5, page_counts=[10, 10, 10, 10, 10])
    assert sum(alloc.values()) == 20
    assert all(c >= 1 for c in alloc.values())
    assert set(alloc.keys()) == {1, 2, 3, 4, 5}


def test_allocate_weights_by_page_count_but_keeps_floor():
    alloc = allocate_beats_across_issues(total=20, n_issues=5, page_counts=[30, 30, 30, 30, 2])
    assert sum(alloc.values()) == 20
    assert alloc[5] >= 2
    assert alloc[1] > alloc[5]


def test_allocate_single_issue_gets_all():
    assert allocate_beats_across_issues(total=18, n_issues=1, page_counts=[22]) == {1: 18}
