"""Small SQLite repository. No connections or files are opened on import."""
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
import hashlib
import json
from pathlib import Path
import sqlite3
from typing import Any, Literal

from jobagent.models import SourceAd

SCHEMA_VERSION = 2

def utc_now() -> str:
    return datetime.now(UTC).isoformat()


def json_text(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


class Database:
    def __init__(self, path: Path):
        self.path = path

    @contextmanager
    def connect(self, read_only: bool = False) -> Iterator[sqlite3.Connection]:
        target = self.path.resolve().as_uri() + "?mode=ro" if read_only else self.path
        connection = sqlite3.connect(target, timeout=10, uri=read_only)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def initialize(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as connection:
            version = connection.execute("PRAGMA user_version").fetchone()[0]
            if version > SCHEMA_VERSION:
                raise ValueError("Database is newer than this application")
            connection.execute("PRAGMA journal_mode=WAL")
            if version == 0:
                connection.executescript("""
                    BEGIN IMMEDIATE;
                    CREATE TABLE jobs (
                        id INTEGER PRIMARY KEY,
                        source TEXT NOT NULL,
                        external_id TEXT NOT NULL,
                        title TEXT NOT NULL,
                        employer TEXT,
                        location TEXT,
                        published_at TEXT,
                        deadline TEXT,
                        removed INTEGER NOT NULL,
                        first_seen TEXT NOT NULL,
                        last_seen TEXT NOT NULL,
                        content_hash TEXT NOT NULL,
                        version INTEGER NOT NULL,
                        payload_json TEXT NOT NULL,
                        UNIQUE(source, external_id)
                    );
                    CREATE TABLE ad_versions (
                        job_id INTEGER NOT NULL REFERENCES jobs(id),
                        version INTEGER NOT NULL,
                        retrieved_at TEXT NOT NULL,
                        content_hash TEXT NOT NULL,
                        payload_json TEXT NOT NULL,
                        raw_json TEXT NOT NULL,
                        PRIMARY KEY(job_id, version)
                    );
                    CREATE TABLE search_runs (
                        id INTEGER PRIMARY KEY,
                        started_at TEXT NOT NULL,
                        finished_at TEXT,
                        status TEXT NOT NULL,
                        parameters_json TEXT NOT NULL,
                        result_json TEXT
                    );
                    CREATE INDEX jobs_published ON jobs(published_at);
                    PRAGMA user_version=1;
                    COMMIT;
                """)
            if version < 2:
                # Preserve the schema-2 contract and historical results. No active
                # application flow reads or writes this legacy table.
                connection.executescript("""
                    BEGIN IMMEDIATE;
                    CREATE TABLE IF NOT EXISTS match_results (
                        id INTEGER PRIMARY KEY,
                        job_id INTEGER NOT NULL,
                        ad_version INTEGER NOT NULL,
                        result_json TEXT NOT NULL,
                        profile_hash TEXT NOT NULL,
                        model TEXT NOT NULL,
                        prompt_version TEXT NOT NULL,
                        created_at TEXT NOT NULL,
                        FOREIGN KEY(job_id, ad_version) REFERENCES ad_versions(job_id, version)
                    );
                    PRAGMA user_version=2;
                    COMMIT;
                """)

    def upsert_page(self, ads: list[SourceAd]) -> dict[str, int]:
        """Commit an entire validated page; identity is source + external ID.

        Hash normalized content, not raw search ranking or retrieval metadata.
        Raw input is retained with each material normalized content version.
        """
        counts = {"inserted": 0, "updated": 0, "unchanged": 0}
        now = utc_now()
        with self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            for ad in ads:
                job = ad.job
                payload = json_text(job.model_dump(mode="json"))
                digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()
                existing = connection.execute(
                    "SELECT id, content_hash, version FROM jobs WHERE source=? AND external_id=?",
                    (job.source, job.external_id),
                ).fetchone()
                if existing and existing["content_hash"] == digest:
                    connection.execute("UPDATE jobs SET last_seen=? WHERE id=?", (now, existing["id"]))
                    counts["unchanged"] += 1
                    continue
                version = existing["version"] + 1 if existing else 1
                values = (job.title, job.employer, job.location,
                          job.published_at.isoformat() if job.published_at else None,
                          job.deadline.isoformat() if job.deadline else None,
                          job.removed, now, digest, version, payload)
                if existing:
                    job_id = existing["id"]
                    connection.execute("""UPDATE jobs SET title=?, employer=?, location=?,
                        published_at=?, deadline=?, removed=?, last_seen=?, content_hash=?,
                        version=?, payload_json=? WHERE id=?""", (*values, job_id))
                    action: Literal["inserted", "updated"] = "updated"
                else:
                    cursor = connection.execute("""INSERT INTO jobs(title, employer, location,
                        published_at, deadline, removed, last_seen, content_hash, version,
                        payload_json, source, external_id, first_seen)
                        VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""", (*values, job.source, job.external_id, now))
                    job_id = cursor.lastrowid
                    action = "inserted"
                connection.execute("INSERT INTO ad_versions VALUES(?,?,?,?,?,?)",
                                   (job_id, version, now, digest, payload, json_text(ad.raw)))
                counts[action] += 1
        return counts

    def start_run(self, parameters: dict[str, Any]) -> int:
        with self.connect() as connection:
            cursor = connection.execute(
                "INSERT INTO search_runs(started_at,status,parameters_json) VALUES(?,?,?)",
                (utc_now(), "running", json_text(parameters)),
            )
            return int(cursor.lastrowid)

    def finish_run(self, run_id: int, status: str, result: dict[str, Any]) -> None:
        with self.connect() as connection:
            connection.execute("UPDATE search_runs SET finished_at=?,status=?,result_json=? WHERE id=?",
                               (utc_now(), status, json_text(result), run_id))

    def list_jobs(self, limit: int = 20, keyword: str | None = None) -> list[dict[str, Any]]:
        sql = "SELECT * FROM jobs"
        params: list[Any] = []
        if keyword:
            sql += " WHERE instr(lower(title || ' ' || coalesce(employer,'') || ' ' || coalesce(location,'')),lower(?)) > 0"
            params.append(keyword)
        sql += " ORDER BY first_seen DESC, id DESC LIMIT ?"
        params.append(limit)
        with self.connect() as connection:
            return [dict(row) for row in connection.execute(sql, params)]

    def get_job(self, job_id: int) -> dict[str, Any] | None:
        with self.connect(read_only=True) as connection:
            row = connection.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
            return dict(row) if row else None

    def history(self, limit: int = 20) -> list[dict[str, Any]]:
        with self.connect() as connection:
            return [dict(row) for row in connection.execute(
                "SELECT * FROM search_runs ORDER BY id DESC LIMIT ?", (limit,))]
