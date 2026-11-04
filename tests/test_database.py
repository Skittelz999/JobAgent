import json

import pytest

from jobagent.database import Database
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
