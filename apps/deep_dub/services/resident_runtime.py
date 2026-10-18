"""常驻GPU服务槽位：真实租约到本作业响应确认；不结束远端模型进程。"""
import asyncio
import base64
from hashlib import sha256
import json
from pathlib import Path
from secrets import token_urlsafe
import time

from anp import STATE_ROOT
from anp.errors import BusinessError
from apps.deep_dub.nodes.resident_job import RULE
from apps.deep_dub.nodes.tts import validate_audio
from apps.deep_dub.services.atomic_journal import write_json
from apps.deep_dub.services.blocking import await_blocking,file_identity
from apps.deep_dub.services.devices import service as devices
from apps.deep_dub.services.execution import service as execution
from apps.deep_dub.services.model_pool import FileLock
from apps.deep_dub.services.resident_transport import Transport
from apps.deep_dub.services.runtime import StageResult


class ResidentOperation:
    def __init__(self,config,attempt_id):
        self.config=config;self.transport=Transport(config);self.attempt_id=attempt_id;self.stopped=True
        self.path=STATE_ROOT/'deep_dub/resident-pool'/(sha256((config.host+':'+str(config.port)).encode()).hexdigest()[:24]+'.json')
        self.data={'state':'working','attempt_id':attempt_id,'remote_job_id':attempt_id,'operation_stopped':True,
            'resident_service_stopped':False,'scope':'本业务常驻服务槽位，非整张GPU独占'}

    def save(self):write_json(self.path,self.data)

    def execute(self,folder,text,voice,emotion,factor,timeout,seed):
        c=self.config;remote=c.root+'/jobs/'+self.attempt_id;files={}
        for name,path in [('voice.wav',voice),('emotion.wav',emotion)]:
            raw=Path(path).read_bytes()
            if not 44<len(raw)<=8*1024**2:raise BusinessError('远端参考超限',422,'RESIDENT_INPUT_INVALID')
            files[name]=raw
        request={'rule_revision':RULE,'job_id':self.attempt_id,'service_host':c.host,'port':c.port,
            'service_pid':c.service_identity['pid'],'service_identity':c.service_identity,'model_id':c.model_id,
            'timeout_seconds':timeout,'input_files':{k:sha256(v).hexdigest() for k,v in files.items()},
            'request':{'text':text,'language':'zh','voice_ref':remote+'/voice.wav','audio':remote+'/emotion.wav',
                'seed':seed,'max_tokens':1500,'do_sample':False,'num_beams':3,
                'options':{'duration_factor':1/factor,'emotion_alpha':1}}}
        started=time.monotonic();self.stopped=False
        self.data.update(operation_stopped=False,request_sha256=sha256(json.dumps(request,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()).hexdigest())
        self.save()
        # 只发一次launch；传输失败也只查询既定ID，不能重新启动未知作业。
        try:self.transport.call('launch',job_id=self.attempt_id,request=request,files={k:base64.b64encode(v).decode() for k,v in files.items()})
        except Exception:self.data['launch_acknowledged']=False;self.save()
        deadline=time.monotonic()+timeout+180;receipt=None
        while time.monotonic()<deadline:
            try:
                receipt=self.transport.call('read',job_id=self.attempt_id)
                if receipt.get('input_sha256') not in (None,self.data['request_sha256']):
                    raise BusinessError('远端回执不是本次冻结输入',409,'RESIDENT_RECEIPT_MISMATCH')
                if not receipt.get('worker_running') and receipt.get('operation_stopped'):
                    if (receipt.get('input_sha256')!=self.data['request_sha256'] or receipt.get('job_id')!=self.attempt_id
                        or receipt.get('status') not in ('succeeded','failed') or receipt.get('service_identity')!=c.service_identity
                        or receipt.get('worker_sha256')!=self.transport.source_sha256
                        or receipt.get('resident_service_stopped') is not False):
                        raise BusinessError('远端停止回执缺少完整本次身份',409,'RESIDENT_RECEIPT_MISMATCH')
                    self.stopped=True;break
                if not receipt.get('worker_running') and receipt.get('status')=='uncertain':break
            except BusinessError as error:
                if error.code!='RESIDENT_TRANSPORT_FAILED':raise
            time.sleep(1)
        self.data.update(operation_stopped=self.stopped,remote_receipt=receipt);self.save()
        if not self.stopped:raise BusinessError('远端运算终态未确认，保留隔离，不停止常驻服务',409,'RESIDENT_OPERATION_UNKNOWN')
        if receipt['status']!='succeeded':raise BusinessError('远端本次作业失败',502,'RESIDENT_JOB_FAILED')
        result=self.transport.call('output',job_id=self.attempt_id)
        if any(result.get(k)!=receipt.get(k) for k in ('input_sha256','output_sha256','output_bytes','worker_pid')):
            raise BusinessError('远端产物回执变化',409,'RESIDENT_RECEIPT_MISMATCH')
        raw=base64.b64decode(result.pop('audio'),validate=True);stats=validate_audio(raw,result['sample_rate'],1)
        if len(raw)!=result['output_bytes'] or sha256(raw).hexdigest()!=result['output_sha256']:
            raise BusinessError('远端音频摘要不符',409,'RESIDENT_OUTPUT_CHANGED')
        output=Path(folder)/'generated.wav'
        with output.open('xb') as file:file.write(raw)
        proof={'rule_revision':RULE,'parameters':{'model_id':c.model_id,'model_family':'index_tts2','model_variant':'2.5',
            'weights_sha256':c.weights_sha256,'runtime_sha256':c.executable_sha256,'source_revision':'unverified-preexisting-resident-runtime',
            'rule_revision':RULE,'backend':'cuda-resident-service','generation_factor':factor,'duration_factor':1/factor,
            'do_sample':False,'num_beams':3,'max_tokens':1500,'seed':seed,'emotion_alpha':1,'quality_scope':'候选生成，非独审/音色身份/自然度通过'},
            'reference_identities':[file_identity(voice),file_identity(emotion)],'audio':stats,
            'operation_stopped':True,'resident_service_stopped':False,'resident_job':receipt,
            'job_wall_seconds':time.monotonic()-started,'semantic_consistency':'not_measured','speaker_identity':'not_measured','naturalness':'not_measured'}
        return output,proof


async def run_resident_stage(actor,run_id,input_hash,handler,config,*,partition,request_key=None):
    await actor.require('deep_dub.task.run');await actor.require('deep_dub.audit');await actor.require('deep_dub.device.manage')
    selected=await execution.begin_stage(actor,run_id,{'node':'generate_audio','partition':partition,'input_hash':input_hash,'device_kind':'gpu'},request_key)
    attempt=selected['attempt']
    if attempt['status']!='queued' or attempt['created_by']!=actor.user['id']:return {'status':attempt['status'],'attempt':attempt}
    operation=ResidentOperation(config,attempt['id']);operation.path.parent.mkdir(parents=True,exist_ok=True)
    with FileLock(operation.path.with_suffix('.lock')):
        if operation.path.exists():
            old=json.loads(operation.path.read_bytes())
            if old.get('state')!='idle' or old.get('operation_stopped') is not True:
                raise BusinessError('旧远端作业未确认收尾，需核对原回执',409,'RESIDENT_POOL_UNKNOWN')
        started=time.monotonic();probe=await operation.transport.request('inspect')
        if not probe['all_owned_operations_stopped']:raise BusinessError('远端仍有本工作区未完成作业',409,'RESIDENT_POOL_BUSY')
        proof={'device_scope':'resident-model-service-slot','all_owned_operations_stopped':True,
            'has_external_processes':True,'resident_service_preserved':True,'physical_gpu_exclusive':False,
            'host_boot_id':config.service_identity['boot_id'],'service_identity':probe['service_identity'],'port':config.port}
        device=await actor.objects.rpc('deep_dub_register_device',p_workspace=actor.workspace_id,
            p_key='anp-resident-tts:'+config.host+':'+str(config.port),p_host=config.host,p_kind='gpu',p_capacity=1)
        device=await actor.objects.rpc('deep_dub_confirm_resident_probe',p_workspace=actor.workspace_id,
            p_device=device['id'],p_expected_version=device['row_version'],p_probe=proof)
        token=token_urlsafe(48);claimed=await devices.acquire(actor,attempt['id'],device['id'],token,config.host+':resident:'+attempt['id'])
        if not claimed['acquired']:return {'status':'queued','attempt':attempt,'reason':claimed.get('reason')}
        lease=claimed['lease'];operation.save();work=None;watcher=None
        async def monitor():
            while True:
                await asyncio.sleep(1);await devices.heartbeat(actor,lease,token)
        try:
            await execution.mark_stage(actor,attempt['id'],'started')
            work=asyncio.create_task(handler(actor,attempt['id'],operation));watcher=asyncio.create_task(monitor())
            done,_=await asyncio.wait([work,watcher],return_when=asyncio.FIRST_COMPLETED)
            if watcher in done:await watcher;raise BusinessError('远端租约监控结束',409,'LEASE_LOST')
            result=await work
            if not isinstance(result,StageResult) or not operation.stopped:raise BusinessError('远端阶段终态不完整',409,'RESIDENT_OPERATION_UNKNOWN')
            await devices.heartbeat(actor,lease,token)
            receipt=await execution.finish_stage(actor,attempt['id'],{'status':'succeeded','outputs':result.outputs,
                'elapsed_seconds':time.monotonic()-started,'lease_id':lease['id'],'fence':lease['fence'],'token':token,'operation_stopped':True})
            if not receipt['committed']:raise BusinessError('远端产物未获准提交',409,'STAGE_COMMIT_REJECTED')
            return {'status':'succeeded','attempt':receipt['attempt']}
        except BaseException as error:
            if work is not None and not work.done():work.cancel();await asyncio.gather(work,return_exceptions=True)
            try:
                await execution.finish_stage(actor,attempt['id'],{'status':'failed','outputs':[],
                    'elapsed_seconds':time.monotonic()-started,'error':{'code':getattr(error,'code','REMOTE_WORKER_FAILED')},
                    'lease_id':lease['id'],'fence':lease['fence'],'token':token,'operation_stopped':operation.stopped})
            except Exception:
                if operation.stopped:
                    try:await devices.release(actor,lease,token)
                    except Exception:pass
            raise
        finally:
            if watcher is not None:watcher.cancel();await asyncio.gather(watcher,return_exceptions=True)
            operation.data.update(state='idle' if operation.stopped else 'uncertain',operation_stopped=operation.stopped);operation.save()
