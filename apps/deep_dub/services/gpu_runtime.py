"""本机GPU阶段：持有跨进程锁和租约到模型进程真实退出；不操作远端或外部模型。"""
import asyncio
from secrets import token_urlsafe
import time

from anp.errors import BusinessError
from apps.deep_dub.services.blocking import await_blocking
from apps.deep_dub.services.devices import service as devices
from apps.deep_dub.services.execution import service as execution
from apps.deep_dub.services.model_pool import LocalModelPool
from apps.deep_dub.services.runtime import StageResult


async def run_gpu_stage(actor,run_id,input_hash,handler,config,*,partition,request_key=None):
    await actor.require('deep_dub.task.run')
    stage=await execution.begin_stage(actor,run_id,{'node':'generate_audio','partition':partition,
        'input_hash':input_hash,'device_kind':'gpu'},request_key)
    attempt=stage['attempt']
    if attempt['status']!='queued' or attempt['created_by']!=actor.user['id']:
        return {'status':attempt['status'],'attempt':attempt}
    # 摘要核对在作业计时和领取后执行，不能当作整片之外的免费准备。
    started=time.monotonic()
    with LocalModelPool(attempt['id'],config) as pool:
        await actor.require('deep_dub.device.manage')
        device=await actor.objects.rpc('deep_dub_register_device',p_workspace=actor.workspace_id,
            p_key=pool.key,p_host=pool.host,p_kind='gpu',p_capacity=1)
        device=await devices.confirm_probe(actor,device,pool.proof())
        token=token_urlsafe(48)
        acquired=await devices.acquire(actor,attempt['id'],device['id'],token,pool.host+':'+str(pool.data['owner_pid']),60)
        if not acquired['acquired']:return {'status':'queued','attempt':attempt,'reason':acquired.get('reason','DEVICE_UNAVAILABLE')}
        lease=acquired['lease'];operation=None;watcher=None
        async def run():
            await await_blocking(config.verify_files)
            return await handler(actor,attempt['id'],pool)
        async def monitor():
            while True:
                await asyncio.sleep(1)
                await devices.heartbeat(actor,lease,token,60)
        try:
            operation=asyncio.create_task(run());watcher=asyncio.create_task(monitor())
            done,_=await asyncio.wait([operation,watcher],return_when=asyncio.FIRST_COMPLETED)
            if watcher in done:
                await watcher
                raise BusinessError('GPU租约监控意外结束',409,'LEASE_LOST')
            result=await operation
            if not isinstance(result,StageResult):raise TypeError('阶段必须返回StageResult')
            await devices.heartbeat(actor,lease,token,60)
            if not pool.stopped():raise BusinessError('模型进程尚未确认退出',409,'OPERATION_NOT_STOPPED')
            receipt=await execution.finish_stage(actor,attempt['id'],{'status':'succeeded','outputs':result.outputs,
                'elapsed_seconds':time.monotonic()-started,'lease_id':lease['id'],'fence':lease['fence'],
                'token':token,'operation_stopped':True})
            if not receipt['committed']:raise BusinessError('GPU结果未获准提交',409,'STAGE_COMMIT_REJECTED')
            return {'status':'succeeded','attempt':receipt['attempt']}
        except BaseException as error:
            if operation is not None and not operation.done():
                operation.cancel();await asyncio.gather(operation,return_exceptions=True)
            stopped=pool.stopped()
            code=error.code if isinstance(error,BusinessError) else ('WORKER_CANCELLED' if isinstance(error,asyncio.CancelledError) else 'WORKER_FAILED')
            try:
                await execution.finish_stage(actor,attempt['id'],{'status':'failed','outputs':[],
                    'elapsed_seconds':time.monotonic()-started,'error':{'code':code},'lease_id':lease['id'],
                    'fence':lease['fence'],'token':token,'operation_stopped':stopped})
            except Exception:
                if stopped:
                    try:await devices.release(actor,lease,token)
                    except Exception:pass
            raise
        finally:
            if watcher is not None:watcher.cancel();await asyncio.gather(watcher,return_exceptions=True)
