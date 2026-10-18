"""声音版本词对齐与RMS代理；只分析落点，不签发审核或改写时间线。"""
from hashlib import sha256
import json
from math import ceil,isfinite
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import UUID

from pydantic import Field

from anp.errors import BusinessError
from apps.deep_dub.nodes.transcribe import align_words,ALIGN_RULE
from apps.deep_dub.nodes.alignment import summarize_words,signal_profile,placement_metrics,RULE
from apps.deep_dub.services.artifacts import service as artifacts
from apps.deep_dub.services.blocking import await_blocking,file_identity
from apps.deep_dub.services.execution import service as execution
from apps.deep_dub.services.gpu_runtime import run_gpu_stage
from apps.deep_dub.services.model_pool import configuration
from apps.deep_dub.services.runtime import StageResult
from apps.deep_dub.services.versions import Contract,identifier,operation_key


class AudioTimingInput(Contract):
    audio_attempt_id:UUID
    playback_start_ms:int|None=Field(default=None,ge=0,strict=True)
    timeout_seconds:int=Field(default=300,ge=5,le=900,strict=True)


async def checked_evidence(actor,attempt):
    if len(attempt['outputs'])!=1:raise BusinessError('时序阶段缺少唯一证据',409,'ALIGN_EVIDENCE_INVALID')
    records=await actor.objects.select('deep_dub_artifacts',workspace_id='eq.'+actor.workspace_id,id='eq.'+attempt['outputs'][0])
    if not records or records[0]['stage_attempt_id']!=attempt['id'] or records[0]['media_type']!='application/json' or records[0]['bytes']>2*1024**2:
        raise BusinessError('时序证据类型、大小或归属不符',409,'ALIGN_EVIDENCE_INVALID')
    with TemporaryDirectory(prefix='anp-timing-proof-') as folder:
        file=Path(folder)/'proof.json';await artifacts.download(actor,records[0]['id'],file);proof=json.loads(file.read_bytes())
    inputs=proof.get('input',{});model=proof.get('model',{});signal=proof.get('signal',{})
    stages=await actor.objects.select('deep_dub_stages',workspace_id='eq.'+actor.workspace_id,id='eq.'+attempt['stage_id'])
    audios=await actor.objects.select('deep_dub_audio_attempts',workspace_id='eq.'+actor.workspace_id,id='eq.'+str(inputs.get('audio_attempt_id')))
    if not stages or not audios:raise BusinessError('时序证据输入不存在',409,'ALIGN_EVIDENCE_INVALID')
    audio=audios[0]
    assets=await actor.objects.select('deep_dub_artifacts',workspace_id='eq.'+actor.workspace_id,id='eq.'+audio['artifact_id'])
    texts=await actor.objects.select('deep_dub_translations',workspace_id='eq.'+actor.workspace_id,id='eq.'+audio['translation_id'])
    canonical=json.dumps(inputs,ensure_ascii=False,sort_keys=True,separators=(',',':'))
    if (not assets or not texts or sha256(canonical.encode()).hexdigest()!=stages[0]['input_hash']
        or inputs.get('artifact_id')!=audio['artifact_id'] or inputs.get('audio_sha256')!=assets[0]['sha256']
        or inputs.get('translation_id')!=texts[0]['id'] or model.get('input',{}).get('sha256')!=assets[0]['sha256']
        or model.get('supplied_transcript')!=texts[0]['chinese_text'] or model.get('clamp_timestamps_to_audio') is not False
        or model.get('family')!='qwen3_forced_aligner' or model.get('rule_revision')!=ALIGN_RULE
        or model.get('operation_stopped') is not True or sha256(texts[0]['chinese_text'].encode()).hexdigest()!=inputs.get('text_sha256')
        or any(model.get(k)!=inputs.get(k) for k in ('model_id','weights_sha256','runtime_sha256','source_revision'))):
        raise BusinessError('时序回执与实际WAV、译稿或模型不匹配',409,'ALIGN_EVIDENCE_INVALID')
    words=summarize_words(model.get('words'),model.get('normalized_samples'),texts[0]['chinese_text'])
    if (signal.get('rule_revision')!=RULE or signal.get('sample_rate')!=16000 or signal.get('sample_count')!=model['normalized_samples']
        or abs(model['normalized_samples']/16-assets[0]['duration_ms'])>3
        or proof.get('alignment')!=words):
        raise BusinessError('时序样本数或词摘要不匹配',409,'ALIGN_EVIDENCE_INVALID')
    placement=placement_metrics(words,signal,start_ms=inputs['playback_start_ms'],source_start_ms=inputs['source_start_ms'],
        source_end_ms=inputs['source_end_ms'],video_duration_ms=inputs['video_duration_ms'],max_advance_ms=inputs['max_advance_ms'])
    if proof.get('placement')!=placement:raise BusinessError('指定落点测量不匹配',409,'ALIGN_EVIDENCE_INVALID')
    return proof


