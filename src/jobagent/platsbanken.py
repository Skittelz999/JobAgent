"""Read-only JobTech adapter. Does not use an LLM or browser automation."""
from datetime import UTC, datetime, timedelta
from email.utils import parsedate_to_datetime
import random
import time
from typing import Any, Callable
from zoneinfo import ZoneInfo

import httpx
from pydantic import BaseModel, ConfigDict, Field, StrictInt, ValidationError

from jobagent.models import JobAd, SourceAd
from jobagent.settings import Source, Search
from jobagent.sources import SourceError, SourceBatch


class Total(BaseModel):
    value: StrictInt = Field(ge=0)
    relation: str | None = None


class Page(BaseModel):
    model_config = ConfigDict(extra="ignore")
    total: Total
    hits: list[dict[str, Any]]


def api_datetime(value: Any, timezone: str) -> datetime | None:
    if value is None or value == "":
        return None
    if not isinstance(value, str):
        raise ValueError("Expected ISO datetime string")
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=ZoneInfo(timezone))
    return parsed.astimezone(UTC)


def normalize(raw: dict[str, Any], timezone: str) -> SourceAd:
    external_id = raw.get("id")
    title = raw.get("headline")
    if not isinstance(external_id, (str, int)) or isinstance(external_id, bool) or not str(external_id).strip():
        raise ValueError("Missing source ad ID")
    if not isinstance(title, str) or not title.strip():
        raise ValueError("Missing ad headline")
    employer = raw.get("employer") or {}
    address = raw.get("workplace_address") or {}
    description = raw.get("description") or {}
    application = raw.get("application_details") or {}
    if not all(isinstance(value, dict) for value in (employer, address, description, application)):
        raise ValueError("Invalid nested ad fields")
    # Absence of a work-arrangement field is not evidence of on-site work.
    # The API remote search flag is phrase-based, so do not infer from it.
    job = JobAd(
        source="platsbanken", external_id=str(external_id), title=title.strip(),
        employer=employer.get("name"), location=address.get("municipality") or address.get("city"),
        description=description.get("text") or "",
        original_url=raw.get("webpage_url"), application_url=application.get("url"),
        published_at=api_datetime(raw.get("publication_date"), timezone),
        deadline=api_datetime(raw.get("application_deadline"), timezone),
        removed=raw.get("removed", False),
        source_requirements={key: raw.get(key) for key in (
            "must_have", "nice_to_have", "experience_required", "occupation", "occupation_group",
            "workplace_model", "workplace_addresses", "employment_type", "working_hours_type")},
    )
    return SourceAd(job=job, raw=raw)


class Platsbanken:
    name = "platsbanken"

    def batches(self, spec: Search):
        now = datetime.now(UTC)
        zone = ZoneInfo(self.settings.timezone)
        after = (now - timedelta(days=spec.days)).astimezone(zone).strftime("%Y-%m-%dT%H:%M:%S")
        before = now.astimezone(zone).strftime("%Y-%m-%dT%H:%M:%S")
        for query in spec.queries:
            outcome = {"query": query, "status": "complete", "pages": 0, "received": 0,
                       "unique_seen": 0, "reported_total": None}
            seen = set()
            try:
                for page_number in range(spec.max_pages):
                    offset = page_number * spec.page_size
                    if offset > 2000:
                        outcome.update(status="partial", reason="API offset limit reached; narrow query or date window")
                        break
                    page = self.search(query, offset, spec.page_size, after, before)
                    outcome["reported_total"] = max(outcome["reported_total"] or 0, page.total.value)
                    outcome["pages"] += 1
                    outcome["received"] += len(page.hits)
                    ads, invalid = [], 0
                    previous_count = len(seen)
                    for raw in page.hits:
                        try:
                            ad = normalize(raw, self.settings.timezone)
                        except (ValueError, TypeError):
                            invalid += 1
                            outcome.update(status="partial", reason="One or more invalid ads skipped")
                            continue
                        ads.append(ad)
                        seen.add(ad.job.external_id)
                    outcome["unique_seen"] = len(seen)
                    if page.total.relation not in (None, "eq"):
                        outcome.update(status="partial", reason="API reports a lower bound, not an exact total")
                    yield SourceBatch(ads, invalid, dict(outcome))
                    if len(seen) >= outcome["reported_total"]:
                        break
                    if len(page.hits) < spec.page_size:
                        outcome.update(status="partial", reason="Results ended before the reported total")
                        break
                    if len(seen) == previous_count:
                        outcome.update(status="partial", reason="Page contains no new valid IDs")
                        break
                else:
                    outcome.update(status="partial", reason="Configured page limit reached")
            except SourceError as exc:
                outcome.update(status="failed", reason=str(exc))
            yield SourceBatch(outcome=dict(outcome))

    def __init__(self, settings: Source, client: httpx.Client | None = None,
                 sleep: Callable[[float], None] = time.sleep):
        self.settings = settings
        self.client = client or httpx.Client(timeout=settings.timeout_seconds, follow_redirects=False,
                                             headers={"User-Agent": "LocalJobAgent/0.1", "Accept": "application/json"})
        self._owns_client = client is None
        self._sleep = sleep
        self._last_request: float | None = None

    def __enter__(self) -> "Platsbanken":
        return self

    def __exit__(self, *args: object) -> None:
        if self._owns_client:
            self.client.close()

    def _retry_delay(self, response: httpx.Response | None, attempt: int) -> float:
        if response is not None and (value := response.headers.get("Retry-After")):
            try:
                delay = float(value)
            except ValueError:
                try:
                    delay = (parsedate_to_datetime(value) - datetime.now(UTC)).total_seconds()
                except (ValueError, TypeError, OverflowError):
                    delay = 0
            if delay > 60:
                # Stop rather than retrying earlier than the provider allows.
                raise SourceError("Provider requests a longer pause; try again later")
            if delay > 0:
                return delay
        return min(2 ** attempt + random.uniform(0, 0.25), 30)

    def search(self, query: str, offset: int, limit: int,
               published_after: str, published_before: str) -> Page:
        params = {"q": query, "offset": offset, "limit": limit, "sort": "pubdate-desc",
                  "published-after": published_after, "published-before": published_before}
        for attempt in range(self.settings.max_attempts):
            if self._last_request is not None:
                remaining = self.settings.request_interval_seconds - (time.monotonic() - self._last_request)
                if remaining > 0:
                    self._sleep(remaining)
            self._last_request = time.monotonic()
            response = None
            try:
                response = self.client.get(self.settings.base_url + "/search", params=params)
            except httpx.TransportError:
                if attempt + 1 == self.settings.max_attempts:
                    raise SourceError("JobTech connection failed after bounded retries") from None
            else:
                if response.status_code == 200:
                    try:
                        return Page.model_validate(response.json())
                    except (ValueError, ValidationError):
                        raise SourceError("JobTech returned an invalid search response") from None
                if response.status_code != 429 and response.status_code < 500:
                    raise SourceError(f"JobTech rejected request (HTTP {response.status_code})")
                if attempt + 1 == self.settings.max_attempts:
                    raise SourceError(f"JobTech unavailable after bounded retries (HTTP {response.status_code})")
            self._sleep(self._retry_delay(response, attempt))
        raise SourceError("JobTech request failed")
