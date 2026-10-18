"""真实本机CPU阶段：租约/心跳、FFmpeg、取消与撤权；非整片生产或GPU验收。"""
import asyncio
from datetime import datetime,timezone
import json
from pathlib import Path
import subprocess
import tempfile
import time
from uuid import uuid4

import pytest
import httpx

from anp.auth.identity import actor_from_token
from anp.config import secret,value
from anp.errors import BusinessError
from anp.management.bootstrap import create_user
from anp.sdk.http import async_client
from apps.deep_dub.nodes.media import _run,executable
from apps.deep_dub.services.artifacts import service as artifacts
from apps.deep_dub.services.devices import service as devices
from apps.deep_dub.services.execution import service as execution
from apps.deep_dub.services.local_pool import LocalCpuPool
from apps.deep_dub.services.observations import deliver_one
from apps.deep_dub.services.processing import decode_artifact
from apps.deep_dub.services.projects import service as projects
from apps.deep_dub.services.runtime import run_cpu_stage,StageResult
from apps.deep_dub.services.versions import service as versions
from tests.test_deep_dub_contracts_live import context,PROFILE,pytestmark


async def setup(actor,mode='local'):
    project=await projects.create(actor,{'name':'[运行器验收] 本机CPU媒体池',
        'source_url':'https://example.invalid/anp-runtime-acceptance'},'runtime-contract-fixture-v1')
    profile=await versions.create_quality_profile(actor,PROFILE)
    run=await execution.queue(actor,project['id'],{'quality_profile_id':profile['id'],
        'purpose':'acceptance','mode':mode,'flow_revision':'local-cpu-runtime-v1','expected_version':project['row_version']})
    await execution.start(actor,run['id'])
    return project,profile,run


async def flush(actor):
    for _ in range(50):
        item=await deliver_one(actor)
        if not item['claimed']:return
        assert item['sent']
    raise AssertionError('观测未排空')


def evidence(name,data):
    Path('.local/deep-dub-runtime-'+name+'.json').write_bytes((json.dumps({
        'checked_at_utc':datetime.now(timezone.utc).isoformat(),
        'scope':'真实本工作区单并发CPU池与子进程；acceptance，非物理CPU独占、GPU或整片RTF验收',
        **data},ensure_ascii=False,indent=2)+'\n').encode())


