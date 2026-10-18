"""显式启用的真实V100常驻服务生成；不是整片生产、质量或RTF达标。"""
import asyncio,json,os,site,subprocess,sys,time
from hashlib import sha256
from uuid import uuid4
from pathlib import Path
import httpx,pytest
from anp import ROOT
from anp.auth.session import login,current_actor,session_path
from anp.config import secret,value
from anp.sdk.http import async_client
from apps.deep_dub.services.execution import service as execution
from apps.deep_dub.services.artifacts import service as artifacts
from apps.deep_dub.services.candidates import service as candidates
from apps.deep_dub.services.references import prepare
from apps.deep_dub.nodes.media import executable,probe_media
from apps.deep_dub.services.resident_transport import configuration,Transport
from tests.test_deep_dub_source_acquisition_live import flush

pytestmark=pytest.mark.skipif(os.environ.get('ANP_DEEP_DUB_RESIDENT_LIVE')!='1',reason='需真实已核对V100常驻服务与原片参考，不自动重跑未知作业')


async def prepare_inputs(actor,plan,file,tmp_path):
    """同一排队运行登记新的验收来源；逐步保存，仅重放确切输入，不套用旧来源。"""
    def save():file.write_bytes((json.dumps(plan,ensure_ascii=False,indent=2)+'\n').encode())
    state=plan.setdefault('preparation',{});save()
    original=json.loads(Path('D:/github/dub-bench-zh/dubbing-studio-an/work/tasks/task-a0d1ec97dbd1/task.json').read_bytes())
    clip=tmp_path/'source-26s.mp4'
    subprocess.run([executable('ffmpeg'),'-v','error','-i',original['source']['path'],'-t','26','-map','0:v:0','-map','0:a:0','-c','copy',str(clip)],check=True,capture_output=True,timeout=30)
    fingerprint=sha256(clip.read_bytes()).hexdigest()
    if 'attempt_id' not in state:
        stage=await execution.begin_stage(actor,plan['run_id'],{'node':'acceptance_resident_source','input_hash':fingerprint},'resident-source:'+plan['run_id'])
        state['attempt_id']=stage['attempt']['id'];save()
    attempt=state['attempt_id'];started=time.monotonic()
    current=(await actor.objects.select('deep_dub_stage_attempts',id='eq.'+attempt))[0]
    if current['status']=='queued':await execution.mark_stage(actor,attempt,'started')
    if 'media' not in state:
        probe=await probe_media(clip)
        state['media']=await artifacts.upload(actor,attempt,clip,'video/mp4',probe['duration_ms']);save()
    if 'source' not in state:
        state['source']=await candidates.source(actor,attempt,{'artifact_id':state['media']['id'],'duration_ms':state['media']['duration_ms'],
            'identity_evidence':{'acceptance_only':True,'existing_public_source_clip':True,'independent_asr':False}},'resident-source:'+attempt);save()
    if 'parts' not in state:
        state['parts']=await candidates.transcript(actor,attempt,{'source_id':state['source']['id'],'strategy_revision':'resident-provided-text-v1',
            'cues':[{'cue_no':0,'speaker_key':'resident-acceptance','source_text':original['segments'][0]['sourceText'],'start_ms':780,'end_ms':25300}],
            'segments':[{'cue_numbers':[0]}]},'resident-text:'+attempt);save()
    segment=(await actor.objects.select('deep_dub_segments',segment_set_id='eq.'+state['parts']['id']))[0]
    if 'note' not in state:
        note=tmp_path/'context.json';note.write_text(json.dumps({'scope':'人工给定测试译稿，不代表独立内容通过','source_text':original['segments'][0]['sourceText']},ensure_ascii=False),encoding='utf-8')
        state['note']=await artifacts.upload(actor,attempt,note,'application/json');save()
    if 'translation' not in state:
        state['translation']=await candidates.translation(actor,attempt,{'segment_id':segment['id'],'context_artifact_id':state['note']['id'],
            'chinese_text':'我这就开始。我们将讨论海明威、菲茨杰拉德与福克纳。','model_id':'human-provided-acceptance-text',
            'prompt_revision':'resident-provided-text-v1'},'resident-translation:'+attempt);save()
    if current['status']!='succeeded':
        await execution.finish_stage(actor,attempt,{'status':'succeeded','outputs':[state['media']['id'],state['note']['id']],
            'elapsed_seconds':time.monotonic()-started,'operation_stopped':True})
    for kind in ('voice','emotion'):
        result=await prepare(actor,plan['run_id'],{'segment_id':segment['id'],'kind':kind},'resident-'+kind+':'+attempt)
        assert result['status']=='succeeded'
    voice=(await actor.objects.select('deep_dub_voice_references',source_id='eq.'+state['source']['id']))[0]
    emotion=(await actor.objects.select('deep_dub_emotion_references',source_id='eq.'+state['source']['id']))[0]
    plan['body']={'segment_id':segment['id'],'translation_id':state['translation']['id'],'voice_reference_id':voice['id'],
        'emotion_reference_id':emotion['id'],'executor':'resident','generation_factor':1.,'timeout_seconds':300,'seed':42}
    plan['scope']='同一排队运行的新26秒来源及人工测试译稿；非正式整片或质量通过';save()


