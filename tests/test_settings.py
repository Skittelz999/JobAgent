from pathlib import Path

import pytest
from pydantic import ValidationError

from jobagent.settings import LLMSettings, Search, Source, load_settings

ROOT = Path(__file__).resolve().parents[1]


def test_defaults_resolve_to_project_not_current_directory(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    settings = load_settings(ROOT)
    assert settings.storage.database == ROOT / "var/jobagent.sqlite3"
    assert not (tmp_path / "var").exists()


def test_environment_override(monkeypatch):
    monkeypatch.setenv("JOBAGENT_DATABASE", "var/test.sqlite3")
    settings = load_settings(ROOT)
    assert settings.storage.database == ROOT / "var/test.sqlite3"


def test_llm_environment_override(monkeypatch):
    monkeypatch.setenv("JOBAGENT_LLM_BASE_URL", "http://127.0.0.1:1234")
    monkeypatch.setenv("JOBAGENT_LLM_MODEL", "local-model")
    settings = load_settings(ROOT)
    assert settings.llm.base_url == "http://127.0.0.1:1234/v1"
    assert settings.llm.model == "local-model"


@pytest.mark.parametrize("url", ["https://example.com/v1", "http://localhost:bad/v1", "http://user:pass@localhost/v1"])
def test_llm_rejects_nonlocal_or_invalid_endpoint(url):
    with pytest.raises(ValidationError):
        LLMSettings(base_url=url)


@pytest.mark.parametrize("kwargs", [{"page_size": 101}, {"queries": [" "]}, {"days": 0}])
def test_invalid_search_configuration(kwargs):
    with pytest.raises(ValidationError):
        Search(**kwargs)


def test_reject_credentials_in_source_url():
    with pytest.raises(ValidationError):
        Source(base_url="https://user:password@example.com")


@pytest.mark.parametrize("legacy_section", ["matching", "profile"])
@pytest.mark.parametrize("command", ["config-check", "search"])
def test_obsolete_settings_rejected_without_creating_database(tmp_path, capsys, legacy_section, command):
    from jobagent.cli import main
    root = tmp_path / "project"
    (root / "config").mkdir(parents=True)
    (root / "config/settings.toml").write_text(
        f"[{legacy_section}]\nminimum_score = 70\n", encoding="utf-8")
    assert main(["--root", str(root), command]) == 1
    error = capsys.readouterr().err
    assert legacy_section in error and "Extra inputs are not permitted" in error
    assert not (root / "var").exists()


def test_dotenv_loaded_without_overriding_existing_environment(tmp_path, monkeypatch):
    root = tmp_path / "project"
    (root / "config").mkdir(parents=True)
    (root / "config/settings.toml").write_text((ROOT / "config/settings.toml").read_text(encoding="utf-8"), encoding="utf-8")
    (root / ".env").write_text(
        "# Comment line\n"
        "JOBAGENT_LLM_PROVIDER=openai\n"
        "export JOBAGENT_OPENAI_MODEL='gpt-4.1-mini'\n"
        'OPENAI_API_KEY="dotenv-secret-key"\n'
        "JOBAGENT_DATABASE=var/from-dotenv.sqlite3 # inline comment\n",
        encoding="utf-8",
    )
    monkeypatch.delenv("JOBAGENT_LLM_PROVIDER", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setenv("JOBAGENT_OPENAI_MODEL", "env-wins-model")
    monkeypatch.delenv("JOBAGENT_DATABASE", raising=False)

    settings = load_settings(root)
    assert settings.llm.provider == "openai"
    assert settings.llm.openai_model == "env-wins-model"
    assert settings.storage.database == (root / "var/from-dotenv.sqlite3").resolve()
    import os
    assert os.environ["OPENAI_API_KEY"] == "dotenv-secret-key"
    assert "dotenv-secret-key" not in settings.model_dump_json()
