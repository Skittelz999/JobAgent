import httpx
import pytest

from jobagent.database import Database
from jobagent.platsbanken import Platsbanken, SourceError, normalize
from jobagent.search import collect
from jobagent.settings import Search, Source


def raw(ad_id="1", **changes):
    return {"id": ad_id, "headline": "Javautvecklare", "employer": {"name": "Företag"},
            "description": {"text": "Java och Spring Boot"},
            "publication_date": "2026-10-28T11:39:10", **changes}


def source(handler, attempts=3, sleeps=None):
    client = httpx.Client(transport=httpx.MockTransport(handler))
    return Platsbanken(Source(request_interval_seconds=0, max_attempts=attempts), client,
                       sleep=(sleeps.append if sleeps is not None else lambda _: None))


@pytest.fixture
def db(tmp_path):
    database = Database(tmp_path / "jobs.db")
    database.initialize()
    return database


def test_pagination_duplicate_queries_and_fixed_window(db):
    requests = []
    def handler(request):
        requests.append(request)
        offset = int(request.url.params["offset"])
        return httpx.Response(200, json={"total": {"value": 3},
                                         "hits": [raw(str(i)) for i in range(offset, min(offset + 2, 3))]})
    api = source(handler)
    result = collect(db, api, Search(queries=["Java", "Spring"], page_size=2))
    assert result["status"] == "complete"
    assert result["inserted"] == 3
    assert result["unchanged"] == 3
    assert len(db.list_jobs()) == 3
    assert [r.url.params["offset"] for r in requests] == ["0", "2", "0", "2"]
    assert len({r.url.params["published-before"] for r in requests}) == 1


def test_page_limit_reports_partial(db):
    api = source(lambda _: httpx.Response(200, json={"total": {"value": 10}, "hits": [raw()]}))
    result = collect(db, api, Search(page_size=1, max_pages=1))
    assert result["status"] == "partial"
    assert "page limit" in result["queries"][0]["reason"]
    assert db.history()[0]["status"] == "partial"


def test_offset_limit_is_never_exceeded(db):
    offsets = []
    def handler(request):
        offset = int(request.url.params["offset"])
        offsets.append(offset)
        return httpx.Response(200, json={"total": {"value": 9999},
                                         "hits": [raw(str(i)) for i in range(offset, offset + 100)]})
    result = collect(db, source(handler), Search(max_pages=30))
    assert result["status"] == "partial"
    assert max(offsets) == 2000


def test_one_source_failure_does_not_lose_saved_pages(db):
    def handler(request):
        if request.url.params["offset"] == "0":
            return httpx.Response(200, json={"total": {"value": 2}, "hits": [raw()]})
        return httpx.Response(503)
    result = collect(db, source(handler, attempts=1), Search(page_size=1))
    assert result["status"] == "partial"
    assert len(db.list_jobs()) == 1


def test_invalid_ad_skipped_and_search_marked_partial(db):
    api = source(lambda _: httpx.Response(200, json={"total": {"value": 2},
                                                    "hits": [raw(), {"id": "bad"}]}))
    result = collect(db, api, Search())
    assert result["invalid"] == 1
    assert result["status"] == "partial"
    assert len(db.list_jobs()) == 1


def test_rate_limit_retry_after():
    sleeps = []
    calls = []
    def handler(request):
        calls.append(request)
        if len(calls) == 1:
            return httpx.Response(429, headers={"Retry-After": "2"})
        return httpx.Response(200, json={"total": {"value": 0}, "hits": []})
    assert source(handler, sleeps=sleeps).search("Java", 0, 100, "a", "b").hits == []
    assert sleeps == [2]


def test_long_retry_after_stops_instead_of_retrying_early():
    api = source(lambda _: httpx.Response(429, headers={"Retry-After": "120"}))
    with pytest.raises(SourceError, match="longer pause"):
        api.search("Java", 0, 100, "a", "b")


def test_timeout_retries_bounded(db):
    calls = []
    def handler(request):
        calls.append(request)
        raise httpx.ReadTimeout("timeout")
    result = collect(db, source(handler), Search())
    assert len(calls) == 3
    assert result["status"] == "failed"


def test_bad_response_shape_is_not_an_empty_success(db):
    result = collect(db, source(lambda _: httpx.Response(200, json={})), Search())
    assert result["status"] == "failed"


def test_empty_search_is_successful(db):
    result = collect(db, source(lambda _: httpx.Response(200, json={"total": {"value": 0}, "hits": []})), Search())
    assert result["status"] == "complete"


def test_repeated_pages_cannot_claim_complete(db):
    result = collect(db, source(lambda _: httpx.Response(200, json={"total": {"value": 3}, "hits": [raw()]})),
                     Search(page_size=1))
    assert result["status"] == "partial"
    assert result["queries"][0]["pages"] == 2


def test_null_fields_and_timezone_conversion():
    job = normalize(raw(employer=None, workplace_address=None, application_details=None), "Europe/Stockholm").job
    assert job.employer is None
    assert job.work_arrangement == "unknown"
    assert job.published_at.isoformat() == "2026-10-28T10:39:10+00:00"
    summer = normalize(raw(publication_date="2026-07-01T12:00:00"), "Europe/Stockholm").job
    assert summer.published_at.hour == 10


def test_unsafe_url_rejected():
    with pytest.raises(ValueError):
        normalize(raw(webpage_url="javascript:alert(1)"), "Europe/Stockholm")
