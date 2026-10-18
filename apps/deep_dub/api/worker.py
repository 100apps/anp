"""单次能力令牌入口与引擎入口；均需原用户授权，不接受服务角色或用户JWT替代。"""
from fastapi import APIRouter,Header
from pydantic import BaseModel,ConfigDict
from uuid import UUID
import secrets

from anp.errors import BusinessError
from apps.deep_dub.services.delegation import dispatch_decode_job,vault
from anp.config import secret

router=APIRouter(tags=['deep_dub_worker'])


class DecodeJobRequest(BaseModel):
    model_config=ConfigDict(extra='forbid')
    run_id:UUID


@router.post('/internal/deep_dub/jobs/{attempt_id}/decode')
async def execute(attempt_id:UUID,body:DecodeJobRequest,authorization:str|None=Header(None)):
    if not authorization or not authorization.startswith('Bearer '):
        raise BusinessError('缺少作业授权',401,'INVALID_JOB_GRANT')
    return await dispatch_decode_job(authorization.removeprefix('Bearer '),str(body.run_id),str(attempt_id))


engine_router=APIRouter(prefix='/internal/deep_dub/engine-jobs',tags=['deep_dub_worker'])


@engine_router.post('/{attempt_id}/decode')
async def engine_execute(attempt_id:UUID,body:DecodeJobRequest,authorization:str|None=Header(None)):
    if not secrets.compare_digest(authorization or '', 'Bearer '+secret('WORKER_TOKEN')):
        raise BusinessError('引擎服务凭据不正确',401,'INVALID_ENGINE_CREDENTIAL')
    token=vault.engine_token(str(body.run_id),str(attempt_id))
    return await dispatch_decode_job(token,str(body.run_id),str(attempt_id))


router.include_router(engine_router)
