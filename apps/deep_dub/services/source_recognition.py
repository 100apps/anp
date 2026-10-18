"""来源ASR阶段，保留整轨覆盖与模型身份；尚不签发原文、分段或字幕审核。"""
import asyncio
from hashlib import sha256
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Literal
from uuid import UUID

from pydantic import Field

from anp.errors import BusinessError
from apps.deep_dub.nodes.source_asr import recognize_source,checked_coverage,RULE
from apps.deep_dub.services.artifacts import service as artifacts
from apps.deep_dub.services.execution import service as execution
from apps.deep_dub.services.gpu_runtime import run_gpu_stage
from apps.deep_dub.services.model_pool import configuration
from apps.deep_dub.services.runtime import StageResult
from apps.deep_dub.services.versions import Contract,identifier,operation_key


class SourceRecognitionInput(Contract):
    source_id:UUID
    language:Literal['English','Chinese']='English'
    chunk_seconds:int=Field(default=60,ge=30,le=90,strict=True)
    timeout_seconds:int=Field(default=3600,ge=60,le=7200,strict=True)


async def checked_evidence(actor,attempt):
    if len(attempt['outputs'])!=2:raise BusinessError('来源识别缺少PCM及证据',409,'SOURCE_ASR_EVIDENCE_INVALID')
    rows=await actor.objects.select('deep_dub_artifacts',workspace_id='eq.'+actor.workspace_id,id='in.('+','.join(attempt['outputs'])+')')
    notes=[r for r in rows if r['media_type']=='application/json' and r['stage_attempt_id']==attempt['id'] and r['bytes']<=8*1024**2]
    pcm=[r for r in rows if r['media_type']=='audio/wav' and r['stage_attempt_id']==attempt['id'] and r['sample_rate']==16000]
    if len(notes)!=1 or len(pcm)!=1:raise BusinessError('来源识别产物类型或归属不符',409,'SOURCE_ASR_EVIDENCE_INVALID')
    with TemporaryDirectory(prefix='anp-source-asr-proof-') as folder:
        file=Path(folder)/'proof.json';await artifacts.download(actor,notes[0]['id'],file);proof=json.loads(file.read_bytes())
    inputs=proof.get('input',{});recognition=proof.get('recognition',{})
    if not isinstance(inputs,dict) or not isinstance(recognition,dict):
        raise BusinessError('来源识别证据结构无效',409,'SOURCE_ASR_EVIDENCE_INVALID')
    stages=await actor.objects.select('deep_dub_stages',workspace_id='eq.'+actor.workspace_id,id='eq.'+attempt['stage_id'])
    sources=await actor.objects.select('deep_dub_sources',workspace_id='eq.'+actor.workspace_id,id='eq.'+str(inputs.get('source_id')))
    assets=await actor.objects.select('deep_dub_artifacts',workspace_id='eq.'+actor.workspace_id,id='eq.'+str(inputs.get('artifact_id')))
    canonical=json.dumps(inputs,ensure_ascii=False,sort_keys=True,separators=(',',':'))
    coverage=checked_coverage(recognition)
    if (not stages or not sources or not assets or stages[0]['node']!='source_recognition'
        or sha256(canonical.encode()).hexdigest()!=stages[0]['input_hash']
        or sources[0]['artifact_id']!=assets[0]['id'] or inputs.get('source_sha256')!=assets[0]['sha256']
        or sources[0]['project_id']!=attempt['project_id'] or assets[0]['project_id']!=attempt['project_id']
        or stages[0]['partition_key']!=sources[0]['id'] or inputs.get('rule_revision')!=RULE
        or recognition.get('media_probe',{}).get('duration_ms')!=sources[0]['duration_ms']
        or recognition.get('input',{}).get('sha256')!=assets[0]['sha256'] or proof.get('pcm_artifact_id')!=pcm[0]['id']
        or recognition.get('normalized_input',{}).get('sha256')!=pcm[0]['sha256']
        or abs(recognition['pcm']['sample_count']/16-pcm[0]['duration_ms'])>.5
        or recognition['language_hint']!=inputs['language'] or recognition['chunk_seconds']!=inputs['chunk_seconds']
        or any(recognition['model'][k]!=inputs[k] for k in ('model_id','weights_sha256','runtime_sha256','source_revision'))
        or proof.get('coverage')!=coverage or recognition.get('coverage')!=coverage):
        raise BusinessError('来源识别证据与来源、模型或输入摘要不匹配',409,'SOURCE_ASR_EVIDENCE_INVALID')
    return proof,notes[0]


