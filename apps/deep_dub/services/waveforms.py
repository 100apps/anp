"""波形沿用阶段/产物关系，只有成功阶段输出可供读取；读取仍检查原用户权限。"""
import json
from hashlib import sha256
from math import ceil,floor,isfinite
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import UUID

from pydantic import Field

from anp.errors import BusinessError
from apps.deep_dub.nodes.waveform import waveform,RULE,RATE,BIN,MAX_SECONDS
from apps.deep_dub.services.artifacts import service as artifacts
from apps.deep_dub.services.media import service as media
from apps.deep_dub.services.runtime import run_cpu_stage,StageResult
from apps.deep_dub.services.versions import Contract,identifier,operation_key


class WaveformInput(Contract):
    artifact_id:UUID
    device_id:UUID|None=None
    timeout_seconds:int=Field(default=600,ge=1,le=3600,strict=True)


def fingerprint(item):
    return sha256(json.dumps([item['id'],item['sha256'],item['bytes'],RULE],separators=(',',':')).encode()).hexdigest()


def window(data,start=0,end=None,points=640):
    duration=data['duration_seconds'];end=duration if end is None else end
    if not all(isinstance(x,(int,float)) and not isinstance(x,bool) and isfinite(x) for x in (start,end)) or not 0<=start<end<=duration+.01:
        raise BusinessError('波形窗口超出音频范围',422,'WAVEFORM_WINDOW_INVALID')
    if not isinstance(points,int) or isinstance(points,bool) or not 16<=points<=2048:
        raise BusinessError('波形点数应为16至2048',422,'WAVEFORM_WINDOW_INVALID')
    end=min(end,duration);peaks=data['peaks'];left=floor(start*RATE/BIN);right=min(len(peaks),ceil(end*RATE/BIN))
    count=right-left;bins=min(points,count)
    if bins<=0:raise BusinessError('波形窗口没有样本',422,'WAVEFORM_WINDOW_INVALID')
    reduced=[max(peaks[left+i*count//bins:left+(i+1)*count//bins]) for i in range(bins)]
    return {'durationSec':end-start,'peaks':reduced,'startSec':start,'ruleRevision':RULE,'qualityEvaluated':False}


def validate(data,item):
    try:
        peaks=data['peaks'];count=data['sample_count']
        valid=(data['artifact_id']==item['id'] and data['input_sha256']==item['sha256'] and data['input_bytes']==item['bytes']
            and data['rule_revision']==RULE and data['sample_rate']==RATE and data['samples_per_peak']==BIN
            and isinstance(count,int) and not isinstance(count,bool) and 0<count<=MAX_SECONDS*RATE
            and data['duration_seconds']==count/RATE and data['quality_evaluated'] is False
            and data['amplitude_scale']=='absolute-full-scale' and isinstance(peaks,list) and len(peaks)==ceil(count/BIN)
            and all(isinstance(x,(int,float)) and not isinstance(x,bool) and isfinite(x) and 0<=x<=1 for x in peaks))
    except (KeyError,TypeError,ValueError):valid=False
    if not valid:raise BusinessError('波形产物与输入或规则不匹配',409,'WAVEFORM_INVALID')


async def prepare(actor,run_id,payload,request_key=None):
    await actor.require('deep_dub.task.run')
    body=WaveformInput.model_validate(payload);run_id=identifier(run_id)
    item=await media.describe(actor,str(body.artifact_id))
    runs=await actor.objects.select('deep_dub_runs',workspace_id='eq.'+actor.workspace_id,id='eq.'+run_id)
    if not runs or runs[0]['project_id']!=item['project_id']:
        raise BusinessError('运行与音频不属于同一项目',404,'NOT_FOUND')
    async def handler(current,attempt_id):
        with TemporaryDirectory(prefix='anp-waveform-') as folder:
            source=Path(folder)/'input';await artifacts.download(current,item['id'],source)
            result=await waveform(source,body.timeout_seconds);result['artifact_id']=item['id']
            validate(result,item)
            output=Path(folder)/'waveform.json';output.write_bytes(json.dumps(result,separators=(',',':')).encode())
            saved=await artifacts.upload(current,attempt_id,output,'application/json')
            return StageResult(outputs=[saved['id']])
    return await run_cpu_stage(actor,run_id,'waveform',fingerprint(item),handler,partition=item['id'],
        request_key=operation_key(request_key),device_id=str(body.device_id) if body.device_id else None)


async def read(actor,artifact_id,*,start=0,end=None,points=640):
    item=await media.describe(actor,artifact_id)
    stages=await actor.objects.select('deep_dub_stages',workspace_id='eq.'+actor.workspace_id,project_id='eq.'+item['project_id'],
        node='eq.waveform',partition_key='eq.'+item['id'],input_hash='eq.'+fingerprint(item),status='eq.succeeded',order='created_at.desc,id.desc',limit=1)
    if not stages:raise BusinessError('波形尚未生成；需由有执行权限的用户准备',404,'WAVEFORM_NOT_READY')
    attempts=await actor.objects.select('deep_dub_stage_attempts',workspace_id='eq.'+actor.workspace_id,
        stage_id='eq.'+stages[0]['id'],status='eq.succeeded',order='attempt_no.desc',limit=1)
    if not attempts or len(attempts[0]['outputs'])!=1:raise BusinessError('成功阶段缺少唯一波形产物',409,'WAVEFORM_INVALID')
    outputs=await actor.objects.select('deep_dub_artifacts',workspace_id='eq.'+actor.workspace_id,id='eq.'+identifier(attempts[0]['outputs'][0]))
    if (not outputs or outputs[0]['project_id']!=item['project_id'] or outputs[0]['stage_attempt_id']!=attempts[0]['id']
        or outputs[0]['media_type']!='application/json' or outputs[0]['bytes']>16*1024**2):
        raise BusinessError('波形产物归属或大小不合法',409,'WAVEFORM_INVALID')
    with TemporaryDirectory(prefix='anp-waveform-read-') as folder:
        file=Path(folder)/'wave.json';await artifacts.download(actor,outputs[0]['id'],file)
        try:data=json.loads(file.read_bytes())
        except (ValueError,UnicodeError):raise BusinessError('波形产物不是有效JSON',409,'WAVEFORM_INVALID') from None
    validate(data,item)
    return window(data,start,end,points)