def test_real_resident_speech_api_two_cli_evidence_and_preserved_service(identities,monkeypatch,tmp_path):
    monkeypatch.setenv('ANP_PROFILE','resident-live-'+uuid4().hex)
    file=ROOT/'.local/deep-dub-resident-speech-plan.json'
    local_journal=ROOT/'.local/deep_dub/gpu-pool/1eb343271a51306f697a263a.json';local_before=sha256(local_journal.read_bytes()).hexdigest()
    async def scenario():
        await login(secret('ADMIN_EMAIL'),secret('ADMIN_PASSWORD'));actor=await current_actor()
        if file.exists():plan=json.loads(file.read_bytes())
        else:
            original=json.loads((ROOT/'.local/deep-dub-speech-live.json').read_bytes())
            project=(await actor.objects.select('deep_dub_projects',id='eq.'+original['project_id']))[0]
            old_run=(await actor.objects.select('deep_dub_runs',id='eq.'+original['run_id']))[0]
            run=await execution.queue(actor,project['id'],{'quality_profile_id':old_run['quality_profile_id'],
                'purpose':'acceptance','mode':'http','flow_revision':'resident-speech-integration-v1','reuse_policy':'approved_versions',
                'expected_version':project['row_version']},'resident-'+uuid4().hex)
            await execution.start(actor,run['id'])
            plan={'project_id':project['id'],'run_id':run['id'],'request_key':uuid4().hex,
                'scope':'新acceptance来源及测试译稿；非正式整片或字幕/质量通过','task_user_id':actor.user['id']}
            file.write_bytes((json.dumps(plan,ensure_ascii=False,indent=2)+'\n').encode())
        run=(await actor.objects.select('deep_dub_runs',id='eq.'+plan['run_id']))[0]
        if run['source_id'] is None and 'body' in plan:
            # 原请求在输入检查处拒绝。原计划单独保留；更正输入使用新键，原排队时钟不变。
            archive=file.with_name('deep-dub-resident-speech-plan-first-rejected.json')
            with archive.open('xb') as stream:stream.write(file.read_bytes())
            plan['first_rejected_request_key']=plan['request_key'];plan['request_key']=uuid4().hex
            del plan['body'];file.write_bytes((json.dumps(plan,ensure_ascii=False,indent=2)+'\n').encode())
        if 'body' not in plan:await prepare_inputs(actor,plan,file,tmp_path)
        async with async_client(base_url='http://127.0.0.1:8181',timeout=1200,trust_env=False) as client:
            path=f'/api/apps/deep_dub/runs/{plan["run_id"]}/speech'
            assert (await client.post(path,json=plan['body'])).status_code==401
            for name,code in [('viewer',403),('outside',404)]:
                user=identities[name]
                r=await client.post(path,json=plan['body'],headers={'Authorization':'Bearer '+user['token'],'X-Workspace-ID':user['workspace_id']})
                assert r.status_code==code
            response=await client.post(path,json=plan['body'],headers={'Authorization':'Bearer '+actor.token,'X-Workspace-ID':actor.workspace_id,'Idempotency-Key':plan['request_key']})
            if response.status_code!=200:(ROOT/'.local/deep-dub-resident-speech-last-response.json').write_bytes(response.content)
            assert response.status_code==200,'真实远端生成未成功，查看私有回执，不重新发起未知作业'
            result=response.json();assert result['status']=='succeeded'
        attempt=result['attempt'];assert attempt['created_by']==plan['task_user_id']
        audio=(await actor.objects.select('deep_dub_audio_attempts',stage_attempt_id='eq.'+attempt['id']))[0]
        note=tmp_path/'evidence.json';await artifacts.download(actor,audio['evidence_artifact_id'],note);proof=json.loads(note.read_bytes())
        assert proof['parameters']['backend']=='cuda-resident-service' and proof['operation_stopped']
        assert proof['resident_service_stopped'] is False and proof['resident_job']['worker_running'] is False
        assert proof['resident_job']['worker_sha256']==Transport(configuration()).source_sha256
        assert all(proof[k]=='not_measured' for k in ('semantic_consistency','speaker_identity','naturalness'))
        wav=tmp_path/'actual.wav';await artifacts.download(actor,audio['artifact_id'],wav)
        assert sha256(wav.read_bytes()).hexdigest()==proof['audio_sha256']==proof['resident_job']['output_sha256']
        leases=await actor.objects.select('deep_dub_device_leases',select='id,device_id,status,operation_stopped',stage_attempt_id='eq.'+attempt['id'])
        assert len(leases)==1 and leases[0]['status']=='released' and leases[0]['operation_stopped'] is True
        device=(await actor.objects.select('deep_dub_devices',id='eq.'+leases[0]['device_id']))[0]
        assert device['probe']['physical_gpu_exclusive'] is False and device['probe']['has_external_processes'] is True
        assert device['probe']['resident_service_preserved'] is True
        scores=await actor.objects.select('deep_dub_scores',stage_attempt_id='eq.'+attempt['id']);assert len(scores)==4
        assert not await actor.objects.select('deep_dub_audits',evidence_artifact_id='eq.'+audio['evidence_artifact_id'])
        await flush(actor)
        async with async_client(timeout=20,trust_env=False,auth=httpx.BasicAuth(secret('LANGFUSE_PUBLIC_KEY'),secret('LANGFUSE_SECRET_KEY'))) as client:
            for _ in range(30):
                r=await client.get(value('langfuse','url')+'/api/public/v3/scores',params={'traceId':plan['run_id'].replace('-',''),'limit':100});r.raise_for_status()
                found={s['id']:s for s in r.json()['data']}
                if all(s['id'] in found for s in scores):break
                await asyncio.sleep(.5)
            assert all(found[s['id']]['value']==(s['value'] if s['measurement_status']=='measured' else 'not_measured') for s in scores)
            observed=await client.get(value('langfuse','url')+'/api/public/v2/observations',params={'traceId':plan['run_id'].replace('-',''),'limit':100})
            observed.raise_for_status();observations=observed.json()['data']
            assert observations and all(o['userId']==plan['task_user_id'] and o['sessionId']==plan['run_id'] and o['environment']=='acceptance' for o in observations)
        return {**plan,'result':result,'proof':proof,'audio':audio,'lease':leases[0],'device_probe':device['probe'],
            'score_ids':[s['id'] for s in scores],'langfuse_scores_verified':True,'langfuse_user_session_verified':True,'http_permissions_verified':True}
    try:
        record=asyncio.run(scenario())
        # 用实际解释器避免Windows venv启动器在测试超时后遗留owner。
        environment={**os.environ,'PYTHONPATH':os.pathsep.join((str(ROOT),str(ROOT/'src'),*site.getsitepackages()))}
        body=record['body'];args=['deep_dub','runs','speech',record['run_id'],body['segment_id'],body['translation_id'],
            body['voice_reference_id'],body['emotion_reference_id'],'--executor','resident','--request-key',record['request_key']]
        for transport in [('--endpoint','http://127.0.0.1:1'),('--transport','http','--endpoint','http://127.0.0.1:8181')]:
            reply=subprocess.run([sys._base_executable,'-m','anp.cli.entry',*transport,*args],cwd=ROOT,env=environment,capture_output=True,text=True,encoding='utf-8',timeout=120)
            assert reply.returncode==0,'CLI重放失败，原计算回执保留'
            assert json.loads(reply.stdout)==record['result']
        assert sha256(local_journal.read_bytes()).hexdigest()==local_before
        record['two_cli_replay_verified']=True;record['local_gpu_journal_unchanged']=True
        (ROOT/'.local/deep-dub-resident-speech-live.json').write_bytes((json.dumps(record,ensure_ascii=False,indent=2)+'\n').encode())
    finally:session_path().unlink(missing_ok=True)
