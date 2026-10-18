"""项目 HTTP 适配；图、节点与工作台完成前暂不注册到运行中的应用目录。"""
from fastapi import APIRouter, Depends, Header, Request, Query
from tempfile import TemporaryDirectory
from pathlib import Path
from pydantic import ValidationError

from anp.auth.dependencies import get_actor
from apps.deep_dub.services.projects import ProjectInput, service
from apps.deep_dub.services.versions import AuditInput, QualityProfileInput, SelectionInput, TimelineInput, service as versions
from apps.deep_dub.services.execution import RunInput, StageInput, StageReceipt, ScoreInput, service as execution
from typing import Literal
from anp.errors import BusinessError
from apps.deep_dub.services.artifacts import service as artifacts, ArtifactMetadata
from apps.deep_dub.services.observations import deliver_one
from apps.deep_dub.services.processing import DecodeInput,decode_artifact
from apps.deep_dub.services.candidates import SourceInput,TranscriptInput,TranslationInput,ReferenceInput,AudioInput,BackgroundInput,RenderInput,service as candidates

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


@router.post('/projects/{project_id}/runs', status_code=201)
async def queue_run(project_id: str, body: RunInput, actor=Depends(get_actor), idempotency_key: str | None=Header(None)):
    return await execution.queue(actor, project_id, body.model_dump(), idempotency_key)


@router.get('/projects/{project_id}/runs')
async def list_runs(project_id: str, actor=Depends(get_actor)):
    return await execution.list(actor, project_id)


@router.post('/runs/{run_id}/start')
async def start_run(run_id: str, actor=Depends(get_actor)):
    return await execution.start(actor, run_id)


@router.post('/runs/{run_id}/cancel')
async def cancel_run(run_id: str, actor=Depends(get_actor)):
    return await execution.cancel(actor, run_id)


@router.post('/runs/{run_id}/stages', status_code=201)
async def begin_stage(run_id: str, body: StageInput, actor=Depends(get_actor), idempotency_key: str | None=Header(None)):
    return await execution.begin_stage(actor, run_id, body.model_dump(), idempotency_key)


@router.post('/attempts/{attempt_id}/mark/{phase}')
async def mark_stage(attempt_id: str, phase: Literal['started', 'loaded'], actor=Depends(get_actor)):
    return await execution.mark_stage(actor, attempt_id, phase)


@router.post('/attempts/{attempt_id}/receipt')
async def finish_stage(attempt_id: str, body: StageReceipt, actor=Depends(get_actor)):
    return await execution.finish_stage(actor, attempt_id, body.model_dump())


@router.post('/attempts/{attempt_id}/scores', status_code=201)
async def record_score(attempt_id: str, body: ScoreInput, actor=Depends(get_actor), idempotency_key: str | None=Header(None)):
    return await execution.record_score(actor, attempt_id, body.model_dump(), idempotency_key)


@router.post('/attempts/{attempt_id}/artifacts', status_code=201)
async def upload_artifact(attempt_id: str, request: Request, actor=Depends(get_actor),
        duration_ms: int | None=Query(None,ge=0), sample_rate: int | None=Query(None,gt=0)):
    await artifacts.authorize_upload(actor,attempt_id)
    try:
        meta=ArtifactMetadata(media_type=request.headers.get('content-type','application/octet-stream').split(';',1)[0].strip(),
                              duration_ms=duration_ms,sample_rate=sample_rate)
    except ValidationError:
        raise BusinessError('产物元数据不合法',422,'VALIDATION_ERROR') from None
    # 不接受客户端宿主文件路径。暂存只活到本次请求结束，长期文件落在 RustFS 命名卷。
    with TemporaryDirectory(prefix='anp-deep-dub-upload-') as folder:
        file=Path(folder)/'upload';size=0
        with file.open('wb') as target:
            async for block in request.stream():
                size+=len(block)
                if size>16*1024**3:
                    raise BusinessError('单个产物超过 16 GiB',413,'ARTIFACT_TOO_LARGE')
                target.write(block)
        return await artifacts.upload(actor,attempt_id,file,meta.media_type,meta.duration_ms,meta.sample_rate)


@router.post('/observations/flush')
async def flush_observation(actor=Depends(get_actor)):
    return await deliver_one(actor)


@router.post('/attempts/{attempt_id}/source',status_code=201)
async def register_source(attempt_id:str,body:SourceInput,actor=Depends(get_actor),idempotency_key:str|None=Header(None)):
    return await candidates.source(actor,attempt_id,body.model_dump(),idempotency_key)


@router.post('/attempts/{attempt_id}/transcript',status_code=201)
async def register_transcript(attempt_id:str,body:TranscriptInput,actor=Depends(get_actor),idempotency_key:str|None=Header(None)):
    return await candidates.transcript(actor,attempt_id,body.model_dump(),idempotency_key)


@router.post('/attempts/{attempt_id}/translation',status_code=201)
async def register_translation(attempt_id:str,body:TranslationInput,actor=Depends(get_actor),idempotency_key:str|None=Header(None)):
    return await candidates.translation(actor,attempt_id,body.model_dump(),idempotency_key)


@router.post('/attempts/{attempt_id}/reference',status_code=201)
async def register_reference(attempt_id:str,body:ReferenceInput,actor=Depends(get_actor),idempotency_key:str|None=Header(None)):
    return await candidates.reference(actor,attempt_id,body.model_dump(),idempotency_key)


@router.post('/attempts/{attempt_id}/audio',status_code=201)
async def register_audio(attempt_id:str,body:AudioInput,actor=Depends(get_actor),idempotency_key:str|None=Header(None)):
    return await candidates.audio(actor,attempt_id,body.model_dump(),idempotency_key)


@router.post('/attempts/{attempt_id}/background',status_code=201)
async def register_background(attempt_id:str,body:BackgroundInput,actor=Depends(get_actor),idempotency_key:str|None=Header(None)):
    return await candidates.background(actor,attempt_id,body.model_dump(),idempotency_key)


@router.post('/attempts/{attempt_id}/render',status_code=201)
async def register_render(attempt_id:str,body:RenderInput,actor=Depends(get_actor),idempotency_key:str|None=Header(None)):
    return await candidates.render(actor,attempt_id,body.model_dump(),idempotency_key)


@router.post('/runs/{run_id}/decode')
async def process_decode(run_id:str,body:DecodeInput,actor=Depends(get_actor),idempotency_key:str|None=Header(None)):
    return await decode_artifact(actor,run_id,body.model_dump(),idempotency_key)
