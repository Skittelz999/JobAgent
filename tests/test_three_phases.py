import json
import re
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from jobagent.analysis import PROMPT_VERSION, analyze_job
from jobagent.database import Database
from jobagent.llm import LLMError, LocalLLM
from jobagent.models import JobAd
from jobagent.processing import analyze_pending, pending_jobs
from jobagent.search import collect_sources, import_ad
from jobagent.settings import Settings, Storage, LLMSettings, Search
from jobagent.sources import ManualImport, ManualSource, SourceError
from jobagent.tasks import TaskQueue, WorkerLock
from jobagent.web import create_app
from test_experience import model_output, envelope, experience_item


def imported(**changes):
    return ManualImport(source="linkedin", title="Backend", original_url="https://example.org/jobs/1",
                        description="At least 4 years of Java experience required.", **changes)


@pytest.fixture
def settings(tmp_path):
    return Settings(storage=Storage(database=tmp_path / "jobs.sqlite3"), llm=LLMSettings(model="offline-model"))


@pytest.fixture
def db(settings):
    result = Database(settings.storage.database)
    result.initialize()
    return result


def model_provider():
    data = model_output()
    data["overview"]["techstack"] = [{"text_sv": "Java", "source_ids": ["p0001"], "category": "language",
                                       "usage": "candidate_requirement", "importance": "mandatory"}]
    return LocalLLM(LLMSettings(model="offline-model"), httpx.Client(transport=httpx.MockTransport(
        lambda _: httpx.Response(200, json=envelope(data)))))


def csrf(client, path="/"):
    response = client.get(path)
    assert response.status_code == 200
    return re.search(r'name="csrf" value="([^"]+)"', response.text).group(1)


def test_import_identity_version_raw_and_source_separation(db):
    value = imported()
    assert import_ad(db, value)["inserted"] == 1
    assert import_ad(db, value)["unchanged"] == 1
    assert import_ad(db, value.model_copy(update={"original_url": value.original_url + "#anchor"}))["unchanged"] == 1
    assert import_ad(db, value.model_copy(update={"description": "Changed\n entire text  "}))["updated"] == 1
    assert db.get_job(1)["version"] == 2
    assert json.loads(db.get_ad_version(1, 2)["raw_json"])["description"] == "Changed\n entire text  "
    assert import_ad(db, value.model_copy(update={"source": "indeed"}))["inserted"] == 1
    with pytest.raises(ValueError):
        import_ad(db, value.model_copy(update={"original_url": "javascript:alert(1)"}))


def test_source_failure_does_not_stop_another_source(db):
    class Broken:
        name = "broken"
        def batches(self, spec):
            raise SourceError("Unavailable")
            yield
    results = collect_sources(db, [Broken(), ManualSource(imported())], Search())
    assert [r["status"] for r in results] == ["failed", "complete"]
    assert len(db.list_jobs()) == 1


def test_pending_skips_current_and_retries_changed_failed_and_old_model(db, settings):
    value = imported()
    import_ad(db, value)
    provider = model_provider()
    assert analyze_pending(db, provider, settings.filter)["succeeded"] == 1
    assert analyze_pending(db, provider, settings.filter)["results"] == []
    assert len(pending_jobs(db, "different-model")) == 1
    import_ad(db, value.model_copy(update={"description": value.description + " Java."}))
    assert len(pending_jobs(db, provider.settings.model)) == 1
    broken = LocalLLM(provider.settings, httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(503))))
    assert analyze_pending(db, broken, settings.filter)["failed"] == 1
    assert len(pending_jobs(db, provider.settings.model)) == 1
    assert analyze_pending(db, provider, settings.filter)["succeeded"] == 1


@pytest.mark.parametrize("change", ["missing", "bad_reference", "duplicate"])
def test_overview_rejects_missing_or_unverifiable_fields(change):
    data = model_output()
    if change == "missing":
        del data["overview"]
    elif change == "bad_reference":
        data["overview"]["summary"][0]["source_ids"] = ["p9999"]
    elif change == "duplicate":
        data["overview"]["summary"][0]["source_ids"] = ["p0001", "p0001"]
    provider = LocalLLM(LLMSettings(), httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(200, json=envelope(data)))))
    with pytest.raises(LLMError):
        analyze_job(JobAd(source="test", external_id="1", title="Role", description="Java experience"), provider)


