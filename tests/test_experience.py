import json

import httpx
import pytest
from pydantic import ValidationError

from jobagent.analysis import AdExtraction, analyze_job, extraction_schema
from jobagent.filtering import evaluate_experience
from jobagent.llm import LLMError, LocalLLM
from jobagent.models import JobAd
from jobagent.passages import number_passages
from jobagent.settings import FilterSettings, LLMSettings


def experience_item(minimum=4, maximum=None, **changes):
    return {"kind": "professional_experience", "minimum_years": minimum, "maximum_years": maximum,
            "importance": "mandatory", "domain": "Java", "alternatives_accepted": False,
            "conditional": False, "ambiguous": False, "source_ids": ["p0001"], "context_ids": [], **changes}


def experience_data(items=None, **changes):
    items = [experience_item()] if items is None else items
    return {"presence": "present" if any(i["kind"] == "professional_experience" for i in items) else "absent",
            "coverage_complete": True, "items": items, **changes}


def model_output(items=None, **changes):
    return {"requirements": [], "experience": experience_data(items),
            "overview": {"summary": [{"text_sv": "Annonsens villkor.", "source_ids": ["p0001"]}],
                         "techstack": [], "education": [], "languages": [], "location": [], "work_arrangement": []}, **changes}


def envelope(data):
    return {"model": "offline-model", "choices": [{"finish_reason": "stop", "message": {
        "content": json.dumps(data, ensure_ascii=False)}}]}


def analyze_mock(text, output):
    calls = []
    def handler(request):
        calls.append(request)
        assert request.url.path == "/v1/chat/completions"
        payload = json.loads(request.content)
        assert "tools" not in payload
        assert "profile" not in json.loads(payload["messages"][1]["content"])
        return httpx.Response(200, json=envelope(output))
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        result = analyze_job(JobAd(source="test", external_id="1", title="Job", description=text),
                             LocalLLM(LLMSettings(), client))
    assert len(calls) == 1
    return result


@pytest.mark.parametrize("years", range(6))
@pytest.mark.parametrize("language", ["sv", "en"])
@pytest.mark.parametrize("importance", ["mandatory", "preferred"])
def test_numeric_years_through_mocked_extraction_and_filter(years, language, importance):
    if language == "sv":
        text = f"Minst {years} års Java-erfarenhet krävs." if importance == "mandatory" else f"{years} års Java-erfarenhet är meriterande."
    else:
        text = f"At least {years} years of Java experience is required." if importance == "mandatory" else f"{years} years of Java experience is a plus."
    result = analyze_mock(text, model_output([experience_item(years, importance=importance)]))
    evaluation = evaluate_experience(result["experience"], text, FilterSettings())
    assert evaluation["decision"] == ("EXCLUDE" if importance == "mandatory" and years > 3 else "KEEP")
    proof = result["experience_evidence"][0]["source_passages"][0]
    assert text[proof["start"]:proof["end"]] == proof["quote"] == text


@pytest.mark.parametrize("minimum,maximum,decision", [(2, 4, "REVIEW"), (3, 5, "REVIEW"),
    (4, 5, "EXCLUDE"), (2, 3, "KEEP"), (3, 3, "KEEP")])
def test_ranges(minimum, maximum, decision):
    text = f"Krav: {minimum}–{maximum} års erfarenhet av Java."
    result = analyze_mock(text, model_output([experience_item(minimum, maximum)]))
    assert evaluate_experience(result["experience"], text, FilterSettings())["decision"] == decision


@pytest.mark.parametrize("text,minimum,maximum,ambiguous,decision", [
    ("0–3 års erfarenhet.", 0, 3, False, "KEEP"),
    ("Är i början av din karriär och har cirka 0–3 års relevant erfarenhet", 0, 3, True, "KEEP"),
    ("0–2 års relevant erfarenhet.", 0, 2, True, "KEEP"),
    ("Upp till 3 års erfarenhet.", None, 3, True, "KEEP"),
    ("Cirka 2–4 års erfarenhet.", 2, 4, True, "REVIEW"),
    ("Cirka 3–5 års erfarenhet.", 3, 5, True, "REVIEW"),
    ("Mer än 3 års erfarenhet.", 3, 3, True, "REVIEW"),
])
def test_uncertainty_is_evaluated_against_the_limit(text, minimum, maximum, ambiguous, decision):
    item = experience_item(minimum, maximum, ambiguous=ambiguous, importance="expected")
    result = analyze_mock(text, model_output([item]))
    assert evaluate_experience(result["experience"], text, FilterSettings())["decision"] == decision