def test_real_decode_stage_produces_evidence_score_releases_and_replays(context):
    _,tokens=context
    with tempfile.TemporaryDirectory(prefix='anp-runtime-test-') as folder:
        file=Path(folder)/'input.mp4'
        subprocess.run([executable('ffmpeg'),'-v','error','-f','lavfi','-i','color=c=gray:s=64x64:r=10:d=2',
            '-c:v','mpeg4',str(file)],check=True,capture_output=True,timeout=20)
        async def run():
            actor=await actor_from_token(tokens['admin']);_,_,queued=await setup(actor)
            try:
                ingest=(await execution.begin_stage(actor,queued['id'],{'node':'ingest_runtime_fixture','input_hash':'e'*64}))['attempt']['id']
                await execution.mark_stage(actor,ingest,'started')
                ingest_started=time.monotonic()
                media=await artifacts.upload(actor,ingest,file,'video/mp4',2000)
                await execution.finish_stage(actor,ingest,{'status':'succeeded','outputs':[media['id']],
                    'elapsed_seconds':time.monotonic()-ingest_started})
                key=str(uuid4())
                result=await decode_artifact(actor,queued['id'],{'artifact_id':media['id']},key)
                assert result['status']=='succeeded'
                attempt=result['attempt'];assert len(attempt['outputs'])==1
                assert result==await decode_artifact(actor,queued['id'],{'artifact_id':media['id']},key)
                leases=await actor.objects.select('deep_dub_device_leases',select='id,device_id,status,operation_stopped,heartbeat_at,acquired_at',stage_attempt_id='eq.'+attempt['id'])
                assert len(leases)==1 and leases[0]['status']=='released' and leases[0]['operation_stopped']
                assert leases[0]['heartbeat_at']>leases[0]['acquired_at']
                scores=await actor.objects.select('deep_dub_scores',stage_attempt_id='eq.'+attempt['id'])
                assert len(scores)==1 and scores[0]['value']==1 and scores[0]['measurement_status']=='measured'
                proof_file=Path(folder)/'proof.json'
                await artifacts.download(actor,attempt['outputs'][0],proof_file)
                proof=json.loads(proof_file.read_text(encoding='utf8'))
                assert proof['artifact_id']==media['id'] and proof['sha256']==media['sha256'] and proof['decoder_exit_code']==0
                await flush(actor)
                async with async_client(timeout=15,trust_env=False,
                    auth=httpx.BasicAuth(secret('LANGFUSE_PUBLIC_KEY'),secret('LANGFUSE_SECRET_KEY'))) as lf:
                    trace_id=queued['id'].replace('-','')
                    for _ in range(20):
                        observed=await lf.get(value('langfuse','url')+'/api/public/v2/observations',params={'traceId':trace_id,'limit':100})
                        measured=await lf.get(value('langfuse','url')+'/api/public/v3/scores',params={'traceId':trace_id,'limit':100})
                        observations=observed.json().get('data',[]) if observed.is_success else []
                        lf_scores=measured.json().get('data',[]) if measured.is_success else []
                        if observations and any(s['id']==scores[0]['id'] for s in lf_scores):break
                        await asyncio.sleep(1)
                    assert observations and all(x['userId']==actor.user['id'] and x['sessionId']==queued['id'] for x in observations)
                    actual_score=next(s for s in lf_scores if s['id']==scores[0]['id'])
                    assert actual_score['dataType']=='NUMERIC' and actual_score['value']==1
                    assert actual_score['environment']=='acceptance' and all(x['environment']=='acceptance' for x in observations)
                evidence('decode',{'run_id':queued['id'],'attempt_id':attempt['id'],'lease_id':leases[0]['id'],
                    'device_id':leases[0]['device_id'],'score_id':scores[0]['id'],'proof_artifact_id':attempt['outputs'][0],
                    'all_owned_operations_stopped':True,'heartbeat_observed':True,'replay_same_attempt':True,'proof':proof,
                    'langfuse_user_session_and_numeric_score_readback':True})
            finally:await execution.cancel(actor,queued['id'])
        asyncio.run(run())


async def slow_handler(actor,attempt_id,started):
    started.set()
    await _run([executable('ffmpeg'),'-v','error','-re','-f','lavfi','-i','color=c=gray:s=64x64:r=10:d=30',
                '-threads','1','-f','null','-'],40)
    return StageResult()


def test_cancel_run_stops_owned_ffmpeg_and_releases_actual_lease(context):
    _,tokens=context
    async def run():
        actor=await actor_from_token(tokens['admin']);_,_,queued=await setup(actor)
        started=asyncio.Event()
        operation=asyncio.create_task(run_cpu_stage(actor,queued['id'],'cancel_ffmpeg','f'*64,
            lambda current,attempt:slow_handler(current,attempt,started),heartbeat_seconds=.2,lease_seconds=10))
        try:
            await asyncio.wait_for(started.wait(),15)
            await execution.cancel(actor,queued['id'])
            with pytest.raises(BusinessError):await asyncio.wait_for(operation,10)
            stages=await actor.objects.select('deep_dub_stages',run_id='eq.'+queued['id'])
            attempt=stages[0]['current_attempt_id']
            leases=await actor.objects.select('deep_dub_device_leases',select='id,device_id,status,operation_stopped',stage_attempt_id='eq.'+attempt)
            assert len(leases)==1 and leases[0]['status']=='released' and leases[0]['operation_stopped']
            with LocalCpuPool('operator-after-cancel') as pool:
                assert pool.stopped() and pool.previous['jobs'] and all(job['exit_code'] is not None for job in pool.previous['jobs'])
                jobs=pool.previous['jobs']
            await flush(actor)
            evidence('cancel',{'run_id':queued['id'],'attempt_id':attempt,'lease_id':leases[0]['id'],
                              'stage_status':stages[0]['status'],'stopped_jobs':jobs,'lease_released':True})
        finally:
            if not operation.done():operation.cancel()
            await asyncio.gather(operation,return_exceptions=True)
            await execution.cancel(actor,queued['id'])
    asyncio.run(run())


