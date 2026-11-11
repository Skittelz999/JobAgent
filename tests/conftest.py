import httpx
import pytest


@pytest.fixture(autouse=True)
def forbid_live_http(monkeypatch):
    """Every HTTP test must explicitly provide a mock transport."""
    monkeypatch.setenv("JOBAGENT_LLM_PROVIDER", "local")
    def blocked(*args, **kwargs):
        raise AssertionError("Live HTTP is forbidden in automated tests; use httpx.MockTransport")
    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", blocked)
