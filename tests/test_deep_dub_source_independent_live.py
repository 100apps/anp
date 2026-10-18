"""当前整片第二套声学识别；模型、样本、原稿、身份、评分及三入口真实验证。"""
import asyncio,json,os,subprocess,sys
from hashlib import sha256
from uuid import uuid4
import httpx
import pytest
from anp import ROOT
from anp.auth.session import login,current_actor,session_path
from anp.config import secret,value
from anp.sdk.http import async_client
from apps.deep_dub.services.source_independent_asr import read_latest
from apps.deep_dub.services.source_review import read as read_queue
from apps.deep_dub.services.model_pool import configuration,inventory
from tests.test_deep_dub_runtime_live import flush

pytestmark=pytest.mark.skipif(os.environ.get('ANP_DEEP_DUB_SOURCE_INDEPENDENT_LIVE')!='1',reason='需已配置第二套模型、待审整轨和冻结平台')


def test_real_second_model_whole_pcm_keeps_source_and_review_pending(identities,monkeypatch):
    record=json.loads((ROOT/'.local/deep-dub-source-preparation-final.json').read_bytes())
    project,source,run=record['project_id'],record['acquisition']['source_id'],record['run_id']
    monkeypatch.setenv('ANP_PROFILE','source-independent-live-'+uuid4().hex)
    async def scenario():
        await login(identities['admin']['email'],identities['admin']['password']);actor=await current_actor()
        before=(await actor.objects.select('deep_dub_runs',id='eq.'+run))[0]
        task=(await actor.objects.select('tasks',id='eq.'+before['task_id']))[0]
        queue=await read_queue(actor,project,source)
        config=configuration('independent_asr');gpu=inventory(config)
        key=uuid4().hex;body={'source_id':source,'timeout_seconds':3600}
        plan={'project_id':project,'source_id':source,'run_id':run,'request_key':key,'body':body,
            'actor_user_id':actor.user['id'],'task_user_id':task['created_by'],'before_run':before,'original_queue_sha256':queue['packet_sha256'],'gpu_probe':gpu}
        (ROOT/'.local/deep-dub-independent-plan.json').write_bytes(json.dumps(plan,ensure_ascii=False,indent=2).encode())
        async with async_client(timeout=3700,trust_env=False) as client:
            response=await client.post('http://127.0.0.1:8181/api/apps/deep_dub/runs/'+run+'/recognize-source-independent',
                headers={'Authorization':'Bearer '+actor.token,'X-Workspace-ID':actor.workspace_id,'Idempotency-Key':key},json=body)
        assert response.status_code==200,response.text[:1000]
        generated=response.json();assert generated['status']=='succeeded'
        plan['result']=generated;(ROOT/'.local/deep-dub-independent-plan.json').write_bytes(json.dumps(plan,ensure_ascii=False,indent=2).encode())
        readback=await read_latest(actor,project,source);measured=readback['proof']['measurement']
        assert measured['summary']['chunk_count']==87 and measured['summary']['sample_coverage']==1
        assert measured['session']['requests']==87 and measured['session']['warm_cache_checks']==86 and measured['session']['operation_stopped']
        assert measured['independent_recognizer'] is True and measured['independent_review'] is False and measured['source_text_modified'] is False
        assert measured['semantic_review']==measured['speaker_review']==measured['subtitle_timing_review']=='not_measured'
        assert measured['model']['family']=='nemotron_asr'
        assert await actor.objects.select('deep_dub_runs',id='eq.'+run)==[before]
        assert (await read_queue(actor,project,source))==queue
        leases=await actor.objects.select('deep_dub_device_leases',select='id,status,operation_stopped',stage_attempt_id='eq.'+generated['attempt']['id'])
        assert len(leases)==1 and leases[0]['status']=='released' and leases[0]['operation_stopped']
        assert not await actor.objects.select('deep_dub_audits',project_id='eq.'+project)
        assert not await actor.objects.select('deep_dub_segment_sets',project_id='eq.'+project)
        scores=await actor.objects.select('deep_dub_scores',stage_attempt_id='eq.'+generated['attempt']['id']);assert len(scores)==6
        await flush(actor)
        async with async_client(timeout=20,trust_env=False,auth=httpx.BasicAuth(secret('LANGFUSE_PUBLIC_KEY'),secret('LANGFUSE_SECRET_KEY'))) as client:
            for _ in range(30):
                response=await client.get(value('langfuse','url')+'/api/public/v3/scores',params={'traceId':run.replace('-',''),'limit':100});response.raise_for_status()
                found={s['id']:s for s in response.json()['data']}
                if all(s['id'] in found for s in scores):break
                await asyncio.sleep(.5)
            for score in scores:assert found[score['id']]['value']==(score['value'] if score['measurement_status']=='measured' else 'not_measured')
            response=await client.get(value('langfuse','url')+'/api/public/v2/observations',params={'traceId':run.replace('-',''),'limit':100,'fields':'basic,metadata'});response.raise_for_status()
            found=[o for o in response.json()['data'] if o['id']==sha256(generated['attempt']['id'].encode()).hexdigest()[:16]]
            assert len(found)==1 and found[0]['userId']==task['created_by'] and found[0]['sessionId']==run and found[0]['metadata']['attempt_id']==generated['attempt']['id']
        result={**plan,'readback':readback,'lease':leases[0],'score_ids':[s['id'] for s in scores],'langfuse_verified':True,
            'source_run_queue_unchanged':True,'no_source_audit_or_segments':True}
        (ROOT/'.local/deep-dub-independent-live.json').write_bytes(json.dumps(result,ensure_ascii=False,indent=2).encode())
        return result
    try:
        result=asyncio.run(scenario())
        for options in [('--endpoint','http://127.0.0.1:1'),('--transport','http','--endpoint','http://127.0.0.1:8181')]:
            replay=subprocess.run([sys.executable,'-m','anp.cli.entry',*options,'deep_dub','runs','recognize-source-independent',run,source,'--request-key',result['request_key']],
                cwd=ROOT,capture_output=True,text=True,encoding='utf-8',timeout=180)
            assert replay.returncode==0,replay.stdout+replay.stderr
            assert json.loads(replay.stdout)==result['result']
            read=subprocess.run([sys.executable,'-m','anp.cli.entry',*options,'deep_dub','projects','source-independent-recognition',project,source],
                cwd=ROOT,capture_output=True,text=True,encoding='utf-8',timeout=90)
            assert read.returncode==0 and json.loads(read.stdout)==result['readback']
        result['two_cli_replay_and_read']=True
        (ROOT/'.local/deep-dub-independent-live.json').write_bytes(json.dumps(result,ensure_ascii=False,indent=2).encode())
    finally:session_path().unlink(missing_ok=True)
