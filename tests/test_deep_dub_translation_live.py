"""真实auto与公开原片新截段；只登记候选，不把人工原文或模型生成作为独立审核。"""
import asyncio
from datetime import datetime,timezone
from hashlib import sha256
import json
from pathlib import Path
import subprocess
import sys
import time
from uuid import uuid4

import httpx
import pytest

from anp import ROOT
from anp.auth.identity import actor_from_token
from anp.auth.session import login,session_path
from anp.config import secret,value
from anp.errors import BusinessError
from anp.sdk.http import async_client
from apps.deep_dub.nodes.media import executable,probe_media
from apps.deep_dub.services.artifacts import service as artifacts
from apps.deep_dub.services.candidates import service as candidates
from apps.deep_dub.services.execution import service as execution
from apps.deep_dub.services.projects import service as projects
from apps.deep_dub.services.versions import service as versions
from apps.deep_dub.services.translation import generate,scores as record_scores,PROMPT_REVISION,EVALUATOR_REVISION
from tests.test_deep_dub_contracts_live import PROFILE,pytestmark
from tests.test_deep_dub_runtime_live import flush
from tests.test_deep_dub_waveform_live import waveform_api

REFERENCE=Path('D:/github/dub-bench-zh/dubbing-studio-an/work/tasks/task-a0d1ec97dbd1/task.json')


