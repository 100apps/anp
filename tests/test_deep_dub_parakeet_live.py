"""完整待审PCM的Parakeet盲转录；不放行来源或复用旧成片。"""
import asyncio,json,os,site,subprocess,sys
from uuid import uuid4
from pathlib import Path
import pytest,httpx
from anp import ROOT
from anp.auth.session import login,current_actor,session_path
from anp.config import secret,value
from anp.sdk.http import async_client
from apps.deep_dub.services.source_independent_asr import read_latest,checked
from apps.deep_dub.services.source_review import read as read_queue
from tests.test_deep_dub_runtime_live import flush

pytestmark=pytest.mark.skipif(os.environ.get('ANP_DEEP_DUB_PARAKEET_LIVE')!='1',reason='需显式完整盲转录与已配置模型；不自动重复未知计算')


def test_whole_source_blind_words_preserve_original_gate_and_two_cli(identities,monkeypatch):
    monkeypatch.setenv('ANP_PROFILE','parakeet-live-'+uuid4().hex)
    planfile=ROOT/'.local/deep-dub-parakeet-plan.json'
    async def scenario():
        await login(secret('ADMIN_EMAIL'),secret('ADMIN_PASSWORD'));actor=await current_actor()
        if planfile.exists():plan=json.loads(planfile.read_bytes())
        else:
            original=json.loads((ROOT/'.local/deep-dub-source-preparation-final.json').read_bytes())
            project,source,run=original['project_id'],original['acquisition']['source_id'],original['run_id']
            before=(await actor.objects.select('deep_dub_runs',id='eq.'+run))[0]
            task=(await actor.objects.select('tasks',id='eq.'+before['task_id']))[0]
            queue=await read_queue(actor,project,source);old=await read_latest(actor,project,source)
            plan={'project_id':project,'source_id':source,'run_id':run,'request_key':uuid4().hex,
                'body':{'source_id':source,'timeout_seconds':3600,'recognizer':'parakeet'},'before_run':before,
                'queue_sha256':queue['packet_sha256'],'nemotron_evidence_artifact_id':old['evidence_artifact_id'],
                'task_user_id':task['created_by']}
            planfile.write_bytes((json.dumps(plan,ensure_ascii=False,indent=2)+'\n').encode())
        async with async_client(base_url='http://127.0.0.1:8181',timeout=3800,trust_env=False,
            headers={'Authorization':'Bearer '+actor.token,'X-Workspace-ID':actor.workspace_id}) as client:
            path=f'/api/apps/deep_dub/runs/{plan["run_id"]}/recognize-source-independent'
            assert (await client.post(path,json=plan['body'],headers={'Authorization':''})).status_code==401
            assert (await client.post(path,json=plan['body'],headers={'Authorization':'Bearer '+identities['viewer']['token']})).status_code==403
            response=await client.post(path,json=plan['body'],headers={'Idempotency-Key':plan['request_key']})
            if response.status_code!=200:(ROOT/'.local/deep-dub-parakeet-last-response.json').write_bytes(response.content)
            assert response.status_code==200,'完整转录未完成，保留原计划/失败/未知作业，不换键重跑'
            result=response.json();assert result['status']=='succeeded'
        data=await read_latest(actor,plan['project_id'],plan['source_id'],'parakeet');assert data['stage_attempt_id']==result['attempt']['id']
        proof=data['proof'];measured=proof['measurement'];assert measured['summary']['sample_coverage']==1
        assert measured['pcm']['sample_count']==41380932 and len(measured['chunks'])==87
        assert measured['model']['family']=='parakeet_tdt' and measured['session']['operation_stopped']
        words=sum(len(c['recognition']['words']) for c in measured['chunks']);assert words>0
        assert all(c['recognition']['context_prompt']=='' and c['recognition']['word_timing_review']=='not_measured' for c in measured['chunks'])
        assert (await actor.objects.select('deep_dub_runs',id='eq.'+plan['run_id']))[0]==plan['before_run']
        assert (await read_queue(actor,plan['project_id'],plan['source_id']))['packet_sha256']==plan['queue_sha256']
        assert (await read_latest(actor,plan['project_id'],plan['source_id']))['evidence_artifact_id']==plan['nemotron_evidence_artifact_id']
        assert not await actor.objects.select('deep_dub_segments',source_id='eq.'+plan['source_id'])
        leases=await actor.objects.select('deep_dub_device_leases',select='id,status,operation_stopped',stage_attempt_id='eq.'+result['attempt']['id'])
        assert len(leases)==1 and leases[0]['status']=='released' and leases[0]['operation_stopped']
        scores=await actor.objects.select('deep_dub_scores',stage_attempt_id='eq.'+result['attempt']['id']);assert len(scores)==6
        await flush(actor)
        async with async_client(timeout=20,trust_env=False,auth=httpx.BasicAuth(secret('LANGFUSE_PUBLIC_KEY'),secret('LANGFUSE_SECRET_KEY'))) as lf:
            for _ in range(30):
                response=await lf.get(value('langfuse','url')+'/api/public/v3/scores',params={'traceId':plan['run_id'].replace('-',''),'limit':100});response.raise_for_status()
                found={s['id']:s for s in response.json()['data']}
                if all(s['id'] in found for s in scores):break
                await asyncio.sleep(.5)
            assert all(found[s['id']]['value']==(s['value'] if s['measurement_status']=='measured' else 'not_measured') for s in scores)
        env={**os.environ,'PYTHONPATH':os.pathsep.join((str(ROOT),str(ROOT/'src'),*site.getsitepackages()))}
        args=['deep_dub','runs','recognize-source-independent',plan['run_id'],plan['source_id'],'--recognizer','parakeet','--request-key',plan['request_key']]
        for options in [('--endpoint','http://127.0.0.1:1'),('--transport','http','--endpoint','http://127.0.0.1:8181')]:
            reply=await asyncio.to_thread(subprocess.run,[sys._base_executable,'-m','anp.cli.entry',*options,*args],cwd=ROOT,env=env,capture_output=True,text=True,encoding='utf-8',timeout=120)
            assert reply.returncode==0 and json.loads(reply.stdout)==result,'同一输入只重放原阶段，不重跑模型'
        return {**plan,'result':result,'readback':data,'lease':leases[0],'native_word_count':words,
            'score_ids':[s['id'] for s in scores],'langfuse_verified':True,'two_cli_replay_verified':True,
            'original_run_queue_nemotron_preserved':True,'scope':'完整原音的新盲识别证据；不代表正文/词边界审核、整片生产或RTF'}
    try:
        record=asyncio.run(scenario())
        (ROOT/'.local/deep-dub-parakeet-live.json').write_bytes((json.dumps(record,ensure_ascii=False,indent=2)+'\n').encode())
    finally:session_path().unlink(missing_ok=True)
