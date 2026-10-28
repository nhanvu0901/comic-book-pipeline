"""Local, source-linked audience and competitor observations for scout cards."""

from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import shutil
from typing import Any, Callable, Mapping
from urllib.parse import urlencode
from urllib.request import Request, urlopen
import re


SCHEMA_VERSION = 4


def _iso8601_duration_seconds(value: Any) -> float | None:
    match = re.fullmatch(r"PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+(?:\.\d+)?)S)?", str(value or ""))
    if not match:
        return None
    hours, minutes, seconds = match.groups()
    return float((int(hours or 0) * 3600) + (int(minutes or 0) * 60) + float(seconds or 0))


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class AudienceEvidenceStore:
    """Separate SQLite registry; it never mutates scout sessions or production data."""

    def __init__(self, root: Path):
        self.path = Path(root) / "audience_evidence.sqlite3"

    def _connect(self, *, write: bool = False) -> sqlite3.Connection:
        if write:
            self.path.parent.mkdir(parents=True, exist_ok=True)
        migrate_existing = write or self.path.exists()
        connection = sqlite3.connect(self.path, timeout=10)
        connection.row_factory = sqlite3.Row
        if migrate_existing:
            connection.execute("PRAGMA foreign_keys=ON")
            version = int(connection.execute("PRAGMA user_version").fetchone()[0])
            if version < 1:
                connection.executescript("""
                    CREATE TABLE IF NOT EXISTS demand_evidence (
                        id INTEGER PRIMARY KEY, candidate_key TEXT NOT NULL, query_text TEXT NOT NULL,
                        url TEXT NOT NULL, quote TEXT NOT NULL, source_type TEXT NOT NULL,
                        confidence TEXT NOT NULL, checked_at TEXT NOT NULL
                    );
                    CREATE TABLE IF NOT EXISTS candidate_videos (
                        candidate_key TEXT NOT NULL, video_id TEXT NOT NULL,
                        relation TEXT NOT NULL DEFAULT 'related', PRIMARY KEY(candidate_key, video_id)
                    );
                    CREATE TABLE IF NOT EXISTS video_snapshots (
                        id INTEGER PRIMARY KEY, video_id TEXT NOT NULL, url TEXT NOT NULL, channel TEXT NOT NULL,
                        published_at TEXT, duration_seconds REAL, views INTEGER NOT NULL, observed_at TEXT NOT NULL,
                        origin TEXT NOT NULL DEFAULT 'unknown'
                    );
                    CREATE TABLE IF NOT EXISTS video_refreshes (
                        id INTEGER PRIMARY KEY, video_id TEXT NOT NULL, checked_at TEXT NOT NULL, error TEXT NOT NULL
                    );
                    CREATE INDEX IF NOT EXISTS audience_demand_key ON demand_evidence(candidate_key, checked_at DESC);
                    CREATE INDEX IF NOT EXISTS audience_video_snapshots ON video_snapshots(video_id, observed_at);
                """)
                version = 1
            if version < 2:
                columns = {str(row[1]) for row in connection.execute("PRAGMA table_info(candidate_videos)")}
                if "relation" not in columns:
                    connection.execute("ALTER TABLE candidate_videos ADD COLUMN relation TEXT NOT NULL DEFAULT 'related'")
                version = 2
            if version < 3:
                connection.execute("""
                    CREATE TABLE IF NOT EXISTS editorial_assessments (
                        candidate_key TEXT PRIMARY KEY, assessment_json TEXT NOT NULL,
                        assessor TEXT NOT NULL, assessed_at TEXT NOT NULL, status TEXT NOT NULL
                    )
                """)
                version = 3
            if version < 4:
                columns = {str(row[1]) for row in connection.execute("PRAGMA table_info(video_snapshots)")}
                if "origin" not in columns:
                    connection.execute("ALTER TABLE video_snapshots ADD COLUMN origin TEXT NOT NULL DEFAULT 'unknown'")
                version = 4
            connection.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
            connection.commit()
        return connection

    def add_demand_evidence(self, *, candidate_key: str, query: str, url: str, quote: str,
                            source_type: str, confidence: str, checked_at: str | None = None) -> None:
        with self._connect(write=True) as connection:
            connection.execute(
                "INSERT INTO demand_evidence(candidate_key, query_text, url, quote, source_type, confidence, checked_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (candidate_key, query, url, quote, source_type, confidence, checked_at or _now()),
            )

    def link_video(self, candidate_key: str, video_id: str, *, relation: str = "related") -> None:
        with self._connect(write=True) as connection:
            connection.execute(
                "INSERT INTO candidate_videos(candidate_key, video_id, relation) VALUES (?, ?, ?) "
                "ON CONFLICT(candidate_key, video_id) DO UPDATE SET relation=excluded.relation",
                (candidate_key, video_id, relation),
            )

    def add_video_snapshot(self, *, video_id: str, url: str, channel: str, published_at: str | None,
                           duration_seconds: float | None, views: int, observed_at: str | None = None,
                           origin: str = "unknown") -> None:
        with self._connect(write=True) as connection:
            connection.execute(
                "INSERT INTO video_snapshots(video_id, url, channel, published_at, duration_seconds, views, observed_at, origin) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (video_id, url, channel, published_at, duration_seconds, int(views), observed_at or _now(), origin),
            )

    def record_refresh_failure(self, video_id: str, error: str, *, checked_at: str | None = None) -> None:
        with self._connect(write=True) as connection:
            connection.execute("INSERT INTO video_refreshes(video_id, checked_at, error) VALUES (?, ?, ?)",
                               (video_id, checked_at or _now(), error))

    def evidence_for(self, candidate_key: str) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        with self._connect() as connection:
            return [dict(row) for row in connection.execute(
                "SELECT query_text AS query, url, quote, source_type, confidence, checked_at FROM demand_evidence WHERE candidate_key = ? ORDER BY checked_at DESC", (candidate_key,))]

    def video_signal(self, video_id: str, *, stale_after_seconds: float = 7 * 86400) -> dict[str, Any]:
        if not self.path.exists():
            return {"status": "unknown", "current_views": None, "growth": {"status": "unknown"}}
        with self._connect() as connection:
            rows = [dict(row) for row in connection.execute(
                "SELECT * FROM video_snapshots WHERE video_id = ? ORDER BY observed_at ASC, id ASC", (video_id,))]
            failure = connection.execute("SELECT error FROM video_refreshes WHERE video_id = ? ORDER BY checked_at DESC, id DESC LIMIT 1", (video_id,)).fetchone()
        if not rows:
            return {"status": "unknown", "current_views": None, "growth": {"status": "unknown"}}
        latest = rows[-1]
        try:
            stale = (datetime.now(timezone.utc) - datetime.fromisoformat(str(latest["observed_at"]))).total_seconds() > stale_after_seconds
        except ValueError:
            stale = True
        growth: dict[str, Any] = {"status": "unknown"}
        if len(rows) >= 2:
            prior = rows[-2]
            try:
                seconds = (datetime.fromisoformat(str(latest["observed_at"])) - datetime.fromisoformat(str(prior["observed_at"]))).total_seconds()
            except ValueError:
                seconds = 0.0
            if seconds > 0:
                growth = {"status": "observed", "views_delta": int(latest["views"]) - int(prior["views"]), "seconds": seconds}
        return {
            "status": "stale" if stale else "observed", "video_id": video_id, "url": latest["url"],
            "channel": latest["channel"], "published_at": latest["published_at"], "duration_seconds": latest["duration_seconds"],
            "current_views": latest["views"], "observed_at": latest["observed_at"], "metadata_origin": latest["origin"], "growth": growth,
            **({"last_refresh_error": failure["error"]} if failure else {}),
        }

    def signals_for(self, candidate_key: str) -> dict[str, dict[str, Any]]:
        evidence = self.evidence_for(candidate_key)
        if not self.path.exists():
            return {"demand": {"status": "unknown"}, "saturation": {"status": "unknown"}}
        with self._connect() as connection:
            linked = [dict(row) for row in connection.execute(
                "SELECT video_id, relation FROM candidate_videos WHERE candidate_key = ? ORDER BY video_id", (candidate_key,))]
        videos = [{**self.video_signal(str(row["video_id"])), "relation": str(row["relation"] or "related")}
                  for row in linked]
        direct_count = sum(1 for row in evidence if str(row.get("confidence") or "").casefold() == "direct")
        return {
            "demand": ({"status": "observed", "count": len(evidence), "direct_count": direct_count,
                        "checked_at": evidence[0]["checked_at"],
                        "cue": f"{direct_count} direct source-linked demand observation(s)."}
                       if evidence else {"status": "unknown"}),
            "saturation": (
                {"status": "observed", "count": len(videos), "videos": videos,
                 "coverage_proof": False,
                 "reason": "Related competitor observations are not proof that this exact question was narrated.",
                 "cohort": {"status": "unknown", "reason": "Public metadata lacks a matched channel, age, and format cohort."}}
                if videos else {"status": "unknown"}
            ),
        }

    def videos_for(self, candidate_key: str) -> list[str]:
        if not self.path.exists():
            return []
        with self._connect() as connection:
            return [str(row[0]) for row in connection.execute(
                "SELECT video_id FROM candidate_videos WHERE candidate_key = ? ORDER BY video_id", (candidate_key,))]

    def last_observed_at(self, video_id: str) -> str | None:
        if not self.path.exists():
            return None
        with self._connect() as connection:
            row = connection.execute(
                "SELECT observed_at FROM video_snapshots WHERE video_id = ? ORDER BY observed_at DESC, id DESC LIMIT 1",
                (video_id,),
            ).fetchone()
        return str(row[0]) if row else None

    def save_quality_assessment(self, candidate_key: str, assessment: Mapping[str, Any], *,
                                assessor: str, status: str = "assessed", assessed_at: str | None = None) -> None:
        """Persist only structured assessment payloads; provenance is explicit."""
        timestamp = assessed_at or _now()
        payload = dict(assessment)
        payload["assessor"] = assessor
        payload["assessed_at"] = timestamp
        with self._connect(write=True) as connection:
            connection.execute(
                "INSERT INTO editorial_assessments(candidate_key, assessment_json, assessor, assessed_at, status) "
                "VALUES (?, ?, ?, ?, ?) ON CONFLICT(candidate_key) DO UPDATE SET "
                "assessment_json=excluded.assessment_json, assessor=excluded.assessor, "
                "assessed_at=excluded.assessed_at, status=excluded.status",
                (candidate_key, json.dumps(payload, ensure_ascii=False), assessor, timestamp, status),
            )

    def quality_for(self, candidate_key: str) -> dict[str, Any] | None:
        if not self.path.exists():
            return None
        with self._connect() as connection:
            row = connection.execute(
                "SELECT assessment_json, assessor, assessed_at, status FROM editorial_assessments WHERE candidate_key = ?",
                (candidate_key,),
            ).fetchone()
        if row is None:
            return None
        try:
            payload = json.loads(str(row["assessment_json"]))
        except json.JSONDecodeError:
            return None
        if not isinstance(payload, dict):
            return None
        payload.setdefault("assessor", row["assessor"])
        payload.setdefault("assessed_at", row["assessed_at"])
        return payload


