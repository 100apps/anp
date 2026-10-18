"""真实图→HTTP Worker→原用户权限→CPU解码；仅验收夹具，不代表整片生产。"""
import asyncio
from datetime import datetime, timezone
import json
from pathlib import Path
import socket
import subprocess
import sys
import time
from uuid import uuid4

from langgraph.checkpoint.memory import InMemorySaver
import pytest

from anp import ROOT
from anp.auth.identity import actor_from_token
from anp.sdk.http import async_client
from anp.errors import BusinessError
from anp.config import secret, value
from anp.management.bootstrap import create_user
from apps.deep_dub.flow.decode import build_decode_graph, JobContext
from apps.deep_dub.nodes.media import executable, _run
from apps.deep_dub.services.artifacts import service as artifacts
from apps.deep_dub.services.delegation import issue_decode_job, dispatch_decode_job
from apps.deep_dub.services.processing import describe_decode
from apps.deep_dub.services.runtime import run_cpu_stage, StageResult
from apps.deep_dub.services.local_pool import LocalCpuPool
from apps.deep_dub.services.execution import service as execution
from tests.test_deep_dub_contracts_live import context, pytestmark
from tests.test_deep_dub_runtime_live import setup, flush


def test_real_graph_worker_scope_replay_and_checkpoint_privacy(context, tmp_path):
    _, tokens = context
    movie = tmp_path/'input.mp4'
    subprocess.run([executable('ffmpeg'), '-v', 'error', '-f', 'lavfi', '-i',
        'color=c=gray:s=64x64:r=10:d=2', '-c:v', 'mpeg4', str(movie)],
        check=True, capture_output=True, timeout=20)
    with socket.socket() as listener:
        listener.bind(('127.0.0.1', 0)); port = listener.getsockname()[1]
    endpoint = f'http://127.0.0.1:{port}'
    server = None
    try:
        with (ROOT/'.local/deep-dub-delegation-worker.log').open('wb') as log:
            server = subprocess.Popen([sys.executable, '-m', 'uvicorn',
                'apps.deep_dub.api.application:app', '--host', '127.0.0.1',
                '--port', str(port), '--no-access-log'], cwd=ROOT, stdout=log, stderr=log,
                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))

            async def run():
                async with async_client(timeout=3, trust_env=False) as client:
                    for _ in range(50):
                        assert server.poll() is None, '临时Worker提前退出'
                        try:
                            if (await client.get(endpoint+'/openapi.json')).is_success: break
                        except Exception: pass
                        await asyncio.sleep(.1)
                    else: raise AssertionError('临时Worker未就绪')
                    actor = await actor_from_token(tokens['admin'])
                    _, _, queued = await setup(actor)
                    run_id = queued['id']
                    try:
                        ingest = (await execution.begin_stage(actor, run_id,
                            {'node':'ingest_delegation_fixture', 'input_hash':'d'*64}))['attempt']['id']
                        await execution.mark_stage(actor, ingest, 'started')
                        started = time.monotonic()
                        media = await artifacts.upload(actor, ingest, movie, 'video/mp4', 2000)
                        await execution.finish_stage(actor, ingest, {'status':'succeeded',
                            'outputs':[media['id']], 'elapsed_seconds':time.monotonic()-started})
                        key = str(uuid4())
                        payload = {'artifact_id':media['id']}
                        path = f'/api/apps/deep_dub/runs/{run_id}/decode-job'
                        denied = await client.post(endpoint+path, json=payload,
                            headers={'Authorization':'Bearer '+tokens['viewer']})
                        assert denied.status_code == 403
                        issued = await client.post(endpoint+path, json=payload,
                            headers={'Authorization':'Bearer '+tokens['admin'], 'Idempotency-Key':key})
                        assert issued.status_code == 201, '签发失败，响应不公开以免泄漏凭据'
                        grant = issued.json(); attempt_id = grant['attempt_id']
                        worker_path = endpoint+f'/internal/deep_dub/jobs/{attempt_id}/decode'
                        body = {'run_id':run_id}
                        for bearer in ('fixture-invalid-shared-worker-key', tokens['admin']):
                            rejected = await client.post(worker_path, json=body,
                                headers={'Authorization':'Bearer '+bearer})
                            assert rejected.status_code == 401
                        headers = {'Authorization':'Bearer '+grant['job_token']}
                        wrong_scope = await client.post(worker_path, json={'run_id':str(uuid4())}, headers=headers)
                        assert wrong_scope.status_code == 403
                        extra = await client.post(worker_path, json={**body, 'artifact_id':str(uuid4())}, headers=headers)
                        assert extra.status_code == 422
                        saver = InMemorySaver()
                        graph = build_decode_graph(endpoint, saver)
                        config = {'configurable':{'thread_id':'acceptance-'+str(uuid4())}}
                        initial = {'run_id':run_id, 'attempt_id':attempt_id}
                        result = await graph.ainvoke(initial, config, context=JobContext(grant['job_token']))
                        assert result['result']['status'] == 'succeeded'
                        replayed = await client.post(worker_path, json=body, headers=headers)
                        assert replayed.status_code == 200
                        assert replayed.json() == result['result']
                        snapshots = [item async for item in graph.aget_state_history(config)]
                        # 不在失败断言中输出令牌或整个快照。
                        serialized = repr(saver.storage)+repr(saver.writes)+repr(snapshots)
                        leaked = any(value in serialized for value in (grant['job_token'], tokens['admin']))
                        assert not leaked, '凭据进入检查点或图状态'
                        short = await issue_decode_job(actor, run_id, payload, key, ttl_seconds=1)
                        await asyncio.sleep(1.1)
                        expired = await client.post(worker_path, json=body,
                            headers={'Authorization':'Bearer '+short['job_token']})
                        assert expired.status_code == 401 and expired.json()['error']['code']=='JOB_GRANT_EXPIRED'
                        await execution.cancel(actor, run_id)
                        cancelled = await client.post(worker_path, json=body, headers=headers)
                        assert cancelled.status_code == 409
                        await flush(actor)
                        Path('.local/deep-dub-delegation-live.json').write_text(json.dumps({
                            'checked_at_utc':datetime.now(timezone.utc).isoformat(),
                            'scope':'真实LangGraph→临时HTTP Worker→Supabase/CPU解码；2秒验收夹具，非整片生产',
                            'run_id':run_id, 'attempt_id':attempt_id,
                            'graph_succeeded':True, 'http_replay_same_receipt':True,
                            'checkpoint_contains_credentials':False,
                            'viewer_denied':True, 'jwt_and_shared_key_denied':True,
                            'wrong_scope_denied':True, 'extra_parameters_denied':True,
                            'expired_grant_denied':True, 'cancelled_run_denied':True,
                            'agent_server_deployment_verified':False,
                        }, ensure_ascii=False, indent=2)+'\n', encoding='utf8')
                    finally:
                        await execution.cancel(actor, run_id)
                        await flush(actor)
            asyncio.run(run())
    finally:
        if server is not None and server.poll() is None:
            server.terminate()
            try: server.wait(timeout=10)
            except subprocess.TimeoutExpired:
                server.kill(); server.wait(timeout=10)


