"""完整音轨的独立听音复核；校准不满足时不得伪装成质量通过。"""
import asyncio,json,os,site,subprocess,sys
from hashlib import sha256
from uuid import uuid4

import pytest
import httpx

from anp import ROOT
from anp.auth.session import login,current_actor,session_path
from anp.sdk.http import async_client
from anp.config import secret,value
from apps.deep_dub.services.auditory_review import read_latest
from apps.deep_dub.services.source_review import read as read_queue
from apps.deep_dub.services.audio_judge_runtime import configuration
from apps.deep_dub.services.model_pool import inventory
from tests.test_deep_dub_source_acquisition_live import flush

pytestmark=pytest.mark.skipif(os.environ.get('ANP_DEEP_DUB_AUDITORY_LIVE')!='1',reason='需已配置独立听音模型与本轮完整来源')


def test_actual_whole_source_auditory_review_and_two_cli_contracts(identities,monkeypatch):
    record=json.loads((ROOT/'.local/deep-dub-source-preparation-final.json').read_bytes())
    project,source,run=record['project_id'],record['acquisition']['source_id'],record['run_id']
    monkeypatch.setenv('ANP_PROFILE','auditory-live-'+uuid4().hex)
    async def fixed(actor):
        return {table:await actor.objects.select(table,project_id='eq.'+project,order='id.asc') for table in
                ('deep_dub_runs','deep_dub_segment_sets','deep_dub_translations','deep_dub_speakers')}
    async def scenario():
        await login(identities['admin']['email'],identities['admin']['password']);actor=await current_actor()
        before=await fixed(actor);row=next(x for x in before['deep_dub_runs'] if x['id']==run)
        task=(await actor.objects.select('tasks',id='eq.'+row['task_id']))[0];queue=await read_queue(actor,project,source)
        body={'source_id':source,'timeout_seconds':3600};key=uuid4().hex
        plan={'project_id':project,'source_id':source,'run_id':run,'body':body,'request_key':key,'actor_user_id':actor.user['id'],
            'task_user_id':task['created_by'],'before_run':row,'queue_sha256':queue['packet_sha256'],'gpu':inventory(configuration())}
        (ROOT/'.local/deep-dub-auditory-plan.json').write_bytes(json.dumps(plan,ensure_ascii=False,indent=2).encode())
        async with async_client(timeout=3700,trust_env=False) as client:
            response=await client.post(f'http://127.0.0.1:8181/api/apps/deep_dub/runs/{run}/review-source-audio',json=body,
                headers={'Authorization':'Bearer '+actor.token,'X-Workspace-ID':actor.workspace_id,'Idempotency-Key':key})
        assert response.status_code==200,response.text[:1000]
        result=response.json();assert result['status']=='succeeded';plan['result']=result
        (ROOT/'.local/deep-dub-auditory-plan.json').write_bytes(json.dumps(plan,ensure_ascii=False,indent=2).encode())
        saved=await read_latest(actor,project,source);m=saved['proof']['measurement']
        async with async_client(base_url='http://127.0.0.1:8181',timeout=90,trust_env=False) as client:
            path=f'/api/apps/deep_dub/projects/{project}/sources/{source}/auditory-review'
            headers={'Authorization':'Bearer '+actor.token,'X-Workspace-ID':actor.workspace_id}
            assert (await client.get(path,headers=headers)).json()==saved
            assert (await client.get(path)).status_code==401
            outside=identities['outside'];viewer=identities['viewer']
            assert (await client.get(path,headers={'Authorization':'Bearer '+outside['token'],'X-Workspace-ID':outside['workspace_id']})).status_code==404
            assert (await client.post(f'/api/apps/deep_dub/runs/{run}/review-source-audio',json=body,headers={
                'Authorization':'Bearer '+viewer['token'],'X-Workspace-ID':viewer['workspace_id']})).status_code==403
        assert m['calibration_passed'] and len(m['controls'])==10 and len(m['windows'])==44
        assert m['session']['requests']==54 and m['session']['operation_stopped'] and m['session']['properties']['modalities']['audio']
        assert m['pcm']['sample_count']==41380932 and sum(m['counts'].values())==44
        assert m['formal_transcript_review']==m['speaker_identity']==m['subtitle_timing_review']=='not_measured'
        assert await fixed(actor)==before and await read_queue(actor,project,source)==queue
        audits=await actor.objects.select('deep_dub_audits',project_id='eq.'+project,evidence_artifact_id='eq.'+saved['evidence_artifact_id'])
        if m['counts']['mismatch']:
            assert audits and all(a['status'] in ('failed','not_measured') for a in audits)
        else:assert not audits
        leases=await actor.objects.select('deep_dub_device_leases',select='id,status,operation_stopped',stage_attempt_id='eq.'+result['attempt']['id'])
        assert len(leases)==1 and leases[0]['status']=='released' and leases[0]['operation_stopped']
        scores=await actor.objects.select('deep_dub_scores',stage_attempt_id='eq.'+result['attempt']['id']);assert len(scores)==10
        await flush(actor)
        async with async_client(timeout=20,trust_env=False,auth=httpx.BasicAuth(secret('LANGFUSE_PUBLIC_KEY'),secret('LANGFUSE_SECRET_KEY'))) as client:
            for _ in range(30):
                response=await client.get(value('langfuse','url')+'/api/public/v3/scores',params={'traceId':run.replace('-',''),'limit':100});response.raise_for_status()
                found={s['id']:s for s in response.json()['data']}
                if all(s['id'] in found for s in scores):break
                await asyncio.sleep(.5)
            for score in scores:assert found[score['id']]['value']==(score['value'] if score['measurement_status']=='measured' else 'not_measured')
            response=await client.get(value('langfuse','url')+'/api/public/v2/observations',params={'traceId':run.replace('-',''),'name':'deep_dub/source_content_review','limit':100,'fields':'basic,metadata'});response.raise_for_status()
            observed=[o for o in response.json()['data'] if o['id']==sha256(result['attempt']['id'].encode()).hexdigest()[:16]]
            assert len(observed)==1 and observed[0]['userId']==task['created_by'] and observed[0]['sessionId']==run and observed[0]['metadata']['attempt_id']==result['attempt']['id']
        private={**plan,'readback':saved,'lease':leases[0],'audit_ids':[a['id'] for a in audits],
                 'score_ids':[s['id'] for s in scores],'source_queue_and_formal_versions_unchanged':True,'langfuse_verified':True,'http_permissions_verified':True}
        (ROOT/'.local/deep-dub-auditory-live.json').write_bytes(json.dumps(private,ensure_ascii=False,indent=2).encode())
        return saved,result
    try:
        saved,result=asyncio.run(scenario())
        cli_environment={**os.environ,'PYTHONPATH':os.pathsep.join((str(ROOT),str(ROOT/'src'),*site.getsitepackages()))}
        for options in [('--endpoint','http://127.0.0.1:1'),('--transport','http','--endpoint','http://127.0.0.1:8181')]:
            read=subprocess.run([sys._base_executable,'-m','anp.cli.entry',*options,'deep_dub','projects','source-auditory-review',project,source],
                cwd=ROOT,capture_output=True,text=True,encoding='utf-8',timeout=120,env=cli_environment)
            assert read.returncode==0,read.stdout+read.stderr
            assert json.loads(read.stdout)==saved
            replay=subprocess.run([sys._base_executable,'-m','anp.cli.entry',*options,'deep_dub','runs','review-source-audio',run,source],
                cwd=ROOT,capture_output=True,text=True,encoding='utf-8',timeout=120,env=cli_environment)
            assert replay.returncode==0,replay.stdout+replay.stderr
            assert json.loads(replay.stdout)['attempt']==result['attempt']
        record=json.loads((ROOT/'.local/deep-dub-auditory-live.json').read_bytes());record['two_cli_read_and_replay']=True
        (ROOT/'.local/deep-dub-auditory-live.json').write_bytes(json.dumps(record,ensure_ascii=False,indent=2).encode())
    finally:session_path().unlink(missing_ok=True)
