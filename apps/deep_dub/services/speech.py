"""绑定确切译稿/音色/情绪版本的真实本机GPU配音，保留未审核状态。"""
from hashlib import sha256
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import UUID

from pydantic import Field

from anp.errors import BusinessError
from apps.deep_dub.nodes.tts import synthesize,RULE
from apps.deep_dub.services.artifacts import service as artifacts
from apps.deep_dub.services.candidates import service as candidates
from apps.deep_dub.services.execution import service as execution
from apps.deep_dub.services.gpu_runtime import run_gpu_stage
from apps.deep_dub.services.model_pool import configuration
from apps.deep_dub.services.runtime import StageResult
from apps.deep_dub.services.versions import Contract,identifier,operation_key
from apps.deep_dub.services.waveforms import prepare as prepare_waveform


class SpeechInput(Contract):
    segment_id:UUID
    translation_id:UUID
    voice_reference_id:UUID
    emotion_reference_id:UUID
    generation_factor:float=Field(default=1.,ge=.90,le=1.35,allow_inf_nan=False,strict=True)
    timeout_seconds:int=Field(default=300,ge=5,le=900,strict=True)
    seed:int=Field(default=42,ge=0,le=2147483647,strict=True)


async def scores(actor,attempt):
    audio=await actor.objects.select('deep_dub_audio_attempts',workspace_id='eq.'+actor.workspace_id,stage_attempt_id='eq.'+attempt['id'])
    if len(audio)!=1 or set(attempt['outputs'])!={audio[0]['artifact_id'],audio[0]['evidence_artifact_id']}:
        raise BusinessError('配音阶段缺少唯一音频及绑定回执',409,'TTS_EVIDENCE_INVALID')
    candidate=audio[0]
    with TemporaryDirectory(prefix='anp-tts-proof-') as folder:
        file=Path(folder)/'evidence.json';await artifacts.download(actor,candidate['evidence_artifact_id'],file)
        proof=json.loads(file.read_bytes())
    stages=await actor.objects.select('deep_dub_stages',workspace_id='eq.'+actor.workspace_id,id='eq.'+attempt['stage_id'])
    outputs=await actor.objects.select('deep_dub_artifacts',workspace_id='eq.'+actor.workspace_id,id='eq.'+candidate['artifact_id'])
    canonical=json.dumps(proof.get('input'),ensure_ascii=False,sort_keys=True,separators=(',',':'))
    if (not stages or not outputs or sha256(canonical.encode()).hexdigest()!=stages[0]['input_hash']
        or proof.get('audio_artifact_id')!=candidate['artifact_id'] or proof.get('audio_sha256')!=outputs[0]['sha256']
        or proof.get('parameters')!=candidate['parameters'] or proof.get('operation_stopped') is not True
        or proof.get('audio',{}).get('duration_ms')!=outputs[0]['duration_ms']):
        raise BusinessError('配音回执、输入摘要与音频不匹配',409,'TTS_EVIDENCE_INVALID')
    await execution.record_score(actor,attempt['id'],{'metric':'tts_pcm_contract','rule_revision':RULE,
        'measurement_status':'measured','value':1,'evidence_artifact_id':candidate['evidence_artifact_id'],
        'details':{'scope':'仅PCM格式、样本与不可变输入/输出关联；非内容、时序或听感通过'}},'tts-contract:'+attempt['id'])
    for metric in ['tts_content_consistency','tts_speaker_identity','tts_naturalness']:
        await execution.record_score(actor,attempt['id'],{'metric':metric,'rule_revision':RULE,
            'measurement_status':'not_measured','value':None,'details':{'scope':'待当前WAV独立回转录/听审，不用生成自评替代'}},metric+':'+attempt['id'])
    return candidate


