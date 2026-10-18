"""实际媒体阶段；输入按权限下载，执行/文件/证据均纳入阶段墙钟。"""
import json
from hashlib import sha256
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import UUID

from pydantic import Field

from anp.errors import BusinessError
from apps.deep_dub.nodes.media import full_decode
from apps.deep_dub.services.artifacts import service as artifacts
from apps.deep_dub.services.execution import service as execution
from apps.deep_dub.services.runtime import run_cpu_stage,StageResult
from apps.deep_dub.services.versions import Contract,identifier,operation_key


class DecodeInput(Contract):
    artifact_id: UUID
    device_id: UUID|None=None
    timeout_seconds: int=Field(default=600,ge=1,le=3600,strict=True)


async def describe_decode(actor,run_id,payload):
    await actor.require('deep_dub.task.run')
    await actor.require('deep_dub.audit')
    body=DecodeInput.model_validate(payload);run_id=identifier(run_id)
    runs=await actor.objects.select('deep_dub_runs',id='eq.'+run_id,workspace_id='eq.'+actor.workspace_id)
    rows=await actor.objects.select('deep_dub_artifacts',id='eq.'+str(body.artifact_id),workspace_id='eq.'+actor.workspace_id)
    if not runs or not rows or runs[0]['project_id']!=rows[0]['project_id']:
        raise BusinessError('运行与媒体产物不存在或不属于同一项目',404,'NOT_FOUND')
    item=rows[0]
    fingerprint=sha256(json.dumps({'artifact_id':item['id'],'sha256':item['sha256'],'bytes':item['bytes'],
        'rule_revision':'ffmpeg-all-av-v1','timeout_seconds':body.timeout_seconds},sort_keys=True).encode()).hexdigest()
    return body,item,fingerprint


async def decode_artifact(actor,run_id,payload,request_key=None):
    run_id=identifier(run_id)
    body,item,fingerprint=await describe_decode(actor,run_id,payload)
    async def handler(current,attempt_id):
        with TemporaryDirectory(prefix='anp-decode-') as directory:
            file=Path(directory)/'input-media'
            await artifacts.download(current,item['id'],file)
            proof=await full_decode(file,body.timeout_seconds)
            if proof['sha256']!=item['sha256'] or proof['bytes']!=item['bytes']:
                raise BusinessError('解码输入已改变',409,'ARTIFACT_INTEGRITY_FAILED')
            proof['artifact_id']=item['id']
            evidence=Path(directory)/'decode.json'
            evidence.write_bytes((json.dumps(proof,ensure_ascii=False,indent=2)+'\n').encode())
            saved=await artifacts.upload(current,attempt_id,evidence,'application/json')
            return StageResult(outputs=[saved['id']])
    result=await run_cpu_stage(actor,run_id,'full_decode',fingerprint,handler,partition=item['id'],
        request_key=operation_key(request_key),device_id=str(body.device_id) if body.device_id else None)
    if result['status']=='succeeded':
        attempt=result['attempt']
        if len(attempt['outputs'])!=1:
            raise BusinessError('解码阶段缺少唯一证据产物',409,'INVALID_DECODE_EVIDENCE')
        with TemporaryDirectory(prefix='anp-decode-evidence-') as directory:
            path=Path(directory)/'proof.json'
            await artifacts.download(actor,attempt['outputs'][0],path)
            try:proof=json.loads(path.read_text(encoding='utf8'))
            except (ValueError,UnicodeError):
                raise BusinessError('解码证据不是有效JSON',409,'INVALID_DECODE_EVIDENCE') from None
        if (not isinstance(proof,dict) or proof.get('artifact_id')!=item['id']
            or proof.get('sha256')!=item['sha256'] or proof.get('bytes')!=item['bytes']
            or proof.get('rule_key')!='full_decode' or proof.get('method')!='technical'
            or proof.get('measured') is not True or proof.get('decoder_exit_code')!=0):
            raise BusinessError('解码证据与当前输入或规则不匹配',409,'INVALID_DECODE_EVIDENCE')
        await execution.record_score(actor,attempt['id'],{'metric':'full_decode','rule_revision':'ffmpeg-all-av-v1',
            'measurement_status':'measured','value':1,'evidence_artifact_id':attempt['outputs'][0],
            'details':{'artifact_id':item['id'],'sha256':item['sha256'],'scope':'技术解码，不代表语义/身份/自然度'}},
            'full-decode:'+attempt['id'])
    return result
