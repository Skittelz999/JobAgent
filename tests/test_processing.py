import json
import sqlite3
from collections import deque
from pathlib import Path

import httpx
import pytest

from jobagent.analysis import PROMPT_VERSION
from jobagent.cli import main
from jobagent.database import Database
from jobagent.filtering import evaluate_experience
from jobagent.llm import LocalLLM
from jobagent.models import JobAd, SourceAd
from jobagent.processing import analyze_stored_job, list_decisions, show_analysis
from jobagent.settings import FilterSettings, LLMSettings
from test_experience import envelope, experience_data, experience_item, model_output

ROOT = Path(__file__).resolve().parents[1]


def record(identifier="1", text="At least 5 years of Java experience required."):
    return SourceAd(job=JobAd(source="test", external_id=identifier, title="Job " + identifier,
                             description=text), raw={"id": identifier, "description": text})


@pytest.fixture
def db(tmp_path):
    database = Database(tmp_path / "jobs.db")
    database.initialize()
    database.upsert_page([record()])
    return database


@pytest.fixture
def mocked_llm(monkeypatch):
    clients = []
    def configure(responses):
        pending = deque(responses)
        calls = []
        def handler(request):
            calls.append(request)
            response = pending.popleft()
            if isinstance(response, Exception):
                raise response
            return response if isinstance(response, httpx.Response) else httpx.Response(200, json=envelope(response))
        client = httpx.Client(transport=httpx.MockTransport(handler))
        clients.append(client)
        provider = LocalLLM(LLMSettings(), client)
        monkeypatch.setattr("jobagent.llm.LocalLLM", lambda settings: LocalLLM(settings, client))
        return provider, calls
    yield configure
    for client in clients:
        client.close()


def source_rows(db):
    with db.connect(read_only=True) as connection:
        return {table: [tuple(row) for row in connection.execute("SELECT * FROM " + table)]
                for table in ("jobs", "ad_versions", "search_runs", "match_results")}


def test_reanalysis_appends_and_preserves_every_source_row(db, mocked_llm):
    before = source_rows(db)
    provider, calls = mocked_llm([model_output(), model_output()])
    first = analyze_stored_job(db, db.get_job(1), provider, FilterSettings())
    second = analyze_stored_job(db, db.get_job(1), provider, FilterSettings())
    assert len(calls) == 2 and first["analysis_id"] != second["analysis_id"]
    assert first["filter"]["decision"] == second["filter"]["decision"] == "EXCLUDE"
    assert source_rows(db) == before
    saved = db.get_analysis(first["analysis_id"])
    assert saved["ad_version"] == 1 and saved["ad_content_hash"] == db.get_job(1)["content_hash"]
    assert saved["model"] == "offline-model" and saved["prompt_version"] == PROMPT_VERSION


def test_concurrent_ad_update_never_attaches_analysis_to_new_version(db):
    old_row = db.get_job(1)
    def handler(request):
        db.upsert_page([record(text="At least 2 years of Java experience required.")])
        return httpx.Response(200, json=envelope(model_output()))
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        result = analyze_stored_job(db, old_row, LocalLLM(LLMSettings(), client), FilterSettings())
    saved = db.get_analysis(result["analysis_id"])
    assert saved["ad_version"] == 1 and saved["ad_content_hash"] == old_row["content_hash"]
    assert db.get_job(1)["version"] == 2
    assert list_decisions(db, FilterSettings(), "EXCLUDE") == []
    review = list_decisions(db, FilterSettings(), "REVIEW")
    assert review[0]["analysis_id"] is None
    assert review[0]["filter"]["reasons"][0]["code"] == "missing_analysis"
    historical = show_analysis(db, result["analysis_id"], FilterSettings())
    assert historical["is_current_ad_version"] is False
    assert historical["current_filter"]["decision"] == "EXCLUDE"


