"""Source-neutral batches and explicit, offline manual import."""
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Protocol
from urllib.parse import urlsplit, urlunsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator
from jobagent.models import JobAd, SourceAd
from jobagent.settings import Search


class SourceError(Exception):
    """Safe source failure without response bodies."""


@dataclass
class SourceBatch:
    ads: list[SourceAd] = field(default_factory=list)
    invalid: int = 0
    outcome: dict = field(default_factory=dict)


class JobSource(Protocol):
    name: str
    def batches(self, spec: Search) -> Iterator[SourceBatch]: ...


class ManualImport(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source: str = Field(min_length=1, max_length=100)
    title: str = Field(min_length=1, max_length=500)
    original_url: str = Field(min_length=1, max_length=4000)
    description: str = Field(min_length=1, max_length=500000)
    employer: str | None = None
    location: str | None = None

    @field_validator("source", "title", "original_url")
    @classmethod
    def trim(cls, value):
        if not value.strip():
            raise ValueError("Field cannot be blank")
        return value.strip()

    @field_validator("description")
    @classmethod
    def full_text(cls, value):
        if not value.strip():
            raise ValueError("Description cannot be blank")
        return value

    def record(self) -> SourceAd:
        JobAd.safe_url(self.original_url)
        url = urlsplit(self.original_url)
        canonical = urlunsplit((url.scheme.lower(), url.netloc.lower(), url.path or "/", url.query, ""))
        job = JobAd(source=self.source.casefold(), external_id="manual:" + canonical,
                    title=self.title, original_url=canonical, description=self.description,
                    employer=self.employer, location=self.location)
        return SourceAd(job=job, raw=self.model_dump())


class ManualSource:
    def __init__(self, value: ManualImport):
        self.ad = value.record()
        self.name = self.ad.job.source

    def batches(self, spec: Search) -> Iterator[SourceBatch]:
        yield SourceBatch([self.ad], outcome={"query": "manual import", "status": "complete",
                          "pages": 1, "received": 1, "unique_seen": 1, "reported_total": 1})


SOURCE_CATALOG = [
    {"id": "platsbanken", "label": "Platsbanken", "mode": "automatic"},
    {"id": "linkedin", "label": "LinkedIn", "mode": "manual"},
    {"id": "indeed", "label": "Indeed", "mode": "manual"},
    {"id": "other", "label": "Andra jobbsidor", "mode": "manual"},
]
