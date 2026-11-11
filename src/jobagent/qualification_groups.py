"""Conservative consolidation; every original quote remains inspectable.

Only normalized repetitions merge. Related but non-identical conditions are linked
for human review, never silently collapsed into an invented qualification.
"""
import re


def normalized(text: str) -> str:
    text = text.casefold().strip().rstrip('.;:')
    text = re.sub(r'^(?:har |du har |vi söker dig som har )', '', text)
    for word, number in {'ett': '1', 'en': '1', 'fem': '5'}.items():
        text = re.sub(r'\b' + word + r'(?= års)', number, text)
    text = text.replace('arbetslivserfarenhet', 'erfarenhet')
    return re.sub(r'\s+', ' ', text)


def topics(text: str) -> set[str]:
    patterns = {
        'education': r'examen|eftergymnasial utbildning',
        'cloud': r'azure|aws',
        'security_approval': r'säkerhetspröv|säkerhetsskydd',
        'languages': r'svenska.*engelska',
        'experience_period': r'(?:minst|at least).*års?.*erfarenhet',
    }
    return {key for key, pattern in patterns.items() if re.search(pattern, text, re.I)}


def consolidate_requirements(requirements: list[dict]) -> list[dict]:
    result, seen = [], {}
    for row in requirements:
        # Strength and scope are part of identity. Conflicts remain explicit.
        key = (normalized(row['quote']), row['kind'], row['conditional'], row['importance'], str(row['scope_context']))
        if key in seen:
            seen[key]['source_passages'].extend(row['source_passages'])
            continue
        seen[key] = row
        row['related_requirement_ids'] = []
        for previous in result:
            if topics(row['quote']) & topics(previous['quote']):
                row['related_requirement_ids'].append(previous['id'])
                previous['related_requirement_ids'].append(row['id'])
        result.append(row)
    return result
