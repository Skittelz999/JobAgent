"""Coordinate stored ads, pure analysis, deterministic filtering and append-only results."""
import json

from jobagent.analysis import COMPATIBLE_PROMPT_VERSIONS, PROMPT_VERSION, analyze_job
from jobagent.database import Database
from jobagent.filtering import evaluate_experience
from jobagent.llm import LLMError, LLMProvider
from jobagent.models import JobAd
from jobagent.settings import FilterSettings


def _evaluate_result(result: dict | None, description: str, settings: FilterSettings) -> dict:
    if not isinstance(result, dict):
        return evaluate_experience(None, description, settings)
    analysis = result.get("analysis", {})
    if not isinstance(analysis, dict):
        return evaluate_experience(None, description, settings)
    return evaluate_experience(result.get("experience"), description, settings,
                               requirements=analysis.get("requirements"))


def analyze_stored_job(database: Database, row: dict, provider: LLMProvider, settings: FilterSettings) -> dict:
    result, error = None, None
    job = JobAd.model_validate_json(row["payload_json"])
    try:
        result = analyze_job(job, provider)
        result["requested_model"] = provider.settings.model
    except LLMError as exc:
        error = str(exc)
    evaluation = _evaluate_result(result, job.description, settings)
    analysis_id = database.save_analysis(row["id"], row["version"], row["content_hash"],
        result=result, error=error, evaluation=evaluation,
        model=result["model"] if result else provider.settings.model, prompt_version=PROMPT_VERSION)
    saved = database.get_analysis(analysis_id)
    return {"analysis_id": analysis_id, "ad_id": row["id"], "ad_version": row["version"],
            "ad_content_hash": row["content_hash"], "title": job.title, "original_url": job.original_url,
            "status": saved["status"], "created_at": saved["created_at"], "error": error,
            **(result or {}), "filter": evaluation}


def current_evaluation(result_json: str | None, status: str | None, prompt_version: str | None,
                       description: str, settings: FilterSettings) -> dict:
    """Use today's filter for compatible results; failed attempts require review."""
    if status != "success" or prompt_version not in COMPATIBLE_PROMPT_VERSIONS:
        evaluation = evaluate_experience(None, description, settings)
        evaluation["reasons"][0]["code"] = (
            "analysis_failed" if status == "error" else "outdated_analysis" if status else "missing_analysis")
        return evaluation
    try:
        result = json.loads(result_json or "null")
    except (ValueError, TypeError):
        result = None
    return _evaluate_result(result, description, settings)


def list_decisions(database: Database, settings: FilterSettings, decision: str,
                   limit: int = 20, keyword: str | None = None) -> list[dict]:
    results = []
    for row in database.jobs_with_latest_analysis(keyword):
        job = JobAd.model_validate_json(row["payload_json"])
        evaluation = current_evaluation(row["analysis_result_json"], row["analysis_status"],
                                        row["analysis_prompt_version"], job.description, settings)
        if evaluation["decision"] != decision:
            continue
        results.append({key: value for key, value in row.items()
                        if key not in {"payload_json", "content_hash", "analysis_result_json"}} | {"filter": evaluation})
        if len(results) >= limit:
            break
    return results


def show_analysis(database: Database, analysis_id: int, settings: FilterSettings) -> dict:
    saved = database.get_analysis(analysis_id)
    if saved is None:
        raise ValueError("No saved analysis with that ID")
    version = database.get_ad_version(saved["job_id"], saved["ad_version"])
    job = JobAd.model_validate_json(version["payload_json"])
    current = database.get_job(saved["job_id"])
    evaluation = current_evaluation(saved["result_json"], saved["status"], saved["prompt_version"],
                                    job.description, settings)
    if version["content_hash"] != saved["ad_content_hash"]:
        evaluation = evaluate_experience(None, job.description, settings)
        evaluation["reasons"][0]["code"] = "content_hash_mismatch"
    return {key: value for key, value in saved.items() if key not in {"result_json", "filter_json"}} | {
        "result": json.loads(saved["result_json"]) if saved["result_json"] else None,
        "saved_filter": json.loads(saved["filter_json"]), "current_filter": evaluation,
        "is_current_ad_version": current["version"] == saved["ad_version"] and current["content_hash"] == saved["ad_content_hash"]}


def pending_jobs(database: Database, model: str, provider: str = "local") -> list[dict]:
    """Only latest successful current-version/current-model/current-prompt results are reusable."""
    return [row for row in database.jobs_with_latest_analysis()
            if not analysis_is_current(row, model, provider)]


def analyze_pending(database: Database, provider: LLMProvider, settings: FilterSettings, progress=None) -> dict:
    rows = pending_jobs(database, provider.settings.model)
    results = []
    for row in rows:
        # Read again: the ad may have changed since selection.
        results.append(analyze_stored_job(database, database.get_job(row["id"]), provider, settings))
        if progress:
            progress({"completed": len(results), "total": len(rows)})
    failed = sum(r["status"] == "error" for r in results)
    return {"results": results, "succeeded": len(results) - failed, "failed": failed}


def analysis_is_current(row: dict, model: str, provider: str = "local") -> bool:
    if row["analysis_status"] != "success" or row["analysis_prompt_version"] != PROMPT_VERSION:
        return False
    result = json.loads(row["analysis_result_json"] or "{}")
    return result.get("provider", "local") == provider and result.get("requested_model", row["analysis_model"]) == model
