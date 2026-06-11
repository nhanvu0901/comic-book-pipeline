# tests/art/test_web_images.py
from art_pipeline.sources import web_images as wi


def test_normalize_license_codes():
    assert wi.normalize_license("pd") == "pd"
    assert wi.normalize_license("Public domain") == "pd"
    assert wi.normalize_license("pdm") == "pd"            # Openverse public-domain mark
    assert wi.normalize_license("cc0") == "cc0"
    assert wi.normalize_license("CC0 1.0") == "cc0"
    assert wi.normalize_license("cc-by-4.0") == "by"
    assert wi.normalize_license("CC BY 4.0") == "by"
    assert wi.normalize_license("cc-by-sa-3.0") == "by-sa"
    assert wi.normalize_license("CC BY-SA 4.0") == "by-sa"


def test_normalize_license_rejects_unsafe():
    assert wi.normalize_license("cc-by-nc-2.0") is None    # startswith("cc-by") trap!
    assert wi.normalize_license("CC BY-NC-SA 4.0") is None
    assert wi.normalize_license("cc-by-nd-4.0") is None
    assert wi.normalize_license("Attribution-NonCommercial") is None
    assert wi.normalize_license("Attribution-NoDerivs") is None
    assert wi.normalize_license("GFDL") is None
    assert wi.normalize_license("") is None
    assert wi.normalize_license(None) is None


COMMONS_PAGE = {
    "title": "File:Georges Seurat 1888.jpg",
    "imageinfo": [{
        "url": "https://upload.wikimedia.org/full.jpg",
        "thumburl": "https://upload.wikimedia.org/1600.jpg",
        "thumbwidth": 1600, "thumbheight": 1200,
        "width": 4000, "height": 3000,
        "mime": "image/jpeg",
        "descriptionurl": "https://commons.wikimedia.org/wiki/File:Georges_Seurat_1888.jpg",
        "extmetadata": {
            "License": {"value": "pd"},
            "LicenseShortName": {"value": "Public domain"},
            "Artist": {"value": "<a href=\"x\">Unknown photographer</a>"},
        },
    }],
}


def test_parse_commons_page_extracts_candidate():
    c = wi.parse_commons_page(COMMONS_PAGE)
    assert c["license"] == "pd"
    assert c["image_url"] == "https://upload.wikimedia.org/1600.jpg"  # prefers thumb
    assert c["title"] == "Georges Seurat 1888"
    assert c["author"] == "Unknown photographer"                      # HTML stripped
    assert c["source_url"].startswith("https://commons.wikimedia.org/wiki/")
    assert c["width"] == 1600


def test_parse_commons_page_rejects_nc_and_nonimage():
    nc = {"title": "File:x.jpg", "imageinfo": [dict(COMMONS_PAGE["imageinfo"][0],
          extmetadata={"License": {"value": "cc-by-nc-2.0"},
                       "LicenseShortName": {"value": "CC BY-NC 2.0"}})]}
    assert wi.parse_commons_page(nc) is None
    svg = {"title": "File:x.svg", "imageinfo": [dict(COMMONS_PAGE["imageinfo"][0],
          mime="image/svg+xml")]}
    assert wi.parse_commons_page(svg) is None


def test_parse_openverse_result():
    r = {"url": "https://img.example/x.jpg", "title": "Saint-Paul asylum",
         "creator": "Jane Doe", "license": "by-sa",
         "foreign_landing_url": "https://example.org/photo", "width": 2000, "height": 1500}
    c = wi.parse_openverse_result(r)
    assert c["license"] == "by-sa" and c["author"] == "Jane Doe"
    assert wi.parse_openverse_result(dict(r, license="by-nc")) is None
