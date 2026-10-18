"""单轮来源准备委派；用户会话只在业务主机ACL保护目录，引擎状态只含ID。"""
import asyncio
from hashlib import sha256
import hmac,json,re,time
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import uuid5,NAMESPACE_URL

import jwt
from pydantic import Field

from anp import STATE_ROOT
from anp.auth.identity import actor_from_token
from anp.errors import BusinessError
from apps.deep_dub.services.delegation import JobVault,canonical
from apps.deep_dub.services.versions import Contract,identifier,operation_key
from apps.deep_dub.services.execution import service as execution
from apps.deep_dub.services.artifacts import service as artifacts
from apps.deep_dub.services import source_acquisition as acquisition,source_recognition as recognition,source_alignment as alignment,source_reference as reference
from apps.deep_dub.services.processing import decode_artifact
from apps.deep_dub.services.model_pool import configuration as model_configuration

REVISION='source-preparation-flow-v1'
STEPS=('acquire','decode','recognize','align','reference','review')


class PreparationInput(Contract):
    reference_url:str|None=Field(default=None,min_length=1,max_length=500)
    language:str=Field(default='English',pattern=r'^(English|Chinese)$')
    chunk_seconds:int=Field(default=60,ge=30,le=90,strict=True)
    ttl_seconds:int=Field(default=600,ge=60,le=1200,strict=True)


class SourceVault(JobVault):
    def load(self,token,run_id,procedure_id):
        if not isinstance(token,str) or not re.fullmatch(r'[0-9a-f]{32}\.[0-9a-f]{64}',token):
            raise BusinessError('来源准备授权不正确',401,'INVALID_SOURCE_GRANT')
        grant_id,signature=token.split('.')
        key=self.key()
        try:encoded=(self.directory/(grant_id+'.json')).read_bytes();record=json.loads(encoded)
        except (OSError,ValueError):raise BusinessError('来源准备授权不正确',401,'INVALID_SOURCE_GRANT') from None
        if not hmac.compare_digest(signature,hmac.new(key,grant_id.encode()+b'.'+encoded,sha256).hexdigest()):
            raise BusinessError('来源准备授权不正确',401,'INVALID_SOURCE_GRANT')
        if (record.get('scope')!='deep_dub.source_preparation' or record.get('version')!=1
            or record.get('run_id')!=identifier(run_id) or record.get('procedure_id')!=identifier(procedure_id)):
            raise BusinessError('来源准备授权范围不符',403,'SOURCE_GRANT_SCOPE_MISMATCH')
        if record.get('expires_at',0)<=time.time():raise BusinessError('来源准备授权已到期，须由原用户重新授权',401,'SOURCE_GRANT_EXPIRED')
        return record


vault=SourceVault(STATE_ROOT/'deep_dub/source-preparation-grants')


def model_identity():
    result={}
    for kind in ('asr','aligner'):
        config=model_configuration(kind)
        result[kind]={k:getattr(config,k) for k in ('model_id','family','weights_sha256','executable_sha256','source_revision')}
    return result


def request_key(procedure_id,step):return 'source-preparation:'+identifier(procedure_id)+':'+step


