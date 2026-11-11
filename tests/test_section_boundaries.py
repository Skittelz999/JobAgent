"""Source segmentation tests: no heading or qualification semantics in Python."""
import re
from dataclasses import asdict
import pytest
from jobagent.passages import number_passages

@pytest.mark.parametrize('text', [
    "Krav:\nJava\nOm oss\nVi är ett företag.",
    "✅ Java•Python•C++•CI/CD",
    "Company heading\r\n  identical  \r\n identical\r\n",
    "Bachelor's degree; a master's degree is a plus.",
    "B.Sc. Computer Science or equivalent experience",
    "Agilt, ex. Scrum och t.ex. Kanban.",
    "• First item• Second item• Third item",
    "2–5 years in:\nAndroid\nKotlin and Java",
])
def test_offsets_are_exact_and_all_nonwhitespace_text_survives(text):
    passages = number_passages(text)
    assert all(text[p.start:p.end] == p.text for p in passages)
    assert len({p.id for p in passages}) == len(passages)
    assert all(left.end <= right.start for left, right in zip(passages, passages[1:]))
    assert re.sub(r"\s", "", ''.join(p.text for p in passages)) == re.sub(r"\s", "", text)
    assert all(set(asdict(p)) == {'id', 'text', 'start', 'end'} for p in passages)


def test_collapsed_bullets_are_independent_source_passages():
    ps = number_passages("Skills\n• Degree• Linux preferably Red Hat• At least 5 years")
    assert [p.text for p in ps] == ['Skills', '• Degree', '• Linux preferably Red Hat', '• At least 5 years']


def test_abbreviations_stay_in_original_clause():
    ps = number_passages("Krav\nAgila metoder, ex. Scrum")
    assert ps[-1].text == 'Agila metoder, ex. Scrum'


def test_repeated_quotes_have_distinct_positions():
    first, second = number_passages('Java\nJava')
    assert first.text == second.text and first.start != second.start
