"""继续核验同一真实待审运行；不重新下载、生成模型输出或回填原失败。"""
import asyncio,json,os,subprocess,sys,time
from pathlib import Path
from uuid import uuid4
import jwt
import pytest
from anp import ROOT
from anp.auth.identity import actor_from_token
from anp.execution.agent_server import engine_client
from apps.deep_dub.services.source_jobs import vault
from apps.deep_dub.services.source_orchestration import read
from apps.deep_dub.services.delegation import _protect_directory
from tests.test_deep_dub_runtime_live import flush

pytestmark=pytest.mark.skipif(os.environ.get('ANP_DEEP_DUB_SOURCE_PREPARATION_RECOVERY_LIVE')!='1',reason='仅继续本轮保留的真实待审运行验收')


def test_existing_real_preparation_review_scoped_leases_and_cli_replays():
    plan=json.loads((ROOT/'.local/deep-dub-source-preparation-plan.json').read_bytes())
    run_id=plan['run_id'];procedure=plan['grant']['procedure_id'];key=plan['request_key']
    grant_record=vault.load(vault.engine_token(run_id,procedure),run_id,procedure)
    # 只恢复本次验收客户端已有会话，放在原私有授权目录下；不向引擎或浏览器传JWT。
    client_root=vault.directory/'replay-client';auth=client_root/'auth';auth.mkdir(parents=True,exist_ok=True)
    _protect_directory(str(auth.resolve()));profile='source-recovery-'+uuid4().hex;session=auth/(profile+'.json')
    with session.open('x',encoding='utf-8') as file:
        json.dump({'access_token':grant_record['actor_token'],'expires_at':jwt.decode(grant_record['actor_token'],options={'verify_signature':False})['exp']},file)
    os.chmod(session,0o600)
    async def run():
        actor=await actor_from_token(grant_record['actor_token'],grant_record['workspace_id'])
        state=await read(actor,run_id,procedure)
        assert state['business_status']=='waiting_review' and state['engine_status']=='interrupted' and state['technical_verified_at'] is None
        assert set(state['source_steps'])=={'acquire','decode','recognize','align','reference'}
        assert all(x['status']=='succeeded' for x in state['source_steps'].values())
        stages=await actor.objects.select('deep_dub_stages',run_id='eq.'+run_id);assert len(stages)==6
        ids={s['current_attempt_id'] for s in stages}
        leases=await actor.objects.select('deep_dub_device_leases',select='id,status,operation_stopped',stage_attempt_id='in.('+','.join(ids)+')')
        assert len(leases)==5 and all(x['status']=='released' and x['operation_stopped'] for x in leases)
        base_env={**os.environ,'ANP_STATE_ROOT':str(client_root.resolve()),'ANP_PROFILE':profile,'PYTHONIOENCODING':'utf-8'}
        for options,env in [(('--endpoint','http://127.0.0.1:1'),{**base_env,'ANP_WORKBENCH_API_URL':'http://127.0.0.1:1','ANP_WORKBENCH_WORKER_URL':'http://127.0.0.1:1','ANP_AGENT_URL':'http://127.0.0.1:1'}),
                            (('--transport','http','--endpoint','http://127.0.0.1:8089'),base_env)]:
            def cli():
                result=subprocess.run([sys.executable,'-m','anp.cli.entry',*options,'deep_dub','runs','prepare-source',run_id,
                    '--reference-url',plan['body']['reference_url'],'--request-key',key],cwd=ROOT,env=env,capture_output=True,text=True,encoding='utf-8',timeout=120)
                assert result.returncode==0,'同一待审运行CLI重放失败；不公开会话'
                data=json.loads(result.stdout);assert data['procedure_id']==procedure and data['status']=='waiting_review'
            await asyncio.to_thread(cli)
        async with engine_client('deep_dub') as engine:
            response=await engine.get('/threads/'+state['thread_id']+'/runs');response.raise_for_status();initial=response.json()[-1]
            response=await engine.post('/threads/'+state['thread_id']+'/runs',json={'assistant_id':'deep_dub','command':{'resume':{'approve':True,'status':'passed'}},'multitask_strategy':'reject'})
            response.raise_for_status();resumed=response.json()['run_id']
            for _ in range(100):
                response=await engine.get('/threads/'+state['thread_id']+'/runs/'+resumed);response.raise_for_status()
                current=await read(actor,run_id,procedure)
                if current['engine_status']=='interrupted' and response.json()['status'] in ('success','interrupted'):break
                assert response.json()['status'] not in ('error','timeout')
                await asyncio.sleep(.2)
            else:pytest.fail('未停回独立复核门禁')
        assert {s['current_attempt_id'] for s in await actor.objects.select('deep_dub_stages',run_id='eq.'+run_id)}==ids
        for table in ('deep_dub_audits','deep_dub_segment_sets','deep_dub_translations','deep_dub_timeline_revisions'):
            assert not await actor.objects.select(table,project_id='eq.'+plan['project_id'])
        await flush(actor)
        record={'scope':'同一完整来源准备运行的后续验收；原272.38秒测试的敏感列查询失败保留，本次未重跑来源或模型',
            'project_id':plan['project_id'],'run_id':run_id,'grant':plan['grant'],
            'launched':{'thread_id':state['thread_id'],'agent_run_id':initial['run_id']},'state':await read(actor,run_id,procedure),
            'request_key':key,'body':plan['body'],'stage_attempt_ids':sorted(ids),'actor_user_id':actor.user['id'],
            'real_new_download':True,'real_asr_and_aligner':True,'real_decode_and_official_reference':True,
            'client_approval_cannot_bypass':True,'no_extra_stage_after_resume':True,'local_cli_without_business_web_or_agent_replay':True,
            'http_cli_direct_replay':True,'all_leases_released':True,'no_source_adoption_or_quality_audit':True,
            'original_whole_test_failed_seconds':272.38,'proof_column_error_fixed_without_permission_change':True}
        (ROOT/'.local/deep-dub-source-preparation-live.json').write_bytes(json.dumps(record,ensure_ascii=False,indent=2).encode())
    try:asyncio.run(run())
    finally:session.unlink(missing_ok=True)
