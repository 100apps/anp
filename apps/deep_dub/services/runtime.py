"""本地 CPU 阶段运行器；心跳和取消与已追踪的子进程生命周期绑定。"""
import asyncio
from dataclasses import dataclass,field
from secrets import token_urlsafe
import time

from anp.errors import BusinessError
from apps.deep_dub.services.devices import service as devices
from apps.deep_dub.services.execution import service as execution
from apps.deep_dub.services.local_pool import LocalCpuPool


@dataclass
class StageResult:
    outputs:list[str]=field(default_factory=list)


async def run_cpu_stage(actor,run_id,node,input_hash,handler,*,partition='main',request_key=None,
                        device_id=None,heartbeat_seconds=1,lease_seconds=60):
    if not .05<=heartbeat_seconds<=20 or not 5<=lease_seconds<=300 or heartbeat_seconds>=lease_seconds/2:
        raise BusinessError('租约与心跳周期不合法',422,'INVALID_LEASE_TIMING')
    await actor.require('deep_dub.task.run')
    stage=await execution.begin_stage(actor,run_id,{'node':node,'partition':partition,
        'input_hash':input_hash,'device_kind':'cpu'},request_key)
    attempt=stage['attempt']
    if attempt['status'] in ('succeeded','failed','cancelled','waiting_review'):
        return {'status':attempt['status'],'attempt':attempt}
    if attempt['created_by']!=actor.user['id'] or attempt['status']!='queued':
        return {'status':attempt['status'],'attempt':attempt,'reason':'ATTEMPT_ALREADY_OWNED'}
    with LocalCpuPool(attempt['id']) as pool:
        if device_id is None:
            device=await devices.register_cpu(actor,pool.key,pool.host)
            device=await devices.confirm_probe(actor,device,pool.proof())
        else:
            rows=await actor.objects.select('deep_dub_devices',id='eq.'+device_id,workspace_id='eq.'+actor.workspace_id)
            if not rows or rows[0]['kind']!='cpu' or rows[0]['host_key']!=pool.host or rows[0]['device_key']!=pool.key or rows[0]['capacity']!=1:
                raise BusinessError('设备不是当前主机的单并发媒体CPU池',409,'INVALID_CPU_POOL')
            device=rows[0]
        token=token_urlsafe(48)
        acquired=await devices.acquire(actor,attempt['id'],device['id'],token,pool.host+':'+str(pool.data['owner_pid']),lease_seconds)
        if not acquired['acquired']:
            return {'status':'queued','attempt':attempt,'reason':acquired.get('reason','DEVICE_UNAVAILABLE')}
        lease=acquired['lease'];started=time.monotonic();operation=None;watcher=None
        async def monitor():
            while True:
                await asyncio.sleep(heartbeat_seconds)
                await devices.heartbeat(actor,lease,token,lease_seconds)
        try:
            await execution.mark_stage(actor,attempt['id'],'started')
            operation=asyncio.create_task(handler(actor,attempt['id']))
            watcher=asyncio.create_task(monitor())
            done,_=await asyncio.wait([operation,watcher],return_when=asyncio.FIRST_COMPLETED)
            if watcher in done:
                await watcher
                raise BusinessError('设备监控意外结束',409,'LEASE_LOST')
            result=await operation
            if not isinstance(result,StageResult):raise TypeError('阶段必须返回 StageResult')
            await devices.heartbeat(actor,lease,token,lease_seconds)
            if not pool.stopped():raise BusinessError('本阶段仍有未结束的子进程',409,'OPERATION_NOT_STOPPED')
            receipt=await execution.finish_stage(actor,attempt['id'],{'status':'succeeded','outputs':result.outputs,
                'elapsed_seconds':time.monotonic()-started,'lease_id':lease['id'],'fence':lease['fence'],
                'token':token,'operation_stopped':True})
            if not receipt['committed']:raise BusinessError('阶段结果未获准提交',409,'STAGE_COMMIT_REJECTED')
            return {'status':'succeeded','attempt':receipt['attempt']}
        except BaseException as error:
            if operation is not None and not operation.done():
                operation.cancel()
                await asyncio.gather(operation,return_exceptions=True)
            stopped=pool.stopped()
            code=error.code if isinstance(error,BusinessError) else ('WORKER_CANCELLED' if isinstance(error,asyncio.CancelledError) else 'WORKER_FAILED')
            try:
                await execution.finish_stage(actor,attempt['id'],{'status':'failed','outputs':[],
                    'elapsed_seconds':time.monotonic()-started,'error':{'code':code},'lease_id':lease['id'],
                    'fence':lease['fence'],'token':token,'operation_stopped':stopped})
            except Exception:
                # 取消/撤权后不覆盖数据库已冻结状态；实际停止后才尝试释放。
                if stopped:
                    try:await devices.release(actor,lease,token)
                    except Exception:pass
            raise
        finally:
            if watcher is not None:
                watcher.cancel();await asyncio.gather(watcher,return_exceptions=True)
