"""当前来源的角色时间证据；共用权限、设备租约和不可变产物，不登记人物或分段。"""
import asyncio
from hashlib import sha256
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import UUID

from pydantic import Field

from anp.errors import BusinessError
from apps.deep_dub.nodes.source_review import canonical
from apps.deep_dub.nodes.source_speakers import RULE, OPTIONS, SESSION_OPTIONS, analyze, verify
from apps.deep_dub.services import source_recognition
from apps.deep_dub.services.artifacts import service as artifacts
from apps.deep_dub.services.blocking import await_blocking
from apps.deep_dub.services.execution import service as execution
from apps.deep_dub.services.gpu_runtime import run_gpu_stage
from apps.deep_dub.services.model_pool import configuration
from apps.deep_dub.services.runtime import StageResult
from apps.deep_dub.services.versions import Contract, identifier, operation_key


class SpeakerInput(Contract):
    source_id: UUID
    timeout_seconds: int = Field(default=3600,ge=60,le=7200,strict=True)


async def generate(actor,run_id,payload,request_key=None):
    await actor.require('deep_dub.task.run'); await actor.require('deep_dub.audit')
    body=SpeakerInput.model_validate(payload); run_id=identifier(run_id)
    runs=await actor.objects.select('deep_dub_runs',workspace_id='eq.'+actor.workspace_id,id='eq.'+run_id)
    if not runs or runs[0]['source_id']!=str(body.source_id) or runs[0]['status'] not in ('running','waiting_review'):
        raise BusinessError('角色分析仅用于本轮绑定的执行中或待审来源',409,'SOURCE_SPEAKER_INPUT_MISMATCH')
    run=runs[0];primary=await source_recognition.read_latest(actor,run['project_id'],str(body.source_id))
    if not primary:raise BusinessError('缺少完整来源PCM与识别证据',409,'SOURCE_SPEAKER_INPUT_MISSING')
    note=(await actor.objects.select('deep_dub_artifacts',workspace_id='eq.'+actor.workspace_id,id='eq.'+primary['evidence_artifact_id']))[0]
    config=configuration('diarization')
    inputs={'rule_revision':RULE,'source_id':str(body.source_id),'pcm_artifact_id':primary['pcm_artifact_id'],
        'asr_evidence_artifact_id':note['id'],'asr_evidence_sha256':note['sha256'],'request_options':OPTIONS,
        'session_options':SESSION_OPTIONS,'timeout_seconds':body.timeout_seconds,'family':config.family,
        'model_id':config.model_id,'weights_sha256':config.weights_sha256,'runtime_sha256':config.executable_sha256,'source_revision':config.source_revision}
    async def handler(current,attempt_id,pool):
        async with asyncio.timeout(body.timeout_seconds):
            with TemporaryDirectory(prefix='anp-source-speakers-') as folder:
                pcm=Path(folder)/'source.wav';await artifacts.download(current,primary['pcm_artifact_id'],pcm)
                measured=await analyze(config,pool,folder,pcm,primary['recognition'],body.timeout_seconds)
                raw=canonical({'input':inputs,'measurement':measured})
                if len(raw)>8*1024**2:raise BusinessError('角色时间证据超过8MiB',422,'SOURCE_SPEAKER_RESPONSE_LIMIT')
                file=Path(folder)/'speakers.json';file.write_bytes(raw)
                receipt=await artifacts.upload(current,attempt_id,file,'application/json')
                return StageResult(outputs=[receipt['id']])
    result=await run_gpu_stage(actor,run_id,sha256(canonical(inputs)).hexdigest(),handler,config,
        partition=str(body.source_id),request_key=operation_key(request_key),node='source_speaker_timing')
    if result['status']=='succeeded':await scores(actor,result['attempt'])
    return result