@pytest.mark.parametrize(("usage", "importance"), [
    ("mentioned", "preferred"),
    ("candidate_requirement", "not_applicable"),
])
def test_conflicting_technology_labels_keep_analysis_and_evidence(usage, importance):
    data = model_output([])
    data["overview"]["techstack"] = [
        {"text_sv": "AEW", "source_ids": ["p0001"], "category": "tool",
         "usage": usage, "importance": importance},
        {"text_sv": "Git", "source_ids": ["p0002"], "category": "tool",
         "usage": "candidate_requirement", "importance": "mandatory"},
    ]
    description = "Meriterande: AEW eller 9LW.\nGit är ett krav."
    job = JobAd(source="test", external_id="1", title="Javautvecklare", description=description)
    with httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(200, json=envelope(data)))) as client:
        result = analyze_job(job, LocalLLM(LLMSettings(), client))
    aew, git = result["overview"]["techstack"]
    assert aew["usage"] == aew["importance"] == "unclear"
    assert aew["source_passages"][0]["quote"] == description.splitlines()[0]
    assert git["usage"] == "candidate_requirement" and git["importance"] == "mandatory"


def test_queue_restart_keeps_completed_analysis_and_recovers_running(db, settings):
    import_ad(db, imported())
    queue = TaskQueue(db, settings, lambda _: model_provider())
    first = queue.enqueue("analyze_pending")
    assert first == queue.enqueue("analyze_pending")
    assert queue.run_once()
    assert queue.get(first)["status"] == "success"
    second = queue.enqueue("analyze_pending")
    with db.connect() as conn:
        conn.execute("UPDATE background_tasks SET status='running' WHERE id=?", (second,))
    restarted = TaskQueue(db, settings, lambda _: model_provider())
    restarted.recover()
    assert restarted.get(first)["status"] == "success"
    assert restarted.get(second)["status"] == "interrupted"
    third = restarted.retry(second)
    assert restarted.run_once()
    assert restarted.get(third)["status"] == "success"
    assert json.loads(restarted.get(third)["result_json"])["results"] == []
    assert not restarted.run_once()


def test_worker_exclusive_lock(tmp_path):
    first, second = WorkerLock(tmp_path / "worker.lock"), WorkerLock(tmp_path / "worker.lock")
    first.acquire()
    try:
        with pytest.raises(RuntimeError):
            second.acquire()
    finally:
        first.release()
    second.acquire()
    second.release()


def test_web_import_analyze_filters_evidence_and_stale_version(settings):
    app = create_app(settings, start_worker=False, provider_factory=lambda _: model_provider())
    with TestClient(app, base_url="http://127.0.0.1") as client:
        token = csrf(client)
        value = imported()
        response = client.post("/import", data={**value.model_dump(exclude_none=True), "csrf": token})
        assert response.status_code == 200 and response.url.path == "/jobs/1"
        assert "At least 4 years" in response.text
        assert client.post("/analyze", data={"csrf": token}).status_code == 200
        assert app.state.queue.run_once()
        detail = client.get("/jobs/1")
        assert detail.status_code == 200
        assert 'href="#p0001"' in detail.text and 'id="p0001"' in detail.text
        assert "Min 4.0" in detail.text or "Min 4" in detail.text
        index = client.get("/")
        assert index.context["count"] == 1  # EXCLUDE must still be visible by default.
        assert client.get("/?technology=Java").context["count"] == 1
        assert client.get("/?years=3").context["count"] == 0
        assert client.get("/?decision=EXCLUDE").context["count"] == 1
        assert client.get("/?source=indeed").context["count"] == 0
        assert client.get("/?q=nonexistent").context["count"] == 0
        value.description = "A changed ad with Python."
        client.post("/import", data={**value.model_dump(exclude_none=True), "csrf": token})
        assert client.get("/?technology=Java").context["count"] == 0
        assert client.get("/?status=pending").context["count"] == 1
        assert 'href="#p0001"' not in client.get("/jobs/1").text
        assert client.get("/static/app.css").status_code == 200
        assert client.get("/api/tasks").json()[0]["status"] == "success"
        assert client.get("/jobs/999").status_code == 404


def test_web_mutations_require_token_origin_host_and_escape_html(settings):
    app = create_app(settings, start_worker=False)
    with TestClient(app, base_url="http://localhost") as client:
        token = csrf(client)
        assert client.post("/analyze", data={"csrf": "bad"}).status_code == 403
        assert client.post("/analyze", data={"csrf": token}, headers={"Origin": "https://evil.example"}).status_code == 403
        assert client.get("/", headers={"host": "evil.example"}).status_code == 400
        value = imported().model_dump(exclude_none=True)
        value["description"] = '<script>alert("x")</script>'
        result = client.post("/import", data={**value, "csrf": token})
        assert "<script>alert" not in result.text and "&lt;script&gt;" in result.text
        assert client.post("/collect", data={"csrf": token, "queries": " ", "days": 30}).status_code == 422
        assert client.post("/collect", data={"csrf": token, "queries": "Java", "days": 30}).status_code == 200
        assert app.state.queue.list()[0]["kind"] == "collect"


