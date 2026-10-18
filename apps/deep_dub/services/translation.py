"""真实模型服务阶段；与生产设备租约分开，断开HTTP不声称已停止上游计算。"""
import asyncio
from hashlib import sha256
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import time
from uuid import UUID

from pydantic import Field

from anp.config import value
from anp.errors import BusinessError
from apps.deep_dub.nodes.translate import translate,PROMPT_REVISION,SYSTEM
from apps.deep_dub.services.artifacts import service as artifacts
from apps.deep_dub.services.candidates import service as candidates
from apps.deep_dub.services.execution import service as execution
from apps.deep_dub.services.versions import Contract,identifier,operation_key

EVALUATOR_REVISION=PROMPT_REVISION+'-response-evidence-v2'


class TranslationInput(Contract):
    segment_id:UUID
    timeout_seconds:int=Field(default=120,ge=5,le=600,strict=True)


async def scores(actor,attempt):
    if len(attempt['outputs'])!=2:raise BusinessError('译稿阶段缺少上下文与唯一生成证据',409,'TRANSLATION_EVIDENCE_INVALID')
    rows=await actor.objects.select('deep_dub_translations',workspace_id='eq.'+actor.workspace_id,stage_attempt_id='eq.'+attempt['id'])
    if len(rows)!=1 or rows[0]['context_artifact_id'] not in attempt['outputs']:
        raise BusinessError('译稿与阶段上下文关联不完整',409,'TRANSLATION_EVIDENCE_INVALID')
    translation=rows[0];context_id=translation['context_artifact_id']
    response_id=next(x for x in attempt['outputs'] if x!=context_id)
    assets=await actor.objects.select('deep_dub_artifacts',workspace_id='eq.'+actor.workspace_id,id='in.('+','.join(attempt['outputs'])+')')
    stages=await actor.objects.select('deep_dub_stages',workspace_id='eq.'+actor.workspace_id,id='eq.'+attempt['stage_id'])
    if len(assets)!=2 or not stages or any(a['stage_attempt_id']!=attempt['id'] or a['media_type']!='application/json' or a['bytes']>3*1024**2 for a in assets):
        raise BusinessError('译稿证据类型、大小或归属不正确',409,'TRANSLATION_EVIDENCE_INVALID')
    with TemporaryDirectory(prefix='anp-translation-proof-') as folder:
        context=Path(folder)/'context.json';response=Path(folder)/'response.json'
        await artifacts.download(actor,context_id,context);await artifacts.download(actor,response_id,response)
        try:proof=json.loads(response.read_bytes());canonical=context.read_bytes()
        except (ValueError,UnicodeError):raise BusinessError('译稿证据不是有效JSON',409,'TRANSLATION_EVIDENCE_INVALID') from None
        if canonical.endswith(b'\n'):canonical=canonical[:-1]
        if (not isinstance(proof,dict) or proof.get('context_artifact_id')!=context_id or proof.get('segment_id')!=translation['segment_id']
            or proof.get('chinese_text')!=translation['chinese_text'] or proof.get('reported_model')!=translation['model_id']
            or proof.get('prompt_revision')!=translation['prompt_revision'] or proof.get('quality_status')!='not_measured'
            or proof.get('independent_review') is not False or proof.get('input_hash')!=stages[0]['input_hash']
            or sha256(canonical).hexdigest()!=stages[0]['input_hash']):
            raise BusinessError('译稿响应证据与段、正文或输入摘要不匹配',409,'TRANSLATION_EVIDENCE_INVALID')
    await execution.record_score(actor,attempt['id'],{'metric':'translation_response_contract','rule_revision':EVALUATOR_REVISION,
        'measurement_status':'measured','value':1,'evidence_artifact_id':response_id,
        'details':{'scope':'仅完整JSON、段ID与非空中文候选；不是语义/事实/数字通过','evidence_binding':'candidate-context-fk-and-content-hash-v2'}},'translation-contract-v2:'+attempt['id'])
    await execution.record_score(actor,attempt['id'],{'metric':'translation_semantics','rule_revision':'independent-review-pending-v1',
        'measurement_status':'not_measured','value':None,'details':{'scope':'候选生成不作为独立内容复核'}},'translation-review-pending:'+attempt['id'])