class PublicVideoMetadataAdapter:
    """Optional YouTube Data API metadata reader; no OAuth or media transfer."""

    def __init__(self, api_key: str | None = None, *, fetch_json: Callable[[str], Mapping[str, Any]] | None = None,
                 metadata_fetch: Callable[[str], Mapping[str, Any]] | None = None):
        self.api_key = api_key if api_key is not None else os.getenv("YOUTUBE_API_KEY", "")
        self.fetch_json = fetch_json or self._fetch_json
        self.metadata_fetch = metadata_fetch or self._ytdlp_metadata

    @staticmethod
    def _fetch_json(url: str) -> Mapping[str, Any]:
        request = Request(url, headers={"User-Agent": "comic-book-pipeline/metadata"})
        with urlopen(request, timeout=8) as response:  # nosec B310: public API URL is constructed below
            return json.loads(response.read().decode("utf-8"))

    @staticmethod
    def _ytdlp_metadata(video_id: str) -> Mapping[str, Any]:
        """Public yt-dlp info probe: skip-download and one JSON response, no media."""
        executable = os.environ.get("YTDLP_BIN", "").strip() or shutil.which("yt-dlp")
        command = [executable] if executable else [sys.executable, "-m", "yt_dlp"]
        url = f"https://www.youtube.com/watch?v={video_id}"
        response = subprocess.run(
            [*command, "--skip-download", "--no-playlist", "--dump-single-json", url],
            capture_output=True, text=True, timeout=20, check=False,
        )
        if response.returncode != 0:
            raise RuntimeError("yt_dlp_metadata_unavailable")
        data = json.loads(response.stdout or "")
        if not isinstance(data, Mapping):
            raise RuntimeError("yt_dlp_metadata_invalid")
        return data

    @staticmethod
    def _store_ytdlp_metadata(store: AudienceEvidenceStore, video_id: str, info: Mapping[str, Any]) -> None:
        timestamp = info.get("release_timestamp") or info.get("timestamp")
        published_at = datetime.fromtimestamp(float(timestamp), timezone.utc).isoformat() if timestamp else str(info.get("upload_date") or "") or None
        store.add_video_snapshot(
            video_id=str(info.get("id") or video_id),
            url=str(info.get("webpage_url") or f"https://www.youtube.com/watch?v={video_id}"),
            channel=str(info.get("channel") or info.get("uploader") or ""),
            published_at=published_at,
            duration_seconds=float(info["duration"]) if info.get("duration") is not None else None,
            views=int(info.get("view_count") or 0), origin="yt_dlp_public_metadata",
        )

    def refresh(self, store: AudienceEvidenceStore, video_id: str, *, candidate_key: str | None = None) -> dict[str, Any]:
        if candidate_key:
            store.link_video(candidate_key, video_id)
        try:
            if not self.api_key:
                self._store_ytdlp_metadata(store, video_id, self.metadata_fetch(video_id))
                return store.video_signal(video_id)
            query = urlencode({"part": "snippet,statistics,contentDetails", "id": video_id, "key": self.api_key})
            payload = self.fetch_json("https://www.googleapis.com/youtube/v3/videos?" + query)
            items = payload.get("items") if isinstance(payload, Mapping) else None
            if not isinstance(items, list) or not items:
                raise ValueError("video_not_found")
            item = items[0]
            snippet = item.get("snippet") or {}
            statistics = item.get("statistics") or {}
            store.add_video_snapshot(video_id=video_id, url=f"https://www.youtube.com/watch?v={video_id}",
                                     channel=str(snippet.get("channelTitle") or ""), published_at=snippet.get("publishedAt"),
                                     duration_seconds=_iso8601_duration_seconds((item.get("contentDetails") or {}).get("duration")),
                                     views=int(statistics.get("viewCount") or 0), origin="youtube_data_api_v3")
            return store.video_signal(video_id)
        except Exception as exc:  # preserve historical observations on all provider failures
            store.record_refresh_failure(video_id, type(exc).__name__ if str(exc) == "" else str(exc)[:160])
            return store.video_signal(video_id)

    def refresh_many(
        self, store: AudienceEvidenceStore, candidate_key: str, *, limit: int = 10,
        min_refresh_seconds: float = 6 * 3600,
    ) -> dict[str, list[str]]:
        """Refresh only a bounded, stale subset; fresh snapshots are cache hits."""
        refreshed: list[str] = []
        skipped_fresh: list[str] = []
        for video_id in store.videos_for(candidate_key)[:max(0, limit)]:
            observed_at = store.last_observed_at(video_id)
            try:
                age = (datetime.now(timezone.utc) - datetime.fromisoformat(observed_at)).total_seconds() if observed_at else None
            except ValueError:
                age = None
            if age is not None and age < min_refresh_seconds:
                skipped_fresh.append(video_id)
                continue
            self.refresh(store, video_id, candidate_key=candidate_key)
            refreshed.append(video_id)
        return {"refreshed": refreshed, "skipped_fresh": skipped_fresh}
