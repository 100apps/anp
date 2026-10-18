"""完整来源词时间阶段：ASR候选、PCM与实际强制对齐模型绑定，不封存分段或质量。"""
import asyncio
from hashlib import sha256
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import UUID

from pydantic import Field

from anp.errors import BusinessError
from apps.deep_dub.nodes.source_alignment import RULE,align_source,summarize
from apps.deep_dub.services.artifacts import service as artifacts
from apps.deep_dub.services.blocking import await_blocking
from apps.deep_dub.services.execution import service as execution
from apps.deep_dub.services.gpu_runtime import run_gpu_stage
from apps.deep_dub.services.model_pool import configuration
from apps.deep_dub.services.runtime import StageResult
from apps.deep_dub.services.source_recognition import read_latest as read_asr,checked_evidence as checked_asr
from apps.deep_dub.services.versions import Contract,identifier,operation_key


class SourceAlignmentInput(Contract):
    source_id:UUID
    timeout_seconds:int=Field(default=3600,ge=60,le=7200,strict=True)


async def align(actor,run_id,payload,request_key=None):
    await actor.require('deep_dub.task.run');await actor.require('deep_dub.audit')
    body=SourceAlignmentInput.model_validate(payload);run_id=identifier(run_id)
    async def row(table,key):
        rows=await actor.objects.select(table,workspace_id='eq.'+actor.workspace_id,id='eq.'+str(key))
        if not rows:raise BusinessError('来源词时间输入不存在或无权访问',404,'NOT_FOUND')
        return rows[0]
    run=await row('deep_dub_runs',run_id);source=await row('deep_dub_sources',body.source_id)
    diagnostic=run['purpose']=='acceptance' and run['reuse_policy']=='approved_versions' and run['source_id'] is None
    if source['project_id']!=run['project_id'] or (source['id']!=run['source_id'] and not diagnostic):
        raise BusinessError('来源词时间输入不属于本轮来源，历史诊断需显式授权复用',409,'SOURCE_ALIGNMENT_INPUT_MISMATCH')
    asr=await read_asr(actor,run['project_id'],source['id'])
    if not asr:raise BusinessError('来源词对齐需要成功的完整音轨识别证据',409,'SOURCE_ASR_REQUIRED')
    asset=await row('deep_dub_artifacts',asr['pcm_artifact_id']);note=await row('deep_dub_artifacts',asr['evidence_artifact_id'])
    config=configuration('aligner')
    inputs={'source_id':source['id'],'source_artifact_id':source['artifact_id'],'pcm_artifact_id':asset['id'],'pcm_file_sha256':asset['sha256'],
        'asr_evidence_artifact_id':note['id'],'asr_evidence_sha256':note['sha256'],'rule_revision':RULE,
        'model_id':config.model_id,'weights_sha256':config.weights_sha256,'runtime_sha256':config.executable_sha256,'source_revision':config.source_revision,
        'transcript_policy':'unreviewed-asr-candidate','timeout_seconds':body.timeout_seconds,
        'binding_scope':'explicit-historical-source-diagnostic' if diagnostic else 'run-bound-source'}
    canonical=json.dumps(inputs,ensure_ascii=False,sort_keys=True,separators=(',',':'))
    async def handler(current,attempt_id,pool):
        with TemporaryDirectory(prefix='anp-source-alignment-') as folder:
            async with asyncio.timeout(body.timeout_seconds):
                pcm=Path(folder)/'input.wav';await artifacts.download(current,asset['id'],pcm)
                measured=await align_source(config,pool,folder,pcm,asr['recognition'])
                proof={'input':inputs,'measurement':measured}
                raw=json.dumps(proof,ensure_ascii=False,separators=(',',':')).encode()
                if len(raw)>24*1024**2:raise BusinessError('整轨词时间证据超出24MiB',422,'SOURCE_ALIGNMENT_RESPONSE_LIMIT')
                file=Path(folder)/'source-words.json';file.write_bytes(raw)
                evidence=await artifacts.upload(current,attempt_id,file,'application/json')
                return StageResult([evidence['id']])
    result=await run_gpu_stage(actor,run_id,sha256(canonical.encode()).hexdigest(),handler,config,
        node='source_word_timing',partition=source['id'],request_key=operation_key(request_key))
    if result['status']=='succeeded':await record_scores(actor,result['attempt'])
    return result