@pytest.mark.parametrize("text,changes", [
    ("Flerårig erfarenhet av Java krävs.", {"minimum_years": None}),
    ("Several years of Java experience required.", {"minimum_years": None}),
    ("Java-erfarenhet krävs.", {"minimum_years": None}),
    ("5 years experience or equivalent education.", {"minimum_years": 5, "alternatives_accepted": True}),
    ("4 års erfarenhet eller likvärdig kompetens.", {"minimum_years": 4, "alternatives_accepted": True}),
    ("För vissa uppdrag krävs minst 5 års Java-erfarenhet.", {"minimum_years": 5, "conditional": True}),
    ("Around 5 years of experience.", {"minimum_years": 5, "ambiguous": True}),
    ("More than 3 years of experience required.", {"minimum_years": 3, "ambiguous": True}),
    ("We expect 5 years of Java experience.", {"minimum_years": 5, "importance": "expected"}),
    ("5 års erfarenhet.", {"minimum_years": 5, "importance": "unclear"}),
    ("5 år inom Java.", {"minimum_years": 5, "kind": "unclear"}),
])
def test_ambiguity_and_alternative_paths_require_review(text, changes):
    result = analyze_mock(text, model_output([experience_item(**changes)]))
    assert evaluate_experience(result["experience"], text, FilterSettings())["decision"] == "REVIEW"


def test_conditional_minimum_at_the_limit_does_not_require_review():
    text = "För vissa uppdrag krävs minst 3 års Java-erfarenhet."
    result = analyze_mock(text, model_output([experience_item(3, conditional=True)]))
    assert evaluate_experience(result["experience"], text, FilterSettings())["decision"] == "KEEP"


@pytest.mark.parametrize("kind,text", [
    ("education_duration", "Fem års universitetsutbildning krävs."),
    ("education_duration", "A five-year university degree is required."),
    ("project_duration", "Projektet pågår i fem år."),
    ("other_duration", "Avtalet löper på fem år."),
])
def test_nonprofessional_durations_do_not_exclude(kind, text):
    result = analyze_mock(text, model_output([experience_item(5, kind=kind, domain="duration")]))
    assert result["experience"]["presence"] == "absent"
    assert evaluate_experience(result["experience"], text, FilterSettings())["decision"] == "KEEP"


def test_absent_experience_and_empty_qualifications_are_valid():
    text = "Vi erbjuder introduktion och intern utbildning."
    result = analyze_mock(text, model_output([]))
    assert result["analysis"]["requirements"] == []
    assert evaluate_experience(result["experience"], text, FilterSettings())["decision"] == "KEEP"


@pytest.mark.parametrize("text,years,decision", [
    ("Minst fem års yrkeserfarenhet krävs.", 5, "EXCLUDE"),
    ("Minst tre års yrkeserfarenhet krävs.", 3, "KEEP"),
    ("At least five years of professional experience required.", 5, "EXCLUDE"),
    ("At least three years of professional experience required.", 3, "KEEP"),
])
def test_explicit_number_words_keep_their_numeric_quantity(text, years, decision):
    result = analyze_mock(text, model_output([experience_item(years)]))
    assert evaluate_experience(result["experience"], text, FilterSettings())["decision"] == decision


@pytest.mark.parametrize("items,decision", [
    ([experience_item(3), experience_item(3, domain="SQL", source_ids=["p0002"])], "KEEP"),
    ([experience_item(2), experience_item(5, domain="SQL", source_ids=["p0002"])], "EXCLUDE"),
    ([experience_item(2), experience_item(5, importance="preferred", source_ids=["p0002"])], "KEEP"),
    ([experience_item(4), experience_item(None, domain="SQL", source_ids=["p0002"])], "EXCLUDE"),
    ([experience_item(5, alternatives_accepted=True), experience_item(2, source_ids=["p0002"])], "REVIEW"),
])
def test_multiple_requirements_are_independent_not_summed(items, decision):
    assert evaluate_experience(experience_data(items), "Java\nSQL", FilterSettings())["decision"] == decision


@pytest.mark.parametrize("requirements,items,decision", [
    ("Cirka 0–3 års Java-erfarenhet.\nMinst 4 års SQL-erfarenhet krävs.",
     [experience_item(0, 3, ambiguous=True), experience_item(4, domain="SQL", source_ids=["p0002"])], "EXCLUDE"),
    ("Minst 4 års Java-erfarenhet krävs.\n2 års SQL-erfarenhet är meriterande.",
     [experience_item(4), experience_item(2, importance="preferred", domain="SQL", source_ids=["p0002"])], "EXCLUDE"),
    ("Minst 4 års Java-erfarenhet krävs.\nFör vissa uppdrag krävs 4 års Java-erfarenhet.",
     [experience_item(4), experience_item(4, conditional=True, source_ids=["p0002"])], "REVIEW"),
    ("Minst 4 års Java-erfarenhet krävs.\nEller flerårig erfarenhet av Kotlin.",
     [experience_item(4), experience_item(None, domain="Kotlin", alternatives_accepted=True,
                                          source_ids=["p0002"], context_ids=["p0001"])], "REVIEW"),
    ("4 års erfarenhet av Java.",
     [experience_item(4), experience_item(4, importance="preferred")], "REVIEW"),
])
def test_related_uncertainty_can_block_an_exclusion(requirements, items, decision):
    for ordered_items in (items, list(reversed(items))):
        result = analyze_mock(requirements, model_output(ordered_items))
        evaluation = evaluate_experience(result["experience"], requirements, FilterSettings())
        assert evaluation["decision"] == decision
        if decision == "REVIEW":
            assert "conflicting_requirement" in {reason["code"] for reason in evaluation["reasons"]}


