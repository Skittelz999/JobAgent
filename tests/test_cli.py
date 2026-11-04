import json
from pathlib import Path

from jobagent.cli import main
from jobagent.platsbanken import Page

ROOT = Path(__file__).resolve().parents[1]


def test_config_check_has_no_storage_side_effect(tmp_path, monkeypatch, capsys):
    path = tmp_path / "not-created" / "jobs.db"
    monkeypatch.setenv("JOBAGENT_DATABASE", str(path))
    assert main(["--root", str(ROOT), "config-check", "--profile", "config/profile.example.toml"]) == 0
    assert json.loads(capsys.readouterr().out)["profile_valid"] is True
    assert not path.parent.exists()


def test_cli_search_list_show_history_without_lm_studio(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("JOBAGENT_DATABASE", str(tmp_path / "jobs.db"))
    def fake_search(self, *args):
        return Page.model_validate({"total": {"value": 1}, "hits": [
            {"id": "1", "headline": "Junior utvecklare", "description": {"text": "Java"}}]})
    monkeypatch.setattr("jobagent.platsbanken.Platsbanken.search", fake_search)
    prefix = ["--root", str(ROOT)]
    assert main([*prefix, "search", "--query", "Java"]) == 0
    assert json.loads(capsys.readouterr().out)["inserted"] == 1
    assert main([*prefix, "search", "--query", "Java"]) == 0
    assert json.loads(capsys.readouterr().out)["unchanged"] == 1
    assert main([*prefix, "list"]) == 0
    job_id = json.loads(capsys.readouterr().out)[0]["id"]
    assert main([*prefix, "show", str(job_id)]) == 0
    assert json.loads(capsys.readouterr().out)["job"]["description"] == "Java"
    assert main([*prefix, "history"]) == 0
    assert len(json.loads(capsys.readouterr().out)) == 2


def test_invalid_cli_override_does_not_create_db(tmp_path, monkeypatch, capsys):
    path = tmp_path / "jobs.db"
    monkeypatch.setenv("JOBAGENT_DATABASE", str(path))
    assert main(["--root", str(ROOT), "search", "--page-size", "101"]) == 1
    assert "Validation error" in capsys.readouterr().err
    assert not path.exists()


def test_partial_search_exit_code(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("JOBAGENT_DATABASE", str(tmp_path / "jobs.db"))
    monkeypatch.setattr("jobagent.platsbanken.Platsbanken.search", lambda *args:
                        Page.model_validate({"total": {"value": 10}, "hits": [{"id": "1", "headline": "Java"}]}))
    assert main(["--root", str(ROOT), "search", "--query", "Java", "--max-pages", "1", "--page-size", "1"]) == 2
    assert json.loads(capsys.readouterr().out)["status"] == "partial"


def test_analyze_is_read_only_and_never_initializes_database(tmp_path, monkeypatch, capsys):
    from jobagent.database import Database
    from jobagent.models import JobAd, SourceAd
    from test_analysis import DESCRIPTION, output

    path = tmp_path / "jobs.db"
    db = Database(path)
    db.initialize()
    db.upsert_page([SourceAd(job=JobAd(source="test", external_id="1", title="Job", description=DESCRIPTION), raw={})])
    before = path.read_bytes()
    monkeypatch.setenv("JOBAGENT_DATABASE", str(path))
    monkeypatch.setattr("jobagent.llm.LocalLLM.generate_json", lambda *args:
                        {"model": "mock", "content": json.dumps(output())})
    monkeypatch.setattr(Database, "initialize", lambda *args: (_ for _ in ()).throw(AssertionError("Must not initialize")))
    assert main(["--root", str(ROOT), "analyze", "1"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["validation"]["human_review_required"] is True
    assert result["ad_version"] == 1
    assert path.read_bytes() == before
    assert db.history() == []


def test_analyze_missing_database_does_not_create_file(tmp_path, monkeypatch, capsys):
    path = tmp_path / "absent.db"
    monkeypatch.setenv("JOBAGENT_DATABASE", str(path))
    assert main(["--root", str(ROOT), "analyze", "188"]) == 1
    assert not path.exists()


def test_search_never_calls_llm(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("JOBAGENT_DATABASE", str(tmp_path / "jobs.db"))
    def fail(*args):
        raise AssertionError("Collection must not call AI")
    monkeypatch.setattr("jobagent.llm.LocalLLM.generate_json", fail)
    monkeypatch.setattr("jobagent.platsbanken.Platsbanken.search", lambda *args:
                        Page.model_validate({"total": {"value": 0}, "hits": []}))
    assert main(["--root", str(ROOT), "search", "--query", "Java"]) == 0
