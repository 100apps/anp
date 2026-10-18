"""独立回转录绑定当前实际WAV与译稿，保留差异；不替用户签发语义通过。"""
from hashlib import sha256
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import UUID

from pydantic import Field

from anp.errors import BusinessError
from apps.deep_dub.nodes.transcribe import transcribe,RULE
from apps.deep_dub.nodes.text_compare import compare,RULE as DIFF_RULE
from apps.deep_dub.services.artifacts import service as artifacts
from apps.deep_dub.services.blocking import await_blocking
from apps.deep_dub.services.execution import service as execution
from apps.deep_dub.services.gpu_runtime import run_gpu_stage
from apps.deep_dub.services.model_pool import configuration
from apps.deep_dub.services.runtime import StageResult
from apps.deep_dub.services.versions import Contract,identifier,operation_key


class AudioReviewInput(Contract):
    audio_attempt_id:UUID
    timeout_seconds:int=Field(default=300,ge=5,le=900,strict=True)


async def checked_evidence(actor,attempt):
    if len(attempt['outputs'])!=1:raise BusinessError('回转录阶段缺少唯一证据',409,'ASR_EVIDENCE_INVALID')
    asset=attempt['outputs'][0]
    receipts=await actor.objects.select('deep_dub_artifacts',workspace_id='eq.'+actor.workspace_id,id='eq.'+asset)
    if not receipts or receipts[0]['stage_attempt_id']!=attempt['id'] or receipts[0]['media_type']!='application/json' or receipts[0]['bytes']>2*1024**2:
        raise BusinessError('回转录证据类型、大小或归属不正确',409,'ASR_EVIDENCE_INVALID')
    with TemporaryDirectory(prefix='anp-asr-proof-') as folder:
        file=Path(folder)/'review.json';await artifacts.download(actor,asset,file)
        proof=json.loads(file.read_bytes())
    inputs=proof.get('input',{});asr=proof.get('recognition',{})
    stages=await actor.objects.select('deep_dub_stages',workspace_id='eq.'+actor.workspace_id,id='eq.'+attempt['stage_id'])
    audios=await actor.objects.select('deep_dub_audio_attempts',workspace_id='eq.'+actor.workspace_id,id='eq.'+str(inputs.get('audio_attempt_id')))
    assets=await actor.objects.select('deep_dub_artifacts',workspace_id='eq.'+actor.workspace_id,id='eq.'+str(inputs.get('artifact_id')))
    expected=proof.get('expected_text');observed=asr.get('text')
    comparison=await await_blocking(compare,expected,observed)
    canonical=json.dumps(inputs,ensure_ascii=False,sort_keys=True,separators=(',',':'))
    if (not stages or not audios or not assets or sha256(canonical.encode()).hexdigest()!=stages[0]['input_hash']
        or audios[0]['artifact_id']!=inputs.get('artifact_id') or assets[0]['sha256']!=inputs.get('audio_sha256')
        or asr.get('input',{}).get('sha256')!=assets[0]['sha256'] or asr.get('context_prompt')!=''
        or asr.get('operation_stopped') is not True or proof.get('comparison')!=comparison
        or any(asr.get(k)!=inputs.get(k) for k in ('model_id','weights_sha256','runtime_sha256','source_revision','language_hint','rule_revision'))):
        raise BusinessError('回转录证据与实际音频或输入摘要不匹配',409,'ASR_EVIDENCE_INVALID')
    translations=await actor.objects.select('deep_dub_translations',workspace_id='eq.'+actor.workspace_id,id='eq.'+audios[0]['translation_id'])
    if (not translations or translations[0]['id']!=inputs.get('translation_id') or translations[0]['chinese_text']!=expected
        or sha256(expected.encode()).hexdigest()!=inputs.get('expected_text_sha256')):
        raise BusinessError('回转录期待文本与绑定译稿不符',409,'ASR_EVIDENCE_INVALID')
    return proof,audios[0]


async def record_scores(actor,attempt):
    proof,audio=await checked_evidence(actor,attempt);comparison=proof['comparison'];asset=attempt['outputs'][0]
    await execution.record_score(actor,attempt['id'],{'metric':'tts_asr_character_error_rate','rule_revision':DIFF_RULE,
        'measurement_status':'measured','value':comparison['character_error_rate'],'evidence_artifact_id':asset,
        'details':{'audio_attempt_id':audio['id'],'scope':comparison['scope']}},'asr-cer:'+attempt['id'])
    await execution.record_score(actor,attempt['id'],{'metric':'tts_asr_semantic_review','rule_revision':RULE,
        'measurement_status':'not_measured','value':None,'details':{'audio_attempt_id':audio['id'],
            'scope':'未签发内容/事实/数字/否定/专名独审通过，ASR精确吻合也不能替代'}},'asr-semantic-pending:'+attempt['id'])


