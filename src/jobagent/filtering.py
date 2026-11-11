"""Deterministic ad filtering. No model calls or candidate comparisons."""
from pydantic import ValidationError

from jobagent.experience import ExperienceAssessment, ExperienceItem, missing_experience_sources, verify_experience
from jobagent.llm import LLMError
from jobagent.passages import number_passages
from jobagent.settings import FilterSettings

FILTER_POLICY_VERSION = "experience-filter-v3"


def _can_affect_exclusion(requirement: ExperienceItem, other: ExperienceItem, needs_review: bool) -> bool:
    """Check whether another item can change the interpretation of this minimum."""
    same_domain = requirement.domain.strip().casefold() == other.domain.strip().casefold()
    if needs_review or other.alternatives_accepted:
        scope = set(requirement.source_ids + requirement.context_ids)
        return same_domain or not scope.isdisjoint(other.source_ids + other.context_ids)
    return (same_domain and not set(requirement.source_ids).isdisjoint(other.source_ids)
            and (requirement.kind, requirement.importance, requirement.minimum_years)
            != (other.kind, other.importance, other.minimum_years))


def evaluate_experience(data: dict | None, description: str, settings: FilterSettings, *,
                        requirements: list[dict] | None = None) -> dict:
    """Revalidate data and references before any decision, including stored results."""
    limit = settings.max_required_years
    def result(decision, reasons):
        return {"decision": decision, "reasons": reasons, "max_required_years": limit,
                "policy_version": FILTER_POLICY_VERSION}
    def reason(code, message, evidence=None):
        return {"code": code, "message_sv": message, **(evidence or {})}
    if data is None or not description.strip():
        return result("REVIEW", [reason("missing_analysis", "En giltig erfarenhetsanalys saknas.")])
    try:
        passages = number_passages(description)
        assessment = ExperienceAssessment.model_validate(data)
        evidence = verify_experience(assessment, passages)
        gaps = missing_experience_sources(assessment, passages, requirements or [])
    except (ValidationError, LLMError, KeyError, TypeError):
        return result("REVIEW", [reason("invalid_analysis", "Analysens struktur eller källreferenser är ogiltiga.")])
    coverage_reviews = []
    if not assessment.coverage_complete or assessment.presence == "unclear":
        coverage_reviews.append(reason("incomplete_analysis", "Erfarenhetskravens förekomst eller täckning är osäker."))
    if gaps:
        coverage_reviews.append(reason("missing_experience_coverage", "Källpassager om möjlig yrkeserfarenhet saknas i erfarenhetsanalysen.",
                                       {"source_passages": gaps}))
    if coverage_reviews:
        return result("REVIEW", coverage_reviews)
    exclusions, reviews = {}, {}
    for item, proof in zip(assessment.items, evidence):
        if item.kind in {"education_duration", "project_duration", "other_duration"} or item.importance == "preferred":
            continue
        details = {**proof, "domain": item.domain, "minimum_years": item.minimum_years,
                   "maximum_years": item.maximum_years, "importance": item.importance}
        index = proof["item_index"]
        if item.kind == "professional_experience":
            within_limit = item.minimum_years is not None and item.minimum_years <= limit
            if item.maximum_years is not None:
                within_limit = item.maximum_years <= limit
            # An ambiguous point can hide an open bound. Conditions or alternatives
            # can make an ambiguous range apply to only part of the requirement.
            bounded_range = (item.maximum_years is not None and item.minimum_years != item.maximum_years
                             and not item.conditional and not item.alternatives_accepted)
            if within_limit and (not item.ambiguous or bounded_range):
                continue
        if (item.kind == "unclear" or item.importance == "unclear" or item.ambiguous
                or item.conditional or item.alternatives_accepted):
            reviews[index] = reason("ambiguous_requirement", "Kravets innebörd, villkor eller alternativa kvalifikationsvägar behöver granskas.", details)
        elif item.minimum_years is None:
            reviews[index] = reason("unspecified_minimum", "Erfarenhetskravet saknar ett entydigt numeriskt minimum.", details)
        elif item.importance == "mandatory" and item.minimum_years > limit:
            exclusions[index] = reason("mandatory_minimum_above_limit", f"Obligatoriskt minimum {item.minimum_years:g} år överstiger gränsen {limit:g} år.", details)
        elif item.maximum_years is not None and item.minimum_years <= limit < item.maximum_years:
            reviews[index] = reason("range_crosses_limit", f"Erfarenhetsintervallet sträcker sig över gränsen {limit:g} år.", details)
        elif item.importance == "expected" and item.minimum_years > limit:
            reviews[index] = reason("unclear_mandatory_status", "Det är oklart om erfarenheten utgör ett obligatoriskt minimikrav.", details)
    review_indices = set(reviews)
    # Keep independent exclusions. Only related evidence can weaken a blocker.
    for index, exclusion in list(exclusions.items()):
        requirement = assessment.items[index]
        if any(_can_affect_exclusion(requirement, other, other_index in review_indices)
               for other_index, other in enumerate(assessment.items) if other_index != index):
            reviews[index] = {**exclusion, "code": "conflicting_requirement",
                              "message_sv": "Relaterade uppgifter gör det obligatoriska minimikravet osäkert."}
            del exclusions[index]
    if exclusions:
        return result("EXCLUDE", [*exclusions.values(), *reviews.values()])
    if reviews:
        return result("REVIEW", list(reviews.values()))
    return result("KEEP", [reason("no_mandatory_minimum_above_limit", f"Inget identifierat obligatoriskt erfarenhetskrav över {limit:g} år.")])
