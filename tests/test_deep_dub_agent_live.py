"""实际平台Agent加载挂载业务子图，调用宿主Worker；非完整整片生产。"""
import asyncio
from datetime import datetime,timezone
import json
from pathlib import Path
import subprocess
import time
from uuid import uuid4,uuid5,NAMESPACE_URL

from anp.auth.identity import actor_from_token
from anp.execution.agent_server import engine_client
from anp.config import secret
from apps.deep_dub.nodes.media import executable
from apps.deep_dub.services.artifacts import service as artifacts
from apps.deep_dub.services.delegation import issue_decode_job
from apps.deep_dub.services.execution import service as execution
from tests.test_deep_dub_contracts_live import context,pytestmark
from tests.test_deep_dub_runtime_live import setup,flush


def test_deployed_agent_graph_executes_own_worker_with_original_identity(context,tmp_path):
    _,tokens=context
    movie=tmp_path/'agent-input.mp4'
    subprocess.run([executable('ffmpeg'),'-v','error','-f','lavfi','-i',
        'color=c=gray:s=64x64:r=10:d=2','-c:v','mpeg4',str(movie)],check=True,capture_output=True,timeout=20)
    async def run():
        actor=await actor_from_token(tokens['admin'])
        _,_,queued=await setup(actor,mode='http')
        try:
            attempt=(await execution.begin_stage(actor,queued['id'],
                {'node':'ingest_agent_fixture','input_hash':'a'*64}))['attempt']['id']
            await execution.mark_stage(actor,attempt,'started');started=time.monotonic()
            media=await artifacts.upload(actor,attempt,movie,'video/mp4',2000)
            await execution.finish_stage(actor,attempt,{'status':'succeeded','outputs':[media['id']],
                'elapsed_seconds':time.monotonic()-started})
            grant=await issue_decode_job(actor,queued['id'],{'artifact_id':media['id']},str(uuid4()))
            thread=str(uuid5(NAMESPACE_URL,'anp:deep_dub:acceptance:'+grant['attempt_id']))
            async with engine_client('deep_dub') as client:
                assistants=await client.post('/assistants/search',json={'graph_id':'deep_dub','limit':10})
                assert assistants.is_success and assistants.json(),'Agent尚未发现挂载业务图'
                created=await client.post('/threads',json={'thread_id':thread,'if_exists':'do_nothing',
                    'metadata':{'app_id':'deep_dub','purpose':'acceptance','run_id':queued['id']}})
                assert created.is_success
                response=await client.post(f'/threads/{thread}/runs/wait',json={
                    'assistant_id':'deep_dub','input':{'run_id':queued['id'],'attempt_id':grant['attempt_id']},
                    'multitask_strategy':'reject'})
                assert response.is_success,'Agent实际图执行请求失败'
                state=response.json()
                assert state.get('result',{}).get('status')=='succeeded','图执行未取得成功业务回执'
                assert state['result']['attempt']['id']==grant['attempt_id']
                saved=await client.get(f'/threads/{thread}/state')
                assert saved.is_success
                leaked=any(credential in json.dumps(saved.json()) for credential in (grant['job_token'],tokens['admin'],secret('WORKER_TOKEN')))
                assert not leaked,'图状态响应含凭据'
                runs=await client.get(f'/threads/{thread}/runs');assert runs.is_success
                assert runs.json() and runs.json()[0]['status']=='success'
                agent_run=runs.json()[0]['run_id']
            # 这里只完成了解码，业务运行不能被通用协调器改成整片完成。
            await asyncio.sleep(1.5)
            current=(await actor.objects.select('deep_dub_runs',id='eq.'+queued['id']))[0]
            task=(await actor.objects.select('tasks',id='eq.'+queued['task_id']))[0]
            assert current['status']=='running' and current['technical_verified_at'] is None
            assert task['status']=='running' and task['mode']=='http'
            leases=await actor.objects.select('deep_dub_device_leases',select='id,status,operation_stopped',
                stage_attempt_id='eq.'+grant['attempt_id'])
            assert len(leases)==1 and leases[0]['status']=='released' and leases[0]['operation_stopped']
            await flush(actor)
            Path('.local/deep-dub-agent-live.json').write_bytes((json.dumps({
                'checked_at_utc':datetime.now(timezone.utc).isoformat(),
                'scope':'实际单平台容器Agent→挂载业务解码子图→8282 Worker→两秒验收媒体；不是整片生产',
                'run_id':queued['id'],'attempt_id':grant['attempt_id'],'thread_id':thread,'agent_run_id':agent_run,
                'agent_status':'success','business_stage_status':'succeeded','business_run_status_before_cleanup':'running',
                'business_task_mode':'http','generic_coordinator_must_not_complete_business':True,
                'technical_verified_at_before_cleanup':None,'credentials_in_thread_state':False,
                'device_lease_released':True,'original_user_id':actor.user['id'],
                'job_token_sent_to_agent':False,'agent_credential_source':'平台原有环境，不放入运行上下文',
            },ensure_ascii=False,indent=2)+'\n').encode())
        finally:
            await execution.cancel(actor,queued['id']);await flush(actor)
    asyncio.run(run())
