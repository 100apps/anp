"""完整原始PCM的真实GPU角色分析；不将模型标签当作人物审核。"""
import asyncio,json,os,subprocess,sys
from hashlib import sha256
from uuid import uuid4

import httpx
import pytest

from anp import ROOT
from anp.auth.session import login,current_actor,session_path
from anp.config import secret,value
from anp.sdk.http import async_client
from apps.deep_dub.services.source_speakers import read_latest
from apps.deep_dub.services.source_review import read as read_queue
from apps.deep_dub.services.model_pool import configuration,inventory
from tests.test_deep_dub_source_acquisition_live import flush

pytestmark=pytest.mark.skipif(os.environ.get('ANP_DEEP_DUB_SOURCE_SPEAKERS_LIVE')!='1',reason='需受控角色模型、整轨PCM和冻结平台')


def test_actual_whole_pcm_speaker_model_keeps_pending_source_and_all_entry_contracts(identities,monkeypatch):
    record=json.loads((ROOT/'.local/deep-dub-source-preparation-final.json').read_bytes())
    project,source,run=record['project_id'],record['acquisition']['source_id'],record['run_id']
    monkeypatch.setenv('ANP_PROFILE','source-speakers-'+uuid4().hex)
    async def unchanged(actor):
        return {table:await actor.objects.select(table,project_id='eq.'+project,order='id.asc') for table in
                ('deep_dub_runs','deep_dub_audits','deep_dub_segment_sets','deep_dub_speakers','deep_dub_translations')}
    async def scenario():
        await login(identities['admin']['email'],identities['admin']['password']);actor=await current_actor()
        before=await unchanged(actor);row=next(x for x in before['deep_dub_runs'] if x['id']==run)
        task=(await actor.objects.select('tasks',id='eq.'+row['task_id']))[0]
        queue=await read_queue(actor,project,source)
        body={'source_id':source,'timeout_seconds':3600};key=uuid4().hex
        plan={'project_id':project,'source_id':source,'run_id':run,'request_key':key,'body':body,'actor_user_id':actor.user['id'],
              'task_user_id':task['created_by'],'before_run':row,'queue_sha256':queue['packet_sha256'],'gpu_probe':inventory(configuration('diarization'))}
        (ROOT/'.local/deep-dub-source-speakers-plan.json').write_bytes(json.dumps(plan,ensure_ascii=False,indent=2).encode())
        async with async_client(timeout=3700,trust_env=False) as client:
            reply=await client.post(f'http://127.0.0.1:8181/api/apps/deep_dub/runs/{run}/analyze-source-speakers',
                headers={'Authorization':'Bearer '+actor.token,'X-Workspace-ID':actor.workspace_id,'Idempotency-Key':key},json=body)
        assert reply.status_code==200,reply.text[:1000]
        generated=reply.json();assert generated['status']=='succeeded'
        plan['result']=generated;(ROOT/'.local/deep-dub-source-speakers-plan.json').write_bytes(json.dumps(plan,ensure_ascii=False,indent=2).encode())
        result=await read_latest(actor,project,source);m=result['proof']['measurement']
        path=f'/api/apps/deep_dub/projects/{project}/sources/{source}/speaker-timing'
        async with async_client(base_url='http://127.0.0.1:8181',timeout=90,trust_env=False) as client:
            headers={'Authorization':'Bearer '+actor.token,'X-Workspace-ID':actor.workspace_id}
            response=await client.get(path,headers=headers)
            assert response.status_code==200 and response.json()==result
            assert (await client.get(path)).status_code==401
            outside=identities['outside']
            assert (await client.get(path,headers={'Authorization':'Bearer '+outside['token'],'X-Workspace-ID':outside['workspace_id']})).status_code==404
            viewer=identities['viewer']
            assert (await client.post(f'/api/apps/deep_dub/runs/{run}/analyze-source-speakers',headers={
                'Authorization':'Bearer '+viewer['token'],'X-Workspace-ID':viewer['workspace_id']},json=body)).status_code==403
        assert m['pcm']['sample_count']==m['submitted_samples']==41380932 and m['session']['requests']==1
        assert m['session']['operation_stopped'] and m['session']['initially_unloaded'] and m['session']['warm_cache_checks']==0
        assert m['speaker_identity']==m['turn_boundary_review']==m['subtitle_timing_review']=='not_measured'
        assert m['model']['family']=='nemotron_3_diar' and m['text_prompt']==''
        assert await unchanged(actor)==before and await read_queue(actor,project,source)==queue
        leases=await actor.objects.select('deep_dub_device_leases',select='id,status,operation_stopped',stage_attempt_id='eq.'+generated['attempt']['id'])
        assert len(leases)==1 and leases[0]['status']=='released' and leases[0]['operation_stopped']
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
            observed=[o for o in response.json()['data'] if o['id']==sha256(generated['attempt']['id'].encode()).hexdigest()[:16]]
            assert len(observed)==1 and observed[0]['userId']==task['created_by'] and observed[0]['sessionId']==run and observed[0]['metadata']['attempt_id']==generated['attempt']['id']
        private={**plan,'readback':result,'lease':leases[0],'score_ids':[s['id'] for s in scores],'langfuse_verified':True,
                 'source_queue_roles_and_run_unchanged':True}
        (ROOT/'.local/deep-dub-source-speakers-live.json').write_bytes(json.dumps(private,ensure_ascii=False,indent=2).encode())
        return actor,result,generated
    try:
        actor,result,generated=asyncio.run(scenario())
        for options in [('--endpoint','http://127.0.0.1:1'),('--transport','http','--endpoint','http://127.0.0.1:8181')]:
            reply=subprocess.run([sys.executable,'-m','anp.cli.entry',*options,'deep_dub','projects','source-speaker-timing',project,source],
                cwd=ROOT,capture_output=True,text=True,encoding='utf-8',timeout=90)
            assert reply.returncode==0,reply.stdout+reply.stderr
            assert json.loads(reply.stdout)==result
            replay=subprocess.run([sys.executable,'-m','anp.cli.entry',*options,'deep_dub','runs','analyze-source-speakers',run,source],
                cwd=ROOT,capture_output=True,text=True,encoding='utf-8',timeout=90)
            assert replay.returncode==0,replay.stdout+replay.stderr
            assert json.loads(replay.stdout)['attempt']==generated['attempt']
        private=json.loads((ROOT/'.local/deep-dub-source-speakers-live.json').read_bytes());private['two_cli_read_and_replay']=True
        (ROOT/'.local/deep-dub-source-speakers-live.json').write_bytes(json.dumps(private,ensure_ascii=False,indent=2).encode())
    finally:session_path().unlink(missing_ok=True)
