"""Source-neutral records, independent of storage and AI."""
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class JobAd(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source: str = Field(min_length=1)
    external_id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    employer: str | None = None
    location: str | None = None
    description: str = ""
    original_url: str | None = None
    application_url: str | None = None
    published_at: datetime | None = None
    deadline: datetime | None = None
    removed: bool = False
    work_arrangement: Literal["unknown", "remote", "hybrid", "onsite"] = "unknown"
    source_requirements: dict[str, Any] = Field(default_factory=dict)

    @field_validator("published_at", "deadline")
    @classmethod
    def require_timezone(cls, value: datetime | None) -> datetime | None:
        if value is not None and value.tzinfo is None:
            raise ValueError("Normalized timestamps must include a timezone")
        return value

    @field_validator("original_url", "application_url")
    @classmethod
    def safe_url(cls, value: str | None) -> str | None:
        from urllib.parse import urlsplit
        if value is None:
            return None
        parsed = urlsplit(value)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
            raise ValueError("Ad URL must use HTTP(S), without credentials")
        return value


class SourceAd(BaseModel):
    job: JobAd
    raw: dict[str, Any]
