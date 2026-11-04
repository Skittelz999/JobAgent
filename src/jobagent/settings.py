"""Explicit configuration loading; no filesystem side effects at import time."""
import os
from pathlib import Path
import tomllib
from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, Field, field_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Storage(StrictModel):
    database: Path = Path("var/jobagent.sqlite3")


class Source(StrictModel):
    base_url: str = "https://jobsearch.api.jobtechdev.se"
    timeout_seconds: float = Field(default=30, gt=0, le=300)
    request_interval_seconds: float = Field(default=1, ge=0, le=60)
    max_attempts: int = Field(default=3, ge=1, le=5)
    timezone: str = "Europe/Stockholm"

    @field_validator("base_url")
    @classmethod
    def validate_url(cls, value: str) -> str:
        from urllib.parse import urlsplit
        parsed = urlsplit(value)
        if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError("source.base_url must be an HTTPS URL without credentials/query/fragment")
        return value.rstrip("/")

    @field_validator("timezone")
    @classmethod
    def validate_timezone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except ZoneInfoNotFoundError as exc:
            raise ValueError("Unknown source timezone") from exc
        return value


class Search(StrictModel):
    queries: list[str] = Field(default_factory=lambda: ["Java"], min_length=1)
    days: int = Field(default=30, ge=1, le=3650)
    page_size: int = Field(default=100, ge=1, le=100)
    max_pages: int = Field(default=21, ge=1, le=2001)

    @field_validator("queries")
    @classmethod
    def nonempty_queries(cls, values: list[str]) -> list[str]:
        if any(not value.strip() for value in values):
            raise ValueError("Search queries cannot be blank")
        return list(dict.fromkeys(value.strip() for value in values))


class Matching(StrictModel):
    minimum_score: int = Field(default=70, ge=0, le=100)


class LLMSettings(StrictModel):
    base_url: str = "http://127.0.0.1:1234/v1"
    model: str = Field(default="openai/gpt-oss-20b", min_length=1)
    timeout_seconds: float = Field(default=180, gt=0, le=600)
    max_tokens: int = Field(default=4000, ge=256, le=16000)
    max_input_chars: int = Field(default=20000, ge=1000, le=100000)

    @field_validator("base_url")
    @classmethod
    def local_endpoint(cls, value: str) -> str:
        from urllib.parse import urlsplit
        parsed = urlsplit(value)
        try:
            parsed.port
        except ValueError as exc:
            raise ValueError("Invalid LLM port") from exc
        if (parsed.scheme not in {"http", "https"}
                or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}
                or parsed.username or parsed.password or parsed.query or parsed.fragment):
            raise ValueError("LLM endpoint must be a local loopback HTTP(S) URL")
        base = value.rstrip("/")
        return base + "/v1" if not parsed.path.strip("/") else base


class Settings(StrictModel):
    storage: Storage = Field(default_factory=Storage)
    source: Source = Field(default_factory=Source)
    search: Search = Field(default_factory=Search)
    matching: Matching = Field(default_factory=Matching)
    llm: LLMSettings = Field(default_factory=LLMSettings)


class Profile(StrictModel):
    experience_years: float = Field(ge=0)
    experience_basis: Literal["part_time", "full_time"]
    university_credits: float = Field(ge=0)
    has_issued_degree: bool
    skills: list[str] = Field(min_length=1)
    roles: list[str] = Field(default_factory=list)
    experience_details: list[str] = Field(default_factory=list)


def load_settings(project_root: Path, config_path: Path | None = None) -> Settings:
    root = project_root.resolve()
    path = config_path or root / "config/settings.toml"
    if not path.is_absolute():
        path = root / path
    with path.open("rb") as stream:
        data = tomllib.load(stream)
    overrides = {
        "JOBAGENT_DATABASE": ("storage", "database"),
        "JOBAGENT_API_BASE_URL": ("source", "base_url"),
        "JOBAGENT_MINIMUM_SCORE": ("matching", "minimum_score"),
        "JOBAGENT_LLM_BASE_URL": ("llm", "base_url"),
        "JOBAGENT_LLM_MODEL": ("llm", "model"),
    }
    for name, (section, key) in overrides.items():
        if name in os.environ:
            data.setdefault(section, {})[key] = os.environ[name]
    settings = Settings.model_validate(data)
    if not settings.storage.database.is_absolute():
        settings.storage.database = root / settings.storage.database
    return settings


def load_profile(path: Path) -> Profile:
    with path.open("rb") as stream:
        return Profile.model_validate(tomllib.load(stream))