def test_browser_forms_with_null_origin_require_valid_token(settings):
    app = create_app(settings, start_worker=False)
    with TestClient(app, base_url="http://127.0.0.1:8765") as client:
        token = csrf(client)
        browser_headers = {"Origin": "null", "Sec-Fetch-Site": "same-origin"}
        for path, fields in [("/analyze", {}), ("/collect", {"queries": "Java", "days": 30})]:
            invalid = client.post(path, data={**fields, "csrf": "fel-å"}, headers=browser_headers)
            assert invalid.status_code == 403
            cross_site = client.post(path, data={**fields, "csrf": token},
                                     headers={**browser_headers, "Sec-Fetch-Site": "cross-site"})
            assert cross_site.status_code == 403
            result = client.post(path, data={**fields, "csrf": token}, headers=browser_headers,
                                 follow_redirects=False)
            assert result.status_code == 303
            assert result.headers["location"] == "/tasks"
        assert {task["kind"] for task in app.state.queue.list()} == {"collect", "analyze_pending"}


def test_schema_three_upgrade_preserves_all_existing_rows(db):
    import_ad(db, imported())
    tables = ("jobs", "ad_versions", "search_runs", "analysis_results", "match_results")
    with db.connect() as conn:
        before = {t: [tuple(r) for r in conn.execute("SELECT * FROM " + t)] for t in tables}
        conn.execute("DROP TABLE background_tasks")
        conn.execute("PRAGMA user_version=3")
    db.initialize()
    with db.connect(read_only=True) as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 4
        for t in tables:
            assert [tuple(r) for r in conn.execute("SELECT * FROM " + t)] == before[t]
        assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"


def test_upgrade_creates_recoverable_sqlite_backup(db, settings):
    import_ad(db, imported())
    with db.connect() as conn:
        conn.execute("DROP TABLE background_tasks")
        conn.execute("PRAGMA user_version=3")
    db.initialize()
    backups = list(settings.storage.database.parent.glob("*.pre-v4.*.sqlite3"))
    assert len(backups) == 1
    backup = Database(backups[0])
    assert backup.get_job(1)["title"] == "Backend"
    with backup.connect(read_only=True) as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 3
        assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"


def test_cli_import_and_pending_flow(tmp_path, monkeypatch, capsys):
    from jobagent.cli import main
    root = Path(__file__).resolve().parents[1]
    monkeypatch.setenv("JOBAGENT_DATABASE", str(tmp_path / "jobs.db"))
    monkeypatch.setattr("jobagent.llm.create_provider", lambda _: model_provider())
    fixture = tmp_path / "ad.json"
    fixture.write_text(imported().model_dump_json(), encoding="utf-8")
    assert main(["--root", str(root), "import", str(fixture)]) == 0
    assert json.loads(capsys.readouterr().out)["inserted"] == 1
    assert main(["--root", str(root), "analyze-pending"]) == 0
    assert json.loads(capsys.readouterr().out)["succeeded"] == 1
    assert main(["--root", str(root), "analyze-pending"]) == 0
    assert json.loads(capsys.readouterr().out)["results"] == []


def test_web_lists_open_ads_in_publication_order(settings):
    from jobagent.platsbanken import normalize
    app = create_app(settings, start_worker=False)
    with TestClient(app, base_url="http://localhost") as client:
        app.state.database.upsert_page([normalize(raw, "Europe/Stockholm") for raw in [
            {"id": 1, "headline": "Expired", "publication_date": "2025-01-03", "application_deadline": "2000-01-01"},
            {"id": 2, "headline": "Newest", "publication_date": "2025-01-02", "application_deadline": "2099-01-01"},
            {"id": 3, "headline": "Older", "publication_date": "2025-01-01"},
            {"id": 4, "headline": "Unknown date"},
        ]])
        assert [row["title"] for row in client.get("/").context["jobs"]] == ["Newest", "Older", "Unknown date"]
        assert client.get("/jobs/1").status_code == 404
        assert client.get("/jobs/3").status_code == 200