@pytest.mark.parametrize("response", [
    httpx.Response(503), httpx.Response(200, json={}), httpx.Response(200, json={"choices": []}),
    httpx.Response(200, json=envelope({})), httpx.Response(200, json=envelope({"requirements": []})),
    httpx.Response(200, json={"model": "x", "choices": [{"finish_reason": "length"}]}),
    httpx.Response(200, json={"model": "x", "choices": [{"finish_reason": "stop", "message": {"content": "bad JSON"}}]}),
    httpx.ReadTimeout("private transport detail"),
    model_output([experience_item(source_ids=["p9999"])]),
    model_output([experience_item(context_ids=["p9999"])]),
])
def test_failed_latest_attempt_is_saved_as_review_instead_of_old_exclusion(db, mocked_llm, response):
    provider, calls = mocked_llm([model_output(), response])
    first = analyze_stored_job(db, db.get_job(1), provider, FilterSettings())
    assert first["filter"]["decision"] == "EXCLUDE"
    failed = analyze_stored_job(db, db.get_job(1), provider, FilterSettings())
    assert len(calls) == 2
    assert failed["status"] == "error" and failed["filter"]["decision"] == "REVIEW"
    saved = db.get_analysis(failed["analysis_id"])
    assert saved["result_json"] is None and saved["error_message"]
    assert "private transport detail" not in saved["error_message"]
    assert list_decisions(db, FilterSettings(), "EXCLUDE") == []
    assert list_decisions(db, FilterSettings(), "REVIEW")[0]["analysis_id"] == failed["analysis_id"]
    assert db.get_analysis(first["analysis_id"])["decision"] == "EXCLUDE"


def test_missing_analysis_and_changed_configuration_are_recomputed_without_llm(db, mocked_llm):
    assert list_decisions(db, FilterSettings(), "REVIEW")[0]["analysis_status"] is None
    provider, calls = mocked_llm([model_output([experience_item(4)])])
    result = analyze_stored_job(db, db.get_job(1), provider, FilterSettings())
    assert list_decisions(db, FilterSettings(max_required_years=4), "KEEP")[0]["id"] == 1
    assert list_decisions(db, FilterSettings(), "EXCLUDE")[0]["id"] == 1
    shown = show_analysis(db, result["analysis_id"], FilterSettings(max_required_years=4))
    assert shown["saved_filter"]["decision"] == "EXCLUDE"
    assert shown["current_filter"]["decision"] == "KEEP"
    assert shown["is_current_ad_version"] is True
    assert len(calls) == 1


def test_stored_ambiguous_range_is_recomputed_without_reanalysis(db):
    db.upsert_page([record(text="Vi söker dig som:\nÄr i början av din karriär och har cirka 0–3 års relevant erfarenhet")])
    row = db.get_job(1)
    item = experience_item(0, 3, ambiguous=True, importance="expected", domain="relevant",
                           source_ids=["p0002"], context_ids=["p0001"])
    saved_filter = {"decision": "REVIEW", "reasons": [{"code": "ambiguous_requirement"}],
                    "max_required_years": 3, "policy_version": "experience-filter-v1"}
    analysis_id = db.save_analysis(1, row["version"], row["content_hash"], result=model_output([item]),
                                   error=None, evaluation=saved_filter, model="offline-model", prompt_version="experience-extraction-v1")
    before = source_rows(db)

    shown = show_analysis(db, analysis_id, FilterSettings())
    assert shown["saved_filter"] == saved_filter
    assert shown["current_filter"]["decision"] == "KEEP"
    assert shown["is_current_ad_version"] is True
    assert list_decisions(db, FilterSettings(), "KEEP")[0]["analysis_id"] == analysis_id
    assert source_rows(db) == before


