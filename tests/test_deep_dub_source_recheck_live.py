"""当前整片的真实接缝重识别；同一待审运行保留原文、计时与门禁。"""
import asyncio,json,os,subprocess,sys
from hashlib import sha256
from uuid import uuid4
import pytest
import httpx
from anp import ROOT
from anp.auth.session import login,current_actor,session_path
from anp.config import secret,value
from anp.sdk.http import async_client
from apps.deep_dub.services.source_recheck import read_latest
from apps.deep_dub.services.source_review import read as review_queue
from apps.deep_dub.services.model_pool import configuration,inventory
from tests.test_deep_dub_runtime_live import flush

pytestmark=pytest.mark.skipif(os.environ.get('ANP_DEEP_DUB_SOURCE_RECHECK_LIVE')!='1',reason='需当前整片待审来源、受控GPU和冻结API')


def test_real_recheck_all_boundaries_and_replay_two_clis_preserves_gate(identities,monkeypatch):
    record=json.loads((ROOT/'.local/deep-dub-source-preparation-final.json').read_bytes())
    project,source,run=record['project_id'],record['acquisition']['source_id'],record['run_id']
    monkeypatch.setenv('ANP_PROFILE','source-recheck-live-'+uuid4().hex)
    async def scenario():
        await login(identities['admin']['email'],identities['admin']['password']);actor=await current_actor()
        before=(await actor.objects.select('deep_dub_runs',id='eq.'+run))[0]
        task=(await actor.objects.select('tasks',id='eq.'+before['task_id']))[0]
        packet=await review_queue(actor,project,source)
        gpu=inventory(configuration('asr'))
        key=uuid4().hex
        body={'source_id':source,'timeout_seconds':1800}
        async with async_client(timeout=1900,trust_env=False) as client:
            response=await client.post('http://127.0.0.1:8181/api/apps/deep_dub/runs/'+run+'/recheck-source-boundaries',
                headers={'Authorization':'Bearer '+actor.token,'X-Workspace-ID':actor.workspace_id,'Idempotency-Key':key},json=body)
        assert response.status_code==200,response.text[:1000]
        generated=response.json();assert generated['status']=='succeeded'
        result=await read_latest(actor,project,source)
        measured=result['proof']['measurement']
        assert measured['summary']['window_count']==43 and measured['session']['requests']==43 and measured['session']['warm_cache_checks']==42
        assert measured['session']['operation_stopped'] and measured['independent_review'] is False and measured['source_text_modified'] is False
        assert measured['semantic_review']==measured['subtitle_timing_review']=='not_measured'
        assert (await actor.objects.select('deep_dub_runs',id='eq.'+run))[0]==before
        assert (await review_queue(actor,project,source))==packet
        lease=await actor.objects.select('deep_dub_device_leases',select='id,status,operation_stopped',stage_attempt_id='eq.'+generated['attempt']['id'])
        assert len(lease)==1 and lease[0]['status']=='released' and lease[0]['operation_stopped']
        assert not await actor.objects.select('deep_dub_audits',project_id='eq.'+project)
        assert not await actor.objects.select('deep_dub_segment_sets',project_id='eq.'+project)
        scores=await actor.objects.select('deep_dub_scores',stage_attempt_id='eq.'+generated['attempt']['id'])
        assert len(scores)==4
        await flush(actor)
        async with async_client(timeout=20,trust_env=False,auth=httpx.BasicAuth(secret('LANGFUSE_PUBLIC_KEY'),secret('LANGFUSE_SECRET_KEY'))) as client:
            for _ in range(30):
                response=await client.get(value('langfuse','url')+'/api/public/v3/scores',params={'traceId':run.replace('-',''),'limit':100})
                response.raise_for_status();found={x['id']:x for x in response.json()['data']}
                if all(s['id'] in found for s in scores):break
                await asyncio.sleep(.5)
            for score in scores:assert found[score['id']]['value']==(score['value'] if score['measurement_status']=='measured' else 'not_measured')
            response=await client.get(value('langfuse','url')+'/api/public/v2/observations',params={'traceId':run.replace('-',''),'limit':100,'fields':'basic,metadata'});response.raise_for_status()
            observations=[o for o in response.json()['data'] if o['id']==sha256(generated['attempt']['id'].encode()).hexdigest()[:16]]
            assert observations and all(o['userId']==task['created_by'] and o['sessionId']==run and o['metadata']['attempt_id']==generated['attempt']['id'] for o in observations)
        out={'project_id':project,'source_id':source,'run_id':run,'request_key':key,'body':body,'result':generated,'readback':result,
             'actor_user_id':actor.user['id'],'trace_task_user_id':task['created_by'],'gpu_inventory':gpu,'lease':lease[0],'score_ids':[s['id'] for s in scores],
             'original_queue_sha256':packet['packet_sha256'],'original_run_unchanged':True,'source_not_modified':True,'no_source_audit_or_segments':True,'langfuse_verified':True}
        (ROOT/'.local/deep-dub-source-recheck-live.json').write_bytes(json.dumps(out,ensure_ascii=False,indent=2).encode())
        return out
    try:
        result=asyncio.run(scenario())
        for options in [('--endpoint','http://127.0.0.1:1'),('--transport','http','--endpoint','http://127.0.0.1:8181')]:
            replay=subprocess.run([sys.executable,'-m','anp.cli.entry',*options,'deep_dub','runs','recheck-source-boundaries',run,source,
                '--request-key',result['request_key']],cwd=ROOT,capture_output=True,text=True,encoding='utf-8',timeout=180)
            assert replay.returncode==0,replay.stdout+replay.stderr
            assert json.loads(replay.stdout)==result['result']
            read=subprocess.run([sys.executable,'-m','anp.cli.entry',*options,'deep_dub','projects','source-boundary-recheck',project,source],
                cwd=ROOT,capture_output=True,text=True,encoding='utf-8',timeout=90)
            assert read.returncode==0 and json.loads(read.stdout)==result['readback']
        result['two_cli_replay_and_read']=True
        (ROOT/'.local/deep-dub-source-recheck-live.json').write_bytes(json.dumps(result,ensure_ascii=False,indent=2).encode())
    finally:session_path().unlink(missing_ok=True)
