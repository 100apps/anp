"""复用已完成43窗口的原尝试，补验身份语义与CLI；不再次执行GPU模型。"""
import asyncio,json,os,subprocess,sys
from pathlib import Path
from uuid import uuid4
import httpx
import pytest
from anp import ROOT
from anp.auth.session import login,current_actor,session_path
from anp.config import secret,value
from anp.sdk.http import async_client
from apps.deep_dub.services.source_recheck import read_latest
from apps.deep_dub.services.source_review import read as review_queue

pytestmark=pytest.mark.skipif(os.environ.get('ANP_DEEP_DUB_SOURCE_RECHECK_RECOVERY_LIVE')!='1',reason='需原接缝已完成尝试，禁止重新运行模型')


def test_original_attempt_identity_scores_and_cli_replay_without_new_model(monkeypatch):
    record=json.loads((ROOT/'.local/deep-dub-source-recheck-recovery.json').read_bytes())
    monkeypatch.setenv('ANP_PROFILE','source-recheck-recovery-'+uuid4().hex)
    project,source,run=record['project_id'],record['source_id'],record['run_id']
    attempt=record['attempt'];proof=record['readback']['proof']['measurement']
    async def before():
        # 仅找回本测试此前创建的执行账号，不修改密码或扩大角色。
        async with async_client(timeout=20,trust_env=False) as client:
            response=await client.get(value('supabase','url')+'/auth/v1/admin/users/'+attempt['created_by'],
                headers={'Authorization':'Bearer '+secret('SERVICE_ROLE_KEY'),'apikey':secret('SERVICE_ROLE_KEY')})
            response.raise_for_status();user=response.json()
        assert user['email'].startswith('test-admin-') and user['email'].endswith('@anp.local')
        await login(user['email'],'ANP-Test-'+secret('ADMIN_PASSWORD')[:24]);actor=await current_actor()
        assert actor.user['id']==attempt['created_by']
        saved=await read_latest(actor,project,source);assert saved==record['readback']
        runs=await actor.objects.select('deep_dub_runs',id='eq.'+run)
        leases=await actor.objects.select('deep_dub_device_leases',select='id,status,operation_stopped',stage_attempt_id='eq.'+attempt['id'])
        assert len(leases)==1 and leases[0]['status']=='released' and leases[0]['operation_stopped']
        scores=await actor.objects.select('deep_dub_scores',stage_attempt_id='eq.'+attempt['id']);assert len(scores)==4
        async with async_client(timeout=20,trust_env=False,auth=httpx.BasicAuth(secret('LANGFUSE_PUBLIC_KEY'),secret('LANGFUSE_SECRET_KEY'))) as client:
            response=await client.get(value('langfuse','url')+'/api/public/v2/observations',params={'traceId':run.replace('-',''),'limit':100,'fields':'basic,metadata'});response.raise_for_status()
            observations=[o for o in response.json()['data'] if o['name']=='deep_dub/source_boundary_recheck']
            assert len(observations)==1
            observation=observations[0]
            assert observation['userId']==record['task_user_id'] and observation['sessionId']==run
            assert observation['metadata']['attempt_id']==attempt['id']
            response=await client.get(value('langfuse','url')+'/api/public/v3/scores',params={'traceId':run.replace('-',''),'limit':100});response.raise_for_status();found={s['id']:s for s in response.json()['data']}
            for score in scores:assert found[score['id']]['value']==(score['value'] if score['measurement_status']=='measured' else 'not_measured')
        packet=await review_queue(actor,project,source)
        audits=await actor.objects.select('deep_dub_audits',project_id='eq.'+project,order='id.asc')
        old=json.loads((ROOT/'.local/deep-dub-source-review-live.json').read_bytes())
        assert packet['packet_sha256']==old['packet_sha256']
        return actor,runs,leases,scores,packet,audits
    try:
        actor,runs,leases,scores,packet,audits=asyncio.run(before())
        for options in [('--endpoint','http://127.0.0.1:1'),('--transport','http','--endpoint','http://127.0.0.1:8181')]:
            replay=subprocess.run([sys.executable,'-m','anp.cli.entry',*options,'deep_dub','runs','recheck-source-boundaries',run,source,
                '--request-key',attempt['request_key']],cwd=ROOT,capture_output=True,text=True,encoding='utf-8',timeout=180)
            assert replay.returncode==0,replay.stdout+replay.stderr
            assert json.loads(replay.stdout)=={'status':'succeeded','attempt':attempt}
            read=subprocess.run([sys.executable,'-m','anp.cli.entry',*options,'deep_dub','projects','source-boundary-recheck',project,source],
                cwd=ROOT,capture_output=True,text=True,encoding='utf-8',timeout=90)
            assert read.returncode==0 and json.loads(read.stdout)==record['readback']
        async def after():
            assert await actor.objects.select('deep_dub_runs',id='eq.'+run)==runs
            assert runs[0]['status']=='waiting_review' and runs[0]['technical_verified_at'] is None
            stages=await actor.objects.select('deep_dub_stages',run_id='eq.'+run,node='eq.source_boundary_recheck')
            attempts=await actor.objects.select('deep_dub_stage_attempts',stage_id='eq.'+stages[0]['id'])
            assert len(stages)==len(attempts)==1
            assert await actor.objects.select('deep_dub_audits',project_id='eq.'+project,order='id.asc')==audits
            assert not await actor.objects.select('deep_dub_segment_sets',project_id='eq.'+project)
        asyncio.run(after())
        assert proof['session']['requests']==43 and proof['session']['operation_stopped'] and proof['summary']['empty_windows']==0
        result={'project_id':project,'source_id':source,'run_id':run,'result':{'status':'succeeded','attempt':attempt},'readback':record['readback'],
            'actor_user_id':attempt['created_by'],'trace_task_user_id':record['task_user_id'],'lease':leases[0],'score_ids':[s['id'] for s in scores],
            'original_queue_sha256':packet['packet_sha256'],'original_run_unchanged':True,'source_not_modified':True,'source_audits_unchanged_in_recovery':True,'source_audits_present_before_recovery':len(audits),'no_segments':True,
            'langfuse_verified':True,'two_cli_replay_and_read':True,'new_model_in_recovery':False,'retained_initial_test_failure_seconds':46.81,
            'initial_test_failure_reason':'错误将Langfuse运行发起者userId与此次管理员执行者混为一谈；按任务原身份核对并保留执行尝试关联'}
        (ROOT/'.local/deep-dub-source-recheck-live.json').write_bytes(json.dumps(result,ensure_ascii=False,indent=2).encode())
    finally:session_path().unlink(missing_ok=True)
