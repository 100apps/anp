"""管理员读取既定远端终态并收尾失联槽位；不重新提交、不停止模型、不补写成功。"""
import json
from anp.errors import BusinessError
from apps.deep_dub.services.execution import service as execution
from apps.deep_dub.services.model_pool import FileLock
from apps.deep_dub.services.resident_runtime import ResidentOperation
from apps.deep_dub.services.resident_transport import configuration
from apps.deep_dub.services.versions import identifier


async def recover(actor,attempt_id):
    await actor.require('deep_dub.device.manage');await actor.require('deep_dub.task.run')
    attempt_id=identifier(attempt_id)
    async def row(table,id):
        rows=await actor.objects.select(table,workspace_id='eq.'+actor.workspace_id,id='eq.'+id)
        if not rows:raise BusinessError('执行或服务不存在',404,'NOT_FOUND')
        return rows[0]
    attempt=await row('deep_dub_stage_attempts',attempt_id);stage=await row('deep_dub_stages',attempt['stage_id'])
    if stage['node']!='generate_audio':raise BusinessError('不是常驻配音阶段',409,'RESIDENT_RECOVERY_INVALID')
    operation=ResidentOperation(configuration(),attempt_id)
    with FileLock(operation.path.with_suffix('.lock')):
        if not operation.path.exists():raise BusinessError('缺少本工作区持久作业记录',409,'RESIDENT_RECOVERY_INVALID')
        journal=json.loads(operation.path.read_bytes())
        if (journal.get('attempt_id')!=attempt_id or journal.get('remote_job_id')!=attempt_id
            or not journal.get('request_sha256')):
            raise BusinessError('持久记录与本次尝试不符',409,'RESIDENT_RECOVERY_INVALID')
        receipt=await operation.transport.request('read',job_id=attempt_id)
        if (receipt.get('job_id')!=attempt_id or receipt.get('input_sha256')!=journal['request_sha256']
            or receipt.get('worker_sha256')!=operation.transport.source_sha256
            or receipt.get('operation_stopped') is not True or receipt.get('worker_running') is not False
            or receipt.get('resident_service_stopped') is not False or receipt.get('status') not in ('succeeded','failed')
            or receipt.get('service_identity')!=operation.config.service_identity):
            raise BusinessError('远端本次运算停止未获完整证明，保留隔离',409,'RESIDENT_OPERATION_UNKNOWN')
        probe=await operation.transport.request('inspect')
        if not probe.get('all_owned_operations_stopped'):
            raise BusinessError('远端仍有本工作区未完成作业',409,'RESIDENT_POOL_BUSY')
        leases=await actor.objects.select('deep_dub_device_leases',select='id,device_id,status,expires_at,operation_stopped',
            workspace_id='eq.'+actor.workspace_id,stage_attempt_id='eq.'+attempt_id)
        if not leases or len({x['device_id'] for x in leases})!=1:
            raise BusinessError('缺少唯一服务设备租约',409,'RESIDENT_RECOVERY_INVALID')
        device=await row('deep_dub_devices',leases[0]['device_id']);c=operation.config
        if device['device_key']!='anp-resident-tts:'+c.host+':'+str(c.port):
            raise BusinessError('槽位与当前配置不符',409,'RESIDENT_RECOVERY_INVALID')
        proof={'device_scope':'resident-model-service-slot','all_owned_operations_stopped':True,
            'has_external_processes':True,'resident_service_preserved':True,'physical_gpu_exclusive':False,
            'host_boot_id':c.service_identity['boot_id'],'service_identity':probe['service_identity'],'port':c.port,
            'remote_receipt':receipt}
        lease_ids=[x['id'] for x in leases]
        await actor.objects.rpc('deep_dub_reconcile_resident',p_workspace=actor.workspace_id,p_device=device['id'],
            p_expected_version=device['row_version'],p_attempt=attempt_id,p_lease_ids=lease_ids,p_probe=proof)
        # 取消保持取消；owner失联只记失败，不用已有WAV冒充正常成功提交。
        current=await row('deep_dub_stage_attempts',attempt_id)
        if current['status'] in ('queued','running') or (current.get('error') or {}).get('code')=='WORKER_LOST':
            current=await execution.close_lost_attempt(actor,attempt_id,lease_ids)
        journal.update(state='idle',operation_stopped=True,remote_receipt=receipt,
            recovery={'method':'administrator-reconciled','attempt_status':current['status']})
        operation.data=journal;operation.save()
        return {'status':current['status'],'attempt':current,'lease_ids':lease_ids,
            'operation_stopped':True,'resident_service_stopped':False,'recovery':'administrator-reconciled'}
