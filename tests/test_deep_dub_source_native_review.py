from copy import deepcopy
import asyncio
from types import SimpleNamespace
import pytest
from anp.errors import BusinessError
from apps.deep_dub.nodes.source_native_review import extend_native_queue
from apps.deep_dub.nodes.source_cross_review import extend_queue
from tests.test_deep_dub_source_cross_review import sample


def native_sample():
    packet,primary,secondary=sample();proof=secondary['measurement']
    proof['model']={'family':'parakeet_tdt'}
    for chunk in proof['chunks']:
        words=chunk['recognition']['text'].split()
        chunk['recognition']['words']=[{'word':word,'start_sample':i*5000,'end_sample':i*5000+4000,'confidence':0} for i,word in enumerate(words)]
    return packet,primary,secondary


def test_native_audition_narrows_numeric_difference_and_keeps_all_old_items():
    packet,primary,proof=native_sample();before=deepcopy((packet,primary,proof))
    result=extend_native_queue(packet,primary,proof,'parakeet')
    rows=[r for r in result['items'] if r['kind']=='parakeet_difference']
    assert len(rows)==3 and {r['difference_operation'] for r in rows}=={'insert','delete','replace'}
    number=next(r for r in rows if r['contains_number'])
    assert number['audition']['precision']=='native-word-candidate'
    assert number['audition']['end_sample']<number['fallback_audition']['end_sample']
    assert all(0<=r['audition']['start_sample']<r['audition']['end_sample']<=primary['pcm']['sample_count'] for r in rows)
    assert any(r['native_anchor_mode']=='adjacent-insertion-deletion' for r in rows)
    assert {r['id'] for r in packet['items']}<={r['id'] for r in result['items']}
    assert all(r['review_status']=='not_measured' for r in result['items'])
    assert (packet,primary,proof)==before
    assert extend_native_queue(packet,primary,proof,'parakeet')==result
    assert extend_native_queue(packet,primary,proof,'another')['packet_sha256']!=result['packet_sha256']


def test_character_equal_but_word_token_boundaries_differ_fall_back_honestly():
    packet,primary,proof=native_sample();chunk=proof['measurement']['chunks'][0]
    first=chunk['recognition']['words'].pop(0)
    chunk['recognition']['words'][:0]=[{**first,'word':'Doc','end_sample':1000},
        {**first,'word':'torow','start_sample':1000}]
    result=extend_native_queue(packet,primary,proof,'parakeet')
    number=next(r for r in result['items'] if r['kind']=='parakeet_difference' and r['contains_number'])
    assert number['audition']==number['fallback_audition']
    assert number['audition']['precision']=='cross-model-chunk-fallback'


def test_parakeet_extension_does_not_replace_existing_nemotron_binding():
    packet,primary,secondary=sample();old=extend_queue(packet,primary,secondary,'nemotron')
    _,_,native=native_sample();result=extend_native_queue(old,primary,native,'parakeet')
    assert result['binding']['secondary_evidence_artifact_id']=='nemotron'
    assert result['binding']['parakeet_evidence_artifact_id']=='parakeet'
    assert {r['id'] for r in old['items']}<={r['id'] for r in result['items']}


@pytest.mark.parametrize('defect',['family','time','source','pcm'])
def test_native_queue_cannot_mix_families_sources_or_bad_times(defect):
    packet,primary,proof=native_sample()
    if defect=='family':proof['measurement']['model']['family']='nemotron_asr'
    if defect=='time':proof['measurement']['chunks'][0]['recognition']['words'][0]['start_sample']=-1
    if defect=='source':proof['input']['source_id']='another'
    if defect=='pcm':proof['measurement']['pcm']={**primary['pcm'],'pcm_sha256':'different'}
    with pytest.raises(BusinessError):extend_native_queue(packet,primary,proof,'parakeet')


def test_native_evidence_is_opt_in_before_any_upstream_read():
    from apps.deep_dub.services.source_review import read
    async def require(permission):assert permission=='deep_dub.project.read'
    async def run():
        for params in ({'kind':'parakeet_difference'},{'include_parakeet':'true'}):
            with pytest.raises(BusinessError):await read(SimpleNamespace(require=require),
                '00000000-0000-4000-8000-000000000001','00000000-0000-4000-8000-000000000002',**params)
    asyncio.run(run())
