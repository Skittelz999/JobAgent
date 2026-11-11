"""Structured Swedish overview with mechanically verified source evidence."""
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field
from jobagent.llm import LLMError


class Fact(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    text_sv: str = Field(min_length=1, max_length=2000)
    source_ids: list[str] = Field(min_length=1, max_length=30)


class Technology(Fact):
    category: Literal["language", "framework", "database", "cloud", "tool", "other"]
    usage: Literal["candidate_requirement", "mentioned"]
    importance: Literal["mandatory", "expected", "preferred", "unclear", "not_applicable"]


class Overview(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    summary: list[Fact] = Field(min_length=1, max_length=8)
    techstack: list[Technology] = Field(max_length=60)
    education: list[Fact] = Field(max_length=20)
    languages: list[Fact] = Field(max_length=20)
    location: list[Fact] = Field(max_length=20)
    work_arrangement: list[Fact] = Field(max_length=20)


OVERVIEW_PROMPT = """
Also return overview. Write concise Swedish text_sv, even for English ads.
summary: 2-5 factual bullets covering the role, responsibilities and important terms;
use one bullet if the ad is very short. Every bullet needs supporting source_ids.
techstack: explicitly named programming languages, frameworks, databases, clouds and
other tools. text_sv is the technology name, not a paragraph. Distinguish a candidate
requirement from a technology merely mentioned in the employer's environment.
Only candidate_requirement has mandatory/expected/preferred/unclear importance;
mentioned must use not_applicable. Preserve the original requirement strength.
education, languages, location, work_arrangement: only explicitly stated facts,
in Swedish with source_ids. Empty lists mean not stated/unknown; do not infer.
All source_ids must exist; do not invent technologies, remote terms or facts.
Before returning, check EVERY generated sentence against the text at its chosen IDs.
Do not cite a nearby heading, a benefit, or a different requirement just because it
is in the same ad. A claim with multiple clauses needs sources for EVERY clause.
Keep each summary bullet short and specific. Do not add seniority from experience
alone. Do not call a preference a requirement in the summary.
Techstack is NOT a list of mandatory skills by default. A sentence under job duties
such as "we develop using C++, Java and LabVIEW" only supports mentioned technology.
A candidate qualification elsewhere is needed to mark candidate_requirement; cite
THAT qualification and its governing heading, not merely the responsibility.
"Cloud experience, for example AWS, Azure or GCP" does NOT require each provider.
List the example providers as mentioned/not_applicable, with the complete cloud
qualification preserved in requirements. Do not erase alternatives or examples.
Include all explicitly named technologies, including Spring Boot when Spring is
also present, and preferred technologies; do not stop after a few examples.
"""


def verify_overview(overview: Overview, passages) -> dict:
    by_id = {p.id: p for p in passages}
    result = overview.model_dump()
    for items in result.values():
        for item in items:
            refs = item["source_ids"]
            if len(refs) != len(set(refs)) or any(ref not in by_id for ref in refs):
                raise LLMError("Invalid overview source reference. Analysis rejected.")
            if "usage" in item and ((item["usage"] == "mentioned") != (item["importance"] == "not_applicable")):
                # Neither label can settle the conflict. Keep the evidence and
                # leave the classification open for review.
                item["usage"] = "unclear"
                item["importance"] = "unclear"
            item["source_passages"] = [{"passage_id": ref, "quote": by_id[ref].text,
                                        "start": by_id[ref].start, "end": by_id[ref].end} for ref in refs]
    return result
