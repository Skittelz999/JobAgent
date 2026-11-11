import json

import httpx
import pytest
from fastapi.testclient import TestClient

from jobagent.analysis import extraction_schema
from jobagent.database import Database
from jobagent.llm import OpenAILLM, LocalLLM, LLMError, create_provider, openai_schema
from jobagent.passages import number_passages
from jobagent.processing import analyze_pending, pending_jobs
from jobagent.search import import_ad
from jobagent.settings import LLMSettings, Settings, Storage, load_settings
from jobagent.web import create_app
from test_three_phases import imported
from test_experience import model_output


def response(data=None, **changes):
    return {"status": "completed", "model": "gpt-4.1-mini-2025-04-14", "output": [
        {"type": "reasoning", "summary": []},
        {"type": "message", "status": "completed", "content": [
            {"type": "output_text", "text": json.dumps(data or model_output())}]}], **changes}


def test_openai_request_auth_structured_output_and_snapshot_cache(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-key-not-real")
    calls = []
    def handler(request):
        calls.append(request)
        assert str(request.url) == "https://api.openai.com/v1/responses"
        assert request.headers["Authorization"] == "Bearer test-key-not-real"
        payload = json.loads(request.content)
        assert payload["model"] == "gpt-4.1-mini"
        assert payload["store"] is False
        assert payload["text"]["format"]["strict"] is True
        assert "tools" not in payload and "temperature" not in payload
        assert "At least 4 years" in payload["input"][1]["content"]
        return httpx.Response(200, json=response())
    settings = Settings(storage=Storage(database=tmp_path / "jobs.db"), llm=LLMSettings(provider="openai"))
    db = Database(settings.storage.database)
    db.initialize()
    import_ad(db, imported())
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        provider = OpenAILLM(settings.llm, client)
        result = analyze_pending(db, provider, settings.filter)
        assert result["succeeded"] == 1
        assert result["results"][0]["provider"] == "openai"
        assert result["results"][0]["requested_model"] == "gpt-4.1-mini"
        assert result["results"][0]["model"] == "gpt-4.1-mini-2025-04-14"
        assert analyze_pending(db, provider, settings.filter)["results"] == []
        assert len(calls) == 1  # Returned snapshot name must not trigger endless reanalysis.
        assert pending_jobs(db, "gpt-4.1-mini", "local")
    assert "test-key-not-real" not in settings.model_dump_json()
    with db.connect(read_only=True) as conn:
        assert "test-key-not-real" not in str([tuple(r) for r in conn.execute("SELECT * FROM analysis_results")])
    app = create_app(settings, start_worker=False)
    with TestClient(app, base_url="http://localhost") as client:
        page = client.get("/")
        assert page.context["jobs"][0]["state"] == "success"
        assert "OpenAI API" in page.text and "skickas annonstexten till OpenAI" in page.text


def test_missing_key_fails_without_network_or_fallback(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    provider = create_provider(LLMSettings(provider="openai"))
    assert isinstance(provider, OpenAILLM)
    assert isinstance(create_provider(LLMSettings()), LocalLLM)
    with pytest.raises(LLMError, match="OPENAI_API_KEY is missing"):
        provider.generate_json([], {})


@pytest.mark.parametrize("status", [301, 400, 401, 403, 429, 500])
def test_api_errors_never_expose_server_body_or_secret(status, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-secret")
    calls = []
    def handler(request):
        calls.append(request)
        return httpx.Response(status, text="private ad and test-secret", headers={"Location": "https://example.com/steal"})
    provider = OpenAILLM(LLMSettings(provider="openai"), httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=True))
    with pytest.raises(LLMError) as exc:
        provider.generate_json([], {})
    assert str(status) in str(exc.value)
    assert "test-secret" not in str(exc.value) and "private ad" not in str(exc.value)
    assert len(calls) == 1


@pytest.mark.parametrize("payload", [
    {}, response(status="incomplete"), response(output=[]),
    response(output=[{"type": "message", "content": [{"type": "refusal", "refusal": "private reason"}]}]),
    response(output=[{"type": "message", "status": "incomplete", "content": []}]),
    response(output=[{"type": "message", "content": [{"type": "output_text", "text": 5}]}]),
])
def test_openai_rejects_incomplete_refusal_and_malformed_responses(payload, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test")
    provider = OpenAILLM(LLMSettings(provider="openai"), httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(200, json=payload))))
    with pytest.raises(LLMError):
        provider.generate_json([], {})


def test_schema_shares_source_enum_without_mutating_original():
    original = extraction_schema(number_passages("\n".join(f"Line {i}" for i in range(200))), include_experience=True)
    adapted = openai_schema(original)
    assert len(adapted["$defs"]["SourcePassageId"]["enum"]) == 200
    assert original["$defs"]["Selection"]["properties"]["passage_id"]["enum"][0] == "p0001"
    assert adapted["$defs"]["Fact"]["properties"]["source_ids"]["items"] == {"$ref": "#/$defs/SourcePassageId"}


def test_provider_env_selection_does_not_put_key_in_config(monkeypatch):
    from pathlib import Path
    monkeypatch.setenv("JOBAGENT_LLM_PROVIDER", "openai")
    monkeypatch.setenv("JOBAGENT_OPENAI_MODEL", "gpt-4.1-mini")
    monkeypatch.setenv("OPENAI_API_KEY", "test-secret")
    settings = load_settings(Path(__file__).resolve().parents[1])
    assert settings.llm.active_model == "gpt-4.1-mini" and settings.llm.provider == "openai"
    assert "test-secret" not in settings.model_dump_json()
