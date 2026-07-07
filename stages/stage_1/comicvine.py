"""Comic Vine cross-check — CONFIRMATION layer for explore_answer (Q&A) mode.

FIX D (design "#1 risk": a fact -> WRONG issue). After research names an item's
source (series + issue# + year + character), this confirms against Comic Vine's
structured DB that the issue REALLY exists and REALLY features the character,
before we download it. It NEVER blocks: any hiccup (no key, network, not found)
returns ok=True/"unverified" so the pipeline proceeds — build_contexts only
*flags* an item for a second look, it does not drop it.

API: https://comicvine.gamespot.com/api/ — needs a real User-Agent (403 without),
?api_key=...&format=json, ~1 req/sec. See config.COMIC_VINE_API_KEY.
"""
from __future__ import annotations

import json
import re
import time
import urllib.parse
import urllib.request

from config import COMIC_VINE_API_KEY

_CV_BASE = "https://comicvine.gamespot.com/api/"
# Comic Vine 403s on the default urllib UA; any descriptive UA is accepted.
_CV_UA = "comic-book-pipeline/1.0 (Q&A issue cross-check)"
_CV_THROTTLE_S = 1.0  # ponytail: rate limit is ~1/s; sleep after each call. Fine for 3-6 items/run.


def _core(s: str) -> str:
    """Loose name key: lowercase, drop punctuation, drop a leading 'the'."""
    t = re.sub(r"[^a-z0-9 ]", "", (s or "").lower()).strip()
    return re.sub(r"^the\s+", "", t).strip()


def _name_matches(entity: str, names: list[str]) -> bool:
    """True if `entity` plausibly names one of the issue's credited characters.

    Lenient on purpose (substring OR shared token): a false 'absent' only flags an
    item for review, so over-matching is cheaper than missing 'Danny Ketch' vs
    'Daniel Ketch'. e.g. 'The Punisher' -> 'punisher' matches credit 'Punisher'."""
    ecore = _core(entity)
    if not ecore:
        return False
    etoks = set(ecore.split())
    for n in names:
        ncore = _core(n)
        if not ncore:
            continue
        if ecore in ncore or ncore in ecore:
            return True
        if etoks & set(ncore.split()):
            return True
    return False


def _cv_get(resource: str, params: dict, key: str) -> dict:
    """One Comic Vine GET (UA header required), JSON-decoded. Sleeps after, to
    respect the ~1 req/sec limit. Raises on network/HTTP/JSON error — the caller
    turns any raise into an 'unverified' (non-fatal) result."""
    q = {"api_key": key, "format": "json", **params}
    url = _CV_BASE + resource + "?" + urllib.parse.urlencode(q)
    req = urllib.request.Request(url, headers={"User-Agent": _CV_UA})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            data = json.loads(r.read().decode("utf-8"))
    finally:
        time.sleep(_CV_THROTTLE_S)
    return data


def _pick_volume(vols: list[dict], series: str, year: str) -> dict | None:
    """From volumes-search results pick the one that was running at `year`.

    Volumes span years, so we don't require start_year == year: prefer an exact
    name match, then the volume with the largest start_year <= year (the one
    publishing at that time), else the earliest. e.g. Deadpool #26 (2010) ->
    'Deadpool' (2008), not 'Deadpool' (2012)."""
    core = _core(series)
    cands = [v for v in vols if _core(v.get("name", "")) == core] \
        or [v for v in vols if core and core in _core(v.get("name", ""))]
    if not cands:
        return None

    def _sy(v):
        try:
            return int(str(v.get("start_year"))[:4])
        except (TypeError, ValueError):
            return None

    yr = int(year[:4]) if (year or "")[:4].isdigit() else None
    if yr is None:
        return cands[0]
    with_year = [(s, v) for v in cands if (s := _sy(v)) is not None]
    le = [(s, v) for s, v in with_year if s <= yr]
    if le:
        return max(le, key=lambda t: t[0])[1]
    if with_year:
        return min(with_year, key=lambda t: t[0])[1]
    return cands[0]


def _unverified(note: str) -> dict:
    return {"ok": True, "matched_issue_id": None, "cover_date": "",
            "character_present": None, "note": note}


