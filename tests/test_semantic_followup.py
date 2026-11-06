"""Mocked semantic examples from documented failures; manual live eval is separate."""
import json
import pytest
from test_analysis import choice
from test_semantic_regressions import ROOT, load_fixture
from jobagent.analysis import Extraction,finalize_extraction
from jobagent.passages import number_passages


def finalize(ad, fragments):
    f, src = load_fixture(ROOT/f'tests/fixtures/latest_baseline/{ad}.json')
    ps = number_passages(src['description'])
    selections=[]
    for fragment,level,kind,conditional in fragments:
        p = next(p for p in ps if fragment in p.text)
        selections.append(choice(p.id,level,kind,conditional))
    return finalize_extraction(Extraction.model_validate({'requirements':selections}),ps,src['title'])


@pytest.mark.parametrize('ad,fragments', [
 (67,[('Omfattande erfarenhet','mandatory','qualification',False),('Keycloak, inklusive','mandatory','qualification',False)]),
 (104,[('Documented experience','mandatory','qualification',False),('Strong communication skills','mandatory','qualification',False)]),
 (175,[('Strong proficiency in Java','expected','qualification',False),('Good understanding of microservices','expected','qualification',False)]),
 (190,[('A university degree','expected','qualification',False),('Comfortable working in Linux','expected','qualification',False)]),
 (29,[('Eventuella krav','unclear','condition',True)]),
 (131,[('För att jobba hos oss','mandatory','condition',False)]),
 (117,[('Denna befattning kräver','mandatory','condition',False)]),
])
def test_correct_model_meaning_survives_postprocessing(ad, fragments):
    result = finalize(ad,fragments)
    assert len(result['requirements']) == len(fragments)
    assert [(r['importance'],r['kind'],r['conditional']) for r in result['requirements']] == [(f[1],f[2],f[3]) for f in fragments]
    assert result['coverage_review']['added_passage_ids'] == []


@pytest.mark.parametrize('ad,fragments', [
 (49,[('Advanced level of Java','expected','qualification',False)]),
 (80,[('flerårig och relevant','expected','qualification',False)]),
 (149,[('Several years of backend','expected','qualification',False)]),
 (131,[('Vidare har du erfarenhet av riskarbete','expected','qualification',False)]),
])
def test_python_does_not_add_employer_sections(ad,fragments):
    # Both older and newer preserved corpora are regression inputs.
    paths=list((ROOT/'tests/fixtures').glob(f'*/{ad}.json'))
    f,src=load_fixture(paths[0]);ps=number_passages(src['description'])
    p=next(p for p in ps if fragments[0][0] in p.text)
    r=finalize_extraction(Extraction.model_validate({'requirements':[choice(p.id)]}),ps,src['title'])
    assert len(r['requirements'])==1
    assert r['requirements'][0]['quote']==p.text


def test_kind_controls_routing_not_heading_words():
    ps=number_passages('Krav\nDevelop and maintain Java services\nOffice three days a week')
    r=finalize_extraction(Extraction.model_validate({'requirements':[
        choice(ps[1].id,'expected','responsibility'),choice(ps[2].id,'mandatory','condition')]}),ps,'Job')
    assert len(r['role_responsibilities'])==len(r['requirements'])==1
    assert r['requirements'][0]['kind']=='condition'


def test_different_strength_or_condition_never_merge():
    ps=number_passages('English\nEnglish\nEnglish')
    data={'requirements':[choice('p0001','mandatory'),choice('p0002','preferred'),choice('p0003','mandatory',conditional=True)]}
    r=finalize_extraction(Extraction.model_validate(data),ps,'Job')
    assert len(r['requirements'])==3
