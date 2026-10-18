"""核验真实失败的原回执与拒绝边界；不是完整来源听音成功验收。"""
import asyncio,json,os
from hashlib import sha256
from pathlib import Path
from uuid import uuid4

import httpx
import pytest

from anp import ROOT
from anp.auth.session import login,current_actor,session_path
from anp.config import secret,value
from anp.sdk.http import async_client
from apps.deep_dub.services.auditory_review import read_latest
from apps.deep_dub.services.source_review import read as read_queue
from tests.test_deep_dub_source_acquisition_live import flush

pytestmark=pytest.mark.skipif(os.environ.get('ANP_DEEP_DUB_AUDITORY_REJECTION_LIVE')!='1',reason='需本轮已保留的三次真实校准失败')


def test_original_failed_attempts_remain_immutable_in_read_only_inspection(identities,monkeypatch):
    plans=[json.loads((ROOT/'.local'/name).read_bytes()) for name in (
        'deep-dub-auditory-first-plan.json','deep-dub-auditory-second-plan.json','deep-dub-auditory-third-plan.json')]
    plan=plans[-1];project,source,run=plan['project_id'],plan['source_id'],plan['run_id']
    monkeypatch.setenv('ANP_PROFILE','auditory-rejection-'+uuid4().hex)
    journal=ROOT/'.local/deep_dub/gpu-pool/1eb343271a51306f697a263a.json';digest=sha256(journal.read_bytes()).hexdigest()
    async def scenario():
        await login(identities['admin']['email'],identities['admin']['password']);actor=await current_actor()
        stages=await actor.objects.select('deep_dub_stages',run_id='eq.'+run,node='eq.source_content_review')
        all_attempts=await actor.objects.select('deep_dub_stage_attempts',stage_id='in.('+','.join(s['id'] for s in stages)+')',order='created_at.asc')
        frozen=json.loads((ROOT/'.local/deep-dub-auditory-rejection.json').read_bytes())['attempts']
        attempts=[a for a in all_attempts if a['id'] in {a['id'] for a in frozen}]
        assert attempts==frozen  # 同一请求键可以出现在别的输入阶段，历史身份必须绑定尝试ID。
        assert len({a['stage_id'] for a in attempts})==2 and len(attempts)==3
        assert {a['request_key'] for a in attempts}=={p['request_key'] for p in plans}
        assert [a['error']['code'] for a in attempts]==['AUDIO_JUDGE_RESPONSE_INVALID','AUDIO_JUDGE_CALIBRATION_FAILED','AUDIO_JUDGE_CALIBRATION_FAILED']
        for attempt in attempts:
            assert attempt['status']=='failed' and attempt['outputs']==[]
            leases=await actor.objects.select('deep_dub_device_leases',select='id,status,operation_stopped',stage_attempt_id='eq.'+attempt['id'])
            assert len(leases)==1 and leases[0]['status']=='released' and leases[0]['operation_stopped']
            assert not await actor.objects.select('deep_dub_artifacts',stage_attempt_id='eq.'+attempt['id'])
            assert not await actor.objects.select('deep_dub_scores',stage_attempt_id='eq.'+attempt['id'])
        assert (await actor.objects.select('deep_dub_runs',id='eq.'+run))[0]==plan['before_run']
        assert (await read_queue(actor,project,source))['packet_sha256']==plan['queue_sha256']
        current=await read_latest(actor,project,source)
        assert current is None or current['stage_attempt_id'] not in {a['id'] for a in attempts}
        async with async_client(base_url='http://127.0.0.1:8181',timeout=90,trust_env=False) as client:
            path=f'/api/apps/deep_dub/projects/{project}/sources/{source}/auditory-review'
            headers={'Authorization':'Bearer '+actor.token,'X-Workspace-ID':actor.workspace_id}
            reply=await client.get(path,headers=headers);assert reply.status_code==200 and reply.json()==current
            assert (await client.get(path)).status_code==401
            outside=identities['outside'];viewer=identities['viewer']
            assert (await client.get(path,headers={'Authorization':'Bearer '+outside['token'],'X-Workspace-ID':outside['workspace_id']})).status_code==404
            assert (await client.post(f'/api/apps/deep_dub/runs/{run}/review-source-audio',json=plan['body'],headers={
                'Authorization':'Bearer '+viewer['token'],'X-Workspace-ID':viewer['workspace_id']})).status_code==403
        await flush(actor)
        async with async_client(timeout=20,trust_env=False,auth=httpx.BasicAuth(secret('LANGFUSE_PUBLIC_KEY'),secret('LANGFUSE_SECRET_KEY'))) as client:
            for _ in range(30):
                reply=await client.get(value('langfuse','url')+'/api/public/v2/observations',params={'traceId':run.replace('-',''),'name':'deep_dub/source_content_review','limit':100,'fields':'basic,metadata'});reply.raise_for_status()
                found={o['id']:o for o in reply.json()['data']}
                if all(sha256(a['id'].encode()).hexdigest()[:16] in found for a in attempts):break
                await asyncio.sleep(.5)
            for attempt in attempts:
                observation=found[sha256(attempt['id'].encode()).hexdigest()[:16]]
                assert observation['userId']==plan['task_user_id'] and observation['sessionId']==run
                assert observation['metadata']['attempt_id']==attempt['id']
        return attempts,current
    try:
        attempts,current=asyncio.run(scenario())
        # 请求键按阶段隔离；换模型/规则时不能用调用执行接口来证明旧回执不变。
        assert sha256(journal.read_bytes()).hexdigest()==digest
        latest=json.loads(journal.read_bytes());assert latest['state']=='idle' and all(j['exit_code'] is not None for j in latest['jobs'])
        (ROOT/'.local/deep-dub-auditory-historical-rejection.json').write_bytes(json.dumps({
            'project_id':project,'source_id':source,'run_id':run,'attempts':attempts,'latest_journal':latest,
            'no_new_model_in_historical_read':True,'http_permissions_verified':True,'langfuse_identity_verified':True,
            'old_attempts_no_outputs_or_scores':True,'old_model_qualified':False},ensure_ascii=False,indent=2).encode())
    finally:session_path().unlink(missing_ok=True)
