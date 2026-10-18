"""项目 HTTP 适配；图、节点与工作台完成前暂不注册到运行中的应用目录。"""
from fastapi import APIRouter, Depends, Header

from anp.auth.dependencies import get_actor
from apps.deep_dub.services.projects import ProjectInput, service

router = APIRouter(prefix='/api/apps/deep_dub/projects', tags=['deep_dub'])


@router.post('', status_code=201)
async def create(body: ProjectInput, actor=Depends(get_actor), idempotency_key: str | None=Header(None)):
    return await service.create(actor, body.model_dump(), idempotency_key)


@router.get('')
async def list_projects(actor=Depends(get_actor)):
    return await service.list(actor)


@router.get('/{project_id}')
async def get_project(project_id: str, actor=Depends(get_actor)):
    return await service.get(actor, project_id)