async def issue(actor,run_id,payload,request=None):
    actor=await actor_from_token(actor.token,actor.workspace_id)
    await actor.require('deep_dub.task.run');await actor.require('deep_dub.audit')
    body=PreparationInput.model_validate(payload);run_id=identifier(run_id);key=operation_key(request)
    # describe核对冻结URL和来源绑定；不允许拿其他来源代替新下载。
    _,_,download,digest=await acquisition.describe(actor,run_id,{'timeout_seconds':3600})
    runs=await actor.objects.select('deep_dub_runs',workspace_id='eq.'+actor.workspace_id,id='eq.'+run_id)
    if not runs or runs[0]['status'] not in ('running','waiting_review'):
        raise BusinessError('来源准备需要执行中或待复核的运行',409,'SOURCE_RUN_NOT_ACTIVE')
    if body.reference_url:reference.reference_location(body.reference_url,download['source_url_snapshot'])
    parameters=body.model_dump(exclude={'ttl_seconds'});models=model_identity()
    binding={'revision':REVISION,'run_id':run_id,'run_input_hash':runs[0]['input_hash'],'download_input_hash':digest,
        'parameters':parameters,'models':models}
    fingerprint=sha256(canonical(binding)).hexdigest()
    procedure_id=str(uuid5(NAMESPACE_URL,f'anp:deep_dub:source-preparation:{actor.workspace_id}:{run_id}:{key}:{fingerprint}'))
    if runs[0]['status']=='waiting_review':
        acquired=await acquisition.read(actor,run_id)
        attempts=await _rows(actor,'deep_dub_stage_attempts',id='eq.'+acquired['stage_attempt_id']) if acquired else []
        if not attempts or attempts[0]['request_key']!=request_key(procedure_id,'acquire'):
            raise BusinessError('待复核运行不能换用其他准备流程',409,'SOURCE_REVIEW_PENDING')
        attempt=attempts[0]
    else:
        selected=await execution.begin_stage(actor,run_id,{'node':'source_acquisition','input_hash':digest,'device_kind':'cpu'},request_key(procedure_id,'acquire'))
        attempt=selected['attempt']
    if attempt['created_by']!=actor.user['id']:
        raise BusinessError('不能为其他执行者续签来源准备',403,'SOURCE_GRANT_OWNER_MISMATCH')
    if attempt['request_key']!=request_key(procedure_id,'acquire'):
        raise BusinessError('原片已属于其他准备请求，须使用原幂等键接续',409,'SOURCE_PREPARATION_KEY_MISMATCH')
    if attempt['status'] not in ('queued','succeeded'):
        raise BusinessError('原片尝试不能续签，须先核对其终态',409,'SOURCE_GRANT_NOT_ISSUABLE')
    expires=min(float(jwt.decode(actor.token,options={'verify_signature':False})['exp']),time.time()+body.ttl_seconds)
    if expires<=time.time():raise BusinessError('登录已到期',401,'UNAUTHENTICATED')
    record={'version':1,'scope':'deep_dub.source_preparation','workspace_id':actor.workspace_id,'user_id':actor.user['id'],
        'actor_token':actor.token,'run_id':run_id,'procedure_id':procedure_id,'input_hash':fingerprint,'binding':binding,
        'request_key':key,'download_attempt_id':attempt['id'],'expires_at':expires}
    token=vault.save(record);vault.bind_engine(token,run_id,procedure_id)
    return {'run_id':run_id,'procedure_id':procedure_id,'expires_at':expires,'download_attempt_id':attempt['id'],'request_key':key}


async def _rows(actor,table,**filters):
    return await actor.objects.select(table,workspace_id='eq.'+actor.workspace_id,**filters)


async def require_stage(actor,run_id,node,partition):
    stages=await _rows(actor,'deep_dub_stages',run_id='eq.'+run_id,node='eq.'+node,partition_key='eq.'+partition)
    attempts=await _rows(actor,'deep_dub_stage_attempts',id='in.('+','.join(s['current_attempt_id'] for s in stages if s['current_attempt_id'])+')',status='eq.succeeded') if stages else []
    if not attempts:raise BusinessError('来源准备前置阶段尚未成功',409,'SOURCE_PREREQUISITE_MISSING')


async def review(actor,record,run,source):
    started=time.monotonic()
    recognized=await recognition.read_latest(actor,run['project_id'],source['id'])
    words=await alignment.read_latest(actor,run['project_id'],source['id'])
    if not recognized or not words:raise BusinessError('缺少来源正文及词时间证据',409,'SOURCE_PREREQUISITE_MISSING')
    compared=await reference.read_latest_reference(actor,run['project_id'],source['id']) if record['binding']['parameters']['reference_url'] else None
    if record['binding']['parameters']['reference_url'] and not compared:
        raise BusinessError('缺少官方参考差异证据',409,'SOURCE_PREREQUISITE_MISSING')
    summary={'revision':REVISION,'procedure_id':record['procedure_id'],'source_id':source['id'],
        'asr_evidence_artifact_id':recognized['evidence_artifact_id'],'word_evidence_artifact_id':words['evidence_artifact_id'],
        'reference_evidence_artifact_id':compared['evidence_artifact_id'] if compared else None,
        'asr_coverage':recognized['coverage'],'word_summary':words['measurement']['summary'],
        'reference_difference_count':compared['proof']['comparison']['difference_count'] if compared else None,
        'source_review':'not_measured','speaker_review':'not_measured','subtitle_timing_review':'not_measured',
        'reason':'来源候选已准备，正文、角色及词边界需独立复核；不创建字幕或生产成功回执'}
    fingerprint=sha256(canonical(summary)).hexdigest()
    if run['status']=='waiting_review':
        stages=await _rows(actor,'deep_dub_stages',run_id='eq.'+run['id'],node='eq.source_preparation_review',
            partition_key='eq.'+record['procedure_id'],input_hash='eq.'+fingerprint,status='eq.waiting_review')
        if not stages:raise BusinessError('待复核输入已变化，不能沿用旧回执',409,'SOURCE_REVIEW_INPUT_CHANGED')
        return {'status':'waiting_review','summary':summary,'review_attempt_id':stages[0]['current_attempt_id']}
    selected=await execution.begin_stage(actor,run['id'],{'node':'source_preparation_review','partition':record['procedure_id'],'input_hash':fingerprint},request_key(record['procedure_id'],'review'))
    attempt=selected['attempt']
    if attempt['status'] in ('queued','running'):
        if attempt['status']=='queued':await execution.mark_stage(actor,attempt['id'],'started')
        with TemporaryDirectory(prefix='anp-source-preparation-') as folder:
            file=Path(folder)/'review.json';file.write_bytes(canonical(summary))
            saved=await artifacts.upload(actor,attempt['id'],file,'application/json')
        await execution.finish_stage(actor,attempt['id'],{'status':'waiting_review','outputs':[saved['id']],
            'elapsed_seconds':time.monotonic()-started,'operation_stopped':True})
    await actor.objects.rpc('deep_dub_wait_for_source_review',p_workspace=actor.workspace_id,p_run=run['id'],p_attempt=attempt['id'])
    return {'status':'waiting_review','summary':summary,'review_attempt_id':attempt['id']}


