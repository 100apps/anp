"""仅终止本测试创建的本机owner；远端业务作业和全部模型继续运行。"""
import asyncio,json,os,site,subprocess,sys,time
from datetime import datetime,timezone
from uuid import uuid4
import pytest
from anp import ROOT
from anp.auth.session import login,current_actor,session_path
from anp.config import secret
from anp.sdk.http import async_client
from apps.deep_dub.services.projects import service as projects
from apps.deep_dub.services.execution import service as execution
from apps.deep_dub.services.resident_transport import Transport,configuration
from apps.deep_dub.services.resident_recovery import recover
from tests.test_deep_dub_resident_live import prepare_inputs
from tests.test_deep_dub_source_acquisition_live import flush

pytestmark=pytest.mark.skipif(os.environ.get('ANP_DEEP_DUB_RESIDENT_OWNER_LIVE')!='1',reason='需显式允许本测试owner失联与真实远端作业恢复')


def test_owned_client_loss_keeps_remote_and_recovers_failed_not_success(monkeypatch,tmp_path):
    monkeypatch.setenv('ANP_PROFILE','resident-owner-'+uuid4().hex)
    file=ROOT/'.local/deep-dub-resident-owner-plan.json'
    assert not file.exists(),'已有作业计划，先核对原作业；禁止自动重新创建'
    async def run():
        await login(secret('ADMIN_EMAIL'),secret('ADMIN_PASSWORD'));actor=await current_actor()
        original=json.loads((ROOT/'.local/deep-dub-resident-speech-live.json').read_bytes())
        old=(await actor.objects.select('deep_dub_runs',id='eq.'+original['run_id']))[0]
        project=await projects.create(actor,{'name':'[真实远端owner失联验收] Yale26秒','source_url':'https://example.invalid/resident-owner-acceptance'})
        run=await execution.queue(actor,project['id'],{'purpose':'acceptance','quality_profile_id':old['quality_profile_id'],
            'flow_revision':'resident-owner-loss-v1','expected_version':project['row_version']},uuid4().hex)
        await execution.start(actor,run['id'])
        plan={'project_id':project['id'],'run_id':run['id'],'request_key':uuid4().hex,'task_user_id':actor.user['id']}
        file.write_bytes((json.dumps(plan,ensure_ascii=False,indent=2)+'\n').encode())
        await prepare_inputs(actor,plan,file,tmp_path)
        code='''import asyncio,json
from pathlib import Path
from anp.auth.session import current_actor
from apps.deep_dub.services.speech import generate
async def main():
 p=json.loads(Path('.local/deep-dub-resident-owner-plan.json').read_bytes())
 actor=await current_actor()
 await generate(actor,p['run_id'],p['body'],p['request_key'])
asyncio.run(main())
'''
        env={**os.environ,'PYTHONPATH':os.pathsep.join((str(ROOT),str(ROOT/'src'),*site.getsitepackages()))}
        log=(ROOT/'.local/deep-dub-resident-owner-child.log').open('wb')
        owner=subprocess.Popen([sys._base_executable,'-c',code],cwd=ROOT,env=env,stdout=log,stderr=subprocess.STDOUT,
            creationflags=subprocess.CREATE_NO_WINDOW if sys.platform=='win32' else 0)
        transport=Transport(configuration());attempt=None;observed=None
        try:
            deadline=time.monotonic()+90
            while time.monotonic()<deadline:
                assert owner.poll() is None,'本测试owner已自行退出，不能算作真实失联'
                stages=await actor.objects.select('deep_dub_stages',run_id='eq.'+run['id'],node='eq.generate_audio')
                if stages:
                    rows=await actor.objects.select('deep_dub_stage_attempts',stage_id='eq.'+stages[0]['id'])
                    if rows:
                        attempt=rows[0]
                        try:observed=await transport.request('read',job_id=attempt['id'])
                        except Exception:observed=None
                        if observed and observed.get('worker_running') and observed.get('status')=='submit_intent':break
                await asyncio.sleep(.25)
            assert observed and observed.get('worker_running') and observed.get('status')=='submit_intent'
            # 只操作Popen返回、尚未退出的本测试进程句柄，不按名称/PID扫描杀其他作业。
            plan.update(owner_pid=owner.pid,attempt_id=attempt['id'],observed_before_owner_loss=observed)
            file.write_bytes((json.dumps(plan,ensure_ascii=False,indent=2)+'\n').encode())
            owner.terminate();await asyncio.to_thread(owner.wait,10)
            async with async_client(base_url='http://127.0.0.1:8181',timeout=120,trust_env=False,
                headers={'Authorization':'Bearer '+actor.token,'X-Workspace-ID':actor.workspace_id}) as client:
                path=f'/api/apps/deep_dub/attempts/{attempt["id"]}/recover-resident'
                early=await client.post(path,json={});assert early.status_code==409
                deadline=time.monotonic()+180
                while time.monotonic()<deadline:
                    leases=await actor.objects.select('deep_dub_device_leases',select='id,status,expires_at,operation_stopped',stage_attempt_id='eq.'+attempt['id'])
                    receipt=await transport.request('read',job_id=attempt['id'])
                    expiry=datetime.fromisoformat(leases[0]['expires_at'].replace('Z','+00:00'))
                    if datetime.now(timezone.utc)>expiry and receipt.get('operation_stopped') and not receipt.get('worker_running'):break
                    await asyncio.sleep(1)
                response=await client.post(path,json={})
                if response.status_code!=200:(ROOT/'.local/deep-dub-resident-owner-last-response.json').write_bytes(response.content)
                assert response.status_code==200,'恢复失败，保留原作业/租约/回执，不重新生成'
                result=response.json();assert result['status']=='failed' and result['attempt']['error']['code']=='WORKER_LOST'
                assert result['attempt']['monotonic_seconds'] is None and result['attempt']['outputs']==[]
                replay=await client.post(path,json={});assert replay.status_code==200 and replay.json()['attempt']==result['attempt']
            assert not await actor.objects.select('deep_dub_audio_attempts',stage_attempt_id='eq.'+attempt['id'])
            leases=await actor.objects.select('deep_dub_device_leases',select='id,status,operation_stopped',stage_attempt_id='eq.'+attempt['id'])
            assert len(leases)==1 and leases[0]['status']=='released' and leases[0]['operation_stopped']
            await flush(actor)
            return {**plan,'result':result,'final_remote_receipt':receipt,'lease':leases[0],
                'early_recovery_status':early.status_code,'idempotent_http_recovery':True,'no_success_candidate':True,
                'owned_client_exit_code':owner.returncode,'scope':'本测试owner真实退出后远端继续，过期后失败收尾；非CUDA中断/整片'}
        finally:
            # 失败时也只收束本测试创建的进程；不自动恢复未知远端作业。
            if owner.poll() is None:owner.terminate();await asyncio.to_thread(owner.wait,10)
            log.close()
    try:
        record=asyncio.run(run())
        (ROOT/'.local/deep-dub-resident-owner-live.json').write_bytes((json.dumps(record,ensure_ascii=False,indent=2)+'\n').encode())
    finally:session_path().unlink(missing_ok=True)


