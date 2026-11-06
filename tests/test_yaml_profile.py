from pathlib import Path

import pytest
from pydantic import ValidationError

from jobagent.cli import main
from jobagent.settings import load_yaml_profile

ROOT = Path(__file__).resolve().parents[1]


def test_user_profile_preserves_documented_facts_without_inventing_totals_or_levels():
    profile = load_yaml_profile(ROOT / "config/profile.yaml")
    data = profile.model_dump(exclude_unset=True)
    assert profile.education.university_credits == 157
    assert profile.education.has_issued_degree is False
    recorded = profile.experience.documented[0]
    assert recorded.role == "Fullstack developer"
    assert recorded.years == 2
    assert recorded.basis == "part_time"
    assert recorded.details == [
        "Java and Spring Boot backend development", "Vue.js frontend development",
        "REST API development", "Database development", "Git version control", "CI/CD workflows",
    ]
    assert "total_professional_years" not in data["experience"]
    assert data["certifications"] == [
        {"name": "Cisco CCNA 1", "category": "Networking"},
        {"name": "Cisco CCNA 2", "category": "Networking"},
        {"name": "Cisco CyberOps", "category": "Cybersecurity"},
    ]
    assert data["skills"]["testing"] == ["JUnit", "MockMvc", "Testcontainers", "TDD"]
    assert data["skills"]["methodologies"] == ["Agile software development"]
    assert data["skills"]["backend"] == ["Spring Boot", "REST API", "JWT", "RBAC"]
    assert data["languages"] == ["Swedish", "English", "Arabic"]
    assert data["other_qualifications"] == ["Driving licence category B"]


def test_missing_facts_are_not_filled_with_false_zero_or_extra_sections(tmp_path):
    path = tmp_path / "profile.yaml"
    path.write_text("skills:\n  programming_languages: [Java]\n", encoding="utf-8")
    assert load_yaml_profile(path).model_dump(exclude_unset=True) == {
        "skills": {"programming_languages": ["Java"]}}


def test_explicit_unknowns_remain_unknown(tmp_path):
    path = tmp_path / "profile.yaml"
    path.write_text("education:\n  has_issued_degree: null\nexperience:\n  total_professional_years: null\n", encoding="utf-8")
    data = load_yaml_profile(path).model_dump(exclude_unset=True)
    assert data["education"]["has_issued_degree"] is None
    assert data["experience"]["total_professional_years"] is None


@pytest.mark.parametrize("text", [
    "", "null", "[]", "{}", "skills: [Java]", "languages: Swedish",
    "skills: {programming_languages: [123]}", "education: {has_issued_degree: 'false'}",
    "education: {university_credits: -1}", "experience: {total_professional_years: true}",
    "experience: {documented: [{role: Dev, years: -2, basis: part_time}]}",
    "experience: {documented: [{role: Dev, basis: half_time}]}",
    "invented_section: []", "education: {invented_fact: false}",
    "skills: [private-secret", "!!python/object/apply:builtins.str [private-secret]",
    "certifications: Cisco CCNA 1", "certifications: [{category: Networking}]",
    "certifications: [{name: Cisco CCNA 1, category: false}]",
    "skills: {testing: TDD}", "skills: {testing: [false]}",
    "skills: {methodologies: Agile}",
])
def test_invalid_yaml_rejected(tmp_path, text):
    path = tmp_path / "profile.yaml"
    path.write_text(text, encoding="utf-8")
    with pytest.raises((ValueError, ValidationError)):
        load_yaml_profile(path)


def test_yaml_config_check_has_no_database_side_effect(tmp_path, monkeypatch, capsys):
    path = tmp_path / "absent" / "jobs.db"
    monkeypatch.setenv("JOBAGENT_DATABASE", str(path))
    assert main(["--root", str(ROOT), "config-check", "--profile", "config/profile.yaml"]) == 0
    assert '"profile_valid": true' in capsys.readouterr().out
    assert not path.parent.exists()


def test_yaml_syntax_error_is_safe_and_prevents_model_call(tmp_path, monkeypatch, capsys):
    path = tmp_path / "profile.yaml"
    path.write_text("skills: [private-secret", encoding="utf-8")
    monkeypatch.setattr("jobagent.llm.LocalLLM.generate_json", lambda *a: pytest.fail("No call"))
    assert main(["--root", str(ROOT), "match", "188", "--profile", str(path)]) == 1
    error = capsys.readouterr().err
    assert "Invalid YAML profile syntax" in error
    assert "private-secret" not in error
