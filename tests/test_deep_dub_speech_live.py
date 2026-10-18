"""显式开启的本机真实GPU生成；acceptance，非整片性能、语义或听感通过。"""
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
from apps.deep_dub.services.model_pool import configuration
from tests.test_deep_dub_contracts_live import PROFILE,pytestmark as base_marks
from tests.test_deep_dub_runtime_live import flush
from tests.test_deep_dub_waveform_live import waveform_api

pytestmark=[base_marks,pytest.mark.skipif(os.environ.get('ANP_DEEP_DUB_GPU_LIVE')!='1',reason='需显式启用本机真实GPU验收')]
REFERENCE=Path('D:/github/dub-bench-zh/dubbing-studio-an/work/tasks/task-a0d1ec97dbd1/task.json')


@pytest.mark.parametrize('mode',['complete','cancel'])
def test_real_owned_gpu_speech_receipt_permissions_and_cli(identities,waveform_api,tmp_path,monkeypatch,mode):
    if not REFERENCE.is_file():pytest.skip('需既有公开原片，不用夹具冒充真实声音')
    original=json.loads(REFERENCE.read_text(encoding='utf-8'));first=original['segments'][0]
    monkeypatch.setenv('ANP_PROFILE','speech-'+uuid4().hex);monkeypatch.setenv('PYTHONIOENCODING','utf-8')
    file=tmp_path/'source-26s.mp4'
    subprocess.run([executable('ffmpeg'),'-v','error','-i',original['source']['path'],'-t','26','-map','0:v:0','-map','0:a:0','-c','copy',str(file)],check=True,capture_output=True,timeout=30)
    async def run():
        actor=await actor_from_token(identities['admin']['token'])
        project=await projects.create(actor,{'name':'[真实GPU配音验收] Yale26秒','source_url':'https://example.invalid/provided-public-source-speech'})
        profile=await versions.create_quality_profile(actor,PROFILE)
        queued=await execution.queue(actor,project['id'],{'purpose':'acceptance','quality_profile_id':profile['id'],
            'flow_revision':'owned-gpu-speech-v1','expected_version':project['row_version']})
        await execution.start(actor,queued['id']);pending=None
        try:
            attempt=(await execution.begin_stage(actor,queued['id'],{'node':'acceptance_speech_source','input_hash':'d'*64}))['attempt']['id']
            await execution.mark_stage(actor,attempt,'started');started=time.monotonic()
            probe=await probe_media(file);media=await artifacts.upload(actor,attempt,file,'video/mp4',probe['duration_ms'])
            source=await candidates.source(actor,attempt,{'artifact_id':media['id'],'duration_ms':probe['duration_ms'],
                'identity_evidence':{'acceptance_only':True,'existing_public_source_clip':True,'independent_asr':False}})
            parts=await candidates.transcript(actor,attempt,{'source_id':source['id'],'strategy_revision':'provided-speech-text-v1',
                'cues':[{'cue_no':0,'speaker_key':'speaker','source_text':first['sourceText'],'start_ms':780,'end_ms':25300}],
                'segments':[{'cue_numbers':[0]}]})
            segment=(await actor.objects.select('deep_dub_segments',segment_set_id='eq.'+parts['id']))[0]
            context=tmp_path/'provided-context.json';context.write_bytes(json.dumps({'scope':'人工给定配音验收文本，不冒充独立翻译通过','source_text':first['sourceText']},ensure_ascii=False).encode())
            note=await artifacts.upload(actor,attempt,context,'application/json')
            chinese='我这就开始。我们将讨论海明威、菲茨杰拉德与福克纳。' if mode=='complete' else '这是一条用于受控取消的配音文本，不代表译文内容验收。'*20
            translation=await candidates.translation(actor,attempt,{'segment_id':segment['id'],'context_artifact_id':note['id'],
                'chinese_text':chinese,'model_id':'human-provided-acceptance-text',
                'prompt_revision':'provided-speech-text-v1'})
            await execution.finish_stage(actor,attempt,{'status':'succeeded','outputs':[media['id'],note['id']],
                'elapsed_seconds':time.monotonic()-started,'operation_stopped':True})
            for kind in ['voice','emotion']:
                assert (await prepare(actor,queued['id'],{'segment_id':segment['id'],'kind':kind},kind+'-'+uuid4().hex))['status']=='succeeded'
            voice=(await actor.objects.select('deep_dub_voice_references',source_id='eq.'+source['id']))[0]
            emotion=(await actor.objects.select('deep_dub_emotion_references',source_id='eq.'+source['id']))[0]
            body={'segment_id':segment['id'],'translation_id':translation['id'],'voice_reference_id':voice['id'],
                'emotion_reference_id':emotion['id'],'generation_factor':1.,'timeout_seconds':300}
            endpoint=waveform_api+f'/api/apps/deep_dub/runs/{queued["id"]}/speech';key=uuid4().hex
            async with async_client(timeout=360,trust_env=False,headers={'Authorization':'Bearer '+identities['admin']['token']}) as client:
                assert (await client.post(endpoint,json=body,headers={'Authorization':'Bearer '+identities['viewer']['token']})).status_code==403
                assert (await client.post(endpoint,json=body,headers={'Authorization':''})).status_code==401
                assert (await client.post(endpoint,json=body,headers={'Authorization':'Bearer '+identities['outside']['token']})).status_code in (403,404)
                if mode=='cancel':
                    operation=asyncio.create_task(client.post(endpoint,json=body,headers={'Idempotency-Key':key}))
                    pending=operation
                    deadline=time.monotonic()+60;loaded=False
                    async with async_client(timeout=2,trust_env=False) as native:
                        while time.monotonic()<deadline:
                            if operation.done():
                                finished=await operation
                                raise AssertionError('取消前配音已结束，未验证真实推理取消：'+str(finished.status_code))
                            try:
                                models=await native.get(f'http://127.0.0.1:{configuration().port}/v1/models')
                                loaded=models.status_code==200 and models.json()['data'][0]['loaded'] is True
                                if loaded:break
                            except Exception:pass
                            await asyncio.sleep(.1)
                    assert loaded,'未观察到本次模型实际加载'
                    await execution.cancel(actor,queued['id'])
                    cancelled=await asyncio.wait_for(operation,30)
                    assert cancelled.status_code!=200
                    stages=await actor.objects.select('deep_dub_stages',run_id='eq.'+queued['id'],node='eq.generate_audio')
                    assert len(stages)==1
                    attempts=await actor.objects.select('deep_dub_stage_attempts',stage_id='eq.'+stages[0]['id'])
                    assert len(attempts)==1 and attempts[0]['status']=='cancelled'
                    leases=await actor.objects.select('deep_dub_device_leases',select='id,status,operation_stopped',stage_attempt_id='eq.'+attempts[0]['id'])
                    assert len(leases)==1 and leases[0]['status']=='released' and leases[0]['operation_stopped'] is True
                    assert not await actor.objects.select('deep_dub_audio_attempts',project_id='eq.'+project['id'])
                    return {'project_id':project['id'],'run_id':queued['id'],'stage_attempt_id':attempts[0]['id'],
                        'loaded_before_cancel':True,'lease_released_after_owned_process_stop':True,'no_audio_candidate':True,
                        'scope':'acceptance，真实CUDA推理取消；非生产完成'}
                response=await client.post(endpoint,json=body,headers={'Idempotency-Key':key})
                if response.status_code!=200:
                    Path('.local/deep-dub-speech-last-failure.json').write_bytes(response.content)
                assert response.status_code==200, '真实GPU配音失败，响应不公开；检查私有模型日志'
                stage=response.json();assert stage['status']=='succeeded'
            assert stage==await generate(actor,queued['id'],body,key)
            audio=(await actor.objects.select('deep_dub_audio_attempts',stage_attempt_id='eq.'+stage['attempt']['id']))[0]
            proof=tmp_path/'tts-evidence.json';await artifacts.download(actor,audio['evidence_artifact_id'],proof)
            details=json.loads(proof.read_bytes());assert details['operation_stopped'] is True
            assert details['audio']['sample_count']>0 and details['semantic_consistency']=='not_measured'
            assert not await actor.objects.select('deep_dub_audits',project_id='eq.'+project['id'])
            leases=await actor.objects.select('deep_dub_device_leases',select='id,status,operation_stopped',stage_attempt_id='eq.'+stage['attempt']['id'])
            assert len(leases)==1 and leases[0]['status']=='released'
            await login(identities['admin']['email'],identities['admin']['password'])
            def cli(*options):
                result=subprocess.run([sys.executable,'-m','anp.cli.entry',*options,'deep_dub','runs','speech',queued['id'],segment['id'],
                    translation['id'],voice['id'],emotion['id'],'--request-key',key],cwd=ROOT,capture_output=True,text=True,encoding='utf-8',timeout=60)
                assert result.returncode==0,'配音CLI重放失败，响应不公开';return json.loads(result.stdout)
            assert await asyncio.to_thread(cli,'--endpoint','http://127.0.0.1:1')==stage
            assert await asyncio.to_thread(cli,'--transport','http','--endpoint',waveform_api)==stage
            await flush(actor)
            return {'project_id':project['id'],'run_id':queued['id'],'stage_attempt_id':stage['attempt']['id'],
                'audio_attempt_id':audio['id'],'audio_artifact_id':audio['artifact_id'],'evidence_artifact_id':audio['evidence_artifact_id'],
                'audio_metrics':details['audio'],'startup_seconds':details['startup_seconds'],
                'request_with_cold_load_seconds':details['request_with_cold_load_seconds'],'process_lifecycle_seconds':details['process_lifecycle_seconds'],
                'model_parameters':details['parameters'],'owned_server_stopped':True,'lease_released':True,'same_stage_cli_replay':True,
                'content_identity_naturalness':'not_measured','scope':'acceptance，既有原文/人工中文新生成；不是整片生产或RTF'}
        finally:
            await execution.cancel(actor,queued['id'])
            if pending is not None and not pending.done():
                await asyncio.wait_for(pending,30)
            await flush(actor)
    try:
        record=asyncio.run(run());record.update({'checked_at_utc':datetime.now(timezone.utc).isoformat(),'run_cancelled':True})
        Path('.local/deep-dub-speech-live'+('-cancel' if mode=='cancel' else '')+'.json').write_bytes((json.dumps(record,ensure_ascii=False,indent=2)+'\n').encode())
    finally:session_path().unlink(missing_ok=True)
