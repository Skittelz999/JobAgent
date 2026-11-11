"""Model contract and source checks for experience and other durations."""
import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from jobagent.llm import LLMError
from jobagent.passages import Passage


EXPERIENCE_WORD = r"\b(?:\w*erfarenhet(?:en|er)?|experience)\b"
EXPERIENCE = re.compile(EXPERIENCE_WORD, re.IGNORECASE)
EXPLICIT_EXPERIENCE = re.compile(
    rf"\b(?:flerårig\w*|(?:minst|at least|(?:a )?minimum of)\s+\w+(?:[.,]\d+)?\s+(?:års?|years?)"
    rf"|several years)\s+(?:[\w-]+\s+){{0,4}}{EXPERIENCE_WORD}",
    re.IGNORECASE,
)
PREFERRED_EXPERIENCE = re.compile(r"\b(?:meriterande|preferred|a plus|nice to have|desirable)\b", re.IGNORECASE)
REQUIRED = re.compile(r"\b(?:krävs|obligatorisk\w*|must have|(?<!not )required)\b", re.IGNORECASE)
EDUCATION_ALTERNATIVE = re.compile(
    rf"\b(?:education|degree|utbildning|examen)\b[^.!?;]*?\b(?:or|eller|alternativt)\s+"
    rf"(?:equivalent|motsvarande|likvärdig)\s+(?:[\w-]+\s+){{0,2}}{EXPERIENCE_WORD}",
    re.IGNORECASE,
)


class ExperienceItem(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    kind: Literal["professional_experience", "education_duration", "project_duration", "other_duration", "unclear"]
    minimum_years: float | None = Field(ge=0, allow_inf_nan=False)
    maximum_years: float | None = Field(ge=0, allow_inf_nan=False)
    importance: Literal["mandatory", "expected", "preferred", "unclear"]
    domain: str = Field(min_length=1, max_length=300)
    alternatives_accepted: bool
    conditional: bool
    ambiguous: bool
    source_ids: list[str] = Field(min_length=1, max_length=12)
    context_ids: list[str] = Field(max_length=12)

    @model_validator(mode="after")
    def ordered_years(self):
        if (self.minimum_years is not None and self.maximum_years is not None
                and self.minimum_years > self.maximum_years):
            raise ValueError("Minimum years cannot exceed maximum years")
        return self


class ExperienceAssessment(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    presence: Literal["present", "absent", "unclear"]
    coverage_complete: bool
    items: list[ExperienceItem] = Field(max_length=60)

    @model_validator(mode="after")
    def consistent_presence(self):
        professional = any(item.kind == "professional_experience" for item in self.items)
        if (self.presence == "absent" and professional) or (self.presence == "present" and not professional):
            raise ValueError("Experience presence contradicts the extracted items")
        return self


def _source(passage: Passage) -> dict:
    return {"passage_id": passage.id, "quote": passage.text, "start": passage.start, "end": passage.end}


def verify_experience(assessment: ExperienceAssessment, passages: list[Passage]) -> list[dict]:
    """Construct quotes from the original text, never from model-generated quotes."""
    by_id = {p.id: p for p in passages}
    evidence = []
    for index, item in enumerate(assessment.items):
        for refs in (item.source_ids, item.context_ids):
            if len(set(refs)) != len(refs) or any(ref not in by_id for ref in refs):
                raise LLMError("Invalid or duplicate experience source reference. Analysis rejected.")
        evidence.append({"item_index": index, "source_passages": [_source(by_id[ref]) for ref in item.source_ids],
                         "scope_context": [_source(by_id[ref]) for ref in item.context_ids]})
    return evidence


def missing_experience_sources(assessment: ExperienceAssessment, passages: list[Passage],
                               requirements: list[dict]) -> list[dict]:
    """Flag possible omissions using exact source text and the general qualifications."""
    covered = {ref for item in assessment.items if item.kind in {"professional_experience", "unclear"}
               for ref in item.source_ids}
    known_ids = {passage.id for passage in passages}
    qualifications, preferred = set(), set()
    for row in requirements:
        if row["kind"] != "qualification":
            continue
        ids = {source["passage_id"] for source in row["source_passages"]}
        if not ids or not ids <= known_ids:
            raise LLMError("Invalid qualification source reference. Analysis rejected.")
        (preferred if row["importance"] == "preferred" else qualifications).update(ids)
        # A consolidated row can cite repetitions of the same qualification.
        if ids & covered:
            covered.update(ids)

    gaps = []
    preferred_only = preferred - qualifications
    for passage in passages:
        if passage.id in covered:
            continue
        text = passage.text
        explicit = EXPLICIT_EXPERIENCE.search(text)
        is_preferred = passage.id in preferred_only or PREFERRED_EXPERIENCE.search(text)
        if is_preferred and not (explicit and REQUIRED.search(text)):
            continue
        if explicit or (
            passage.id in qualifications and EXPERIENCE.search(EDUCATION_ALTERNATIVE.sub(" ", text))
        ):
            gaps.append(_source(passage))
    return gaps
