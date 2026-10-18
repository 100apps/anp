"""独立听音复核的同契约服务；保留校准原音，失败判定不改稿、不解除待审。"""
import asyncio
from hashlib import sha256
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import UUID

from pydantic import Field

from anp.errors import BusinessError
from apps.deep_dub.nodes.auditory_review import RULE,analyze,verify
from apps.deep_dub.nodes.source_review import canonical
from apps.deep_dub.services import source_recognition
from apps.deep_dub.services.artifacts import service as artifacts
from apps.deep_dub.services.audio_judge_runtime import configuration
from apps.deep_dub.services.blocking import await_blocking
from apps.deep_dub.services.execution import service as execution
from apps.deep_dub.services.gpu_runtime import run_gpu_stage
from apps.deep_dub.services.runtime import StageResult
from apps.deep_dub.services.versions import Contract,identifier,operation_key,service as versions


class ReviewInput(Contract):
    source_id:UUID
    timeout_seconds:int=Field(default=3600,ge=60,le=7200,strict=True)


async def generate(actor,run_id,payload,request_key=None):
    await actor.require('deep_dub.task.run');await actor.require('deep_dub.audit');await actor.require('deep_dub.device.manage')
    body=ReviewInput.model_validate(payload);run_id=identifier(run_id)
    runs=await actor.objects.select('deep_dub_runs',workspace_id='eq.'+actor.workspace_id,id='eq.'+run_id)
    if not runs or runs[0]['source_id']!=str(body.source_id) or runs[0]['status'] not in ('running','waiting_review'):
        raise BusinessError('独立听音仅用于当前绑定的执行中或待审来源',409,'AUDIO_JUDGE_INPUT_MISMATCH')
    run=runs[0];primary=await source_recognition.read_latest(actor,run['project_id'],str(body.source_id))
    if not primary:raise BusinessError('缺少完整原始PCM与识别证据',409,'AUDIO_JUDGE_INPUT_MISSING')
    note=(await actor.objects.select('deep_dub_artifacts',workspace_id='eq.'+actor.workspace_id,id='eq.'+primary['evidence_artifact_id']))[0]
    config=configuration()
    inputs={'rule_revision':RULE,'source_id':str(body.source_id),'pcm_artifact_id':primary['pcm_artifact_id'],
        'asr_evidence_artifact_id':note['id'],'asr_evidence_sha256':note['sha256'],'timeout_seconds':body.timeout_seconds,
        'model_id':config.model_id,'family':config.family,'weights_sha256':config.weights_sha256,'mmproj_sha256':config.mmproj_sha256,
        'runtime_sha256':config.executable_sha256,'runtime_dependencies_sha256':sha256(canonical(config.runtime_files)).hexdigest(),'source_revision':config.source_revision}
    async def handler(current,attempt_id,pool):
        async with asyncio.timeout(body.timeout_seconds):
            with TemporaryDirectory(prefix='anp-auditory-review-') as folder:
                pcm=Path(folder)/'source.wav';await artifacts.download(current,primary['pcm_artifact_id'],pcm)
                measured=await analyze(config,pool,folder,pcm,primary['recognition'],body.timeout_seconds)
                control_assets={}
                for row in measured['controls']:
                    index=row['control_index'];control_assets[str(index)]=await artifacts.upload(current,attempt_id,Path(folder)/f'control-{index}.wav','audio/wav')
                proof={'input':inputs,'measurement':measured,'control_artifact_ids':{k:v['id'] for k,v in control_assets.items()}}
                raw=canonical(proof)
                if len(raw)>16*1024**2:raise BusinessError('听音复核证据超过16MiB',422,'AUDIO_JUDGE_RESPONSE_LIMIT')
                file=Path(folder)/'review.json';file.write_bytes(raw);saved=await artifacts.upload(current,attempt_id,file,'application/json')
                return StageResult(outputs=[saved['id']]+[control_assets[str(i)]['id'] for i in range(4)])
    result=await run_gpu_stage(actor,run_id,sha256(canonical(inputs)).hexdigest(),handler,config,partition=str(body.source_id),
        request_key=operation_key(request_key),node='source_content_review')
    if result['status']=='succeeded':await scores(actor,result['attempt'])
    return result


