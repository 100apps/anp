"""真实服务候选登记；仅2秒无语音的合成视频，原文/译文均是标明的契约夹具。"""
import asyncio
from datetime import datetime,timezone
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import time
from uuid import uuid4

import httpx
import pytest

from anp.auth.identity import actor_from_token
from anp.sdk.http import async_client
from apps.deep_dub.services.projects import service as projects
from apps.deep_dub.services.execution import service as execution
from apps.deep_dub.services.versions import service as versions
from apps.deep_dub.services.artifacts import service as artifacts
from apps.deep_dub.services.candidates import service as candidates
from apps.deep_dub.services.observations import deliver_one
from tests.test_deep_dub_contracts_live import context,PROFILE,pytestmark


def test_source_transcript_and_translation_via_real_identity_and_same_service(context):
    ffmpeg,ffprobe=shutil.which('ffmpeg'),shutil.which('ffprobe')
    assert ffmpeg and ffprobe,'需要真实生成并探测合成验收视频'
    with tempfile.TemporaryDirectory(prefix='anp-candidate-test-') as folder:
        video=Path(folder)/'fixture.mp4'
        subprocess.run([ffmpeg,'-v','error','-f','lavfi','-i','color=c=gray:s=64x64:r=10:d=2',
            '-an','-c:v','mpeg4',str(video)],check=True,capture_output=True,timeout=20)
        probe=subprocess.run([ffprobe,'-v','error','-show_entries','format=duration','-of','json',str(video)],
                             check=True,capture_output=True,text=True,timeout=10)
        duration=round(float(json.loads(probe.stdout)['format']['duration'])*1000)
        assert duration==2000
        app,tokens=context

        async def run():
            actor=await actor_from_token(tokens['admin'])
            project=await projects.create(actor,{'name':'[契约验收] 无语音合成视频',
                'source_url':'https://example.invalid/anp-candidate-acceptance'},'candidate-contract-fixture-v1')
            quality=await versions.create_quality_profile(actor,PROFILE)
            queued=await execution.queue(actor,project['id'],{'quality_profile_id':quality['id'],
                'purpose':'acceptance','flow_revision':'candidate-contract-v1','expected_version':project['row_version']})
            try:
                await execution.start(actor,queued['id'])
                attempt=(await execution.begin_stage(actor,queued['id'],{'node':'acceptance_candidates','input_hash':'d'*64}))['attempt']['id']
                await execution.mark_stage(actor,attempt,'started');started=time.monotonic()
                original=await artifacts.upload(actor,attempt,video,'video/mp4',duration)
                source_body={'artifact_id':original['id'],'duration_ms':duration,
                    'identity_evidence':{'fixture_only':True,'generated_by':'ffmpeg lavfi color，实际无语音，原文/译文均为契约夹具'}}
                key=str(uuid4())
                async with async_client(transport=httpx.ASGITransport(app=app),base_url='http://acceptance',
                        headers={'Authorization':'Bearer '+tokens['admin']}) as client:
                    response=await client.post(f'/api/apps/deep_dub/attempts/{attempt}/source',json=source_body,headers={'Idempotency-Key':key})
                    assert response.status_code==201,response.text
                    source=response.json()
                    assert source==await candidates.source(actor,attempt,source_body,key)
                    transcript_body={'source_id':source['id'],'strategy_revision':'acceptance-cue-only-v1',
                        'cues':[{'cue_no':0,'speaker_key':'fixture-role','source_text':'Twelve samples remain.','start_ms':0,'end_ms':500},
                                {'cue_no':1,'speaker_key':'fixture-role','source_text':'Do not remove them.','start_ms':500,'end_ms':1000}],
                        'segments':[{'cue_numbers':[0,1]}]}
                    key=str(uuid4())
                    response=await client.post(f'/api/apps/deep_dub/attempts/{attempt}/transcript',json=transcript_body,headers={'Idempotency-Key':key})
                    assert response.status_code==201,response.text
                    parts=response.json();assert parts['sealed']
                    assert parts==await candidates.transcript(actor,attempt,transcript_body,key)
                    segments=await actor.objects.select('deep_dub_segments',workspace_id='eq.'+actor.workspace_id,segment_set_id='eq.'+parts['id'])
                    assert len(segments)==1
                    context_file=Path(folder)/'context.json'
                    context_file.write_text(json.dumps({'fixture_only':True,'source_text':segments[0]['source_text'],
                        'instruction':'仅测试候选登记，不是源语音识别或模型翻译'}),encoding='utf8')
                    context_artifact=await artifacts.upload(actor,attempt,context_file,'application/json')
                    translation_body={'segment_id':segments[0]['id'],'context_artifact_id':context_artifact['id'],
                        'chinese_text':'还剩十二份样本。不要移除它们。','model_id':'acceptance-manual-fixture','prompt_revision':'contract-v1'}
                    key=str(uuid4())
                    response=await client.post(f'/api/apps/deep_dub/attempts/{attempt}/translation',json=translation_body,headers={'Idempotency-Key':key})
                    assert response.status_code==201,response.text
                    translation=response.json()
                    assert translation==await candidates.translation(actor,attempt,translation_body,key)
                    denied=await client.post(f'/api/apps/deep_dub/attempts/{attempt}/translation',json=translation_body,
                        headers={'Authorization':'Bearer '+tokens['viewer']})
                    assert denied.status_code==403
                    audits=await actor.objects.select('deep_dub_audits',translation_id='eq.'+translation['id'])
                    assert audits==[]
                    invalid={**transcript_body,'segments':[{'cue_numbers':[0]}]}
                    assert (await client.post(f'/api/apps/deep_dub/attempts/{attempt}/transcript',json=invalid)).status_code==422
                    receipt=await execution.finish_stage(actor,attempt,{'status':'succeeded',
                        'outputs':[original['id'],context_artifact['id']],
                        'elapsed_seconds':time.monotonic()-started,'operation_stopped':True})
                    assert receipt['committed']
                for _ in range(20):
                    delivered=await deliver_one(actor)
                    if not delivered['claimed']:break
                    assert delivered['sent']
                Path('.local/deep-dub-candidates-live.json').write_bytes((json.dumps({
                    'checked_at_utc':datetime.now(timezone.utc).isoformat(),
                    'scope':'真实Supabase/RustFS+ASGI；2秒无语音合成视频及人工夹具原文/译文，不是生产或媒体质量验收',
                    'purpose':'acceptance','run_id':queued['id'],'source_id':source['id'],
                    'segment_set_id':parts['id'],'translation_id':translation['id'],
                    'video_duration_ms':duration,'source_replay_same':True,'transcript_replay_same':True,
                    'translation_replay_same':True,'viewer_write_forbidden':True,
                    'incomplete_transcript_rejected':True,'no_automatic_quality_audits':True},ensure_ascii=False,indent=2)+'\n').encode())
            finally:
                await execution.cancel(actor,queued['id'])
        asyncio.run(run())