@pytest.mark.skipif(os.environ.get('ANP_DEEP_DUB_RESIDENT_OWNER_RECOVER')!='1',reason='仅显式恢复同一个已失联作业，禁止重跑模型')
def test_recover_existing_owner_attempt_after_restricted_read_failure(monkeypatch):
    monkeypatch.setenv('ANP_PROFILE','resident-owner-recovery-'+uuid4().hex)
    async def run():
        plan=json.loads((ROOT/'.local/deep-dub-resident-owner-plan.json').read_bytes())
        await login(secret('ADMIN_EMAIL'),secret('ADMIN_PASSWORD'));actor=await current_actor()
        before=(await actor.objects.select('deep_dub_runs',id='eq.'+plan['run_id']))[0]
        receipt=await Transport(configuration()).request('read',job_id=plan['attempt_id'])
        assert receipt['job_id']==plan['attempt_id'] and receipt['operation_stopped'] and not receipt['worker_running']
        async with async_client(base_url='http://127.0.0.1:8181',timeout=120,trust_env=False,
            headers={'Authorization':'Bearer '+actor.token,'X-Workspace-ID':actor.workspace_id}) as client:
            response=await client.post(f'/api/apps/deep_dub/attempts/{plan["attempt_id"]}/recover-resident',json={})
            if response.status_code!=200:(ROOT/'.local/deep-dub-resident-owner-recovery-response.json').write_bytes(response.content)
            assert response.status_code==200,'只恢复原尝试，不重新提交远端请求'
            result=response.json()
        assert result['status']=='failed' and result['attempt']['error']['code']=='WORKER_LOST'
        assert result['attempt']['monotonic_seconds'] is None and result['attempt']['outputs']==[]
        assert (await recover(actor,plan['attempt_id']))['attempt']==result['attempt']
        leases=await actor.objects.select('deep_dub_device_leases',select='id,status,operation_stopped',stage_attempt_id='eq.'+plan['attempt_id'])
        assert len(leases)==1 and leases[0]['status']=='released' and leases[0]['operation_stopped']
        assert not await actor.objects.select('deep_dub_audio_attempts',stage_attempt_id='eq.'+plan['attempt_id'])
        after=(await actor.objects.select('deep_dub_runs',id='eq.'+plan['run_id']))[0]
        assert after==before
        await flush(actor)
        environment={**os.environ,'PYTHONPATH':os.pathsep.join((str(ROOT),str(ROOT/'src'),*site.getsitepackages()))}
        for options in [('--endpoint','http://127.0.0.1:1'),('--transport','http','--endpoint','http://127.0.0.1:8181')]:
            command=[sys._base_executable,'-m','anp.cli.entry',*options,'deep_dub','recover-resident',plan['attempt_id']]
            reply=await asyncio.to_thread(subprocess.run,command,cwd=ROOT,env=environment,capture_output=True,text=True,encoding='utf-8',timeout=120)
            assert reply.returncode==0,'CLI只重放原恢复，不重新提交模型'
            assert json.loads(reply.stdout)['attempt']==result['attempt']
        return {**plan,'result':result,'final_remote_receipt':receipt,'lease':leases[0],
            'original_read_failure_preserved':True,'http_and_local_service_recovery':True,
            'two_cli_recovery_verified':True,'no_success_candidate':True,'run_unchanged':True,
            'scope':'原失联owner同尝试恢复；未重新生成，未补记成功/单调耗时'}
    try:
        record=asyncio.run(run())
        (ROOT/'.local/deep-dub-resident-owner-recovered.json').write_bytes((json.dumps(record,ensure_ascii=False,indent=2)+'\n').encode())
    finally:session_path().unlink(missing_ok=True)
