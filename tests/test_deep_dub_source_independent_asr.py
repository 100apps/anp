from copy import deepcopy
import wave
import pytest
from anp.errors import BusinessError
from apps.deep_dub.nodes.source_independent_asr import verify,RULE
from apps.deep_dub.nodes.transcribe import NEMOTRON_RULE
from apps.deep_dub.nodes.source_asr import windows
from apps.deep_dub.nodes.source_reference import compare_reference
from tests.test_deep_dub_source_alignment import recognition


@pytest.fixture
def data(tmp_path):
    pcm=tmp_path/'source.wav'
    with wave.open(str(pcm),'wb') as output:
        output.setnchannels(1);output.setsampwidth(2);output.setframerate(16000);output.writeframes(b'\0\0'*16000*62)
    primary=recognition(pcm,tmp_path)
    model={'family':'nemotron_asr','model_id':'independent-model','weights_sha256':'d'*64,'runtime_sha256':'e'*64,'source_revision':'f'*40}
    chunks=[]
    for index,(start,end) in enumerate(windows(primary['pcm']['sample_count'],30)):
        chunks.append({'index':index,'start_sample':start,'end_sample':end,'pcm_sha256':'a'*64,'normalized_pcm_sha256':'a'*64,
            'recognition':{**model,'rule_revision':NEMOTRON_RULE,'context_prompt':'','language_hint':'English','normalized_samples':end-start,
                'model_session_id':'second-model-session','operation_stopped':False,'text':'hello world'}})
    proof={'rule_revision':RULE,'pcm':primary['pcm'],'model':model,'chunks':chunks,
        'session':{'session_id':'second-model-session','initially_unloaded':True,'operation_stopped':True,'requests':3,'warm_cache_checks':2},
        'independent_recognizer':True,'independent_review':False,'source_text_modified':False,
        'semantic_review':'not_measured','speaker_review':'not_measured','subtitle_timing_review':'not_measured'}
    proof['primary_comparison']=compare_reference('\n'.join(c['recognition']['text'] for c in primary['chunks']),[{'text':c['recognition']['text']} for c in chunks])
    return primary,proof


def test_full_pcm_coverage_with_different_model_does_not_certify_content(data):
    primary,proof=data;before=deepcopy((primary,proof));result=verify(proof,primary)
    assert result['sample_coverage']==1 and result['chunk_count']==3 and result['primary_difference_count']==0
    assert (primary,proof)==before and proof['independent_review'] is False


@pytest.mark.parametrize('defect',['same_weights','same_family','prompt','missing_chunk','skip_samples','wrong_session','not_stopped','claimed_quality','changed_text'])
def test_independence_coverage_and_unmeasured_review_are_bound(data,defect):
    primary,proof=data
    if defect=='same_weights':proof['model']['weights_sha256']=primary['model']['weights_sha256']
    if defect=='same_family':proof['model']['family']='qwen3_asr'
    if defect=='prompt':proof['chunks'][0]['recognition']['context_prompt']='Original source words'
    if defect=='missing_chunk':proof['chunks'].pop()
    if defect=='skip_samples':proof['chunks'][1]['start_sample']+=100
    if defect=='wrong_session':proof['chunks'][1]['recognition']['model_session_id']='original-session'
    if defect=='not_stopped':proof['session']['operation_stopped']=False
    if defect=='claimed_quality':proof['semantic_review']='passed'
    if defect=='changed_text':proof['chunks'][0]['recognition']['text']='Different number 25'
    with pytest.raises(BusinessError):verify(proof,primary)