async def record_scores(actor,attempt):
    proof,note=await checked_evidence(actor,attempt);coverage=proof['coverage']
    for metric,value in {'source_asr_sample_coverage':coverage['sample_coverage'],'source_asr_empty_chunks':coverage['empty_chunks'],
        'source_asr_model_sessions':coverage['model_sessions'],'source_asr_content_accuracy':None,'source_asr_subtitle_timing':None}.items():
        await execution.record_score(actor,attempt['id'],{'metric':metric,'rule_revision':RULE,
            'measurement_status':'not_measured' if value is None else 'measured','value':value,
            'evidence_artifact_id':note['id'] if value is not None else None,
            'details':{'source_id':proof['input']['source_id'],'scope':coverage['scope']}},metric+':'+attempt['id'])


async def recognize(actor,run_id,payload,request_key=None):
    await actor.require('deep_dub.task.run');await actor.require('deep_dub.audit')
    body=SourceRecognitionInput.model_validate(payload);run_id=identifier(run_id)
    async def row(table,id):
        rows=await actor.objects.select(table,workspace_id='eq.'+actor.workspace_id,id='eq.'+str(id))
        if not rows:raise BusinessError('来源识别输入不存在或无权访问',404,'NOT_FOUND')
        return rows[0]
    run=await row('deep_dub_runs',run_id);source=await row('deep_dub_sources',body.source_id)
    asset=await row('deep_dub_artifacts',source['artifact_id'])
    if (source['project_id']!=run['project_id'] or run['source_id']!=source['id'] or asset['project_id']!=run['project_id']
        or not asset['media_type'].startswith(('video/','audio/')) or not 0<asset['bytes']<=4*1024**3
        or not 0<source['duration_ms']<=14400000 or source['duration_ms']!=asset['duration_ms']):
        raise BusinessError('来源不属于本轮或超出识别限制',409,'SOURCE_ASR_INPUT_MISMATCH')
    config=configuration('asr')
    inputs={'source_id':source['id'],'artifact_id':asset['id'],'source_sha256':asset['sha256'],
        'language':body.language,'chunk_seconds':body.chunk_seconds,'timeout_seconds':body.timeout_seconds,'rule_revision':RULE,
        'model_id':config.model_id,'weights_sha256':config.weights_sha256,'runtime_sha256':config.executable_sha256,'source_revision':config.source_revision}
    fingerprint=sha256(json.dumps(inputs,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()).hexdigest()
    async def handler(current,attempt_id,pool):
        async with asyncio.timeout(body.timeout_seconds):
            with TemporaryDirectory(prefix='anp-source-asr-') as folder:
                original=Path(folder)/'source.media';await artifacts.download(current,asset['id'],original)
                recognition,normalized=await recognize_source(config,pool,folder,original,language=body.language,chunk_seconds=body.chunk_seconds)
                coverage=checked_coverage(recognition)
                pcm=await artifacts.upload(current,attempt_id,normalized,'audio/wav',round(recognition['pcm']['sample_count']/16),16000)
                proof={'input':inputs,'recognition':recognition,'coverage':coverage,'pcm_artifact_id':pcm['id']}
                encoded=json.dumps(proof,ensure_ascii=False,separators=(',',':')).encode()
                if len(encoded)>8*1024**2:raise BusinessError('来源识别证据超过8MiB',422,'SOURCE_ASR_RESPONSE_LIMIT')
                file=Path(folder)/'source-asr.json';file.write_bytes(encoded)
                note=await artifacts.upload(current,attempt_id,file,'application/json')
                return StageResult(outputs=[pcm['id'],note['id']])
    result=await run_gpu_stage(actor,run_id,fingerprint,handler,config,partition=source['id'],
        request_key=operation_key(request_key),node='source_recognition')
    if result['status']=='succeeded':await record_scores(actor,result['attempt'])
    return result


async def read_latest(actor,project_id,source_id):
    await actor.require('deep_dub.project.read');project_id=identifier(project_id);source_id=identifier(source_id)
    source=await actor.objects.select('deep_dub_sources',workspace_id='eq.'+actor.workspace_id,project_id='eq.'+project_id,id='eq.'+source_id)
    if not source:raise BusinessError('来源不存在或无权访问',404,'NOT_FOUND')
    stages=await actor.objects.select('deep_dub_stages',workspace_id='eq.'+actor.workspace_id,project_id='eq.'+project_id,node='eq.source_recognition',partition_key='eq.'+source_id)
    if not stages:return None
    attempts=await actor.objects.select('deep_dub_stage_attempts',workspace_id='eq.'+actor.workspace_id,stage_id='in.('+','.join(s['id'] for s in stages)+')',status='eq.succeeded',order='finished_at.desc,id.desc',limit=1)
    if not attempts:return None
    proof,note=await checked_evidence(actor,attempts[0])
    return {'source_id':source_id,'stage_attempt_id':attempts[0]['id'],'evidence_artifact_id':note['id'],
        'pcm_artifact_id':proof['pcm_artifact_id'],'coverage':proof['coverage'],'recognition':proof['recognition']}
