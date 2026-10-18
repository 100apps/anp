"""专用 Worker 接口；不接受服务角色、通用 Worker 密钥或客户端原用户JWT。"""
from fastapi import APIRouter,Header
from pydantic import BaseModel,ConfigDict
from uuid import UUID

from anp.errors import BusinessError
from apps.deep_dub.services.delegation import dispatch_decode_job

router=APIRouter(prefix='/internal/deep_dub/jobs',tags=['deep_dub_worker'])


class DecodeJobRequest(BaseModel):
    model_config=ConfigDict(extra='forbid')
    run_id:UUID


@router.post('/{attempt_id}/decode')
async def execute(attempt_id:UUID,body:DecodeJobRequest,authorization:str|None=Header(None)):
    if not authorization or not authorization.startswith('Bearer '):
        raise BusinessError('缺少作业授权',401,'INVALID_JOB_GRANT')
    return await dispatch_decode_job(authorization.removeprefix('Bearer '),str(body.run_id),str(attempt_id))
