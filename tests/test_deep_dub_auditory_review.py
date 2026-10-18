import asyncio
from copy import deepcopy
import json
import sys
import wave
from hashlib import sha256

import pytest

from anp.errors import BusinessError
from apps.deep_dub.nodes.auditory_review import CONTROLS,SPEECH_SCRIPT,RULE,calibrated,calibration_summary,verdict,verify
from apps.deep_dub.nodes.media import _run
from apps.deep_dub.nodes.source_asr import pcm_info
from apps.deep_dub.services.local_pool import LocalCpuPool
from apps.deep_dub.services.blocking import file_identity
from tests.test_deep_dub_source_alignment import recognition


def controls():
    rows=[]
    for index,(spoken,candidate,status,kind) in enumerate(CONTROLS):
        rows.append({'control_index':index,'spoken_text':spoken,'candidate':candidate,'result':{
            'audio':{'sha256':str(index)*64,'bytes':1000},
            'verdict':{'window_id':str(index),'status':status,'reason':'fixture only',
                'issues':[] if kind is None else [{'kind':kind,'candidate_excerpt':candidate,'heard_excerpt':spoken,'reason':'fixture only'}]}}})
    return rows


def test_calibration_requires_counterfactual_audio_and_both_number_and_negation_detection():
    rows=controls();assert calibrated(rows)
    for defect in ('always_matches','uncertain','same_audio','changed_gold','missing','duplicate','wrong_heard','boolean_id'):
        broken=deepcopy(rows)
        if defect=='always_matches':
            for row in broken:row['result']['verdict'].update(status='matched',issues=[])
        if defect=='uncertain':broken[1]['result']['verdict']['status']='uncertain'
        if defect=='same_audio':broken[1]['result']['audio']['sha256']=broken[0]['result']['audio']['sha256']
        if defect=='changed_gold':broken[1]['spoken_text']='There are twelve samples.'
        if defect=='missing':broken.pop()
        if defect=='duplicate':broken[1]['control_index']=0
        if defect=='wrong_heard':broken[1]['result']['verdict']['issues'][0]['heard_excerpt']='There are twenty samples.'
        if defect=='boolean_id':broken[1]['control_index']=True
        assert not calibrated(broken),defect


def test_calibration_does_not_confuse_taxonomy_with_correct_hearing_or_hide_wrong_numbers():
    rows=controls()
    for row in rows:
        for issue in row['result']['verdict']['issues']:issue['kind']='meaning'
    assert calibrated(rows)
    assert calibration_summary(rows)=={'decision_correct':10,'decision_total':10,'critical_excerpt_correct':5,
        'critical_excerpt_total':5,'taxonomy_correct':0,'taxonomy_total':5,'taxonomy_ratio':0.0}
    for index,text in ((1,'There are 20 samples.'),(3,'Do not remove the samples.'),(5,'The meeting starts at 6.'),
                       (7,'The valve is not open.'),(9,'The package is for Eve.')):
        broken=deepcopy(rows);broken[index]['result']['verdict']['issues'][0]['heard_excerpt']=text
        assert not calibrated(broken)


def test_verdict_requires_bound_id_model_complete_output_and_consistent_status():
    data={'model':'fixture','choices':[{'finish_reason':'stop','message':{'content':json.dumps(controls()[1]['result']['verdict'])}}]}
    assert verdict(data,'1','fixture')['status']=='mismatch'
    for defect in ('wrong_id','wrong_model','length','bad_status','extra'):
        bad=deepcopy(data)
        if defect=='wrong_id':expected='other'
        else:expected='1'
        if defect=='wrong_model':bad['model']='other'
        if defect=='length':bad['choices'][0]['finish_reason']='length'
        if defect in ('bad_status','extra'):
            body=json.loads(bad['choices'][0]['message']['content'])
            if defect=='bad_status':body['status']='matched'
            else:body['run_shell_command']='do not execute'
            bad['choices'][0]['message']['content']=json.dumps(body)
        with pytest.raises(BusinessError):verdict(bad,expected,'fixture')


def test_evidence_rejects_altered_audio_candidate_coverage_and_invented_quality(tmp_path):
    source=tmp_path/'source.wav'
    with wave.open(str(source),'wb') as output:
        output.setnchannels(1);output.setsampwidth(2);output.setframerate(16000);output.writeframes(b'\0\0'*32000)
    primary=recognition(source,tmp_path);chunk=primary['chunks'][0]
    chunk['recognition']['input']=file_identity(tmp_path/'asr-chunk-0.wav')
    candidate=chunk['recognition']['text']
    measured={'rule_revision':RULE,'pcm':primary['pcm'],'controls':controls(),'calibration_passed':True,
        'calibration_summary':calibration_summary(controls()),
        'speaker_identity':'not_measured','subtitle_timing_review':'not_measured','formal_transcript_review':'not_measured',
        'model':{'family':'voxtral'},'session':{'operation_stopped':True,'requests':1+len(CONTROLS),'properties':{'modalities':{'audio':True}}},
        'windows':[{**{key:chunk[key] for key in ('index','start_sample','end_sample','pcm_sha256')},'candidate_text':candidate,
            'result':{'audio':chunk['recognition']['input'],'verdict':{'window_id':'source-0000-'+sha256(candidate.encode()).hexdigest()[:12],
                'status':'matched','reason':'fixture only','issues':[]}}}], 'counts':{'matched':1,'mismatch':0,'uncertain':0}}
    assert verify(measured,primary)==measured['counts']
    for defect in ('audio','candidate','coverage','identity','quality','requests','counts','calibration','window'):
        bad=deepcopy(measured)
        if defect=='audio':bad['windows'][0]['result']['audio']['sha256']='0'*64
        if defect=='candidate':bad['windows'][0]['candidate_text']='changed'
        if defect=='coverage':bad['windows'][0]['end_sample']-=1
        if defect=='identity':bad['model']['family']='other'
        if defect=='quality':bad['formal_transcript_review']='passed'
        if defect=='requests':bad['session']['requests']=4
        if defect=='counts':bad['counts']['matched']=2
        if defect=='calibration':bad['controls'][1]['result']['verdict']['status']='matched'
        if defect=='window':bad['windows'][0]['result']['verdict']['window_id']='other'
        with pytest.raises(BusinessError):verify(bad,primary)


@pytest.mark.skipif(sys.platform!='win32',reason='校准声音使用已有Windows英语语音，不安装或修改系统设置')
def test_real_system_voice_controls_are_distinct_complete_pcm_and_process_exits(tmp_path):
    script=tmp_path/'controls.ps1';script.write_bytes(SPEECH_SCRIPT.encode('utf-8-sig'))
    async def scenario():
        with LocalCpuPool('auditory-controls-fixture',tmp_path/'pool') as pool:
            await _run(['powershell.exe','-NoProfile','-NonInteractive','-File',str(script),'-OutputDir',str(tmp_path)],60)
            samples=[pcm_info(tmp_path/f'control-{i}.wav') for i in range(len(CONTROLS))]
            assert all(row['sample_count']>16000 for row in samples)
            assert len({row['pcm_sha256'] for row in samples})==len(CONTROLS)
            assert pool.stopped() and len(pool.data['jobs'])==1
            assert (tmp_path/'voice.txt').read_text(encoding='utf-8-sig').strip()
    asyncio.run(scenario())
