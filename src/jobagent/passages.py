"""Mechanical source segmentation. No requirements, headings or skills inferred."""
from dataclasses import dataclass
import re

SOURCE_POLICY_VERSION = "source-offsets-v1"


@dataclass(frozen=True)
class Passage:
    id: str
    text: str
    start: int
    end: int


def number_passages(description: str) -> list[Passage]:
    """Keep exact Python character offsets, including original bullet markers.

    Split physical lines, collapsed bullet lists, sentences and semicolon clauses.
    Relations between clauses belong to the model and remain explicit context
    references. Even unknown headings and employer prose are sent unchanged.
    """
    passages = []
    for line in re.finditer(r"[^\r\n]+", description):
        boundaries = {0, len(line[0])}
        boundaries.update(m.start() for m in re.finditer(r"[•✓✔✅☑]", line[0]))
        for match in re.finditer(r"(?<=[.!?])\s+(?=[A-ZÅÄÖ])|(?<=;)\s+", line[0]):
            if re.search(r"(?:t\.ex\.|ex\.|bl\.a\.|m\.m\.|e\.g\.|i\.e\.|B\.Sc\.|M\.Sc\.|Ph\.D\.)$", line[0][:match.start()], re.I):
                continue
            boundaries.add(match.end())
        points = sorted(boundaries)
        for left, right in zip(points, points[1:]):
            raw = line[0][left:right]
            text = raw.strip()
            if not text:
                continue
            start = line.start() + left + len(raw) - len(raw.lstrip())
            end = start + len(text)
            assert description[start:end] == text
            passages.append(Passage(f"p{len(passages)+1:04d}", text, start, end))
    return passages