@pytest.mark.parametrize("change", [
    {"source_ids": ["p9999"]}, {"source_ids": ["p001"]}, {"source_ids": []},
    {"context_ids": ["p9999"]}, {"source_ids": ["p0001", "p0001"]},
    {"minimum_years": -1}, {"minimum_years": 5, "maximum_years": 2},
    {"minimum_years": "5"}, {"minimum_years": True}, {"maximum_years": float("inf")},
])
def test_invalid_schema_or_references_never_exclude(change):
    data = experience_data([experience_item(**change)])
    assert evaluate_experience(data, "Minst fem år krävs.", FilterSettings())["decision"] == "REVIEW"
    with pytest.raises(LLMError):
        analyze_mock("Minst fem år krävs.", {"requirements": [], "experience": data})


@pytest.mark.parametrize("data", [None, {}, {"presence": "absent"},
    experience_data(coverage_complete=False), experience_data(presence="unclear"),
    experience_data(presence="absent"), experience_data([], presence="present")])
def test_missing_incomplete_or_inconsistent_analysis_is_review(data):
    assert evaluate_experience(data, "Annons", FilterSettings())["decision"] == "REVIEW"


def test_separate_context_passages_preserve_exact_quotes():
    text = "Krav\nMinst fem års erfarenhet\neller motsvarande utbildning"
    data = experience_data([experience_item(5, source_ids=["p0002"], context_ids=["p0001", "p0003"],
                                          alternatives_accepted=True)])
    result = analyze_mock(text, model_output(experience=data))
    evaluation = evaluate_experience(result["experience"], text, FilterSettings())
    assert evaluation["decision"] == "REVIEW"
    reason = evaluation["reasons"][0]
    assert reason["source_passages"][0]["quote"] == "Minst fem års erfarenhet"
    assert [p["quote"] for p in reason["scope_context"]] == ["Krav", "eller motsvarande utbildning"]


def test_schema_has_required_fields_and_per_ad_source_enums():
    for text in ("Java", "Krav\nMinst 5 års Java-erfarenhet"):
        passages = number_passages(text)
        schema = extraction_schema(passages, include_experience=True)
        assert set(schema["required"]) == {"requirements", "experience", "overview"}
        for name in ("source_ids", "context_ids"):
            assert schema["$defs"]["ExperienceItem"]["properties"][name]["items"]["enum"] == [p.id for p in passages]
        assert set(schema["$defs"]["ExperienceItem"]["required"]) == set(schema["$defs"]["ExperienceItem"]["properties"])
    assert "enum" not in AdExtraction.model_json_schema()["$defs"]["ExperienceItem"]["properties"]["source_ids"]["items"]


def test_old_model_contract_is_not_silently_treated_as_no_experience():
    with pytest.raises(LLMError, match="schema validation"):
        analyze_mock("5 years required", {"requirements": []})


@pytest.mark.parametrize("value", [-1, float("nan"), float("inf"), True, "3"])
def test_invalid_filter_configuration(value):
    with pytest.raises(ValidationError):
        FilterSettings(max_required_years=value)


def test_changed_limit_and_zero_and_fractional_boundaries():
    data = experience_data([experience_item(3)])
    assert evaluate_experience(data, "3 years required", FilterSettings(max_required_years=2))["decision"] == "EXCLUDE"
    assert evaluate_experience(data, "3 years required", FilterSettings(max_required_years=3))["decision"] == "KEEP"
    assert evaluate_experience(experience_data([experience_item(0)]), "0 years", FilterSettings(max_required_years=0))["decision"] == "KEEP"
    assert evaluate_experience(experience_data([experience_item(3.5)]), "3.5 years", FilterSettings())["decision"] == "EXCLUDE"


@pytest.mark.parametrize("maximum,decision", [(0, "KEEP"), (2, "KEEP"), (3, "KEEP"), (4, "REVIEW")])
def test_explicit_upper_bound_without_minimum(maximum, decision):
    assert evaluate_experience(experience_data([experience_item(None, maximum)]),
                               f"At most {maximum} years", FilterSettings())["decision"] == decision


def test_unquantified_merit_does_not_trigger_exclusion_or_review():
    assert evaluate_experience(experience_data([experience_item(None, importance="preferred")]),
                               "Several years is a plus", FilterSettings())["decision"] == "KEEP"


def test_missing_original_text_is_not_assumed_to_have_no_experience():
    assert evaluate_experience(experience_data([]), "", FilterSettings())["decision"] == "REVIEW"


@pytest.mark.parametrize("changes", [{"importance": "preferred"}, {"kind": "responsibility"}, {"conditional": True}])
def test_contradictory_general_and_experience_classifications_are_rejected(changes):
    selection = {"passage_id": "p0001", "kind": "qualification", "importance": "mandatory",
                 "conditional": False, "context_ids": [], **changes}
    with pytest.raises(LLMError, match="contradicts"):
        analyze_mock("Five years required", model_output(requirements=[selection]))
