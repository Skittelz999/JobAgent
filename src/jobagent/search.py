"""Manual collection service; persists each page before requesting another."""
from datetime import UTC, datetime, timedelta
import logging
from typing import Any
from zoneinfo import ZoneInfo

from pydantic import ValidationError

from jobagent.database import Database
from jobagent.platsbanken import Platsbanken, SourceError, normalize
from jobagent.settings import Search

logger = logging.getLogger(__name__)


def collect(database: Database, source: Platsbanken, spec: Search) -> dict[str, Any]:
    now = datetime.now(UTC)
    timezone = source.settings.timezone
    def api_time(value: datetime) -> str:
        return value.astimezone(ZoneInfo(timezone)).strftime("%Y-%m-%dT%H:%M:%S")

    after, before = api_time(now - timedelta(days=spec.days)), api_time(now)
    parameters = {**spec.model_dump(), "published_after": after, "published_before": before,
                  "source_timezone": timezone, "source": "platsbanken"}
    run_id = database.start_run(parameters)
    result: dict[str, Any] = {"run_id": run_id, "inserted": 0, "updated": 0, "unchanged": 0,
                              "invalid": 0, "queries": []}
    try:
        for query in spec.queries:
            outcome: dict[str, Any] = {"query": query, "status": "complete", "pages": 0,
                                       "received": 0, "unique_seen": 0, "reported_total": None}
            seen: set[str] = set()
            result["queries"].append(outcome)
            offset = 0
            try:
                for _ in range(spec.max_pages):
                    if offset > 2000:
                        outcome.update(status="partial", reason="API offset limit reached; narrow query or date window")
                        break
                    page = source.search(query, offset, spec.page_size, after, before)
                    outcome["reported_total"] = max(outcome["reported_total"] or 0, page.total.value)
                    outcome["pages"] += 1
                    outcome["received"] += len(page.hits)
                    ads = []
                    new_ids = set()
                    for raw in page.hits:
                        try:
                            ad = normalize(raw, timezone)
                        except (ValueError, TypeError, ValidationError):
                            result["invalid"] += 1
                            outcome.update(status="partial", reason="One or more invalid ads skipped")
                            continue
                        new_ids.add(ad.job.external_id)
                        ads.append(ad)
                    counts = database.upsert_page(ads)
                    for action, count in counts.items():
                        result[action] += count
                    previous_count = len(seen)
                    seen.update(new_ids)
                    outcome["unique_seen"] = len(seen)
                    logger.info("page_saved", extra={"run_id": run_id, "page": outcome["pages"], **counts})
                    if page.total.relation not in (None, "eq"):
                        outcome.update(status="partial", reason="API reports a lower bound, not an exact total")
                    if len(seen) >= outcome["reported_total"]:
                        break
                    if not page.hits or len(page.hits) < spec.page_size:
                        outcome.update(status="partial", reason="Results ended before the reported total")
                        break
                    if len(seen) == previous_count:
                        outcome.update(status="partial", reason="Page contains no new valid IDs")
                        break
                    offset += spec.page_size
                else:
                    outcome.update(status="partial", reason="Configured page limit reached")
            except SourceError as exc:
                outcome.update(status="failed", reason=str(exc))
        statuses = [item["status"] for item in result["queries"]]
        status = "complete" if all(value == "complete" for value in statuses) else "partial"
        if all(value == "failed" for value in statuses) and not any(item["pages"] for item in result["queries"]):
            status = "failed"
        result["status"] = status
        database.finish_run(run_id, status, result)
    except KeyboardInterrupt:
        database.finish_run(run_id, "interrupted", result)
        raise
    except Exception:
        database.finish_run(run_id, "failed", {**result, "error": "Collection interrupted by local error"})
        raise
    return result