async def record_scores(actor,attempt):
    proof=await checked_evidence(actor,attempt);a=proof['alignment'];p=proof['placement'];s=proof['signal']
    values={'alignment_timestamp_structure':int(a['valid_timestamps']),'alignment_transcript_token_coverage':int(a['transcript_token_coverage']),
        'alignment_zero_length_words':a['zero_length_content_words'],'rms_active_frame_ratio':s['active_frame_count']/ceil(s['sample_count']/160),
        'audio_source_window_duration_ratio':p['source_window_duration_ratio'],'audio_window_onset_delta_ms':p['energy_onset_delta_ms'],
        'audio_window_offset_delta_ms':p['energy_offset_delta_ms'],'audio_proposed_video_bounds':int(p['video_bounds_ok']),
        'audio_proposed_advance_bound':int(p['start_within_advance_bound'])}
    for metric,value in values.items():
        if value is not None and (isinstance(value,bool) or not isinstance(value,(int,float)) or not isfinite(value)):
            raise BusinessError('时序代理数值无效',409,'ALIGN_EVIDENCE_INVALID')
        await execution.record_score(actor,attempt['id'],{'metric':metric,'rule_revision':RULE,
            'measurement_status':'measured' if value is not None else 'not_measured','value':value,
            'evidence_artifact_id':attempt['outputs'][0] if value is not None else None,
            'details':{'audio_attempt_id':proof['input']['audio_attempt_id'],'scope':'词时间戳/能量/指定落点代理，非内容、时序可接受性或听感审核'}},metric+':'+attempt['id'])


