"""Replay mechanics for preserved corpora, NOT an eval of the new model prompt.

Legacy output has no kind/conditional/context fields. This explicit test-only
adapter marks legacy rows as qualifications without inventing semantic upgrades.
Old frozen policy tests live in the task's before/ snapshot; auto-fill and regex
strength expectations have been intentionally replaced by no-addition contracts.
"""
import json
from pathlib import Path
import pytest
from jobagent.analysis import Extraction,finalize_extraction
from jobagent.passages import number_passages
from jobagent.settings import load_profile

ROOT = Path(__file__).resolve().parents[1]
PROFILE = load_profile(ROOT / 'config/profile.example.toml')
FIXTURES = sorted((ROOT/'tests/fixtures').glob('*/*.json'))


def load_fixture(path):
    f = json.loads(path.read_text(encoding='utf-8'))
    source = f['source'] if isinstance(f.get('source'), dict) else f
    return f, source


def legacy_adapter(f, description, passages):
    offsets, cursor = {}, 0
    for key, text in f['original_passages'].items():
        start = description.index(text, cursor)
        offsets[key] = (start, start + len(text))
        cursor = start + len(text)
    selections = []
    for item in f['recorded_model_selection']['requirements']:
        start,end = offsets[item['passage_id']]
        for p in passages:
            if start <= p.start and p.end <= end:
                selections.append({'passage_id':p.id,'kind':'qualification',
                                   'importance':item['importance'],'conditional':False,'context_ids':[]})
    return selections


@pytest.mark.parametrize('path', FIXTURES, ids=lambda p:p.stem)
def test_legacy_raw_choices_preserved_without_automatic_additions(path):
    f, source = load_fixture(path)
    passages = number_passages(source['description'])
    selected = legacy_adapter(f, source['description'], passages)
    # Large collapsed lists may exceed the production schema cap after expanding;
    # test in independent batches, never bypass schema or truncate real requests.
    for start in range(0,len(selected),60):
        batch = selected[start:start+60]
        r = finalize_extraction(Extraction.model_validate({'requirements':batch}),passages,PROFILE,'Replay')
        rows = r['requirements']
        assert r['coverage_review']['added_passage_ids'] == []
        assert r['coverage_review']['excluded_nonqualification_ids'] == []
        allowed = {x['passage_id']:x['importance'] for x in batch}
        for row in rows:
            assert row['importance'] == allowed[row['passage_id']]
            assert row['classification_review_required'] is True
            for s in row['source_passages']:
                assert source['description'][s['start']:s['end']] == s['quote']
                assert s['passage_id'] in allowed
