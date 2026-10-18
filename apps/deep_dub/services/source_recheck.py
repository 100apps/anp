"""待审来源返修使用独立GPU尝试与原用户身份；保存额外证据，不解除质量门禁。"""
import asyncio
from hashlib import sha256
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Annotated
from uuid import UUID

from pydantic import Field
from anp.errors import BusinessError
from apps.deep_dub.nodes.source_recheck import RULE, plan, recheck, check
from apps.deep_dub.nodes.source_review import canonical
from apps.deep_dub.services import source_recognition
from apps.deep_dub.services.artifacts import service as artifacts
from apps.deep_dub.services.blocking import await_blocking
from apps.deep_dub.services.execution import service as execution
from apps.deep_dub.services.gpu_runtime import run_gpu_stage
from apps.deep_dub.services.model_pool import configuration
from apps.deep_dub.services.runtime import StageResult
from apps.deep_dub.services.versions import Contract, identifier, operation_key


class RecheckInput(Contract):
    source_id: UUID
    boundaries: list[Annotated[int, Field(ge=1,le=499,strict=True)]] | None = Field(default=None, min_length=1, max_length=499)
    timeout_seconds: int = Field(default=1800,ge=60,le=3600,strict=True)


async def generate(actor, run_id, payload, request_key=None):
    await actor.require('deep_dub.task.run'); await actor.require('deep_dub.audit')
    body = RecheckInput.model_validate(payload)
    run_id = identifier(run_id)
    runs = await actor.objects.select('deep_dub_runs',workspace_id='eq.'+actor.workspace_id,id='eq.'+run_id)
    if not runs or runs[0]['source_id'] != str(body.source_id) or runs[0]['status'] != 'waiting_review':
        raise BusinessError('接缝返修仅用于本轮待审来源',409,'SOURCE_RECHECK_INPUT_MISMATCH')
    run = runs[0]
    recognized = await source_recognition.read_latest(actor,run['project_id'],str(body.source_id))
    if not recognized:raise BusinessError('缺少原始识别证据',409,'SOURCE_RECHECK_INPUT_MISSING')
    ranges = plan(recognized['recognition'],body.boundaries)
    notes = await actor.objects.select('deep_dub_artifacts',workspace_id='eq.'+actor.workspace_id,id='eq.'+recognized['evidence_artifact_id'])
    config = configuration('asr')
    inputs = {'source_id':str(body.source_id),'rule_revision':RULE,'asr_evidence_artifact_id':recognized['evidence_artifact_id'],
              'asr_evidence_sha256':notes[0]['sha256'],'pcm_artifact_id':recognized['pcm_artifact_id'],
              'boundaries':[r['boundary_index'] for r in ranges],'timeout_seconds':body.timeout_seconds,
              'model_id':config.model_id,'weights_sha256':config.weights_sha256,'runtime_sha256':config.executable_sha256,'source_revision':config.source_revision}
    fingerprint = sha256(canonical(inputs)).hexdigest()
    async def handler(current,attempt_id,pool):
        async with asyncio.timeout(body.timeout_seconds):
            with TemporaryDirectory(prefix='anp-source-recheck-') as folder:
                pcm = Path(folder)/'source.wav'
                await artifacts.download(current,recognized['pcm_artifact_id'],pcm)
                measured = await recheck(config,pool,folder,pcm,recognized['recognition'],inputs['boundaries'])
                file = Path(folder)/'recheck.json'; file.write_bytes(canonical({'input':inputs,'measurement':measured}))
                saved = await artifacts.upload(current,attempt_id,file,'application/json')
                return StageResult(outputs=[saved['id']])
    result = await run_gpu_stage(actor,run_id,fingerprint,handler,config,partition=str(body.source_id),
                                 request_key=operation_key(request_key),node='source_boundary_recheck')
    if result['status']=='succeeded':await scores(actor,result['attempt'])
    return result


