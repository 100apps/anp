"""真实常驻作业取消：等待本请求终态，不结束既有模型；不是CUDA内核取消。"""
import asyncio,json,os,time
from uuid import uuid4
import pytest
from anp import ROOT
from anp.auth.session import login,current_actor,session_path
from anp.config import secret
from anp.sdk.http import async_client
from apps.deep_dub.services.projects import service as projects
from apps.deep_dub.services.execution import service as execution
from apps.deep_dub.services.resident_transport import Transport,configuration
from tests.test_deep_dub_resident_live import prepare_inputs
from tests.test_deep_dub_source_acquisition_live import flush

pytestmark=pytest.mark.skipif(os.environ.get('ANP_DEEP_DUB_RESIDENT_CANCEL_LIVE')!='1',reason='需显式真实常驻作业取消验收')


def test_cancel_preserves_resident_waits_for_response_and_rejects_candidate(monkeypatch,tmp_path):
    monkeypatch.setenv('ANP_PROFILE','resident-cancel-'+uuid4().hex)
    planfile=ROOT/'.local/deep-dub-resident-cancel-plan.json'
    assert not planfile.exists(),'已有持久作业计划，先读取原终态；禁止自动新建或重发未知作业'
    async def run():
        await login(secret('ADMIN_EMAIL'),secret('ADMIN_PASSWORD'));actor=await current_actor()
        previous=json.loads((ROOT/'.local/deep-dub-resident-speech-live.json').read_bytes())
        old=(await actor.objects.select('deep_dub_runs',id='eq.'+previous['run_id']))[0]
        project=await projects.create(actor,{'name':'[真实远端取消验收] Yale26秒','source_url':'https://example.invalid/provided-public-source-resident-cancel'})
        run=await execution.queue(actor,project['id'],{'purpose':'acceptance','quality_profile_id':old['quality_profile_id'],
            'flow_revision':'resident-cancel-v1','expected_version':project['row_version']},uuid4().hex)
        await execution.start(actor,run['id'])
        plan={'project_id':project['id'],'run_id':run['id'],'request_key':uuid4().hex,'task_user_id':actor.user['id']}
        planfile.write_bytes((json.dumps(plan,ensure_ascii=False,indent=2)+'\n').encode())
        await prepare_inputs(actor,plan,planfile,tmp_path)
        transport=Transport(configuration());pending=None;cancelled=False
        async with async_client(base_url='http://127.0.0.1:8181',timeout=1200,trust_env=False,
            headers={'Authorization':'Bearer '+actor.token,'X-Workspace-ID':actor.workspace_id}) as client:
            try:
                pending=asyncio.create_task(client.post(f'/api/apps/deep_dub/runs/{run["id"]}/speech',json=plan['body'],headers={'Idempotency-Key':plan['request_key']}))
                deadline=time.monotonic()+90;observed=None;attempt=None
                while time.monotonic()<deadline:
                    assert not pending.done(),'远端请求已结束，未观察到进行中取消，不将其算作通过'
                    stages=await actor.objects.select('deep_dub_stages',run_id='eq.'+run['id'],node='eq.generate_audio')
                    if stages:
                        attempts=await actor.objects.select('deep_dub_stage_attempts',stage_id='eq.'+stages[0]['id'])
                        if attempts:
                            attempt=attempts[0]
                            try:observed=await transport.request('read',job_id=attempt['id'])
                            except Exception:observed=None
                            if observed and observed.get('worker_running') and observed.get('status')=='submit_intent':break
                    await asyncio.sleep(.25)
                assert observed and observed.get('worker_running') and observed.get('status')=='submit_intent'
                plan['observed_before_cancel']=observed;planfile.write_bytes((json.dumps(plan,ensure_ascii=False,indent=2)+'\n').encode())
                started=time.monotonic();await execution.cancel(actor,run['id']);cancelled=True
                response=await asyncio.wait_for(asyncio.shield(pending),600)
                assert response.status_code!=200
                final=await transport.request('read',job_id=attempt['id'])
                assert final['operation_stopped'] and not final['worker_running'] and final['resident_service_stopped'] is False
                leases=await actor.objects.select('deep_dub_device_leases',select='id,status,operation_stopped',stage_attempt_id='eq.'+attempt['id'])
                assert len(leases)==1 and leases[0]['status']=='released' and leases[0]['operation_stopped']
                assert not await actor.objects.select('deep_dub_audio_attempts',stage_attempt_id='eq.'+attempt['id'])
                current=(await actor.objects.select('deep_dub_stage_attempts',id='eq.'+attempt['id']))[0]
                assert current['status']=='cancelled'
                probe=await transport.request('inspect');assert probe['all_owned_operations_stopped']
                await flush(actor)
                return {**plan,'attempt_id':attempt['id'],'lease':leases[0],'final_remote_receipt':final,
                    'cancel_to_response_seconds':time.monotonic()-started,'response_status':response.status_code,
                    'no_audio_candidate_committed':True,'scope':'真实远端请求处理中取消并等终态，不是CUDA内核中断或整片验收'}
            finally:
                if not cancelled:await execution.cancel(actor,run['id'])
                if pending is not None and not pending.done():await asyncio.wait_for(asyncio.shield(pending),600)
    try:
        record=asyncio.run(run())
        (ROOT/'.local/deep-dub-resident-cancel-live.json').write_bytes((json.dumps(record,ensure_ascii=False,indent=2)+'\n').encode())
    finally:session_path().unlink(missing_ok=True)
