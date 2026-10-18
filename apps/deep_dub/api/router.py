"""项目 HTTP 适配；图、节点与工作台完成前暂不注册到运行中的应用目录。"""
from fastapi import APIRouter, Depends, Header

from anp.auth.dependencies import get_actor
from apps.deep_dub.services.projects import ProjectInput, service
from apps.deep_dub.services.versions import AuditInput, QualityProfileInput, SelectionInput, TimelineInput, service as versions

router = APIRouter(prefix='/api/apps/deep_dub', tags=['deep_dub'])


@router.post('/projects', status_code=201)
async def create(body: ProjectInput, actor=Depends(get_actor), idempotency_key: str | None=Header(None)):
    return await service.create(actor, body.model_dump(), idempotency_key)


@router.get('/projects')
async def list_projects(actor=Depends(get_actor)):
    return await service.list(actor)


@router.get('/projects/{project_id}')
async def get_project(project_id: str, actor=Depends(get_actor)):
    return await service.get(actor, project_id)


@router.post('/quality-profiles', status_code=201)
async def quality_profile(body: QualityProfileInput, actor=Depends(get_actor)):
    return await versions.create_quality_profile(actor, body.model_dump())


@router.post('/projects/{project_id}/audits', status_code=201)
async def record_audit(project_id: str, body: AuditInput, actor=Depends(get_actor), idempotency_key: str | None=Header(None)):
    return await versions.record_audit(actor, project_id, body.model_dump(), idempotency_key)


@router.post('/projects/{project_id}/selections', status_code=201)
async def select_version(project_id: str, body: SelectionInput, actor=Depends(get_actor), idempotency_key: str | None=Header(None)):
    return await versions.select_version(actor, project_id, body.model_dump(), idempotency_key)


@router.post('/projects/{project_id}/timeline', status_code=201)
async def commit_timeline(project_id: str, body: TimelineInput, actor=Depends(get_actor), idempotency_key: str | None=Header(None)):
    return await versions.commit_timeline(actor, project_id, body.model_dump(), idempotency_key)