async def generate(actor,run_id,payload,request_key=None):
    await actor.require('deep_dub.task.run');await actor.require('deep_dub.audit')
    body=SpeechInput.model_validate(payload);run_id=identifier(run_id)
    async def row(table,id):
        rows=await actor.objects.select(table,workspace_id='eq.'+actor.workspace_id,id='eq.'+str(id))
        if not rows:raise BusinessError('配音输入不存在或无权访问',404,'NOT_FOUND')
        return rows[0]
    run=await row('deep_dub_runs',run_id);segment=await row('deep_dub_segments',body.segment_id)
    translation=await row('deep_dub_translations',body.translation_id)
    voice=await row('deep_dub_voice_references',body.voice_reference_id)
    emotion=await row('deep_dub_emotion_references',body.emotion_reference_id)
    parts=await row('deep_dub_segment_sets',segment['segment_set_id'])
    if (run['project_id']!=segment['project_id'] or run['source_id']!=segment['source_id'] or not parts['sealed']
        or any(r['project_id']!=run['project_id'] for r in [translation,voice,emotion])
        or translation['segment_id']!=segment['id'] or voice['speaker_id']!=segment['speaker_id']
        or voice['source_id']!=run['source_id'] or emotion['segment_id']!=segment['id'] or emotion['source_id']!=run['source_id']
        or (emotion['start_ms'],emotion['end_ms'])!=(segment['start_ms'],segment['end_ms'])):
        raise BusinessError('译稿、音色或情绪不是本轮本段的确切版本',409,'TTS_INPUT_MISMATCH')
    v=await row('deep_dub_artifacts',voice['processed_artifact_id']);e=await row('deep_dub_artifacts',emotion['artifact_id'])
    if any(a['media_type']!='audio/wav' or a['bytes']>8*1024**2 for a in [v,e]):
        raise BusinessError('参考必须是有界WAV音频',422,'TTS_INPUT_INVALID')
    config=configuration()
    inputs={'segment_id':segment['id'],'translation_id':translation['id'],'chinese_text':translation['chinese_text'],
        'voice_reference_id':voice['id'],'voice_artifact_id':v['id'],'voice_sha256':v['sha256'],
        'emotion_reference_id':emotion['id'],'emotion_artifact_id':e['id'],'emotion_sha256':e['sha256'],
        'source_id':run['source_id'],'model_id':config.model_id,'weights_sha256':config.weights_sha256,
        'runtime_sha256':config.executable_sha256,'source_revision':config.source_revision,'rule_revision':RULE,
        'generation_factor':body.generation_factor,'timeout_seconds':body.timeout_seconds,'seed':body.seed}
    fingerprint=sha256(json.dumps(inputs,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()).hexdigest()
    async def handler(current,attempt_id,pool):
        with TemporaryDirectory(prefix='anp-tts-') as folder:
            fixed=Path(folder)/'voice.wav';feeling=Path(folder)/'emotion.wav'
            await artifacts.download(current,v['id'],fixed);await artifacts.download(current,e['id'],feeling)
            output,proof=await synthesize(config,pool,folder,translation['chinese_text'],fixed,feeling,
                body.generation_factor,body.timeout_seconds,body.seed)
            wav=await artifacts.upload(current,attempt_id,output,'audio/wav',proof['audio']['duration_ms'],proof['audio']['sample_rate'])
            proof.update(input=inputs,audio_artifact_id=wav['id'],audio_sha256=wav['sha256'])
            note=Path(folder)/'evidence.json';note.write_bytes(json.dumps(proof,ensure_ascii=False,separators=(',',':')).encode())
            receipt=await artifacts.upload(current,attempt_id,note,'application/json')
            await candidates.audio(current,attempt_id,{'segment_id':segment['id'],'translation_id':translation['id'],
                'voice_reference_id':voice['id'],'emotion_reference_id':emotion['id'],'artifact_id':wav['id'],
                'evidence_artifact_id':receipt['id'],'generation_factor':body.generation_factor,'parameters':proof['parameters']},'tts:'+attempt_id)
            return StageResult(outputs=[wav['id'],receipt['id']])
    result=await run_gpu_stage(actor,run_id,fingerprint,handler,config,partition=segment['id'],request_key=operation_key(request_key))
    if result['status']=='succeeded':
        audio=await scores(actor,result['attempt'])
        wave=await prepare_waveform(actor,run_id,{'artifact_id':audio['artifact_id']},'speech-wave:'+audio['id'])
        if wave['status']!='succeeded':
            raise BusinessError('配音已保存，波形仍在准备；请刷新并重试相同请求',409,'TTS_WAVEFORM_PENDING')
    return result