async def checked(actor,attempt):
    if len(attempt['outputs'])!=1:raise BusinessError('角色时间缺少唯一证据',409,'SOURCE_SPEAKER_EVIDENCE_INVALID')
    assets=await actor.objects.select('deep_dub_artifacts',workspace_id='eq.'+actor.workspace_id,id='eq.'+attempt['outputs'][0])
    if not assets or assets[0]['stage_attempt_id']!=attempt['id'] or assets[0]['project_id']!=attempt['project_id'] or assets[0]['media_type']!='application/json' or assets[0]['bytes']>8*1024**2:
        raise BusinessError('角色时间证据归属或大小无效',409,'SOURCE_SPEAKER_EVIDENCE_INVALID')
    with TemporaryDirectory(prefix='anp-speaker-proof-') as folder:
        file=Path(folder)/'proof.json';await artifacts.download(actor,assets[0]['id'],file)
        try:
            proof=json.loads(file.read_bytes());inputs=proof['input'];measured=proof['measurement']
            primary=await source_recognition.read_latest(actor,attempt['project_id'],inputs['source_id'])
            assert primary and primary['evidence_artifact_id']==inputs['asr_evidence_artifact_id'] and primary['pcm_artifact_id']==inputs['pcm_artifact_id']
            notes=await actor.objects.select('deep_dub_artifacts',workspace_id='eq.'+actor.workspace_id,id='eq.'+inputs['asr_evidence_artifact_id'])
            stages=await actor.objects.select('deep_dub_stages',workspace_id='eq.'+actor.workspace_id,id='eq.'+attempt['stage_id'])
            assert notes[0]['sha256']==inputs['asr_evidence_sha256'] and inputs['rule_revision']==RULE
            assert inputs['request_options']==OPTIONS and inputs['session_options']==SESSION_OPTIONS
            assert stages[0]['node']=='source_speaker_timing' and stages[0]['partition_key']==inputs['source_id'] and stages[0]['input_hash']==sha256(canonical(inputs)).hexdigest()
            assert all(measured['model'][k]==inputs[k] for k in ('family','model_id','weights_sha256','runtime_sha256','source_revision'))
            assert measured['summary']==await await_blocking(verify,measured,primary['recognition'])
        except (KeyError,TypeError,ValueError,AssertionError,IndexError):
            raise BusinessError('角色时间与完整PCM、原始识别、模型或阶段输入不一致',409,'SOURCE_SPEAKER_EVIDENCE_INVALID') from None
    return proof


async def read_latest(actor,project_id,source_id):
    await actor.require('deep_dub.project.read'); project_id,source_id=identifier(project_id),identifier(source_id)
    rows=await actor.objects.select('deep_dub_sources',workspace_id='eq.'+actor.workspace_id,project_id='eq.'+project_id,id='eq.'+source_id)
    if not rows:raise BusinessError('来源不存在或无权访问',404,'NOT_FOUND')
    stages=await actor.objects.select('deep_dub_stages',workspace_id='eq.'+actor.workspace_id,project_id='eq.'+project_id,node='eq.source_speaker_timing',partition_key='eq.'+source_id)
    if not stages:return None
    attempts=await actor.objects.select('deep_dub_stage_attempts',workspace_id='eq.'+actor.workspace_id,stage_id='in.('+','.join(s['id'] for s in stages)+')',status='eq.succeeded',order='finished_at.desc,id.desc',limit=1)
    if not attempts:return None
    return {'stage_attempt_id':attempts[0]['id'],'evidence_artifact_id':attempts[0]['outputs'][0],'proof':await checked(actor,attempts[0])}


async def scores(actor,attempt):
    proof=await checked(actor,attempt);summary=proof['measurement']['summary']
    metrics={'source_speaker_count':summary['speaker_count'],'source_speaker_active_fraction':summary['active_fraction'],
        'source_speaker_overlap_fraction':summary['overlap_fraction'],'source_speaker_invalid_turns':len(summary['invalid_turn_indexes']),
        'source_speaker_identity':None,'source_speaker_boundary_review':None}
    for metric,value in metrics.items():
        await execution.record_score(actor,attempt['id'],{'metric':metric,'rule_revision':RULE,
            'measurement_status':'not_measured' if value is None else 'measured','value':value,
            'evidence_artifact_id':attempt['outputs'][0] if value is not None else None,'details':{'scope':summary['scope']}},metric+':'+attempt['id'])
