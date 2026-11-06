import json
from pathlib import Path

import httpx
import pytest
from pydantic import ValidationError

from jobagent.cli import main
from jobagent.database import Database
from jobagent.llm import LLMError, LocalLLM
from jobagent.matching import GROUPED_PROMPT_VERSION, GroupedMatchResult, match_job
from jobagent.models import JobAd, SourceAd
from jobagent.settings import LLMSettings, load_yaml_profile

ROOT = Path(__file__).resolve().parents[1]
GROUPS = ("required", "preferred", "practical")


def profile():
    return load_yaml_profile(ROOT / "config/profile.yaml")


def job():
    return JobAd(source="test", external_id="1", title="Job", description="Java. Meriter: Kafka. Ort: X.")


def empty_groups():
    return {key: [] for key in GROUPS}


@pytest.mark.parametrize("status", ["TRUE", "PARTIAL", "FALSE", "UNKNOWN"])
@pytest.mark.parametrize("group", GROUPS)
def test_group_and_status_preserved_without_semantic_rewriting(status, group):
    raw = empty_groups()
    raw[group] = [{"requirement": "Kubernetes", "status": status, "reason": "Modellens motivering."}]
    calls = []

    class Provider:
        settings = LLMSettings()

        def generate_json(self, messages, schema):
            calls.append(messages)
            assert set(schema["required"]) == set(GROUPS)
            assert set(schema["properties"]) == set(GROUPS)
            return {"model": "mock", "content": json.dumps(raw)}

    result = match_job(job(), profile(), Provider(), grouped=True)
    assert len(calls) == 1
    assert {key: result[key] for key in GROUPS} == raw
    assert result["prompt_version"] == GROUPED_PROMPT_VERSION
    assert "requirements" not in result
    assert result["validation"]["semantic_correctness_verified"] is False


@pytest.mark.parametrize("raw", [
    "not JSON private-secret", "{}", '{"required": [], "preferred": []}',
    json.dumps({**empty_groups(), "extra": []}),
    json.dumps({**empty_groups(), "practical": {}}),
    json.dumps({**empty_groups(), "required": [{"requirement": "Java", "status": "supported", "reason": "X"}]}),
    json.dumps({**empty_groups(), "required": [{"requirement": "Java", "status": True, "reason": "X"}]}),
    json.dumps({**empty_groups(), "required": [{"requirement": "Java", "status": "TRUE", "reason": "X", "type": "required"}]}),
])
def test_invalid_grouped_response_is_rejected_once_without_saving(tmp_path, monkeypatch, capsys, raw):
    db = Database(tmp_path / "jobs.db")
    db.initialize()
    db.upsert_page([SourceAd(job=job(), raw={})])
    monkeypatch.setenv("JOBAGENT_DATABASE", str(db.path))
    calls = []

    def generate(*args):
        calls.append(args)
        return {"model": "mock", "content": raw}

    monkeypatch.setattr("jobagent.llm.LocalLLM.generate_json", generate)
    assert main(["--root", str(ROOT), "match", "1", "--grouped"]) == 1
    output = capsys.readouterr()
    assert not output.out and "JSON schema validation" in output.err
    assert "private-secret" not in output.err
    assert len(calls) == 1
    with db.connect(read_only=True) as c:
        assert c.execute("SELECT count(*) FROM match_results").fetchone()[0] == 0


def test_grouped_cli_storage_and_readback_preserve_duplicates_and_wrong_groups(tmp_path, monkeypatch, capsys):
    db = Database(tmp_path / "jobs.db")
    db.initialize()
    db.upsert_page([SourceAd(job=job(), raw={})])
    monkeypatch.setenv("JOBAGENT_DATABASE", str(db.path))
    row = {"requirement": "Kafka", "status": "FALSE", "reason": "Oförändrad, även om felaktig."}
    raw = {"required": [row], "preferred": [row], "practical": []}
    calls = []

    def generate(*args):
        calls.append(args)
        return {"model": "mock", "content": json.dumps(raw)}

    monkeypatch.setattr("jobagent.llm.LocalLLM.generate_json", generate)
    assert main(["--root", str(ROOT), "match", "1", "--grouped"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert {key: result[key] for key in GROUPS} == raw
    assert main(["--root", str(ROOT), "match-show", str(result["result_id"])]) == 0
    assert json.loads(capsys.readouterr().out) == result
    assert len(calls) == 1


def test_both_formats_send_identical_user_data_with_one_request_each():
    requests = []

    def handler(request):
        payload = json.loads(request.content)
        requests.append(payload)
        properties = payload["response_format"]["json_schema"]["schema"]["properties"]
        raw = empty_groups() if "required" in properties else {"requirements": []}
        return httpx.Response(200, json={"model": "mock", "choices": [
            {"finish_reason": "stop", "message": {"content": json.dumps(raw)}}]})

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        provider = LocalLLM(LLMSettings(), client)
        plain = match_job(job(), profile(), provider)
        grouped = match_job(job(), profile(), provider, grouped=True)
    assert len(requests) == 2
    assert requests[0]["messages"][1] == requests[1]["messages"][1]
    assert requests[1]["messages"][0]["content"].startswith(requests[0]["messages"][0]["content"])
    assert plain["profile_hash"] == grouped["profile_hash"]


def test_empty_groups_are_valid_without_inventing_rows():
    assert GroupedMatchResult.model_validate(empty_groups()).model_dump() == empty_groups()
    with pytest.raises(ValidationError):
        GroupedMatchResult.model_validate({"requirements": []})


def test_grouped_oversized_input_rejected_before_call():
    class Provider:
        settings = LLMSettings(max_input_chars=1000)

        def generate_json(self, *args):
            pytest.fail("No oversized request")

    with pytest.raises(LLMError, match="max_input_chars"):
        match_job(job(), profile(), Provider(), grouped=True)