async def generate(actor,run_id,payload,request_key=None):
    await actor.require('deep_dub.task.run')
    await actor.require('deep_dub.audit')
    body=TranslationInput.model_validate(payload);run_id=identifier(run_id)
    runs=await actor.objects.select('deep_dub_runs',workspace_id='eq.'+actor.workspace_id,id='eq.'+run_id)
    parts=await actor.objects.select('deep_dub_segments',workspace_id='eq.'+actor.workspace_id,id='eq.'+str(body.segment_id))
    if not runs or not parts or runs[0]['project_id']!=parts[0]['project_id']:
        raise BusinessError('运行与原文段不属于同一项目',404,'NOT_FOUND')
    run,segment=runs[0],parts[0]
    sets=await actor.objects.select('deep_dub_segment_sets',workspace_id='eq.'+actor.workspace_id,id='eq.'+segment['segment_set_id'],sealed='eq.true')
    if not sets or run['source_id']!=sets[0]['source_id']:
        raise BusinessError('原文尚未封存或不属于运行来源',409,'TRANSLATION_INPUT_INVALID')
    before=await actor.objects.select('deep_dub_segments',workspace_id='eq.'+actor.workspace_id,segment_set_id='eq.'+segment['segment_set_id'],
        segment_no='lt.'+str(segment['segment_no']),order='segment_no.desc',limit=2)
    after=await actor.objects.select('deep_dub_segments',workspace_id='eq.'+actor.workspace_id,segment_set_id='eq.'+segment['segment_set_id'],
        segment_no='gt.'+str(segment['segment_no']),order='segment_no.asc',limit=2)
    def content(s):return {k:s[k] for k in ('id','segment_no','speaker_id','start_ms','end_ms','source_text')}
    context={'schema_version':1,'project_id':segment['project_id'],'source_id':sets[0]['source_id'],
        'segment_set_id':segment['segment_set_id'],'target':content(segment),
        'context':{'preceding':[content(s) for s in reversed(before)],'following':[content(s) for s in after]}}
    model=value('deep_dub','translation_model','auto')
    if not isinstance(model,str) or not model.strip() or len(model)>200 or model.strip()=='demo-mock':
        raise BusinessError('需要配置真实译稿模型，默认auto',503,'TRANSLATION_MODEL_UNCONFIGURED')
    model=model.strip()
    canonical=json.dumps({'context':context,'model':model,'system':SYSTEM,'prompt_revision':PROMPT_REVISION,
        'timeout_seconds':body.timeout_seconds},ensure_ascii=False,sort_keys=True,separators=(',',':'))
    if len(canonical.encode())>128*1024:raise BusinessError('原文上下文超过128KiB，应先合理分段',413,'TRANSLATION_INPUT_TOO_LARGE')
    fingerprint=sha256(canonical.encode()).hexdigest()
    stage=await execution.begin_stage(actor,run_id,{'node':'translate','partition':segment['id'],'input_hash':fingerprint},operation_key(request_key))
    attempt=stage['attempt']
    if stage.get('acquired') is not True or attempt['status']!='queued' or attempt['created_by']!=actor.user['id']:
        if attempt['status']=='succeeded' and attempt['created_by']==actor.user['id']:await scores(actor,attempt)
        return {'status':attempt['status'],'attempt':attempt}
    await execution.mark_stage(actor,attempt['id'],'started')
    started=time.monotonic();operation=None;watcher=None
    async def monitor():
        while True:
            await asyncio.sleep(1)
            await actor.require('deep_dub.task.run')
            await actor.require('deep_dub.audit')
            current=await actor.objects.select('deep_dub_runs',workspace_id='eq.'+actor.workspace_id,id='eq.'+run_id)
            tasks=await actor.objects.select('tasks',workspace_id='eq.'+actor.workspace_id,id='eq.'+run['task_id'])
            if not current or current[0]['status']!='running' or not tasks or tasks[0]['status']!='running' or tasks[0]['cancel_requested']:
                raise BusinessError('运行已取消或不再执行',409,'RUN_NOT_RUNNING')
    async def perform():
        with TemporaryDirectory(prefix='anp-translation-') as folder:
            path=Path(folder)/'context.json';path.write_bytes((canonical+'\n').encode())
            evidence=await artifacts.upload(actor,attempt['id'],path,'application/json')
            generated=await translate(context,model=model,user_id=actor.user['id'],run_id=run_id,attempt_id=attempt['id'],timeout=body.timeout_seconds)
            result=Path(folder)/'candidate.json';result.write_bytes(json.dumps({**generated,'segment_id':segment['id'],
                'context_artifact_id':evidence['id'],'input_hash':fingerprint},ensure_ascii=False,separators=(',',':')).encode())
            saved=await artifacts.upload(actor,attempt['id'],result,'application/json')
            await candidates.translation(actor,attempt['id'],{'segment_id':segment['id'],'context_artifact_id':evidence['id'],
                'chinese_text':generated['chinese_text'],'model_id':generated['reported_model'],'prompt_revision':PROMPT_REVISION},'translate:'+attempt['id'])
            return [evidence['id'],saved['id']]
    try:
        operation=asyncio.create_task(perform());watcher=asyncio.create_task(monitor())
        done,_=await asyncio.wait([operation,watcher],return_when=asyncio.FIRST_COMPLETED)
        if watcher in done:await watcher
        outputs=await operation
        committed=await execution.finish_stage(actor,attempt['id'],{'status':'succeeded','outputs':outputs,
            'elapsed_seconds':time.monotonic()-started,'operation_stopped':True})
        if not committed['committed']:raise BusinessError('译稿阶段未获准提交',409,'STAGE_COMMIT_REJECTED')
        # 生成不是语义独审；此处不创建passed审核或填造质量分数。
        result={'status':'succeeded','attempt':committed['attempt']}
    except BaseException as error:
        if operation is not None and not operation.done():
            operation.cancel();await asyncio.gather(operation,return_exceptions=True)
        try:
            await execution.finish_stage(actor,attempt['id'],{'status':'failed','outputs':[],'elapsed_seconds':time.monotonic()-started,
                'error':{'code':error.code if isinstance(error,BusinessError) else 'TRANSLATION_SERVICE_FAILED',
                    'external_compute_stopped':'unknown','scope':'已关闭本次HTTP请求，不推定上游任务停止'},'operation_stopped':False})
        except Exception:pass  # 已取消/撤权的数据库状态不覆盖；保留失败尝试与已上传证据。
        raise
    finally:
        if watcher is not None:
            watcher.cancel();await asyncio.gather(watcher,return_exceptions=True)
    await scores(actor,result['attempt'])
    return result
