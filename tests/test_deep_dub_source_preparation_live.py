"""正式Agent真实来源准备至待复核；保留中断供重建持久化验收，不是完整译配。"""
import asyncio,json,os,subprocess,sys,time
from uuid import uuid4
import pytest
from anp import ROOT
from anp.auth.identity import actor_from_token
from anp.auth.session import login,session_path
from anp.sdk.http import async_client
from anp.execution.agent_server import engine_client
from apps.deep_dub.services.projects import service as projects
from apps.deep_dub.services.versions import service as versions
from apps.deep_dub.services.execution import service as execution
from apps.deep_dub.services.source_acquisition import configuration
from apps.deep_dub.nodes.source_fetch import command
from apps.deep_dub.services.source_orchestration import read
from tests.test_deep_dub_contracts_live import PROFILE
from tests.test_deep_dub_runtime_live import flush

pytestmark=pytest.mark.skipif(os.environ.get('ANP_DEEP_DUB_SOURCE_PREPARATION_LIVE')!='1',reason='需正式冻结平台/Worker、真实下载、两模型及官方参考')


def test_agent_whole_source_preparation_stops_at_review_and_local_replays_without_business_web(identities,monkeypatch):
    monkeypatch.setenv('ANP_PROFILE','source-preparation-'+uuid4().hex);monkeypatch.setenv('PYTHONIOENCODING','utf-8')
    async def run():
        actor=await actor_from_token(identities['admin']['token'])
        project=await projects.create(actor,{'name':'[来源编排验收] Yale全片至待复核','source_url':'https://www.youtube.com/watch?v=QXXp1bHd6gI'})
        profile=await versions.create_quality_profile(actor,PROFILE)
        queued=await execution.queue(actor,project['id'],{'quality_profile_id':profile['id'],'purpose':'acceptance',
            'flow_revision':'source-preparation-flow-v1','expected_version':project['row_version']})
        await execution.start(actor,queued['id']);success=False
        key=uuid4().hex;body={'reference_url':'https://oyc.yale.edu/history/hist-119/lecture-1','ttl_seconds':600}
        base='http://127.0.0.1:8089/api/apps/deep_dub/runs/'+queued['id']
        try:
            async with async_client(timeout=30,trust_env=False,headers={'Authorization':'Bearer '+identities['admin']['token']}) as client:
                assert (await client.post(base+'/source-preparation-job',json=body,headers={'Authorization':'Bearer '+identities['viewer']['token']})).status_code==403
                assert (await client.post(base+'/source-preparation-job',json=body,headers={'Authorization':''})).status_code==401
                assert (await client.post(base+'/source-preparation-job',json=body,headers={'Authorization':'Bearer '+identities['outside']['token']})).status_code in (403,404)
                response=await client.post(base+'/source-preparation-job',json=body,headers={'Idempotency-Key':key})
                assert response.status_code==200,'授权失败，不输出原始会话'
                grant=response.json();folder=ROOT/'.local/deep_dub/source-fetch'/grant['download_attempt_id']
                plan={'project_id':project['id'],'run_id':queued['id'],'grant':grant,'request_key':key,'body':body,
                    'command':command(configuration(),'https://www.youtube.com/watch?v=QXXp1bHd6gI',folder)}
                (ROOT/'.local/deep-dub-source-preparation-plan.json').write_bytes(json.dumps(plan,ensure_ascii=False,indent=2).encode())
                if os.environ.get('ANP_DEEP_DUB_SOURCE_PREPARATION_SHOW_COMMAND')=='1':
                    marker=ROOT/'.local/deep-dub-source-preparation-go.json'
                    for _ in range(600):
                        if marker.is_file() and json.loads(marker.read_bytes()).get('procedure_id')==grant['procedure_id']:break
                        await asyncio.sleep(1)
                    else:pytest.fail('下载命令尚未展示，未启动图')
                response=await client.post(base+'/source-preparation',json=body,headers={'Idempotency-Key':key})
                if response.status_code!=200:(ROOT/'.local/deep-dub-source-preparation-last-failure.json').write_bytes(response.content)
                assert response.status_code==200,'平台图启动失败，诊断留私有回执'
                launched=response.json();assert launched['procedure_id']==grant['procedure_id']
                deadline=time.monotonic()+620
                while True:
                    state=await read(actor,queued['id'],grant['procedure_id'])
                    if state['engine_status']=='interrupted' and state['business_status']=='waiting_review':break
                    if state['engine_status'] in ('error','timeout','success'):
                        async with engine_client('deep_dub') as engine:
                            diagnostic=await engine.get(f'/threads/{launched["thread_id"]}/runs/{launched["agent_run_id"]}')
                            (ROOT/'.local/deep-dub-source-preparation-engine-failure.json').write_bytes(diagnostic.content)
                        pytest.fail('图未停在来源复核门禁；原始诊断保留')
                    assert time.monotonic()<deadline,'来源准备超过验收等待上限；不自动重启'
                    await asyncio.sleep(2)
                assert set(state['source_steps'])=={'acquire','decode','recognize','align','reference'}
                assert all(v['status']=='succeeded' for v in state['source_steps'].values()) and state['technical_verified_at'] is None
                stages=await actor.objects.select('deep_dub_stages',run_id='eq.'+queued['id']);assert len(stages)==6
                attempt_ids={s['current_attempt_id'] for s in stages}
                latest=await client.get(base+'/source-preparation',headers={'Authorization':'Bearer '+identities['viewer']['token']})
                assert latest.status_code==200 and latest.json()==state
                assert (await client.get(base+'/source-preparation',headers={'Authorization':''})).status_code==401
                assert (await client.get(base+'/source-preparation',headers={'Authorization':'Bearer '+identities['outside']['token']})).status_code in (403,404)
                async with engine_client('deep_dub') as engine:
                    resumed=await engine.post(f'/threads/{launched["thread_id"]}/runs',json={'assistant_id':'deep_dub','command':{'resume':{'approve':True,'status':'passed'}},'multitask_strategy':'reject'})
                    assert resumed.is_success
                    resumed_id=resumed.json()['run_id']
                    for _ in range(100):
                        observed=await engine.get(f'/threads/{launched["thread_id"]}/runs/{resumed_id}');observed.raise_for_status()
                        refreshed=await read(actor,queued['id'],grant['procedure_id'])
                        if refreshed['engine_status']=='interrupted' and observed.json()['status'] in ('interrupted','success'):break
                        assert observed.json()['status'] not in ('error','timeout')
                        await asyncio.sleep(.2)
                    else:pytest.fail('恢复输入未重新停在待审门禁')
                assert {s['current_attempt_id'] for s in await actor.objects.select('deep_dub_stages',run_id='eq.'+queued['id'])}==attempt_ids
            await login(identities['admin']['email'],identities['admin']['password'])
            local_env={**os.environ,'ANP_WORKBENCH_API_URL':'http://127.0.0.1:1','ANP_WORKBENCH_WORKER_URL':'http://127.0.0.1:1','ANP_AGENT_URL':'http://127.0.0.1:1'}
            for options,env in [(('--endpoint','http://127.0.0.1:1'),local_env),(('--transport','http','--endpoint','http://127.0.0.1:8089'),os.environ.copy())]:
                def cli():
                    result=subprocess.run([sys.executable,'-m','anp.cli.entry',*options,'deep_dub','runs','prepare-source',queued['id'],'--reference-url',body['reference_url'],'--request-key',key],
                        cwd=ROOT,env=env,capture_output=True,text=True,encoding='utf-8',timeout=120)
                    if result.returncode:(ROOT/'.local/deep-dub-source-preparation-cli-failure.json').write_text(result.stdout,encoding='utf-8')
                    assert result.returncode==0,'直接本地/HTTP准备重放失败；错误留私有文件'
                    return json.loads(result.stdout)
                replay=await asyncio.to_thread(cli);assert replay['status']=='waiting_review' and replay['procedure_id']==grant['procedure_id']
            leases=await actor.objects.select('deep_dub_device_leases',select='id,status,operation_stopped',stage_attempt_id='in.('+','.join(attempt_ids)+')')
            assert len(leases)==5 and all(x['status']=='released' and x['operation_stopped'] for x in leases)
            for table in ('deep_dub_audits','deep_dub_segment_sets','deep_dub_translations','deep_dub_timeline_revisions'):
                assert not await actor.objects.select(table,project_id='eq.'+project['id'])
            await flush(actor)
            record={'project_id':project['id'],'run_id':queued['id'],'grant':grant,'launched':launched,'state':await read(actor,queued['id'],grant['procedure_id']),
                'request_key':key,'body':body,'stage_attempt_ids':sorted(attempt_ids),'actor_user_id':actor.user['id'],
                'real_new_download':True,'real_asr_and_aligner':True,'real_decode_and_official_reference':True,
                'client_approval_cannot_bypass':True,'no_extra_stage_after_resume':True,'local_cli_without_business_web_or_agent_replay':True,
                'http_cli_direct_replay':True,'all_leases_released':True,'no_source_adoption_or_quality_audit':True}
            (ROOT/'.local/deep-dub-source-preparation-live.json').write_bytes(json.dumps(record,ensure_ascii=False,indent=2).encode());success=True
        finally:
            current=(await actor.objects.select('deep_dub_runs',id='eq.'+queued['id']))[0]
            if not (os.environ.get('ANP_DEEP_DUB_SOURCE_PREPARATION_KEEP')=='1' and (success or current['status']=='waiting_review')):
                await execution.cancel(actor,queued['id']);await flush(actor)
    try:asyncio.run(run())
    finally:session_path().unlink(missing_ok=True)
