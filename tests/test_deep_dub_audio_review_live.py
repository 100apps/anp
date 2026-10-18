"""真实新WAV→独立ASR→字符差异；不签发语义/听感通过。"""
import asyncio
from datetime import datetime,timezone
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from uuid import uuid4

import pytest

from anp import ROOT
from anp.auth.identity import actor_from_token
from anp.auth.session import login,session_path
from anp.sdk.http import async_client
from apps.deep_dub.nodes.media import executable,probe_media
from apps.deep_dub.services.artifacts import service as artifacts
from apps.deep_dub.services.candidates import service as candidates
from apps.deep_dub.services.execution import service as execution
from apps.deep_dub.services.projects import service as projects
from apps.deep_dub.services.versions import service as versions
from apps.deep_dub.services.references import prepare
from apps.deep_dub.services.speech import generate
from apps.deep_dub.services.audio_review import review,read_latest,record_scores
from tests.test_deep_dub_contracts_live import PROFILE,pytestmark as base_mark
from tests.test_deep_dub_runtime_live import flush
from tests.test_deep_dub_waveform_live import waveform_api

pytestmark=[base_mark,pytest.mark.skipif(os.environ.get('ANP_DEEP_DUB_ASR_LIVE')!='1',reason='需显式启用真实GPU与ASR验收')]
REFERENCE=Path('D:/github/dub-bench-zh/dubbing-studio-an/work/tasks/task-a0d1ec97dbd1/task.json')