async def checked(actor,attempt):
    if len(attempt['outputs'])!=1:raise BusinessError('缺少唯一接缝证据',409,'SOURCE_RECHECK_EVIDENCE_INVALID')
    assets = await actor.objects.select('deep_dub_artifacts',workspace_id='eq.'+actor.workspace_id,id='eq.'+attempt['outputs'][0])
    if (not assets or assets[0]['stage_attempt_id']!=attempt['id'] or assets[0]['project_id']!=attempt['project_id']
        or assets[0]['media_type']!='application/json' or assets[0]['bytes']>8*1024**2):
        raise BusinessError('接缝证据归属、类型或大小无效',409,'SOURCE_RECHECK_EVIDENCE_INVALID')
    with TemporaryDirectory(prefix='anp-recheck-proof-') as folder:
        file=Path(folder)/'proof.json';await artifacts.download(actor,assets[0]['id'],file)
        try:
            proof=json.loads(file.read_bytes());inputs=proof['input'];measurement=proof['measurement']
            stages=await actor.objects.select('deep_dub_stages',workspace_id='eq.'+actor.workspace_id,id='eq.'+attempt['stage_id'])
            recognized=await source_recognition.read_latest(actor,attempt['project_id'],inputs['source_id'])
            notes=await actor.objects.select('deep_dub_artifacts',workspace_id='eq.'+actor.workspace_id,id='eq.'+inputs['asr_evidence_artifact_id'])
            assert recognized and notes and stages
            assert inputs['asr_evidence_artifact_id']==recognized['evidence_artifact_id'] and inputs['asr_evidence_sha256']==notes[0]['sha256']
            assert inputs['pcm_artifact_id']==recognized['pcm_artifact_id'] and inputs['rule_revision']==RULE
            assert stages[0]['node']=='source_boundary_recheck' and stages[0]['partition_key']==inputs['source_id']
            assert sha256(canonical(inputs)).hexdigest()==stages[0]['input_hash']
            assert measurement['boundaries']==inputs['boundaries']
            assert all(measurement['model'][k]==inputs[k] for k in ('model_id','weights_sha256','runtime_sha256','source_revision'))
            assert measurement['summary']==await await_blocking(check,measurement,recognized['recognition'])
        except (KeyError,ValueError,TypeError,AssertionError):
            raise BusinessError('接缝证据与ASR、来源或模型不一致',409,'SOURCE_RECHECK_EVIDENCE_INVALID') from None
    return proof


async def read_latest(actor,project_id,source_id):
    await actor.require('deep_dub.project.read')
    project_id,source_id=identifier(project_id),identifier(source_id)
    sources=await actor.objects.select('deep_dub_sources',workspace_id='eq.'+actor.workspace_id,project_id='eq.'+project_id,id='eq.'+source_id)
    if not sources:raise BusinessError('来源不存在或无权访问',404,'NOT_FOUND')
    stages=await actor.objects.select('deep_dub_stages',workspace_id='eq.'+actor.workspace_id,project_id='eq.'+project_id,node='eq.source_boundary_recheck',partition_key='eq.'+source_id)
    if not stages:return None
    attempts=await actor.objects.select('deep_dub_stage_attempts',workspace_id='eq.'+actor.workspace_id,stage_id='in.('+','.join(s['id'] for s in stages)+')',status='eq.succeeded',order='finished_at.desc,id.desc',limit=1)
    if not attempts:return None
    return {'stage_attempt_id':attempts[0]['id'],'evidence_artifact_id':attempts[0]['outputs'][0],'proof':await checked(actor,attempts[0])}


async def scores(actor,attempt):
    proof=await checked(actor,attempt);summary=proof['measurement']['summary']
    for metric,value in {'source_recheck_windows':summary['window_count'],'source_recheck_empty_windows':summary['empty_windows'],
                         'source_recheck_semantics':None,'source_recheck_subtitle_timing':None}.items():
        await execution.record_score(actor,attempt['id'],{'metric':metric,'rule_revision':RULE,'measurement_status':'not_measured' if value is None else 'measured',
            'value':value,'evidence_artifact_id':attempt['outputs'][0] if value is not None else None,'details':{'scope':summary['scope']}},metric+':'+attempt['id'])
