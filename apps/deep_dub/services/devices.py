"""设备 API 适配；不通过显存或负载猜测设备空闲，不缓存授权。"""
from anp.errors import BusinessError
from apps.deep_dub.services.versions import identifier


class DeviceService:
    async def register_cpu(self,actor,key,host):
        await actor.require('deep_dub.device.manage')
        return await actor.objects.rpc('deep_dub_register_device',p_workspace=actor.workspace_id,
            p_key=key,p_host=host,p_kind='cpu',p_capacity=1)

    async def confirm_probe(self,actor,device,proof):
        await actor.require('deep_dub.device.manage')
        return await actor.objects.rpc('deep_dub_confirm_device_probe',p_workspace=actor.workspace_id,
            p_device=device['id'],p_expected_version=device['row_version'],p_probe=proof)

    async def acquire(self,actor,attempt_id,device_id,token,owner,seconds=60):
        await actor.require('deep_dub.task.run')
        return await actor.objects.rpc('deep_dub_acquire_lease',p_workspace=actor.workspace_id,
            p_attempt=identifier(attempt_id),p_device=identifier(device_id),p_owner=owner,p_token=token,
            p_slots=1,p_seconds=seconds,p_request_key='local-runtime:'+identifier(attempt_id))

    async def heartbeat(self,actor,lease,token,seconds=60):
        await actor.require('deep_dub.task.run')
        result=await actor.objects.rpc('deep_dub_heartbeat_lease',p_workspace=actor.workspace_id,
            p_lease=lease['id'],p_fence=lease['fence'],p_token=token,p_seconds=seconds)
        if not result['renewed']:raise BusinessError('设备租约已失效',409,'LEASE_LOST')
        return result

    async def release(self,actor,lease,token):
        await actor.require('deep_dub.task.run')
        return await actor.objects.rpc('deep_dub_release_lease',p_workspace=actor.workspace_id,
            p_lease=lease['id'],p_fence=lease['fence'],p_token=token,p_operation_stopped=True)


service=DeviceService()