def test_grant_expiry_during_owned_process_waits_for_stop(context, tmp_path, monkeypatch):
    """真实租约与进程；以受控长FFmpeg替换解码体，验证委派截止清理，不声称媒体成功。"""
    _, tokens = context

    async def run():
        actor = await actor_from_token(tokens['admin'])
        _, _, queued = await setup(actor)
        run_id = queued['id']
        path = tmp_path/'fixture.json'
        path.write_text('{"scope":"delegation-expiry-acceptance"}', encoding='utf8')
        started = asyncio.Event()
        try:
            ingest = (await execution.begin_stage(actor, run_id,
                {'node':'ingest_expiry_fixture','input_hash':'c'*64}))['attempt']['id']
            await execution.mark_stage(actor, ingest, 'started')
            stamp = time.monotonic()
            artifact = await artifacts.upload(actor, ingest, path, 'application/json')
            await execution.finish_stage(actor, ingest, {'status':'succeeded', 'outputs':[artifact['id']],
                'elapsed_seconds':time.monotonic()-stamp})
            key = str(uuid4()); payload = {'artifact_id':artifact['id']}
            grant = await issue_decode_job(actor, run_id, payload, key, ttl_seconds=4)

            async def controlled_decode(current, selected_run, parameters, request_key):
                body, item, fingerprint = await describe_decode(current, selected_run, parameters)
                async def handler(current_actor, attempt_id):
                    started.set()
                    await _run([executable('ffmpeg'), '-v', 'error', '-re', '-f', 'lavfi', '-i',
                        'color=c=gray:s=64x64:r=10:d=30', '-f', 'null', '-'], 40)
                    return StageResult()
                return await run_cpu_stage(current, selected_run, 'full_decode', fingerprint, handler,
                    partition=item['id'], request_key=request_key, heartbeat_seconds=.2, lease_seconds=5)

            monkeypatch.setattr('apps.deep_dub.services.delegation.decode_artifact', controlled_decode)
            operation = asyncio.create_task(dispatch_decode_job(grant['job_token'], run_id, grant['attempt_id']))
            await asyncio.wait_for(started.wait(), 3)
            with pytest.raises(BusinessError) as expired:
                await asyncio.wait_for(operation, 10)
            assert expired.value.code == 'JOB_GRANT_EXPIRED'
            leases = await actor.objects.select('deep_dub_device_leases',
                select='id,status,operation_stopped', stage_attempt_id='eq.'+grant['attempt_id'])
            assert len(leases)==1 and leases[0]['status']=='released' and leases[0]['operation_stopped']
            with LocalCpuPool('operator-after-delegation-expiry') as pool:
                jobs = pool.proof()['previous_completed_jobs']
                assert jobs and all(job['exit_code'] is not None for job in jobs)
            stages = await actor.objects.select('deep_dub_stages', run_id='eq.'+run_id, node='eq.full_decode')
            assert stages and stages[0]['status']=='failed'
            Path('.local/deep-dub-delegation-expiry-live.json').write_text(json.dumps({
                'checked_at_utc':datetime.now(timezone.utc).isoformat(),
                'scope':'受控30秒FFmpeg代替解码体，真实委派TTL/CPU租约/物理停止；非媒体成功验收',
                'run_id':run_id, 'attempt_id':grant['attempt_id'], 'lease_id':leases[0]['id'],
                'deadline_error':'JOB_GRANT_EXPIRED', 'stage_status':'failed',
                'lease_released':True, 'owned_processes_stopped':jobs,
            }, ensure_ascii=False, indent=2)+'\n', encoding='utf8')
        finally:
            if 'operation' in locals() and not operation.done():
                operation.cancel(); await asyncio.gather(operation, return_exceptions=True)
            await execution.cancel(actor, run_id)
            await flush(actor)
    asyncio.run(run())