def test_real_auto_candidate_http_replay_cli_and_langfuse(identities,waveform_api,tmp_path,monkeypatch):
    if not REFERENCE.is_file():pytest.skip('需用户本机公开原片；不能使用合成语音替代这项验证')
    assert value('supabase','url')=='http://127.0.0.1:8089/supabase'
    monkeypatch.setenv('ANP_PROFILE','translation-'+uuid4().hex);monkeypatch.setenv('PYTHONIOENCODING','utf-8')
    before=sha256(REFERENCE.read_bytes()).hexdigest();original=json.loads(REFERENCE.read_text(encoding='utf-8'));segment=original['segments'][0]
    # 仅读原片，不读旧译稿/WAV/成片；新截取26秒开发夹具，不进入生产RTF。
    file=tmp_path/'source-26s.mp4'
    subprocess.run([executable('ffmpeg'),'-v','error','-i',original['source']['path'],'-t','26','-map','0:v:0','-map','0:a:0',
        '-c','copy',str(file)],check=True,capture_output=True,timeout=30)
    endpoint=waveform_api;token=identities['admin']['token']
    async def run():
        actor=await actor_from_token(token)
        project=await projects.create(actor,{'name':'[真实模型验收] Yale首段译稿候选','source_url':'https://www.youtube.com/watch?v=QXXp1bHd6gI'})
        quality=await versions.create_quality_profile(actor,PROFILE)
        queued=await execution.queue(actor,project['id'],{'purpose':'acceptance','quality_profile_id':quality['id'],
            'flow_revision':'translation-auto-v1','expected_version':project['row_version']})
        await execution.start(actor,queued['id'])
        try:
            attempt=(await execution.begin_stage(actor,queued['id'],{'node':'acceptance_public_source','input_hash':'d'*64}))['attempt']['id']
            await execution.mark_stage(actor,attempt,'started');started=time.monotonic()
            probe=await probe_media(file);media=await artifacts.upload(actor,attempt,file,'video/mp4',probe['duration_ms'])
            source=await candidates.source(actor,attempt,{'artifact_id':media['id'],'duration_ms':probe['duration_ms'],
                'identity_evidence':{'acceptance_only':True,'source_text_provenance':'旧工作台人工/既有原文，未经本轮独立ASR审核','new_clip_seconds':26}})
            part=await candidates.transcript(actor,attempt,{'source_id':source['id'],'strategy_revision':'provided-source-text-v1',
                'cues':[{'cue_no':0,'speaker_key':'speaker','source_text':segment['sourceText'],'start_ms':round(segment['startSec']*1000),'end_ms':round(segment['endSec']*1000)}],
                'segments':[{'cue_numbers':[0]}]})
            rows=await actor.objects.select('deep_dub_segments',segment_set_id='eq.'+part['id']);target=rows[0]
            await execution.finish_stage(actor,attempt,{'status':'succeeded','outputs':[media['id']],
                'elapsed_seconds':time.monotonic()-started,'operation_stopped':True})
            path=f'/api/apps/deep_dub/runs/{queued["id"]}/translate';body={'segment_id':target['id'],'timeout_seconds':180};key=str(uuid4())
            async with async_client(timeout=210,trust_env=False,headers={'Authorization':'Bearer '+token}) as client:
                assert (await client.post(endpoint+path,json=body,headers={'Authorization':'Bearer '+identities['viewer']['token']})).status_code==403
                assert (await client.post(endpoint+path,json=body,headers={'Authorization':''})).status_code==401
                result=await client.post(endpoint+path,json=body,headers={'Idempotency-Key':key})
                assert result.status_code==200,'真实模型生成失败，诊断留私有日志'
                stage=result.json();assert stage['status']=='succeeded' and len(stage['attempt']['outputs'])==2
                assert (await client.post(endpoint+path,json=body,headers={'Idempotency-Key':key})).json()==stage
                assert await generate(actor,queued['id'],body,key)==stage
                # 数据库规范化产物顺序；倒序重放也必须绑定同一响应证据。
                await record_scores(actor,{**stage['attempt'],'outputs':list(reversed(stage['attempt']['outputs']))})
                foreign=await client.post(endpoint+path,json=body,headers={'Authorization':'Bearer '+identities['outside']['token']})
                assert foreign.status_code in (403,404)
            translations=await actor.objects.select('deep_dub_translations',segment_id='eq.'+target['id'])
            assert len(translations)==1 and translations[0]['stage_attempt_id']==stage['attempt']['id']
            assert translations[0]['prompt_revision']==PROMPT_REVISION and translations[0]['model_id']!='demo-mock'
            assert not await actor.objects.select('deep_dub_audits',translation_id='eq.'+translations[0]['id'])
            context_id=translations[0]['context_artifact_id'];response_id=next(x for x in stage['attempt']['outputs'] if x!=context_id)
            proof=tmp_path/'generation.json';await artifacts.download(actor,response_id,proof)
            generated=json.loads(proof.read_bytes());assert generated['requested_model']=='auto' and generated['quality_status']=='not_measured'
            scores=await actor.objects.select('deep_dub_scores',stage_attempt_id='eq.'+stage['attempt']['id'])
            assert len(scores)==2
            contract=next(s for s in scores if s['metric']=='translation_response_contract');unknown=next(s for s in scores if s['metric']=='translation_semantics')
            assert contract['value']==1 and contract['rule_revision']==EVALUATOR_REVISION and contract['evidence_artifact_id']==response_id and unknown['measurement_status']=='not_measured' and unknown['value'] is None
            await flush(actor)
            async with async_client(timeout=20,trust_env=False,auth=httpx.BasicAuth(secret('LANGFUSE_PUBLIC_KEY'),secret('LANGFUSE_SECRET_KEY'))) as lf:
                for _ in range(20):
                    data=await lf.get(value('langfuse','url')+'/api/public/v3/scores',params={'traceId':queued['id'].replace('-',''),'limit':100})
                    items=data.json().get('data',[]) if data.is_success else []
                    if {contract['id'],unknown['id']}<={s['id'] for s in items}:break
                    await asyncio.sleep(.5)
                assert next(s for s in items if s['id']==unknown['id'])['value']=='not_measured'
            # 生成/补写评分都是运行操作；三入口重放在取消前验收，不放宽已取消运行的写入限制。
            await login(identities['admin']['email'],identities['admin']['password'])
            def cli(*options):
                result=subprocess.run([sys.executable,'-m','anp.cli.entry',*options,'deep_dub','runs','translate',queued['id'],target['id'],
                    '--timeout-seconds','180','--request-key',key],cwd=ROOT,capture_output=True,text=True,encoding='utf-8',timeout=60)
                assert result.returncode==0,'CLI同一候选重放失败，响应不公开';return json.loads(result.stdout)
            assert await asyncio.to_thread(cli,'--endpoint','http://127.0.0.1:1')==stage
            assert await asyncio.to_thread(cli,'--transport','http','--endpoint',endpoint)==stage
            return {'project':project['id'],'run':queued['id'],'target':target['id'],'key':key,'stage':stage,'translation':translations[0],
                'generation':generated,'scores':[contract['id'],unknown['id']],'context_id':context_id,'response_id':response_id}
        finally:await execution.cancel(actor,queued['id']);await flush(actor)
    try:
        record=asyncio.run(run())
        assert sha256(REFERENCE.read_bytes()).hexdigest()==before
        Path('.local/deep-dub-translation-live.json').write_bytes((json.dumps({'checked_at_utc':datetime.now(timezone.utc).isoformat(),
            'purpose':'acceptance','project_id':record['project'],'run_id':record['run'],'segment_id':record['target'],
            'translation_id':record['translation']['id'],'stage_attempt_id':record['stage']['attempt']['id'],
            'context_artifact_id':record['context_id'],'response_artifact_id':record['response_id'],
            'chinese_text':record['translation']['chinese_text'],'requested_model':'auto','reported_model':record['generation']['reported_model'],
            'usage':record['generation']['usage'],'gateway_elapsed_seconds':record['generation']['elapsed_seconds'],
            'http_equals_local_service_and_both_cli':True,'same_key_one_candidate':True,'viewer_anonymous_foreign_denied':True,
            'langfuse_contract_numeric_and_semantics_not_measured':True,'score_ids':record['scores'],
            'evaluator_revision':EVALUATOR_REVISION,'reversed_output_order_verified':True,
            'no_passed_audit':True,'old_task_unchanged':True,'new_public_source_clip':True,'source_text_independently_reviewed':False,
            'run_cancelled':True,'production_started':False},ensure_ascii=False,indent=2)+'\n').encode())
    finally:session_path().unlink(missing_ok=True)