async def checked(actor,attempt):
    if len(attempt['outputs'])!=5 or len(set(attempt['outputs']))!=5:raise BusinessError('听音复核需要JSON与四份校准原音',409,'AUDIO_JUDGE_EVIDENCE_INVALID')
    assets=await actor.objects.select('deep_dub_artifacts',workspace_id='eq.'+actor.workspace_id,id='in.('+','.join(attempt['outputs'])+')')
    by_id={a['id']:a for a in assets}
    if len(assets)!=5 or any(a['stage_attempt_id']!=attempt['id'] or a['project_id']!=attempt['project_id'] for a in assets):
        raise BusinessError('听音证据归属不一致',409,'AUDIO_JUDGE_EVIDENCE_INVALID')
    note=by_id[attempt['outputs'][0]]
    if note['media_type']!='application/json' or note['bytes']>16*1024**2:raise BusinessError('听音证据类型或大小无效',409,'AUDIO_JUDGE_EVIDENCE_INVALID')
    with TemporaryDirectory(prefix='anp-auditory-proof-') as folder:
        file=Path(folder)/'proof.json';await artifacts.download(actor,note['id'],file)
        try:
            proof=json.loads(file.read_bytes());inputs=proof['input'];m=proof['measurement']
            primary=await source_recognition.read_latest(actor,attempt['project_id'],inputs['source_id'])
            assert primary and primary['evidence_artifact_id']==inputs['asr_evidence_artifact_id'] and primary['pcm_artifact_id']==inputs['pcm_artifact_id']
            original=(await actor.objects.select('deep_dub_artifacts',workspace_id='eq.'+actor.workspace_id,id='eq.'+inputs['asr_evidence_artifact_id']))[0]
            stages=await actor.objects.select('deep_dub_stages',workspace_id='eq.'+actor.workspace_id,id='eq.'+attempt['stage_id'])
            assert original['sha256']==inputs['asr_evidence_sha256'] and inputs['rule_revision']==RULE
            assert stages[0]['node']=='source_content_review' and stages[0]['partition_key']==inputs['source_id'] and stages[0]['input_hash']==sha256(canonical(inputs)).hexdigest()
            assert all(m['model'][key]==inputs[key] for key in ('family','model_id','weights_sha256','mmproj_sha256','runtime_sha256','runtime_dependencies_sha256','source_revision'))
            assert await await_blocking(verify,m,primary['recognition'])==m['counts']
            assert set(proof['control_artifact_ids'])=={'0','1','2','3'}
            assert set(proof['control_artifact_ids'].values())==set(attempt['outputs'][1:])
            for row in m['controls']:
                asset=by_id[proof['control_artifact_ids'][str(row['control_index'])]]
                assert asset['media_type']=='audio/wav' and asset['sha256']==row['result']['audio']['sha256'] and asset['bytes']==row['result']['audio']['bytes']
        except (KeyError,TypeError,ValueError,AssertionError,IndexError):
            raise BusinessError('听音复核与来源、候选、模型或校准原音不一致',409,'AUDIO_JUDGE_EVIDENCE_INVALID') from None
    return proof


async def read_latest(actor,project_id,source_id):
    await actor.require('deep_dub.project.read');project_id,source_id=identifier(project_id),identifier(source_id)
    sources=await actor.objects.select('deep_dub_sources',workspace_id='eq.'+actor.workspace_id,project_id='eq.'+project_id,id='eq.'+source_id)
    if not sources:raise BusinessError('来源不存在或无权访问',404,'NOT_FOUND')
    stages=await actor.objects.select('deep_dub_stages',workspace_id='eq.'+actor.workspace_id,project_id='eq.'+project_id,node='eq.source_content_review',partition_key='eq.'+source_id)
    if not stages:return None
    attempts=await actor.objects.select('deep_dub_stage_attempts',workspace_id='eq.'+actor.workspace_id,stage_id='in.('+','.join(s['id'] for s in stages)+')',status='eq.succeeded',order='finished_at.desc,id.desc',limit=1)
    if not attempts:return None
    return {'stage_attempt_id':attempts[0]['id'],'evidence_artifact_id':attempts[0]['outputs'][0],'proof':await checked(actor,attempts[0])}


async def scores(actor,attempt):
    proof=await checked(actor,attempt);m=proof['measurement']
    metrics={**{'source_auditory_'+key+'_windows':value for key,value in m['counts'].items()},
             'source_auditory_calibration_passed':1,'source_auditory_formal_transcript':None,'source_auditory_speaker_identity':None}
    for name,value in metrics.items():
        await execution.record_score(actor,attempt['id'],{'metric':name,'rule_revision':RULE,'measurement_status':'not_measured' if value is None else 'measured',
            'value':value,'evidence_artifact_id':attempt['outputs'][0] if value is not None else None,'details':{'scope':m['scope']}},name+':'+attempt['id'])
    if m['counts']['mismatch']:
        stage=(await actor.objects.select('deep_dub_stages',workspace_id='eq.'+actor.workspace_id,id='eq.'+attempt['stage_id']))[0]
        run=(await actor.objects.select('deep_dub_runs',workspace_id='eq.'+actor.workspace_id,id='eq.'+stage['run_id']))[0]
        await versions.record_audit(actor,attempt['project_id'],{'kind':'source','subject_id':proof['input']['source_id'],'quality_profile_id':run['quality_profile_id'],
            'rule_key':'transcript_coverage','rule_revision':RULE,'status':'failed','method':'independent_model','evidence_artifact_id':attempt['outputs'][0],
            'details':{'asr_evidence_artifact_id':proof['input']['asr_evidence_artifact_id'],'counts':m['counts'],'scope':'原ASR候选的直接听音缺陷；不改稿，不签发正式转录通过'}},'source-auditory:'+attempt['id'])
