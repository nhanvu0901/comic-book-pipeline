"""stages.stage_1.storage.project_folder_name — a project name typed by hand becomes the
project's folder. The UI runs on Windows, which refuses : * ? " < > | in a folder name
and cannot make a folder named after a device (CON, NUL, COM1...); a '/' would nest
folders anywhere."""
from stages.stage_1.storage import project_folder_name


def test_a_title_with_punctuation_becomes_a_slug():
    assert project_folder_name("Ms. Marvel: No Normal?") == "ms_marvel_no_normal"
    assert project_folder_name('Wolverine / "Origins" <1>') == "wolverine_origins_1"


def test_a_name_that_already_is_a_folder_name_is_kept_whole():
    long_slug = "a_series_slug_taken_from_its_batcave_url_that_runs_past_sixty_chars"
    assert project_folder_name(long_slug) == long_slug
    assert project_folder_name("  dark-venom_2023 ") == "dark-venom_2023"


def test_windows_device_names_get_a_suffix():
    assert project_folder_name("CON") == "CON_project"
    assert project_folder_name("nul") == "nul_project"
    assert project_folder_name("com1") == "com1_project"


def test_nothing_usable_gives_an_empty_name_for_the_caller_to_handle():
    assert project_folder_name("???") == ""
    assert project_folder_name("   ") == ""