async def dispatch(run_id,procedure_id,step):
    if step not in STEPS:raise BusinessError('来源准备步骤不存在',404,'NOT_FOUND')
    token=vault.engine_token(run_id,procedure_id);record=vault.load(token,run_id,procedure_id)
    actor=await actor_from_token(record['actor_token'],record['workspace_id'])
    if actor.user['id']!=record['user_id']:raise BusinessError('来源准备身份不符',403,'SOURCE_GRANT_OWNER_MISMATCH')
    await actor.require('deep_dub.task.run');await actor.require('deep_dub.audit')
    runs=await _rows(actor,'deep_dub_runs',id='eq.'+record['run_id'])
    if not runs or runs[0]['status'] not in ('running','waiting_review') or runs[0]['input_hash']!=record['binding']['run_input_hash']:
        raise BusinessError('来源准备已取消、停止或输入变化',409,'STALE_SOURCE_GRANT')
    run=runs[0]
    if run['status']=='waiting_review' and step!='review':raise BusinessError('来源已待复核，禁止重新启动生产阶段',409,'SOURCE_REVIEW_PENDING')
    if model_identity()!=record['binding']['models']:
        raise BusinessError('来源准备模型配置变化，须重新授权',409,'SOURCE_MODEL_BINDING_CHANGED')
    _,_,_,digest=await acquisition.describe(actor,run_id,{'timeout_seconds':3600})
    if digest!=record['binding']['download_input_hash']:raise BusinessError('来源下载上下文变化，须重新授权',409,'SOURCE_INPUT_CHANGED')
    async def execute():
        key=request_key(procedure_id,step)
        if step=='acquire':
            result=await acquisition.acquire(actor,run_id,{'timeout_seconds':3600},key)
            if result['attempt']['id']!=record['download_attempt_id']:raise BusinessError('原片尝试超出授权',409,'SOURCE_GRANT_SCOPE_MISMATCH')
        else:
            acquired=await acquisition.read(actor,run_id)
            if not acquired or acquired['stage_attempt_id']!=record['download_attempt_id']:
                raise BusinessError('来源未由本次授权获取',409,'SOURCE_GRANT_SCOPE_MISMATCH')
            source=(await _rows(actor,'deep_dub_sources',id='eq.'+acquired['source_id']))[0]
            if step=='decode':result=await decode_artifact(actor,run_id,{'artifact_id':source['artifact_id'],'timeout_seconds':1800},key)
            else:
                await require_stage(actor,run_id,'full_decode',source['artifact_id'])
                params=record['binding']['parameters']
                if step=='recognize':result=await recognition.recognize(actor,run_id,{'source_id':source['id'],
                    'language':params['language'],'chunk_seconds':params['chunk_seconds'],'timeout_seconds':3600},key)
                else:
                    await require_stage(actor,run_id,'source_recognition',source['id'])
                    if step=='align':result=await alignment.align(actor,run_id,{'source_id':source['id'],'timeout_seconds':3600},key)
                    elif step=='reference':
                        await require_stage(actor,run_id,'source_word_timing',source['id'])
                        if not params['reference_url']:return {'status':'skipped','reason':'REFERENCE_NOT_CONFIGURED'}
                        result=await reference.compare(actor,run_id,{'source_id':source['id'],'reference_url':params['reference_url']},key)
                    else:
                        await require_stage(actor,run_id,'source_word_timing',source['id'])
                        return await review(actor,record,run,source)
        return {'status':result['status'],'attempt_id':result['attempt']['id'],'outputs':result['attempt']['outputs']}
    try:return await asyncio.wait_for(execute(),max(.001,record['expires_at']-time.time()))
    except asyncio.TimeoutError:raise BusinessError('来源准备授权到期，当前执行已停止',401,'SOURCE_GRANT_EXPIRED') from None
