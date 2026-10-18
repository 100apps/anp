"""在本机CPU租约中准备固定音色或逐段情绪参考，追加不可变登记与证据。"""
from hashlib import sha256
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Literal
from uuid import UUID

from pydantic import Field

from anp.errors import BusinessError
from apps.deep_dub.nodes.references import prepare_reference,RULE
from apps.deep_dub.services.artifacts import service as artifacts
from apps.deep_dub.services.candidates import service as candidates
from apps.deep_dub.services.runtime import run_cpu_stage,StageResult
from apps.deep_dub.services.versions import Contract,identifier,operation_key


class ReferencePreparation(Contract):
    segment_id:UUID
    kind:Literal['voice','emotion']
    voice_start_ms:int|None=Field(default=None,ge=0,strict=True)
    voice_end_ms:int|None=Field(default=None,gt=0,strict=True)
    filter_mode:Literal['raw','speech-band']='raw'
    device_id:UUID|None=None


async def prepare(actor,run_id,payload,request_key=None):
    await actor.require('deep_dub.task.run')
    body=ReferencePreparation.model_validate(payload);run_id=identifier(run_id)
    runs=await actor.objects.select('deep_dub_runs',workspace_id='eq.'+actor.workspace_id,id='eq.'+run_id)
    segments=await actor.objects.select('deep_dub_segments',workspace_id='eq.'+actor.workspace_id,id='eq.'+str(body.segment_id))
    if not runs or not segments or runs[0]['project_id']!=segments[0]['project_id'] or runs[0]['source_id']!=segments[0]['source_id']:
        raise BusinessError('参考段与运行不属于同一项目或来源',404,'NOT_FOUND')
    segment=segments[0]
    sets=await actor.objects.select('deep_dub_segment_sets',workspace_id='eq.'+actor.workspace_id,id='eq.'+segment['segment_set_id'],sealed='eq.true')
    sources=await actor.objects.select('deep_dub_sources',workspace_id='eq.'+actor.workspace_id,id='eq.'+segment['source_id'])
    if not sets or not sources:raise BusinessError('来源或封存分段不存在',404,'NOT_FOUND')
    start,end=segment['start_ms'],segment['end_ms']
    if body.kind=='voice':
        start=start if body.voice_start_ms is None else body.voice_start_ms
        end=min(segment['end_ms'],start+10000) if body.voice_end_ms is None else body.voice_end_ms
        if not segment['start_ms']<=start<end<=segment['end_ms'] or not 3000<=end-start<=20000:
            raise BusinessError('固定音色须为同一角色段内3–20秒原声窗口',422,'REFERENCE_WINDOW_INVALID')
    elif body.voice_start_ms is not None or body.voice_end_ms is not None:
        raise BusinessError('情绪参考必须使用完整准确原声窗，不能传音色窗口',422,'REFERENCE_WINDOW_INVALID')
    if end-start>120000:raise BusinessError('参考窗超过120秒，应先合理分段',422,'REFERENCE_WINDOW_INVALID')
    source=sources[0];assets=await actor.objects.select('deep_dub_artifacts',workspace_id='eq.'+actor.workspace_id,id='eq.'+source['artifact_id'])
    if not assets:raise BusinessError('原片不存在或无权访问',404,'NOT_FOUND')
    asset=assets[0];subject=segment['speaker_id'] if body.kind=='voice' else segment['id']
    inputs={'source_id':source['id'],'artifact_id':asset['id'],'source_sha256':asset['sha256'],'subject_id':subject,
        'segment_id':segment['id'],'start_ms':start,'end_ms':end,'kind':body.kind,'filter_mode':body.filter_mode,'rule_revision':RULE}
    fingerprint=sha256(json.dumps(inputs,sort_keys=True,separators=(',',':')).encode()).hexdigest()
    async def handler(current,attempt_id):
        with TemporaryDirectory(prefix='anp-reference-') as folder:
            file=Path(folder)/'source';await artifacts.download(current,asset['id'],file)
            raw,processed,proof=await prepare_reference(file,folder,start,end,body.filter_mode)
            original=await artifacts.upload(current,attempt_id,raw,'audio/wav',proof['original']['duration_ms'],24000)
            cleaned=original if processed==raw else await artifacts.upload(current,attempt_id,processed,'audio/wav',proof['processed']['duration_ms'],24000)
            note=Path(folder)/'evidence.json';note.write_bytes(json.dumps({**inputs,**proof,'original_artifact_id':original['id'],
                'processed_artifact_id':cleaned['id'],'input_hash':fingerprint},ensure_ascii=False,separators=(',',':')).encode())
            evidence=await artifacts.upload(current,attempt_id,note,'application/json')
            await candidates.reference(current,attempt_id,{'kind':body.kind,'source_id':source['id'],'subject_id':subject,'start_ms':start,
                'end_ms':end,'original_artifact_id':original['id'],'processed_artifact_id':cleaned['id'],
                'processing':{'rule_revision':RULE,'filter_mode':body.filter_mode,'evidence_artifact_id':evidence['id'],
                    'original_metrics':proof['original'],'processed_metrics':proof['processed'],
                    'speaker_identity':'not_measured','listening_quality':'not_measured'}},'reference:'+attempt_id)
            return StageResult(outputs=list(dict.fromkeys([original['id'],cleaned['id'],evidence['id']])))
    return await run_cpu_stage(actor,run_id,'prepare_'+body.kind+'_reference',fingerprint,handler,partition=str(body.segment_id),
        request_key=operation_key(request_key),device_id=str(body.device_id) if body.device_id else None)
