"""Persist source-neutral batches without depending on a particular API."""
from datetime import UTC, datetime
import logging
from jobagent.database import Database
from jobagent.settings import Search
from jobagent.sources import JobSource, SourceError

logger = logging.getLogger(__name__)


def collect(database: Database, source: JobSource, spec: Search, progress=None, *, skip_expired=True) -> dict:
    run_id = database.start_run({**spec.model_dump(), "source": source.name})
    result = {"run_id": run_id, "source": source.name, "inserted": 0, "updated": 0,
              "unchanged": 0, "invalid": 0, "queries": []}
    if skip_expired:
        result["skipped_expired"] = 0
    outcomes = {}
    try:
        try:
            for batch in source.batches(spec):
                outcome = batch.outcome
                outcomes[outcome["query"]] = dict(outcome)
                counts = database.upsert_page(
                    batch.ads, skip_new_expired_at=datetime.now(UTC) if skip_expired else None)
                for action, count in counts.items():
                    result[action] += count
                result["invalid"] += batch.invalid
                result["queries"] = list(outcomes.values())
                logger.info("page_saved", extra={"run_id": run_id, "page": outcome["pages"], **counts})
                if progress:
                    progress(result)
        except SourceError as exc:
            outcomes["source_error"] = {"query": "source_error", "status": "failed", "pages": 0, "reason": str(exc)}
        result["queries"] = list(outcomes.values())
        values = result["queries"]
        status = "complete" if values and all(o["status"] == "complete" for o in values) else "partial"
        if not values or all(o["status"] == "failed" and not o["pages"] for o in values):
            status = "failed"
        result["status"] = status
        database.finish_run(run_id, status, result)
    except BaseException as exc:
        database.finish_run(run_id, "interrupted" if isinstance(exc, (KeyboardInterrupt, InterruptedError)) else "failed",
                            {**result, "error": "Collection interrupted"})
        raise
    return result


def collect_sources(database: Database, sources: list[JobSource], spec: Search, progress=None) -> list[dict]:
    return [collect(database, source, spec, progress) for source in sources]


def import_ad(database: Database, value) -> dict:
    from jobagent.sources import ManualSource
    return collect(database, ManualSource(value), Search(), skip_expired=False)