def test_valid_grant_does_not_cache_revoked_user_permissions(context, tmp_path):
    """单独验收用户撤权，不修改管理员、通用viewer或其他运行任务身份。"""
    _, tokens = context
    async def run():
        admin = await actor_from_token(tokens['admin'])
        password = 'ANP-Delegation-Test-'+secret('ADMIN_PASSWORD')[:24]
        user = await create_user('anp-test-delegation-worker@anp.local', password, 'developer', admin.workspace_id)
        queued = None
        try:
            async with async_client(timeout=20, trust_env=False) as client:
                response = await client.post(value('supabase','url')+'/auth/v1/token?grant_type=password',
                    headers={'apikey':secret('ANON_KEY')}, json={'email':user['email'],'password':password})
                assert response.is_success, '专用测试用户登录失败'
                worker = await actor_from_token(response.json()['access_token'])
            _, _, queued = await setup(worker)
            ingest = (await execution.begin_stage(worker, queued['id'],
                {'node':'ingest_revoke_grant_fixture','input_hash':'b'*64}))['attempt']['id']
            await execution.mark_stage(worker, ingest, 'started')
            stamp = time.monotonic()
            path = tmp_path/'fixture.json'; path.write_text('{"scope":"revocation"}',encoding='utf8')
            artifact = await artifacts.upload(worker, ingest, path, 'application/json')
            await execution.finish_stage(worker, ingest, {'status':'succeeded','outputs':[artifact['id']],
                'elapsed_seconds':time.monotonic()-stamp})
            grant = await issue_decode_job(worker, queued['id'], {'artifact_id':artifact['id']}, str(uuid4()))
            await admin.objects.rpc('set_member_role', p_workspace=admin.workspace_id, p_user=user['id'], p_role='viewer')
            with pytest.raises(BusinessError) as denied:
                await dispatch_decode_job(grant['job_token'], queued['id'], grant['attempt_id'])
            assert denied.value.status==403 and denied.value.code=='FORBIDDEN'
            leases = await admin.objects.select('deep_dub_device_leases', select='id,status',
                stage_attempt_id='eq.'+grant['attempt_id'])
            assert not leases
            Path('.local/deep-dub-delegation-revocation-live.json').write_text(json.dumps({
                'checked_at_utc':datetime.now(timezone.utc).isoformat(),
                'scope':'真实Supabase专用身份签发后撤权，未执行媒体操作',
                'run_id':queued['id'], 'attempt_id':grant['attempt_id'], 'test_user_id':user['id'],
                'unexpired_grant_permission_rechecked':True, 'device_lease_created':False,
            }, ensure_ascii=False, indent=2)+'\n', encoding='utf8')
        finally:
            await admin.objects.rpc('set_member_role', p_workspace=admin.workspace_id, p_user=user['id'], p_role='viewer')
            if queued is not None:
                await execution.cancel(admin, queued['id']); await flush(admin)
    asyncio.run(run())
