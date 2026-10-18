"""来源准备引擎入口；服务凭据之外还必须有原用户的本轮限时授权。"""
import secrets
from uuid import UUID
from fastapi import APIRouter,Header
from pydantic import BaseModel,ConfigDict
from anp.config import secret
from anp.errors import BusinessError
from apps.deep_dub.services.source_jobs import dispatch

router=APIRouter(prefix='/internal/deep_dub/source-preparation')


class SourceJobRequest(BaseModel):
    model_config=ConfigDict(extra='forbid')
    run_id:UUID


@router.post('/{procedure_id}/{step}')
async def execute(procedure_id:UUID,step:str,body:SourceJobRequest,authorization:str|None=Header(None)):
    if not secrets.compare_digest(authorization or '', 'Bearer '+secret('WORKER_TOKEN')):
        raise BusinessError('来源准备引擎凭据不正确',401,'INVALID_ENGINE_CREDENTIAL')
    return await dispatch(str(body.run_id),str(procedure_id),step)