def test_queued_replay_never_starts_second_provider_and_cancel_keeps_unknown(identities,tmp_path,monkeypatch):
    """真实数据库并发/取消；受控等待函数不是实际上游停止证明，不生成译稿。"""
    import apps.deep_dub.services.translation as service
    file=tmp_path/'cancel-fixture.mp4'
    subprocess.run([executable('ffmpeg'),'-v','error','-f','lavfi','-i','color=s=64x64:r=10:d=2','-an',
        '-c:v','libx264','-pix_fmt','yuv420p',str(file)],check=True,capture_output=True,timeout=20)
    async def run():
        actor=await actor_from_token(identities['admin']['token'])
        project=await projects.create(actor,{'name':'[验收] 译稿并发取消','source_url':'https://example.invalid/translation-cancel-only'})
        profile=await versions.create_quality_profile(actor,PROFILE)
        queued=await execution.queue(actor,project['id'],{'purpose':'acceptance','quality_profile_id':profile['id'],
            'flow_revision':'translate-cancel-contract-v1','expected_version':project['row_version']})
        await execution.start(actor,queued['id']);operation=None
        claimed=asyncio.Event();allow_start=asyncio.Event();provider_started=asyncio.Event();provider_closed=asyncio.Event();calls=[]
        original_mark=execution.mark_stage
        async def hold_mark(*args,**kwargs):
            claimed.set();await allow_start.wait();return await original_mark(*args,**kwargs)
        async def waiting_provider(*args,**kwargs):
            calls.append(True);provider_started.set()
            try:await asyncio.Event().wait()
            finally:provider_closed.set()
        try:
            ingest=(await execution.begin_stage(actor,queued['id'],{'node':'acceptance_cancel_input','input_hash':'f'*64}))['attempt']['id']
            await execution.mark_stage(actor,ingest,'started')
            ingest_started=time.monotonic()
            media=await artifacts.upload(actor,ingest,file,'video/mp4',2000)
            source=await candidates.source(actor,ingest,{'artifact_id':media['id'],'duration_ms':2000,'identity_evidence':{'cancel_test_only':True,'no_speech':True}})
            parts=await candidates.transcript(actor,ingest,{'source_id':source['id'],'strategy_revision':'cancel-fixture-only',
                'cues':[{'cue_no':0,'speaker_key':'fixture','source_text':'Cancellation fixture, no speech.','start_ms':0,'end_ms':1000}],
                'segments':[{'cue_numbers':[0]}]})
            await execution.finish_stage(actor,ingest,{'status':'succeeded','outputs':[media['id']],
                'elapsed_seconds':time.monotonic()-ingest_started,'operation_stopped':True})
            target=(await actor.objects.select('deep_dub_segments',segment_set_id='eq.'+parts['id']))[0]
            monkeypatch.setattr(execution,'mark_stage',hold_mark);monkeypatch.setattr(service,'translate',waiting_provider)
            body={'segment_id':target['id']};key=str(uuid4())
            operation=asyncio.create_task(generate(actor,queued['id'],body,key))
            await asyncio.wait_for(claimed.wait(),10)
            replay=await asyncio.wait_for(generate(actor,queued['id'],body,key),5)
            assert replay['status']=='queued' and calls==[]
            allow_start.set();await asyncio.wait_for(provider_started.wait(),10)
            overlap=await generate(actor,queued['id'],body,str(uuid4()))
            assert overlap['status']=='running' and len(calls)==1
            await execution.cancel(actor,queued['id'])
            with pytest.raises(BusinessError) as error:await asyncio.wait_for(operation,5)
            assert error.value.code=='RUN_NOT_RUNNING' and provider_closed.is_set()
            attempts=await actor.objects.select('deep_dub_stage_attempts',id='eq.'+replay['attempt']['id'])
            assert attempts[0]['status']=='cancelled' and attempts[0]['outputs']==[]
            assert not await actor.objects.select('deep_dub_translations',project_id='eq.'+project['id'])
            assert not await actor.objects.select('deep_dub_scores',stage_attempt_id='eq.'+attempts[0]['id'])
            Path('.local/deep-dub-translation-cancel.json').write_bytes((json.dumps({'checked_at_utc':datetime.now(timezone.utc).isoformat(),
                'purpose':'acceptance','run_id':queued['id'],'queued_replay_did_not_start_second_provider':True,'running_replay_did_not_duplicate':True,
                'controlled_provider_calls':len(calls),'controlled_local_wait_cancelled':True,'attempt_status':'cancelled','committed_outputs':0,
                'new_translation_count':0,'quality_score_count':0,'actual_upstream_cancellation_verified':False},ensure_ascii=False,indent=2)+'\n').encode())
        finally:
            allow_start.set()
            if operation is not None and not operation.done():operation.cancel();await asyncio.gather(operation,return_exceptions=True)
            await execution.cancel(actor,queued['id']);await flush(actor)
    asyncio.run(run())