async def checked_evidence(actor,attempt):
    if len(attempt['outputs'])!=1:raise BusinessError('来源词时间缺少唯一证据',409,'SOURCE_ALIGNMENT_EVIDENCE_INVALID')
    async def row(table,key):
        rows=await actor.objects.select(table,workspace_id='eq.'+actor.workspace_id,id='eq.'+str(key))
        if not rows:raise BusinessError('来源词时间证据缺少关联输入',409,'SOURCE_ALIGNMENT_EVIDENCE_INVALID')
        return rows[0]
    note=await row('deep_dub_artifacts',attempt['outputs'][0])
    if note['stage_attempt_id']!=attempt['id'] or note['media_type']!='application/json' or note['bytes']>24*1024**2:
        raise BusinessError('来源词时间证据类型、大小或归属不符',409,'SOURCE_ALIGNMENT_EVIDENCE_INVALID')
    with TemporaryDirectory(prefix='anp-source-word-proof-') as folder:
        file=Path(folder)/'proof.json';await artifacts.download(actor,note['id'],file);proof=json.loads(file.read_bytes())
    try:
        inputs=proof['input'];measured=proof['measurement']
        source=await row('deep_dub_sources',inputs['source_id']);pcm=await row('deep_dub_artifacts',inputs['pcm_artifact_id'])
        asr_note=await row('deep_dub_artifacts',inputs['asr_evidence_artifact_id']);asr_attempt=await row('deep_dub_stage_attempts',asr_note['stage_attempt_id'])
        asr,_=await checked_asr(actor,asr_attempt);stage=await row('deep_dub_stages',attempt['stage_id'])
        canonical=json.dumps(inputs,ensure_ascii=False,sort_keys=True,separators=(',',':'))
        assert asr_attempt['status']=='succeeded' and asr_attempt['project_id']==source['project_id']==attempt['project_id']
        assert asr['input']['source_id']==source['id'] and asr['pcm_artifact_id']==pcm['id']
        assert source['artifact_id']==inputs['source_artifact_id'] and pcm['sha256']==inputs['pcm_file_sha256']
        assert asr_note['sha256']==inputs['asr_evidence_sha256'] and measured['input']['sha256']==pcm['sha256']
        assert stage['node']=='source_word_timing' and stage['partition_key']==source['id']
        assert inputs['rule_revision']==RULE and inputs['transcript_policy']=='unreviewed-asr-candidate'
        assert sha256(canonical.encode()).hexdigest()==stage['input_hash']
        assert all(measured['model'][key]==inputs[key] for key in ('model_id','weights_sha256','runtime_sha256','source_revision'))
        summary=await await_blocking(summarize,measured,asr['recognition'])
        assert summary==measured['summary']
    except (AssertionError,KeyError,TypeError,ValueError):
        raise BusinessError('来源词时间证据与ASR、PCM、来源或模型不匹配',409,'SOURCE_ALIGNMENT_EVIDENCE_INVALID') from None
    return proof


async def record_scores(actor,attempt):
    proof=await checked_evidence(actor,attempt);summary=proof['measurement']['summary']
    metrics={'source_word_count':summary['word_count'],'source_word_invalid_chunks':summary['invalid_timestamp_chunks'],
        'source_word_uncovered_chunks':summary['uncovered_transcript_chunks'],'source_word_zero_length':summary['zero_length_content_words'],
        'source_word_cross_chunk_overlap_samples':summary['cross_chunk_overlap_samples'],
        'source_word_semantics':None,'source_word_subtitle_timing':None}
    for metric,value in metrics.items():
        await execution.record_score(actor,attempt['id'],{'metric':metric,'rule_revision':RULE,
            'measurement_status':'not_measured' if value is None else 'measured','value':value,
            'evidence_artifact_id':attempt['outputs'][0] if value is not None else None,
            'details':{'scope':summary['scope']}},metric+':'+attempt['id'])


async def read_latest(actor,project_id,source_id):
    await actor.require('deep_dub.project.read');project_id=identifier(project_id);source_id=identifier(source_id)
    sources=await actor.objects.select('deep_dub_sources',workspace_id='eq.'+actor.workspace_id,project_id='eq.'+project_id,id='eq.'+source_id)
    if not sources:raise BusinessError('来源不存在或无权访问',404,'NOT_FOUND')
    stages=await actor.objects.select('deep_dub_stages',workspace_id='eq.'+actor.workspace_id,project_id='eq.'+project_id,node='eq.source_word_timing',partition_key='eq.'+source_id)
    if not stages:return None
    attempts=await actor.objects.select('deep_dub_stage_attempts',workspace_id='eq.'+actor.workspace_id,stage_id='in.('+','.join(s['id'] for s in stages)+')',status='eq.succeeded',order='finished_at.desc,id.desc',limit=1)
    if not attempts:return None
    proof=await checked_evidence(actor,attempts[0])
    return {'source_id':source_id,'stage_attempt_id':attempts[0]['id'],'evidence_artifact_id':attempts[0]['outputs'][0],
        'performed_at':attempts[0]['finished_at'],'input':proof['input'],'measurement':proof['measurement']}