def test_actual_new_wav_backtranscription_private_evidence_and_three_entry_replay(identities,waveform_api,tmp_path,monkeypatch):
    if not REFERENCE.is_file():pytest.skip('需已有公开原片，不能使用夹具冒充真实识别')
    original=json.loads(REFERENCE.read_bytes());first=original['segments'][0]
    monkeypatch.setenv('ANP_PROFILE','asr-'+uuid4().hex);monkeypatch.setenv('PYTHONIOENCODING','utf-8')
    file=tmp_path/'source.mp4'
    subprocess.run([executable('ffmpeg'),'-v','error','-i',original['source']['path'],'-t','26','-map','0:v:0','-map','0:a:0','-c','copy',str(file)],check=True,capture_output=True,timeout=30)
    async def run():
        actor=await actor_from_token(identities['admin']['token'])
        project=await projects.create(actor,{'name':'[独立回转录验收] Yale26秒','source_url':'https://example.invalid/provided-source-backtranscription'})
        profile=await versions.create_quality_profile(actor,PROFILE)
        queued=await execution.queue(actor,project['id'],{'purpose':'acceptance','quality_profile_id':profile['id'],
            'flow_revision':'asr-review-acceptance-v1','expected_version':project['row_version']})
        await execution.start(actor,queued['id'])
        try:
            attempt=(await execution.begin_stage(actor,queued['id'],{'node':'provided_asr_source','input_hash':'8'*64}))['attempt']['id']
            await execution.mark_stage(actor,attempt,'started');started=time.monotonic()
            probe=await probe_media(file);media=await artifacts.upload(actor,attempt,file,'video/mp4',probe['duration_ms'])
            source=await candidates.source(actor,attempt,{'artifact_id':media['id'],'duration_ms':probe['duration_ms'],
                'identity_evidence':{'acceptance_only':True,'existing_public_source_clip':True,'independent_source_asr':False}})
            parts=await candidates.transcript(actor,attempt,{'source_id':source['id'],'strategy_revision':'provided-asr-reference-v1',
                'cues':[{'cue_no':0,'speaker_key':'speaker','source_text':first['sourceText'],'start_ms':780,'end_ms':25300}],
                'segments':[{'cue_numbers':[0]}]})
            segment=(await actor.objects.select('deep_dub_segments',segment_set_id='eq.'+parts['id']))[0]
            context=tmp_path/'context.json';context.write_bytes(json.dumps({'scope':'人工给定中文测试新WAV回转录，不是完整翻译通过'},ensure_ascii=False).encode())
            note=await artifacts.upload(actor,attempt,context,'application/json')
            translation=await candidates.translation(actor,attempt,{'segment_id':segment['id'],'context_artifact_id':note['id'],
                'chinese_text':'我这就开始。我们将讨论海明威、菲茨杰拉德与福克纳。','model_id':'human-provided-acceptance-text','prompt_revision':'provided-asr-text-v1'})
            await execution.finish_stage(actor,attempt,{'status':'succeeded','outputs':[media['id'],note['id']],
                'elapsed_seconds':time.monotonic()-started,'operation_stopped':True})
            for kind in ['voice','emotion']:
                assert (await prepare(actor,queued['id'],{'segment_id':segment['id'],'kind':kind},'asr-'+kind+'-'+uuid4().hex))['status']=='succeeded'
            voice=(await actor.objects.select('deep_dub_voice_references',source_id='eq.'+source['id']))[0]
            emotion=(await actor.objects.select('deep_dub_emotion_references',source_id='eq.'+source['id']))[0]
            generated=await generate(actor,queued['id'],{'segment_id':segment['id'],'translation_id':translation['id'],
                'voice_reference_id':voice['id'],'emotion_reference_id':emotion['id'],'seed':12345},'asr-new-wav-'+uuid4().hex)
            assert generated['status']=='succeeded'
            audio=(await actor.objects.select('deep_dub_audio_attempts',stage_attempt_id='eq.'+generated['attempt']['id']))[0]
            assert await read_latest(actor,project['id'],audio['id']) is None
            endpoint=waveform_api+f'/api/apps/deep_dub/runs/{queued["id"]}/review-audio'
            body={'audio_attempt_id':audio['id'],'timeout_seconds':300};key=uuid4().hex
            async with async_client(timeout=360,trust_env=False,headers={'Authorization':'Bearer '+identities['admin']['token']}) as client:
                assert (await client.post(endpoint,json=body,headers={'Authorization':'Bearer '+identities['viewer']['token']})).status_code==403
                assert (await client.post(endpoint,json=body,headers={'Authorization':''})).status_code==401
                assert (await client.post(endpoint,json=body,headers={'Authorization':'Bearer '+identities['outside']['token']})).status_code in (403,404)
                response=await client.post(endpoint,json=body,headers={'Idempotency-Key':key})
                if response.status_code!=200:Path('.local/deep-dub-asr-last-failure.json').write_bytes(response.content)
                assert response.status_code==200,'真实ASR失败，原始诊断保留私有日志'
                stage=response.json();assert stage['status']=='succeeded'
                get=waveform_api+f'/api/apps/deep_dub/projects/{project["id"]}/audio/{audio["id"]}/backtranscription'
                view=await client.get(get,headers={'Authorization':'Bearer '+identities['viewer']['token']});assert view.status_code==200
                result=view.json();assert result['audio_attempt_id']==audio['id'] and result['recognized_text'] and result['semantic_review']=='not_measured'
                assert (await client.get(get,headers={'Authorization':''})).status_code==401
                assert (await client.get(get,headers={'Authorization':'Bearer '+identities['outside']['token']})).status_code in (403,404)
            assert stage==await review(actor,queued['id'],body,key)
            await record_scores(actor,stage['attempt'])
            evidence=tmp_path/'asr-proof.json';await artifacts.download(actor,stage['attempt']['outputs'][0],evidence)
            proof=json.loads(evidence.read_bytes());assert proof['recognition']['context_prompt']==''
            assert proof['input']['audio_attempt_id']==audio['id'] and proof['recognition']['operation_stopped'] is True
            scores=await actor.objects.select('deep_dub_scores',stage_attempt_id='eq.'+stage['attempt']['id'])
            assert len(scores)==2
            measured=next(s for s in scores if s['metric']=='tts_asr_character_error_rate')
            assert measured['value']==result['character_error_rate'] and measured['evidence_artifact_id']==stage['attempt']['outputs'][0]
            assert next(s for s in scores if s['measurement_status']=='not_measured')['value'] is None
            assert not await actor.objects.select('deep_dub_audits',project_id='eq.'+project['id'])
            leases=await actor.objects.select('deep_dub_device_leases',select='id,status,operation_stopped',stage_attempt_id='eq.'+stage['attempt']['id'])
            assert len(leases)==1 and leases[0]['status']=='released' and leases[0]['operation_stopped'] is True
            await login(identities['admin']['email'],identities['admin']['password'])
            def cli(*options):
                p=subprocess.run([sys.executable,'-m','anp.cli.entry',*options,'deep_dub','runs','review-audio',queued['id'],audio['id'],'--request-key',key],
                    cwd=ROOT,capture_output=True,text=True,encoding='utf-8',timeout=60)
                assert p.returncode==0,'ASR CLI重放失败，响应不公开';return json.loads(p.stdout)
            assert await asyncio.to_thread(cli,'--endpoint','http://127.0.0.1:1')==stage
            assert await asyncio.to_thread(cli,'--transport','http','--endpoint',waveform_api)==stage
            alignment=None
            if os.environ.get('ANP_DEEP_DUB_ALIGN_LIVE')=='1':
                from tests.deep_dub_alignment_live_checks import verify_alignment
                alignment=await verify_alignment(actor,queued,audio,waveform_api,identities,tmp_path)
            await flush(actor)
            return {'project_id':project['id'],'run_id':queued['id'],'new_audio_attempt_id':audio['id'],'asr_stage_attempt_id':stage['attempt']['id'],
                'evidence_artifact_id':stage['attempt']['outputs'][0],'readback':result,'recognition':proof['recognition'],'comparison':proof['comparison'],
                'same_stage_three_entry_replay':True,'viewer_read_only':True,'anonymous_outside_denied':True,'no_quality_audit':True,'lease_released':True,
                'alignment':alignment}
        finally:await execution.cancel(actor,queued['id']);await flush(actor)
    try:
        record=asyncio.run(run());record.update({'checked_at_utc':datetime.now(timezone.utc).isoformat(),'run_cancelled':True,'purpose':'acceptance'})
        Path('.local/deep-dub-asr-live.json').write_bytes((json.dumps(record,ensure_ascii=False,indent=2)+'\n').encode())
    finally:session_path().unlink(missing_ok=True)
