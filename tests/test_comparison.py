from pathlib import Path

import pytest

from jobagent.comparison import compare_requirement, degree_condition, skill_evidence
from jobagent.settings import load_profile

PROFILE = load_profile(Path(__file__).resolve().parents[1] / "config/profile.example.toml")


def compare(quote, **changes):
    requirement = {"quote": quote, "match": "supported", "profile_fact_keys": ["skills"],
                   "explanation_sv": "Microservices kan antas från fullstack."}
    requirement.update(changes)
    compare_requirement(requirement, PROFILE)
    return requirement


def test_postgresql_supports_sql_without_proving_years():
    result = compare("God erfarenhet av Java, JavaScript och SQL")
    assert any(e["supports"] == "SQL" and e["value"] == "PostgreSQL" for e in result["profile_evidence"])
    assert result["match"] == "partial"
    assert not any(e["value"] == "MongoDB" for e in result["profile_evidence"])


def test_test_tools_support_related_automation_not_squish():
    evidence = skill_evidence("Praktisk erfarenhet av automattest, gärna med Squish eller liknande verktyg", PROFILE)
    assert {e["value"] for e in evidence} >= {"JUnit", "MockMvc", "Testcontainers"}
    assert all(e["kind"] == "related_knowledge" for e in evidence)
    assert not any(e["supports"] == "Squish" for e in evidence)


def test_fullstack_does_not_prove_microservices_and_boot_is_not_mvc():
    assert compare("Erfarenhet av microservices")["match"] == "unknown"
    result = compare("Spring MVC")
    assert result["match"] == "partial"
    assert result["profile_evidence"][0]["kind"] == "related_knowledge"
    assert "kan antas" not in result["explanation_sv"]


def test_known_absence_of_degree_differs_from_unknown_languages():
    degree = compare("Har en avslutad kandidat- eller masterexamen inom datateknik eller motsvarande relevant område")
    assert degree["match"] == "not_met"
    assert degree["profile_evidence"][0]["value"] is False
    assert compare("Flytande svenska och engelska")["match"] == "unknown"


@pytest.mark.parametrize("quote", ["Kandidatexamen eller motsvarande erfarenhet", "Degree or equivalent knowledge"])
def test_experience_alternative_prevents_automatic_degree_failure(quote):
    assert compare(quote)["match"] == "partial"


def test_equivalent_field_is_not_equivalent_work_experience():
    assert degree_condition("Master's degree in IT or an equivalent field") == "degree"
    assert degree_condition("Bachelor's degree or equivalent") == "ambiguous"


def test_years_not_converted_to_fulltime_or_invented():
    assert compare("Minst 3 års erfarenhet av Java de senaste 6 åren")["match"] == "not_met"
    vague = compare("Några års erfarenhet av systemutveckling")
    assert vague["match"] == "partial"
    assert "inget numeriskt minimum" in vague["explanation_sv"]
    assert compare("Anställningen är på heltid")["match"] == "unknown"


def test_direct_role_and_activity_have_supported_evidence():
    assert compare("Erfarenhet av fullstackutveckling")["match"] == "supported"
    assert compare("Erfarenhet av att utveckla REST-baserade API:er")["match"] == "supported"


def test_similar_names_do_not_imply_the_wrong_technology():
    evidence = skill_evidence("JavaScript och NoSQL", PROFILE)
    assert not any(e["value"] in {"Java", "PostgreSQL"} for e in evidence)


def test_swedish_written_minimum_is_not_confused_with_vague_years():
    assert compare("Minst fem års arbetslivserfarenhet som systemutvecklare")["match"] == "not_met"


def test_education_without_degree_condition_uses_studies_without_requiring_a_degree():
    result = compare("Relevant högskole- eller universitetsutbildning")
    assert result["match"] == "partial"
    assert any(e["value"] == 157 for e in result["profile_evidence"])


def test_common_skill_names_keep_direct_evidence_without_inventing_frameworks():
    assert any(e["value"] == "Vue.js" for e in skill_evidence("Vue, React eller Angular", PROFILE))
    assert any(e["value"] == "Java" for e in skill_evidence("Erfarenhet av Javautveckling", PROFILE))


def test_equivalent_education_is_reviewed_without_assuming_degree_or_experience_equivalence():
    result = compare("Civilingenjörsexamen inom datateknik eller motsvarande utbildning")
    assert result["match"] == "unknown"
    assert degree_condition(result["quote"]) == "ambiguous"


def test_university_credits_do_not_prove_thesis_or_summer_employment():
    result = compare("Har du examensarbete eller sommarjobb är det meriterande")
    assert result["match"] == "unknown"
    assert not any(e["profile_path"] == "university_credits" for e in result["profile_evidence"])


def test_general_work_duration_does_not_establish_test_leadership():
    result = compare("Flerårig aktuell erfarenhet av testledning")
    assert result["match"] == "unknown"


def test_programming_languages_support_knowledge_without_assuming_interest():
    result = compare("Goda programmeringskunskaper och intresse för systemutveckling")
    assert result["match"] == "partial"
    assert {e["value"] for e in result["profile_evidence"]} >= {"Java", "Python", "JavaScript"}


@pytest.mark.parametrize("quote", ["IT-säkerhet i systemutvecklingen", "Systemarkitektur och backend", "Typescript, Node.js, NestJS och Backend For Frontend (BFF)"])
def test_generic_fullstack_role_is_not_evidence_of_specialist_skills(quote):
    result = compare(quote)
    assert result["match"] == "unknown"
    assert not any(e["kind"] == "documented_role" for e in result["profile_evidence"])