async def analyze(actor,run_id,payload,request_key=None):
    await actor.require('deep_dub.task.run');await actor.require('deep_dub.audit')
    body=AudioTimingInput.model_validate(payload);run_id=identifier(run_id)
    async def row(table,id):
        rows=await actor.objects.select(table,workspace_id='eq.'+actor.workspace_id,id='eq.'+str(id))
        if not rows:raise BusinessError('时序输入不存在或无权访问',404,'NOT_FOUND')
        return rows[0]
    run=await row('deep_dub_runs',run_id);audio=await row('deep_dub_audio_attempts',body.audio_attempt_id)
    segment=await row('deep_dub_segments',audio['segment_id']);text=await row('deep_dub_translations',audio['translation_id'])
    asset=await row('deep_dub_artifacts',audio['artifact_id']);source=await row('deep_dub_sources',segment['source_id'])
    profile=await row('deep_dub_quality_profiles',run['quality_profile_id'])
    if (run['project_id']!=audio['project_id'] or run['source_id']!=segment['source_id'] or text['segment_id']!=segment['id']
        or asset['project_id']!=run['project_id'] or not asset['media_type'].startswith('audio/') or asset['bytes']>32*1024**2
        or not 0<asset['duration_ms']<=120000 or not 0<len(text['chinese_text'])<=2000):
        raise BusinessError('时序WAV与本轮来源或给定译稿不匹配',409,'ALIGN_INPUT_MISMATCH')
    config=configuration('aligner');start=segment['start_ms'] if body.playback_start_ms is None else body.playback_start_ms
    inputs={'audio_attempt_id':audio['id'],'artifact_id':asset['id'],'audio_sha256':asset['sha256'],
        'translation_id':text['id'],'text_sha256':sha256(text['chinese_text'].encode()).hexdigest(),
        'model_id':config.model_id,'weights_sha256':config.weights_sha256,'runtime_sha256':config.executable_sha256,
        'source_revision':config.source_revision,'rule_revision':RULE,'align_rule_revision':ALIGN_RULE,
        'source_start_ms':segment['start_ms'],'source_end_ms':segment['end_ms'],'video_duration_ms':source['duration_ms'],
        'quality_profile_id':profile['id'],'max_advance_ms':profile['rules']['max_advance_ms'],
        'playback_start_ms':start,'timeout_seconds':body.timeout_seconds}
    fingerprint=sha256(json.dumps(inputs,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()).hexdigest()
    async def handler(current,attempt_id,pool):
        with TemporaryDirectory(prefix='anp-alignment-') as folder:
            file=Path(folder)/'input.wav';await artifacts.download(current,asset['id'],file)
            model=await align_words(config,pool,folder,file,text['chinese_text'],timeout=body.timeout_seconds)
            words=summarize_words(model['words'],model['normalized_samples'],text['chinese_text'])
            def measure():
                normalized=Path(folder)/'asr-16k.wav';signal=signal_profile(normalized)
                if file_identity(normalized)!=model['normalized_input']:raise BusinessError('能量分析输入变化',409,'ALIGN_INPUT_CHANGED')
                return signal
            signal=await await_blocking(measure)
            placement=placement_metrics(words,signal,start_ms=start,source_start_ms=segment['start_ms'],
                source_end_ms=segment['end_ms'],video_duration_ms=source['duration_ms'],max_advance_ms=inputs['max_advance_ms'])
            proof={'input':inputs,'model':model,'alignment':words,'signal':signal,'placement':placement}
            note=Path(folder)/'timing.json';note.write_bytes(json.dumps(proof,ensure_ascii=False,separators=(',',':')).encode())
            receipt=await artifacts.upload(current,attempt_id,note,'application/json')
            return StageResult(outputs=[receipt['id']])
    result=await run_gpu_stage(actor,run_id,fingerprint,handler,config,partition=audio['id'],
        request_key=operation_key(request_key),node='audio_word_timing')
    if result['status']=='succeeded':await record_scores(actor,result['attempt'])
    return result


async def read_latest(actor,project_id,audio_id):
    await actor.require('deep_dub.project.read');project_id=identifier(project_id);audio_id=identifier(audio_id)
    audio=await actor.objects.select('deep_dub_audio_attempts',workspace_id='eq.'+actor.workspace_id,project_id='eq.'+project_id,id='eq.'+audio_id)
    if not audio:raise BusinessError('声音版本不存在或无权访问',404,'NOT_FOUND')
    stages=await actor.objects.select('deep_dub_stages',workspace_id='eq.'+actor.workspace_id,project_id='eq.'+project_id,node='eq.audio_word_timing',partition_key='eq.'+audio_id)
    if not stages:return None
    attempts=await actor.objects.select('deep_dub_stage_attempts',workspace_id='eq.'+actor.workspace_id,stage_id='in.('+','.join(s['id'] for s in stages)+')',status='eq.succeeded',order='finished_at.desc,id.desc',limit=1)
    if not attempts:return None
    proof=await checked_evidence(actor,attempts[0])
    return {'audio_attempt_id':audio_id,'stage_attempt_id':attempts[0]['id'],'evidence_artifact_id':attempts[0]['outputs'][0],
        'performed_at':attempts[0]['finished_at'],'model_id':proof['model']['model_id'],'alignment':proof['alignment'],
        'signal':proof['signal'],'placement':proof['placement'],'semantic_review':'not_measured','timing_review':'not_measured'}