def test_revoked_permission_stops_process_and_requires_operator_reconciliation(context):
    _,tokens=context
    async def run():
        admin=await actor_from_token(tokens['admin'])
        assert await admin.objects.rpc('is_admin')
        password='ANP-Runtime-Test-'+secret('ADMIN_PASSWORD')[:24]
        user=await create_user('anp-test-runtime-worker@anp.local',password,'developer',admin.workspace_id)
        # 管理员正常创建独立测试身份；业务运行始终使用该用户JWT，未修改其他账号权限。
        async with async_client(timeout=20,trust_env=False) as c:
            changed=await c.put(value('supabase','url')+'/auth/v1/admin/users/'+user['id'],
                headers={'apikey':secret('SERVICE_ROLE_KEY'),'Authorization':'Bearer '+secret('SERVICE_ROLE_KEY')},json={'password':password})
            changed.raise_for_status()
            response=await c.post(value('supabase','url')+'/auth/v1/token?grant_type=password',headers={'apikey':secret('ANON_KEY')},
                json={'email':user['email'],'password':password});response.raise_for_status()
        worker=await actor_from_token(response.json()['access_token'])
        with LocalCpuPool('operator-before-revocation-test') as pool:
            device=await devices.register_cpu(admin,pool.key,pool.host)
            device=await devices.confirm_probe(admin,device,pool.proof())
        project,quality,queued=await setup(worker)
        started=asyncio.Event()
        operation=asyncio.create_task(run_cpu_stage(worker,queued['id'],'revoke_ffmpeg','a'*64,
            lambda current,attempt:slow_handler(current,attempt,started),device_id=device['id'],heartbeat_seconds=.2,lease_seconds=5))
        try:
            await asyncio.wait_for(started.wait(),15)
            await admin.objects.rpc('set_member_role',p_workspace=admin.workspace_id,p_user=user['id'],p_role='viewer')
            with pytest.raises(BusinessError) as revoked:await asyncio.wait_for(operation,10)
            assert revoked.value.status==403
            stages=await admin.objects.select('deep_dub_stages',run_id='eq.'+queued['id'])
            attempt=stages[0]['current_attempt_id']
            leases=await admin.objects.select('deep_dub_device_leases',select='id,device_id,status,operation_stopped',stage_attempt_id='eq.'+attempt)
            assert len(leases)==1 and leases[0]['status']=='active' and not leases[0]['operation_stopped']
            await execution.cancel(admin,queued['id'])
            with LocalCpuPool('operator-reconcile-after-revocation') as pool:
                proof=pool.proof();assert proof['all_owned_operations_stopped'] and proof['previous_completed_jobs']
                device=(await admin.objects.select('deep_dub_devices',id='eq.'+device['id']))[0]
                with pytest.raises(BusinessError) as blocked:await devices.confirm_probe(admin,device,proof)
                assert blocked.value.code=='PT409' and '租约' in blocked.value.message
                for _ in range(60):
                    device=(await admin.objects.select('deep_dub_devices',id='eq.'+device['id']))[0]
                    try:
                        await admin.objects.rpc('deep_dub_reconcile_device',p_workspace=admin.workspace_id,p_device=device['id'],
                            p_expected_version=device['row_version'],p_lease_ids=[leases[0]['id']],p_probe=proof)
                        break
                    except BusinessError as error:
                        if error.status!=409:raise
                        await asyncio.sleep(.2)
                else:raise AssertionError('租约正常到期后仍无法凭真实停止证据核对')
            released=(await admin.objects.select('deep_dub_device_leases',select='id,status,operation_stopped',id='eq.'+leases[0]['id']))[0]
            assert released['status']=='released' and released['operation_stopped']
            await flush(admin)
            evidence('revocation',{'run_id':queued['id'],'attempt_id':attempt,'lease_id':released['id'],
                'test_user_id':user['id'],'permission_revocation_observed':True,'stopped_jobs':proof['previous_completed_jobs'],
                'unacknowledged_lease_not_implicitly_released':True,'operator_reconciliation_after_expiry':True})
        finally:
            if not operation.done():operation.cancel()
            await asyncio.gather(operation,return_exceptions=True)
            await admin.objects.rpc('set_member_role',p_workspace=admin.workspace_id,p_user=user['id'],p_role='viewer')
            await execution.cancel(admin,queued['id'])
    asyncio.run(run())