def verify_issue(series: str, issue_number, year: str, entity: str, *, log=print) -> dict:
    """Cross-check one Q&A item's source against Comic Vine.

    Returns {ok, matched_issue_id, cover_date, character_present, note}. ok=False
    means "issue not found / wrong year / character absent" — a real red flag the
    caller surfaces (never a hard failure). Any error or missing key => ok=True,
    note='unverified…' so the pipeline is never blocked by a Comic Vine hiccup."""
    key = (COMIC_VINE_API_KEY or "").strip()
    if not key:
        log("[comicvine] no COMIC_VINE_API_KEY — cross-check skipped (unverified)")
        return _unverified("unverified (no API key)")
    series = (series or "").strip()
    issue_number = str(issue_number or "").strip()
    entity = (entity or "").strip()
    if not series or not issue_number:
        return _unverified("unverified (missing series/issue)")

    try:
        vdata = _cv_get("volumes/",
                        {"filter": f"name:{series}",
                         "field_list": "id,name,start_year", "limit": "100"}, key)
        vol = _pick_volume(vdata.get("results") or [], series, year)
        if not vol:
            return {"ok": False, "matched_issue_id": None, "cover_date": "",
                    "character_present": None,
                    "note": f"no Comic Vine volume named '{series}'"}

        idata = _cv_get("issues/",
                        {"filter": f"volume:{vol['id']},issue_number:{issue_number}",
                         "field_list": "id,cover_date,name,issue_number"}, key)
        issues = idata.get("results") or []
        if not issues:
            return {"ok": False, "matched_issue_id": None, "cover_date": "",
                    "character_present": None,
                    "note": (f"issue #{issue_number} not found in "
                             f"'{vol.get('name')}' ({vol.get('start_year')})")}
        iss = issues[0]
        iss_id = iss.get("id")
        cover_date = iss.get("cover_date") or ""

        ddata = _cv_get(f"issue/4000-{iss_id}/",
                        {"field_list": "character_credits,cover_date,name"}, key)
        res = ddata.get("results") or {}
        cover_date = res.get("cover_date") or cover_date
        cred_names = [c.get("name", "") for c in (res.get("character_credits") or [])]
        present = _name_matches(entity, cred_names)

        problems = []
        cover_year = (cover_date or "")[:4]
        if year and year[:4].isdigit() and cover_year.isdigit() \
                and abs(int(cover_year) - int(year[:4])) > 1:
            problems.append(f"cover_date {cover_date} != source_year {year}")
        if not present:
            problems.append(f"'{entity}' not in character_credits")

        return {"ok": not problems, "matched_issue_id": iss_id,
                "cover_date": cover_date, "character_present": present,
                "note": "verified" if not problems else "; ".join(problems)}
    except Exception as exc:  # noqa: BLE001 - confirmation layer; a CV hiccup must never block
        log(f"[comicvine] cross-check error ({type(exc).__name__}: {exc}) — unverified")
        return _unverified(f"unverified ({type(exc).__name__})")


if __name__ == "__main__":
    # Self-check: no network. Exercise the pure helpers + the no-key short-circuit.
    assert _core("The Punisher") == "punisher"
    assert _core("Man-Thing") == "manthing"
    assert _name_matches("The Punisher", ["Punisher", "Nick Fury"]) is True
    assert _name_matches("Deadpool", ["Wolverine"]) is False
    assert _name_matches("Danny Ketch", ["Daniel Ketch", "Blackout"]) is True  # shared 'ketch'

    vols = [
        {"id": 1, "name": "Deadpool", "start_year": "1997"},
        {"id": 2, "name": "Deadpool", "start_year": "2008"},
        {"id": 3, "name": "Deadpool", "start_year": "2012"},
        {"id": 4, "name": "Deadpool Corps", "start_year": "2010"},
    ]
    assert _pick_volume(vols, "Deadpool", "2010")["id"] == 2  # 2008 vol runs at 2010
    assert _pick_volume(vols, "Deadpool", "2013")["id"] == 3
    assert _pick_volume(vols, "Deadpool", "1990")["id"] == 1  # earliest fallback
    assert _pick_volume([], "Deadpool", "2010") is None

    # No key => unverified, never a raise, never a network call.
    import config
    _saved = config.COMIC_VINE_API_KEY
    globals()["COMIC_VINE_API_KEY"] = ""
    r = verify_issue("Deadpool", "26", "2010", "Deadpool", log=lambda _m: None)
    assert r["ok"] is True and r["note"].startswith("unverified")
    globals()["COMIC_VINE_API_KEY"] = _saved

    print("comicvine self-check OK")
