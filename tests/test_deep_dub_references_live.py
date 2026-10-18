"""实际公开原片参考提取与RustFS/CLI；无TTS或身份听审通过。"""
import asyncio
from datetime import datetime,timezone
import json
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
from apps.deep_dub.services.waveforms import prepare as waveform
from apps.deep_dub.services.references import prepare
from tests.test_deep_dub_contracts_live import PROFILE,pytestmark
from tests.test_deep_dub_runtime_live import flush
from tests.test_deep_dub_waveform_live import waveform_api

REFERENCE=Path('D:/github/dub-bench-zh/dubbing-studio-an/work/tasks/task-a0d1ec97dbd1/task.json')


def test_real_voice_emotion_references_private_playback_and_cli(identities,waveform_api,tmp_path,monkeypatch):
    if not REFERENCE.is_file():pytest.skip('需用户公开原片，不用正弦音替代真实参考')
    original=json.loads(REFERENCE.read_text(encoding='utf-8'));first=original['segments'][0]
    monkeypatch.setenv('ANP_PROFILE','references-'+uuid4().hex);monkeypatch.setenv('PYTHONIOENCODING','utf-8')
    file=tmp_path/'source-26s.mp4'
    subprocess.run([executable('ffmpeg'),'-v','error','-i',original['source']['path'],'-t','26','-map','0:v:0','-map','0:a:0','-c','copy',str(file)],check=True,capture_output=True,timeout=30)
    async def run():
        actor=await actor_from_token(identities['admin']['token'])
        project=await projects.create(actor,{'name':'[原声参考验收] Yale26秒','source_url':'https://example.invalid/provided-public-source-reference'})
        profile=await versions.create_quality_profile(actor,PROFILE)
        queued=await execution.queue(actor,project['id'],{'purpose':'acceptance','quality_profile_id':profile['id'],
            'flow_revision':'reference-preparation-v1','expected_version':project['row_version']})
        await execution.start(actor,queued['id'])
        try:
            attempt=(await execution.begin_stage(actor,queued['id'],{'node':'acceptance_reference_source','input_hash':'b'*64}))['attempt']['id']
            await execution.mark_stage(actor,attempt,'started');started=time.monotonic()
            probe=await probe_media(file);media=await artifacts.upload(actor,attempt,file,'video/mp4',probe['duration_ms'])
            source=await candidates.source(actor,attempt,{'artifact_id':media['id'],'duration_ms':probe['duration_ms'],
                'identity_evidence':{'acceptance_only':True,'existing_public_source_clip':True,'independent_asr':False}})
            parts=await candidates.transcript(actor,attempt,{'source_id':source['id'],'strategy_revision':'provided-reference-text-v1',
                'cues':[{'cue_no':0,'speaker_key':'speaker','source_text':first['sourceText'],'start_ms':780,'end_ms':25300}],
                'segments':[{'cue_numbers':[0]}]})
            target=(await actor.objects.select('deep_dub_segments',segment_set_id='eq.'+parts['id']))[0]
            await execution.finish_stage(actor,attempt,{'status':'succeeded','outputs':[media['id']],
                'elapsed_seconds':time.monotonic()-started,'operation_stopped':True})
            endpoint=waveform_api+f'/api/apps/deep_dub/runs/{queued["id"]}/prepare-reference'
            body={'segment_id':target['id'],'kind':'voice','voice_start_ms':780,'voice_end_ms':10780,'filter_mode':'speech-band'}
            key=str(uuid4())
            async with async_client(timeout=90,trust_env=False,headers={'Authorization':'Bearer '+identities['admin']['token']}) as client:
                assert (await client.post(endpoint,json=body,headers={'Authorization':'Bearer '+identities['viewer']['token']})).status_code==403
                assert (await client.post(endpoint,json=body,headers={'Authorization':''})).status_code==401
                assert (await client.post(endpoint,json=body,headers={'Authorization':'Bearer '+identities['outside']['token']})).status_code in (403,404)
                assert (await client.post(endpoint,json={**body,'voice_end_ms':99999})).status_code==422
                response=await client.post(endpoint,json=body,headers={'Idempotency-Key':key});assert response.status_code==200
                voice_stage=response.json();assert voice_stage['status']=='succeeded' and len(voice_stage['attempt']['outputs'])==3
                assert voice_stage==await prepare(actor,queued['id'],body,key)
                emotion=await client.post(endpoint,json={'segment_id':target['id'],'kind':'emotion'},headers={'Idempotency-Key':'emotion-'+key})
                assert emotion.status_code==200 and emotion.json()['status']=='succeeded'
                assert len(emotion.json()['attempt']['outputs'])==2  # raw共用一个音频artifact，不重复输出。
                invalid=await client.post(endpoint,json={'segment_id':target['id'],'kind':'emotion','voice_start_ms':780})
                assert invalid.status_code==422
            voice=(await actor.objects.select('deep_dub_voice_references',source_id='eq.'+source['id']))[0]
            emotion=(await actor.objects.select('deep_dub_emotion_references',source_id='eq.'+source['id']))[0]
            assert voice['start_ms']==780 and voice['end_ms']==10780 and voice['original_artifact_id']!=voice['processed_artifact_id']
            assert voice['processing']['original_metrics']['sample_count']==voice['processing']['processed_metrics']['sample_count']==240000
            assert emotion['start_ms']==780 and emotion['end_ms']==25300 and emotion['original_artifact_id']==emotion['artifact_id']
            proof=tmp_path/'reference.json';await artifacts.download(actor,voice['processing']['evidence_artifact_id'],proof)
            details=json.loads(proof.read_bytes());assert details['original']['sample_count']==details['processed']['sample_count']==240000
            assert details['original']['speaker_identity']=='not_measured' and details['processed']['listening_quality']=='not_measured'
            assert not await actor.objects.select('deep_dub_audits',project_id='eq.'+project['id'])
            assert not await actor.objects.select('deep_dub_audio_attempts',project_id='eq.'+project['id'])
            for artifact in [voice['original_artifact_id'],voice['processed_artifact_id'],emotion['original_artifact_id']]:
                prepared=await waveform(actor,queued['id'],{'artifact_id':artifact},'reference-wave:'+artifact)
                assert prepared['status']=='succeeded'
            await login(identities['admin']['email'],identities['admin']['password'])
            def cli(*options):
                result=subprocess.run([sys.executable,'-m','anp.cli.entry',*options,'deep_dub','runs','prepare-reference',queued['id'],target['id'],'voice',
                    '--voice-start-ms','780','--voice-end-ms','10780','--filter-mode','speech-band','--request-key',key],cwd=ROOT,capture_output=True,text=True,encoding='utf-8',timeout=90)
                assert result.returncode==0,'参考CLI重放失败，响应不公开';return json.loads(result.stdout)
            assert await asyncio.to_thread(cli,'--endpoint','http://127.0.0.1:1')==voice_stage
            assert await asyncio.to_thread(cli,'--transport','http','--endpoint',waveform_api)==voice_stage
            return {'project_id':project['id'],'run_id':queued['id'],'segment_id':target['id'],'source_id':source['id'],
                'voice_reference_id':voice['id'],'voice_original_artifact_id':voice['original_artifact_id'],'voice_processed_artifact_id':voice['processed_artifact_id'],
                'voice_evidence_artifact_id':voice['processing']['evidence_artifact_id'],'emotion_reference_id':emotion['id'],
                'emotion_artifact_id':emotion['artifact_id'],'voice_window_ms':[780,10780],'emotion_window_ms':[780,25300],
                'voice_stats':details['original'],'processed_stats':details['processed'],'same_stage_replay':True,'local_and_http_cli_same':True,
                'viewer_anonymous_foreign_denied':True,'references_waveforms_prepared':True,'no_audit_or_tts':True}
        finally:await execution.cancel(actor,queued['id']);await flush(actor)
    try:
        record=asyncio.run(run());record.update({'checked_at_utc':datetime.now(timezone.utc).isoformat(),'purpose':'acceptance','run_cancelled':True})
        Path('.local/deep-dub-reference-live.json').write_bytes((json.dumps(record,ensure_ascii=False,indent=2)+'\n').encode())
    finally:session_path().unlink(missing_ok=True)