def test_omitted_iac_requirement_is_reported_in_analysis_and_readback(db, mocked_llm):
    text = ("What is needed in this role:\nEducation or equivalent experience in infrastructure automation\n"
            "Solid experience in infrastructure as code\nA minimum of 3 years of experience in Software Development")
    db.upsert_page([record(text=text)])
    requirements = [{"passage_id": ref, "kind": "qualification", "importance": "mandatory",
                     "conditional": False, "context_ids": ["p0001"]} for ref in ("p0002", "p0003", "p0004")]
    minimum = experience_item(3, 3, source_ids=["p0004"], context_ids=["p0001"])
    provider, calls = mocked_llm([model_output([minimum], requirements=requirements)])
    before = source_rows(db)

    result = analyze_stored_job(db, db.get_job(1), provider, FilterSettings())
    assert result["status"] == "success" and result["experience"]["coverage_complete"] is True
    assert result["filter"]["decision"] == "REVIEW"
    reason, = result["filter"]["reasons"]
    assert reason["code"] == "missing_experience_coverage"
    source, = reason["source_passages"]
    assert source["passage_id"] == "p0003"
    assert source["quote"] == text[source["start"]:source["end"]] == "Solid experience in infrastructure as code"
    assert show_analysis(db, result["analysis_id"], FilterSettings())["current_filter"] == result["filter"]
    assert list_decisions(db, FilterSettings(), "REVIEW")[0]["filter"] == result["filter"]
    assert len(calls) == 1 and source_rows(db) == before


@pytest.mark.parametrize("requirement", [
    "Minst fem års professionell erfarenhet av mjukvaruutveckling krävs.",
    "At least four years of professional experience are required.",
    "Flerårig och aktuell erfarenhet som testare krävs.",
    "Minst fem års erfarenhet krävs, certifiering är meriterande.",
])
def test_late_requirement_missing_from_both_extractions_is_flagged(db, mocked_llm, requirement):
    text = "Vi utvecklar verktyg för test och drift.\n" * 120 + "Krav\n" + requirement
    db.upsert_page([record(text=text)])
    provider, calls = mocked_llm([model_output([])])

    result = analyze_stored_job(db, db.get_job(1), provider, FilterSettings())
    assert len(calls) == 1 and result["status"] == "success"
    assert result["filter"]["decision"] == "REVIEW"
    reason, = result["filter"]["reasons"]
    assert reason["code"] == "missing_experience_coverage"
    assert [source["quote"] for source in reason["source_passages"]] == [requirement]


@pytest.mark.parametrize("text,importance", [
    ("At least five years of experience is a plus.", None),
    ("Meriterande med flerårig erfarenhet av SQL.", None),
    ("Experience in SQL", "preferred"),
    ("Fem års universitetsutbildning krävs.", "mandatory"),
    ("Education or equivalent experience in infrastructure automation", "mandatory"),
    ("At least five years of experience preferred but not required.", None),
])
def test_coverage_control_does_not_flag_merits_or_education(db, mocked_llm, text, importance):
    db.upsert_page([record(text=text)])
    requirements = [{"passage_id": "p0001", "kind": "qualification", "importance": importance,
                     "conditional": False, "context_ids": []}] if importance else []
    provider, _ = mocked_llm([model_output([], requirements=requirements)])

    result = analyze_stored_job(db, db.get_job(1), provider, FilterSettings())
    assert result["filter"]["decision"] == "KEEP"


def test_invalid_hash_rejected_and_error_cannot_be_excluded(db):
    row = db.get_job(1)
    evaluation = evaluate_experience(experience_data(), "Java", FilterSettings())
    arguments = dict(result=model_output(), error=None, evaluation=evaluation, model="mock", prompt_version=PROMPT_VERSION)
    with pytest.raises(ValueError, match="hash"):
        db.save_analysis(1, 1, "wrong", **arguments)
    with pytest.raises(ValueError, match="hash"):
        db.save_analysis(1, 99, row["content_hash"], **arguments)
    with pytest.raises(sqlite3.IntegrityError):
        db.save_analysis(1, 1, row["content_hash"], **{**arguments, "result": None, "error": "failed"})
    assert db.get_analysis(1) is None


