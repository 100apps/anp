"""已完成整轨听音的原尝试恢复核验；只重放，不重新推理或回填首次失败。"""
import asyncio,json,os,site,subprocess,sys
from hashlib import sha256
from uuid import uuid4
import httpx
import pytest
from anp import ROOT
from anp.auth.session import login,current_actor,session_path
from anp.config import secret,value
from anp.sdk.http import async_client
from apps.deep_dub.services.auditory_review import read_latest,checked
from apps.deep_dub.services.source_review import read as read_queue
from tests.test_deep_dub_source_acquisition_live import flush

pytestmark=pytest.mark.skipif(os.environ.get('ANP_DEEP_DUB_AUDITORY_RECOVERY_LIVE')!='1',reason='需本轮已经完成的完整听音原尝试，不得用来启动新模型')


def test_original_completed_whole_audio_evidence_cli_scores_and_no_new_model(identities,monkeypatch):
    plan=json.loads((ROOT/'.local/deep-dub-auditory-plan.json').read_bytes())
    project,source,run=plan['project_id'],plan['source_id'],plan['run_id']
    journal=ROOT/'.local/deep_dub/gpu-pool/1eb343271a51306f697a263a.json'
    before_journal=json.loads(journal.read_bytes());assert before_journal['state']=='idle'
    digest=sha256(journal.read_bytes()).hexdigest()
    monkeypatch.setenv('ANP_PROFILE','auditory-recovery-'+uuid4().hex)
    async def scenario():
        await login(secret('ADMIN_EMAIL'),secret('ADMIN_PASSWORD'));actor=await current_actor()
        stages=await actor.objects.select('deep_dub_stages',run_id='eq.'+run,node='eq.source_content_review',partition_key='eq.'+source)
        attempts=await actor.objects.select('deep_dub_stage_attempts',stage_id='in.('+','.join(s['id'] for s in stages)+')',status='eq.succeeded',order='finished_at.desc')
        assert len(attempts)==1;attempt=attempts[0]
        assert attempt['created_by']==plan['actor_user_id'] and attempt['request_key']==plan['request_key']
        async with async_client(base_url='http://127.0.0.1:8181',timeout=120,trust_env=False) as client:
            headers={'Authorization':'Bearer '+actor.token,'X-Workspace-ID':actor.workspace_id,'Idempotency-Key':plan['request_key']}
            response=await client.post(f'/api/apps/deep_dub/runs/{run}/review-source-audio',json=plan['body'],headers=headers)
            assert response.status_code==200,response.text[:500]
            result=response.json();assert result=={'status':'succeeded','attempt':attempt}
            path=f'/api/apps/deep_dub/projects/{project}/sources/{source}/auditory-review'
            response=await client.get(path,headers=headers);assert response.status_code==200
            saved=response.json()
            assert (await client.get(path)).status_code==401
            outside=identities['outside'];viewer=identities['viewer']
            assert (await client.get(path,headers={'Authorization':'Bearer '+outside['token'],'X-Workspace-ID':outside['workspace_id']})).status_code==404
            assert (await client.post(f'/api/apps/deep_dub/runs/{run}/review-source-audio',json=plan['body'],headers={
                'Authorization':'Bearer '+viewer['token'],'X-Workspace-ID':viewer['workspace_id']})).status_code==403
        assert saved==await read_latest(actor,project,source)
        # 同一不可变回执任意排列仍须识别同一JSON，不能依赖数组第一项。
        proof,note=await checked(actor,{**attempt,'outputs':list(reversed(attempt['outputs']))})
        assert note==saved['evidence_artifact_id'] and proof==saved['proof']
        m=proof['measurement'];assert len(m['controls'])==10 and len(proof['control_artifact_ids'])==10
        assert len(attempt['outputs'])==11 and len(m['windows'])==44 and sum(m['counts'].values())==44
        assert m['session']['requests']==54 and m['session']['operation_stopped'] and m['model']['family']=='qwen2_5_omni'
        assert m['pcm']['sample_count']==41380932 and m['calibration_summary']['decision_correct']==10
        assert m['calibration_summary']['critical_excerpt_correct']==5
        assert all(m[k]=='not_measured' for k in ('formal_transcript_review','speaker_identity','subtitle_timing_review'))
        assert (await actor.objects.select('deep_dub_runs',id='eq.'+run))[0]==plan['before_run']
        assert (await read_queue(actor,project,source))['packet_sha256']==plan['queue_sha256']
        assert not await actor.objects.select('deep_dub_segment_sets',project_id='eq.'+project)
        assert not await actor.objects.select('deep_dub_speakers',project_id='eq.'+project)
        assert not await actor.objects.select('deep_dub_translations',project_id='eq.'+project)
        leases=await actor.objects.select('deep_dub_device_leases',select='id,status,operation_stopped',stage_attempt_id='eq.'+attempt['id'])
        assert len(leases)==1 and leases[0]['status']=='released' and leases[0]['operation_stopped']
        scores=await actor.objects.select('deep_dub_scores',stage_attempt_id='eq.'+attempt['id']);assert len(scores)==10
        audits=await actor.objects.select('deep_dub_audits',project_id='eq.'+project,evidence_artifact_id='eq.'+note,order='created_at.desc,id.desc')
        if m['counts']['mismatch']:
            assert audits
            if saved['consistency']['identical_excerpt_issues']:
                assert audits[0]['status']=='not_measured' and audits[0]['method']=='technical'
            else:assert audits[0]['status']=='failed' and audits[0]['method']=='independent_model'
        else:assert not audits
        assert all(a['status'] in ('failed','not_measured') for a in audits)
        await flush(actor)
        async with async_client(timeout=20,trust_env=False,auth=httpx.BasicAuth(secret('LANGFUSE_PUBLIC_KEY'),secret('LANGFUSE_SECRET_KEY'))) as client:
            for _ in range(30):
                reply=await client.get(value('langfuse','url')+'/api/public/v3/scores',params={'traceId':run.replace('-',''),'limit':100});reply.raise_for_status()
                found={score['id']:score for score in reply.json()['data']}
                if all(score['id'] in found for score in scores):break
                await asyncio.sleep(.5)
            for score in scores:assert found[score['id']]['value']==(score['value'] if score['measurement_status']=='measured' else 'not_measured')
            reply=await client.get(value('langfuse','url')+'/api/public/v2/observations',params={'traceId':run.replace('-',''),'name':'deep_dub/source_content_review','fields':'basic,metadata','limit':100});reply.raise_for_status()
            observed=[row for row in reply.json()['data'] if row['id']==sha256(attempt['id'].encode()).hexdigest()[:16]]
            # 上游V4 events API保留同span的事件行；评分追加会再次投递相同span。
            # 仅容许所有返回字段完全相同的重复，绝不挑一行掩盖身份或内容冲突。
            assert observed and len({json.dumps(row,sort_keys=True,separators=(',',':')) for row in observed})==1
            assert all(row['userId']==plan['task_user_id'] and row['sessionId']==run for row in observed)
        record={**plan,'result':result,'readback':saved,'lease':leases[0],'audit_ids':[a['id'] for a in audits],
            'score_ids':[s['id'] for s in scores],'langfuse_verified':True,'http_permissions_verified':True,
            'source_queue_and_formal_versions_unchanged':True,'recovery_without_model_restart':True,
            'langfuse_observation_rows':len(observed),'langfuse_distinct_observation_payloads':1}
        return record
    try:
        record=asyncio.run(scenario())
        environment={**os.environ,'PYTHONPATH':os.pathsep.join((str(ROOT),str(ROOT/'src'),*site.getsitepackages()))}
        for options in [('--endpoint','http://127.0.0.1:1'),('--transport','http','--endpoint','http://127.0.0.1:8181')]:
            for args,expected in [(['projects','source-auditory-review',project,source],record['readback']),
                (['runs','review-source-audio',run,source,'--request-key',plan['request_key']],record['result'])]:
                reply=subprocess.run([sys._base_executable,'-m','anp.cli.entry',*options,'deep_dub',*args],cwd=ROOT,
                    env=environment,capture_output=True,text=True,encoding='utf-8',timeout=120)
                assert reply.returncode==0,reply.stdout+reply.stderr
                assert json.loads(reply.stdout)==expected
        assert sha256(journal.read_bytes()).hexdigest()==digest
        record['two_cli_read_and_replay']=True
        (ROOT/'.local/deep-dub-auditory-live.json').write_bytes(json.dumps(record,ensure_ascii=False,indent=2).encode())
    finally:session_path().unlink(missing_ok=True)
