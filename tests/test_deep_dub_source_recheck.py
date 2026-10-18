from copy import deepcopy
import wave
import pytest

from anp.errors import BusinessError
from apps.deep_dub.nodes.source_recheck import plan,check,RULE
from apps.deep_dub.nodes.transcribe import RULE as ASR_RULE
from apps.deep_dub.services.source_recheck import RecheckInput
from tests.test_deep_dub_source_alignment import recognition


@pytest.fixture
def asr(tmp_path):
    file=tmp_path/'source.wav'
    with wave.open(str(file),'wb') as output:
        output.setnchannels(1);output.setsampwidth(2);output.setframerate(16000);output.writeframes(b'\0\0'*16000*62)
    return recognition(file,tmp_path)


def test_plan_uses_pcm_boundaries_and_no_reference_text_prompt(asr):
    before=deepcopy(asr);windows=plan(asr)
    assert [w['boundary_index'] for w in windows]==[1,2]
    assert (windows[0]['start_sample'],windows[0]['end_sample'])==(24*16000,36*16000)
    assert windows[1]['end_sample']==62*16000
    assert plan(asr,[2,1])==windows
    assert asr==before


@pytest.mark.parametrize('boundaries',[[0],[True],[3],[1,1],[],[1.0],'1'])
def test_wrong_or_duplicate_boundary_cannot_reach_model(asr,boundaries):
    with pytest.raises(BusinessError):plan(asr,boundaries)


def proof(asr):
    model={'model_id':'fixture-new-asr','weights_sha256':'d'*64,'runtime_sha256':'e'*64,'source_revision':'f'*40}
    rows=[]
    for target in plan(asr):
        rows.append({**target,'pcm_sha256':'a'*64,'normalized_pcm_sha256':'a'*64,
            'recognition':{**model,'family':'qwen3_asr','rule_revision':ASR_RULE,'context_prompt':'',
                'normalized_samples':target['end_sample']-target['start_sample'],'language_hint':'English',
                'model_session_id':'new-session','operation_stopped':False,'text':'New recognition candidate.'}})
    return {'rule_revision':RULE,'pcm':asr['pcm'],'boundaries':[1,2],'windows':rows,'model':model,
        'session':{'operation_stopped':True,'initially_unloaded':True,'requests':2,'warm_cache_checks':1,'session_id':'new-session'},
        'source_text_modified':False,'independent_review':False,'semantic_review':'not_measured','subtitle_timing_review':'not_measured'}


@pytest.mark.parametrize('mutation',['prompt','window','old_context','samples','model','semantic_pass','session','not_stopped'])
def test_recheck_cannot_claim_independence_or_change_source_binding(asr,mutation):
    measured=proof(asr);assert check(measured,asr)['window_count']==2
    row=measured['windows'][0]
    if mutation=='prompt':row['recognition']['context_prompt']='Expected words'
    if mutation=='window':row['start_sample']+=1
    if mutation=='old_context':row['preceding_text']='Another transcript'
    if mutation=='samples':row['recognition']['normalized_samples']+=1
    if mutation=='model':row['recognition']['model_id']='other'
    if mutation=='semantic_pass':measured['semantic_review']='passed'
    if mutation=='session':row['recognition']['model_session_id']='old-session'
    if mutation=='not_stopped':measured['session']['operation_stopped']=False
    with pytest.raises(BusinessError):check(measured,asr)
