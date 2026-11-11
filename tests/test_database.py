import json

import pytest

from jobagent.database import SCHEMA_VERSION, Database
from jobagent.models import JobAd, SourceAd


@pytest.fixture
def db(tmp_path):
    database = Database(tmp_path / "jobs.sqlite3")
    database.initialize()
    return database


def ad(external_id="1", title="Javautvecklare", source="platsbanken"):
    return SourceAd(job=JobAd(source=source, external_id=external_id, title=title,
                             employer="Företag AB", description="Spring Boot"), raw={"id": external_id})


def test_repeat_is_idempotent_and_updates_create_history(db):
    assert db.upsert_page([ad()])["inserted"] == 1
    original = db.list_jobs()[0]
    assert db.upsert_page([ad()])["unchanged"] == 1
    assert db.upsert_page([ad(title="Backendutvecklare")])["updated"] == 1
    current = db.list_jobs()[0]
    assert current["id"] == original["id"]
    assert current["first_seen"] == original["first_seen"]
    assert current["version"] == 2
    with db.connect() as connection:
        versions = connection.execute("SELECT * FROM ad_versions ORDER BY version").fetchall()
    assert len(versions) == 2
    assert json.loads(versions[0]["payload_json"])["title"] == "Javautvecklare"


def test_same_title_is_not_sufficient_to_merge(db):
    db.upsert_page([ad(), ad("2"), ad(source="other")])
    assert len(db.list_jobs()) == 3


def test_raw_ranking_changes_do_not_create_content_version(db):
    record = ad()
    db.upsert_page([record])
    record.raw["relevance"] = 123
    assert db.upsert_page([record])["unchanged"] == 1


def test_initialize_preserves_existing_data(db):
    db.upsert_page([ad()])
    db.initialize()
    assert len(db.list_jobs()) == 1


def test_history_and_safe_keyword(db):
    run = db.start_run({"queries": ["Java"]})
    db.finish_run(run, "partial", {"reason": "page limit"})
    assert db.history()[0]["status"] == "partial"
    db.upsert_page([ad()])
    assert db.list_jobs(keyword="' OR 1=1 --") == []
    assert len(db.list_jobs(keyword="Java")) == 1


def test_page_rollback_on_database_error(db):
    with db.connect() as connection:
        connection.execute("""CREATE TRIGGER reject_second BEFORE INSERT ON jobs
            WHEN NEW.external_id='2' BEGIN SELECT RAISE(ABORT, 'test failure'); END""")
    import sqlite3
    with pytest.raises(sqlite3.IntegrityError):
        db.upsert_page([ad(), ad("2")])
    assert db.list_jobs() == []


def test_schema_two_migration_preserves_all_rows_including_legacy_results(db):
    db.upsert_page([ad()])
    run = db.start_run({"queries": ["Java"]})
    db.finish_run(run, "complete", {})
    # Recreate schema 2 and seed an opaque historical row.
    with db.connect() as connection:
        connection.execute("DROP TABLE background_tasks")
        connection.execute("DROP TABLE analysis_results")
        connection.execute("PRAGMA user_version=2")
        connection.execute("""INSERT INTO match_results
            (job_id, ad_version, result_json, profile_hash, model, prompt_version, created_at)
            VALUES(1, 1, ?, 'historical-hash', 'historical-model', 'historical-prompt', '2026-11-01')""",
            ('{"legacy":true}',))
        tables = ("jobs", "ad_versions", "search_runs", "match_results")
        before = {table: [tuple(row) for row in connection.execute("SELECT * FROM " + table)]
                  for table in tables}
        schema_before = [tuple(row) for row in connection.execute(
            "SELECT type, name, tbl_name, sql FROM sqlite_master ORDER BY type, name")]
    db.initialize()
    db.initialize()
    db.list_jobs()
    db.get_job(1)
    db.history()
    with db.connect(read_only=True) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
        for table, rows in before.items():
            assert [tuple(row) for row in connection.execute("SELECT * FROM " + table)] == rows
        assert [tuple(row) for row in connection.execute(
            "SELECT type, name, tbl_name, sql FROM sqlite_master WHERE name NOT IN ('analysis_results', 'analysis_version_latest', 'background_tasks') ORDER BY type, name")] == schema_before
        assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
    # Collection can create a new ad version while the historical reference stays intact.
    assert db.upsert_page([ad(title="Changed")])["updated"] == 1
    with db.connect(read_only=True) as connection:
        assert connection.execute("SELECT ad_version FROM match_results").fetchone()[0] == 1
        assert connection.execute("SELECT count(*) FROM ad_versions").fetchone()[0] == 2
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []


def test_schema_one_migration_preserves_all_original_rows(db):
    db.upsert_page([ad()])
    run = db.start_run({"queries": ["Java"]})
    db.finish_run(run, "complete", {})
    with db.connect() as connection:
        connection.execute("DROP TABLE background_tasks")
        connection.execute("DROP TABLE analysis_results")
        connection.execute("DROP TABLE match_results")
        connection.execute("PRAGMA user_version=1")
        before = {table: [tuple(r) for r in connection.execute("SELECT * FROM " + table)]
                  for table in ("jobs", "ad_versions", "search_runs")}
    db.initialize()
    db.initialize()
    with db.connect(read_only=True) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
        for table, rows in before.items():
            assert [tuple(r) for r in connection.execute("SELECT * FROM " + table)] == rows
        assert connection.execute("SELECT count(*) FROM match_results").fetchone()[0] == 0


def test_newer_schema_rejected(db):
    with db.connect() as connection:
        connection.execute(f"PRAGMA user_version={SCHEMA_VERSION + 1}")
    with pytest.raises(ValueError, match="newer"):
        db.initialize()