async def review(actor,run_id,payload,request_key=None):
    await actor.require('deep_dub.task.run');await actor.require('deep_dub.audit')
    body=AudioReviewInput.model_validate(payload);run_id=identifier(run_id)
    async def row(table,id):
        items=await actor.objects.select(table,workspace_id='eq.'+actor.workspace_id,id='eq.'+str(id))
        if not items:raise BusinessError('回转录输入不存在或无权访问',404,'NOT_FOUND')
        return items[0]
    run=await row('deep_dub_runs',run_id);audio=await row('deep_dub_audio_attempts',body.audio_attempt_id)
    segment=await row('deep_dub_segments',audio['segment_id']);translation=await row('deep_dub_translations',audio['translation_id'])
    asset=await row('deep_dub_artifacts',audio['artifact_id'])
    if (run['project_id']!=audio['project_id'] or run['source_id']!=segment['source_id']
        or translation['segment_id']!=segment['id'] or asset['project_id']!=run['project_id']
        or not asset['media_type'].startswith('audio/') or not 0<asset['duration_ms']<=120000 or asset['bytes']>32*1024**2):
        raise BusinessError('回转录WAV与本轮来源或绑定译稿不符',409,'ASR_INPUT_MISMATCH')
    await await_blocking(compare,translation['chinese_text'],'')  # 在加载模型前核对比较边界。
    config=configuration('asr')
    inputs={'audio_attempt_id':audio['id'],'artifact_id':asset['id'],'audio_sha256':asset['sha256'],
        'translation_id':translation['id'],'expected_text_sha256':sha256(translation['chinese_text'].encode()).hexdigest(),
        'model_id':config.model_id,'weights_sha256':config.weights_sha256,'runtime_sha256':config.executable_sha256,
        'source_revision':config.source_revision,'rule_revision':RULE,'diff_rule_revision':DIFF_RULE,
        'timeout_seconds':body.timeout_seconds,'language_hint':'Chinese','context_prompt':''}
    fingerprint=sha256(json.dumps(inputs,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()).hexdigest()
    async def handler(current,attempt_id,pool):
        with TemporaryDirectory(prefix='anp-asr-review-') as folder:
            source=Path(folder)/'input.wav';await artifacts.download(current,asset['id'],source)
            recognition=await transcribe(config,pool,folder,source,timeout=body.timeout_seconds)
            comparison=await await_blocking(compare,translation['chinese_text'],recognition['text'])
            proof={'input':inputs,'expected_text':translation['chinese_text'],'recognition':recognition,'comparison':comparison}
            file=Path(folder)/'review.json';file.write_bytes(json.dumps(proof,ensure_ascii=False,separators=(',',':')).encode())
            receipt=await artifacts.upload(current,attempt_id,file,'application/json')
            return StageResult(outputs=[receipt['id']])
    result=await run_gpu_stage(actor,run_id,fingerprint,handler,config,partition=audio['id'],
        request_key=operation_key(request_key),node='audio_backtranscription')
    if result['status']=='succeeded':await record_scores(actor,result['attempt'])
    return result


async def read_latest(actor,project_id,audio_id):
    await actor.require('deep_dub.project.read')
    project_id=identifier(project_id);audio_id=identifier(audio_id)
    audio=await actor.objects.select('deep_dub_audio_attempts',workspace_id='eq.'+actor.workspace_id,project_id='eq.'+project_id,id='eq.'+audio_id)
    if not audio:raise BusinessError('声音版本不存在或无权访问',404,'NOT_FOUND')
    stages=await actor.objects.select('deep_dub_stages',workspace_id='eq.'+actor.workspace_id,project_id='eq.'+project_id,
        node='eq.audio_backtranscription',partition_key='eq.'+audio_id)
    if not stages:return None
    attempts=await actor.objects.select('deep_dub_stage_attempts',workspace_id='eq.'+actor.workspace_id,
        stage_id='in.('+','.join(s['id'] for s in stages)+')',status='eq.succeeded',order='finished_at.desc,id.desc',limit=1)
    if not attempts:return None
    proof,_=await checked_evidence(actor,attempts[0]);recognized=proof['recognition'];comparison=proof['comparison']
    return {'audio_attempt_id':audio_id,'stage_attempt_id':attempts[0]['id'],'evidence_artifact_id':attempts[0]['outputs'][0],
        'performed_at':attempts[0]['finished_at'],'latest_successful':True,'expected_text':proof['expected_text'],
        'recognized_text':recognized['text'],'model_id':recognized['model_id'],'weights_sha256':recognized['weights_sha256'],
        'character_error_rate':comparison['character_error_rate'],'edit_distance':comparison['edit_distance'],
        'difference_spans':comparison['difference_spans'],'difference_spans_truncated':comparison['difference_spans_truncated'],
        'scope':comparison['scope'],'semantic_review':'not_measured'}