def test_filter_before_limit_keyword_and_stale_prompt(db, mocked_llm):
    db.upsert_page([record("2", text="We build tools for software developers."), record("3")])
    provider, _ = mocked_llm([model_output(), model_output([])])
    analyze_stored_job(db, db.get_job(1), provider, FilterSettings())
    analyze_stored_job(db, db.get_job(2), provider, FilterSettings())
    assert list_decisions(db, FilterSettings(), "EXCLUDE", limit=1)[0]["id"] == 1
    assert list_decisions(db, FilterSettings(), "KEEP", keyword="Job 2")[0]["id"] == 2
    assert list_decisions(db, FilterSettings(), "KEEP", keyword="' OR 1=1 --") == []
    with db.connect() as connection:
        connection.execute("UPDATE analysis_results SET prompt_version='old' WHERE job_id=1")
    assert list_decisions(db, FilterSettings(), "EXCLUDE") == []
    review = list_decisions(db, FilterSettings(), "REVIEW", keyword="Job 1")
    assert review[0]["filter"]["reasons"][0]["code"] == "outdated_analysis"


def test_invalid_persisted_references_are_revalidated_at_read_time(db, mocked_llm):
    provider, _ = mocked_llm([model_output()])
    result = analyze_stored_job(db, db.get_job(1), provider, FilterSettings())
    with db.connect() as connection:
        invalid = {"experience": experience_data([experience_item(source_ids=["p9999"])])}
        connection.execute("UPDATE analysis_results SET result_json=? WHERE id=?", (json.dumps(invalid), result["analysis_id"]))
    assert list_decisions(db, FilterSettings(), "EXCLUDE") == []
    assert show_analysis(db, result["analysis_id"], FilterSettings())["current_filter"]["decision"] == "REVIEW"


def test_cli_batch_continues_after_error_and_readback_is_offline(db, mocked_llm, monkeypatch, capsys):
    db.upsert_page([record("2"), record("3", text="We build tools for software developers.")])
    monkeypatch.setenv("JOBAGENT_DATABASE", str(db.path))
    provider, calls = mocked_llm([model_output(), httpx.Response(503), model_output([])])
    prefix = ["--root", str(ROOT)]
    assert main([*prefix, "analyze", "1", "2", "3"]) == 1
    batch = json.loads(capsys.readouterr().out)
    assert batch["succeeded"] == 2 and batch["failed"] == 1
    assert [r["filter"]["decision"] for r in batch["results"]] == ["EXCLUDE", "REVIEW", "KEEP"]
    assert len(calls) == 3
    for decision, identifier in [("EXCLUDE", 1), ("REVIEW", 2), ("KEEP", 3)]:
        assert main([*prefix, "list", "--decision", decision]) == 0
        listed = json.loads(capsys.readouterr().out)
        assert [r["id"] for r in listed] == [identifier]
        assert listed[0]["filter"]["reasons"]
    assert main([*prefix, "analysis-show", str(batch["results"][0]["analysis_id"])]) == 0
    shown = json.loads(capsys.readouterr().out)
    assert shown["current_filter"]["reasons"][0]["source_passages"][0]["quote"]
    assert len(calls) == 3
    assert source_rows(db)["search_runs"] == []


def test_missing_batch_id_prevents_all_llm_calls(db, mocked_llm, monkeypatch, capsys):
    monkeypatch.setenv("JOBAGENT_DATABASE", str(db.path))
    _, calls = mocked_llm([])
    assert main(["--root", str(ROOT), "analyze", "1", "999"]) == 1
    assert "999" in capsys.readouterr().err and calls == []
    assert db.get_analysis(1) is None


def test_empty_ad_saves_review_without_calling_model(db, mocked_llm):
    db.upsert_page([record(text="")])
    provider, calls = mocked_llm([])
    result = analyze_stored_job(db, db.get_job(1), provider, FilterSettings())
    assert result["status"] == "error" and result["filter"]["decision"] == "REVIEW" and calls == []
