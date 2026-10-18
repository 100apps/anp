"""业务HTTP适配；工作台、根CLI与独立开发入口共用同一服务契约。"""
from fastapi import APIRouter, Depends, Header, Request, Query
from tempfile import TemporaryDirectory
from pathlib import Path
from pydantic import ValidationError

from anp.auth.dependencies import get_actor
from apps.deep_dub.services.projects import ProjectInput, service
from apps.deep_dub.services.versions import AuditInput, QualityProfileInput, SelectionInput, TimelineInput, service as versions
from apps.deep_dub.services.execution import RunInput, StageInput, StageReceipt, ScoreInput, LostAttemptInput, service as execution
from typing import Literal
from anp.errors import BusinessError
from apps.deep_dub.services.artifacts import service as artifacts, ArtifactMetadata
from apps.deep_dub.services.observations import deliver_one
from apps.deep_dub.services.processing import DecodeInput,decode_artifact
from apps.deep_dub.services.delegation import issue_decode_job
from apps.deep_dub.services.workbench import RecordKind,service as workbench
from apps.deep_dub.api.media import router as media_router
from apps.deep_dub.services.editor import TranslationEdit, service as editor
from apps.deep_dub.services.waveforms import WaveformInput,prepare as prepare_waveform
from apps.deep_dub.services.translation import TranslationInput as GenerateTranslationInput,generate as generate_translation
from apps.deep_dub.services.references import ReferencePreparation,prepare as prepare_reference
from apps.deep_dub.services.speech import SpeechInput,generate as generate_speech
from apps.deep_dub.services.resident_recovery import recover as recover_resident
from apps.deep_dub.services.audio_review import AudioReviewInput,review as review_audio,read_latest as read_audio_review
from apps.deep_dub.services.audio_timing import AudioTimingInput,analyze as analyze_audio_timing,read_latest as read_audio_timing
from apps.deep_dub.services.source_recognition import SourceRecognitionInput,recognize as recognize_source,read_latest as read_source_recognition
from apps.deep_dub.services.source_reference import SourceReferenceInput,compare as compare_source_reference,read as read_source_reference,read_latest_reference
from apps.deep_dub.services.source_alignment import SourceAlignmentInput,align as align_source,read_latest as read_source_alignment
from apps.deep_dub.services.source_review import read as read_source_review,ReviewKind
from apps.deep_dub.services.source_speakers import SpeakerInput,generate as analyze_speakers,read_latest as read_speakers
from apps.deep_dub.services.auditory_review import ReviewInput as AuditoryInput,generate as review_source_audio,read_latest as read_auditory_review
from apps.deep_dub.services.source_recheck import RecheckInput,generate as recheck_source,read_latest as read_source_recheck
from apps.deep_dub.services.source_independent_asr import IndependentInput,generate as recognize_independent,read_latest as read_independent
from apps.deep_dub.services.source_acquisition import AcquisitionInput,acquire as acquire_source,read as read_source_acquisition
from apps.deep_dub.services.source_jobs import PreparationInput,issue as issue_source_preparation
from apps.deep_dub.services.source_orchestration import launch as launch_source_preparation,read as read_source_preparation,prepare_local,latest as latest_source_preparation
from apps.deep_dub.services.candidates import SourceInput,TranscriptInput,TranslationInput,ReferenceInput,AudioInput,BackgroundInput,RenderInput,service as candidates

router = APIRouter(prefix='/api/apps/deep_dub', tags=['deep_dub'])
router.include_router(media_router)


@router.post('/attempts/{attempt_id}/close-lost')
async def close_lost_attempt(attempt_id:str,body:LostAttemptInput,actor=Depends(get_actor)):
    return await execution.close_lost_attempt(actor,attempt_id,body.lease_ids)


@router.post('/attempts/{attempt_id}/recover-resident')
async def recover_resident_attempt(attempt_id:str,actor=Depends(get_actor)):
    return await recover_resident(actor,attempt_id)


@router.post('/runs/{run_id}/review-source-audio')
async def create_source_audio_review(run_id:str,body:AuditoryInput,actor=Depends(get_actor),idempotency_key:str|None=Header(None)):
    return await review_source_audio(actor,run_id,body.model_dump(),idempotency_key)


@router.get('/projects/{project_id}/sources/{source_id}/auditory-review')
async def get_source_auditory_review(project_id:str,source_id:str,actor=Depends(get_actor)):
    return await read_auditory_review(actor,project_id,source_id)


@router.post('/runs/{run_id}/analyze-source-speakers')
async def create_source_speakers(run_id:str,body:SpeakerInput,actor=Depends(get_actor),idempotency_key:str|None=Header(None)):
    return await analyze_speakers(actor,run_id,body.model_dump(),idempotency_key)


@router.get('/projects/{project_id}/sources/{source_id}/speaker-timing')
async def get_source_speakers(project_id:str,source_id:str,actor=Depends(get_actor)):
    return await read_speakers(actor,project_id,source_id)


@router.post('/runs/{run_id}/recognize-source-independent')
async def create_independent_asr(run_id:str,body:IndependentInput,actor=Depends(get_actor),idempotency_key:str|None=Header(None)):
    return await recognize_independent(actor,run_id,body.model_dump(),idempotency_key)


@router.get('/projects/{project_id}/sources/{source_id}/independent-recognition')
async def get_independent_asr(project_id:str,source_id:str,recognizer:Literal['nemotron','parakeet']='nemotron',actor=Depends(get_actor)):
    return await read_independent(actor,project_id,source_id,recognizer)


@router.post('/runs/{run_id}/recheck-source-boundaries')
async def recheck_source_boundaries(run_id:str,body:RecheckInput,actor=Depends(get_actor),idempotency_key:str|None=Header(None)):
    return await recheck_source(actor,run_id,body.model_dump(),idempotency_key)


@router.get('/projects/{project_id}/sources/{source_id}/boundary-recheck')
async def get_boundary_recheck(project_id:str,source_id:str,actor=Depends(get_actor)):
    return await read_source_recheck(actor,project_id,source_id)


@router.get('/projects/{project_id}/sources/{source_id}/review-queue')
async def get_source_review_queue(project_id:str,source_id:str,offset:int=Query(0,ge=0),limit:int=Query(30,ge=1,le=100),
        kind:ReviewKind='all',expected_hash:str|None=Query(None,min_length=64,max_length=64),include_secondary:bool=False,actor=Depends(get_actor)):
    return await read_source_review(actor,project_id,source_id,offset=offset,limit=limit,kind=kind,expected_hash=expected_hash,include_secondary=include_secondary)


@router.post('/runs/{run_id}/source-preparation-job')
async def authorize_source_preparation(run_id:str,body:PreparationInput,actor=Depends(get_actor),idempotency_key:str|None=Header(None)):
    return await issue_source_preparation(actor,run_id,body.model_dump(),idempotency_key)


@router.post('/runs/{run_id}/prepare-source')
async def prepare_source_direct(run_id:str,body:PreparationInput,actor=Depends(get_actor),idempotency_key:str|None=Header(None)):
    return await prepare_local(actor,run_id,body.model_dump(),idempotency_key)


@router.post('/runs/{run_id}/source-preparation')
async def start_source_preparation(run_id:str,body:PreparationInput,actor=Depends(get_actor),idempotency_key:str|None=Header(None)):
    return await launch_source_preparation(actor,run_id,body.model_dump(),idempotency_key)


@router.get('/runs/{run_id}/source-preparation/{procedure_id}')
async def get_source_preparation(run_id:str,procedure_id:str,actor=Depends(get_actor)):
    return await read_source_preparation(actor,run_id,procedure_id)


@router.get('/runs/{run_id}/source-preparation')
async def get_latest_source_preparation(run_id:str,actor=Depends(get_actor)):
    return await latest_source_preparation(actor,run_id)


@router.post('/runs/{run_id}/acquire-source')
async def create_source_acquisition(run_id:str,body:AcquisitionInput,actor=Depends(get_actor),idempotency_key:str|None=Header(None)):
    return await acquire_source(actor,run_id,body.model_dump(),idempotency_key)


@router.get('/runs/{run_id}/source-acquisition')
async def get_source_acquisition(run_id:str,actor=Depends(get_actor)):
    return await read_source_acquisition(actor,run_id)


@router.post('/runs/{run_id}/recognize-source')
async def create_source_recognition(run_id:str,body:SourceRecognitionInput,actor=Depends(get_actor),idempotency_key:str|None=Header(None)):
    return await recognize_source(actor,run_id,body.model_dump(),idempotency_key)


@router.post('/runs/{run_id}/align-source')
async def create_source_alignment(run_id:str,body:SourceAlignmentInput,actor=Depends(get_actor),idempotency_key:str|None=Header(None)):
    return await align_source(actor,run_id,body.model_dump(),idempotency_key)


@router.get('/projects/{project_id}/sources/{source_id}/word-timing')
async def get_source_alignment(project_id:str,source_id:str,actor=Depends(get_actor)):
    return await read_source_alignment(actor,project_id,source_id)


@router.post('/runs/{run_id}/compare-source-reference')
async def create_source_reference(run_id:str,body:SourceReferenceInput,actor=Depends(get_actor),idempotency_key:str|None=Header(None)):
    return await compare_source_reference(actor,run_id,body.model_dump(),idempotency_key)


@router.get('/attempts/{attempt_id}/source-reference')
async def get_source_reference(attempt_id:str,actor=Depends(get_actor)):
    return await read_source_reference(actor,attempt_id)


@router.get('/projects/{project_id}/sources/{source_id}/reference')
async def get_latest_source_reference(project_id:str,source_id:str,actor=Depends(get_actor)):
    return await read_latest_reference(actor,project_id,source_id)


@router.get('/projects/{project_id}/sources/{source_id}/recognition')
async def get_source_recognition(project_id:str,source_id:str,actor=Depends(get_actor)):
    return await read_source_recognition(actor,project_id,source_id)


@router.post('/runs/{run_id}/align-audio')
async def create_audio_timing(run_id:str,body:AudioTimingInput,actor=Depends(get_actor),idempotency_key:str|None=Header(None)):
    return await analyze_audio_timing(actor,run_id,body.model_dump(),idempotency_key)


@router.get('/projects/{project_id}/audio/{audio_id}/timing')
async def get_audio_timing(project_id:str,audio_id:str,actor=Depends(get_actor)):
    return await read_audio_timing(actor,project_id,audio_id)


@router.post('/runs/{run_id}/review-audio')
async def create_audio_review(run_id:str,body:AudioReviewInput,actor=Depends(get_actor),idempotency_key:str|None=Header(None)):
    return await review_audio(actor,run_id,body.model_dump(),idempotency_key)


@router.get('/projects/{project_id}/audio/{audio_id}/backtranscription')
async def get_audio_review(project_id:str,audio_id:str,actor=Depends(get_actor)):
    return await read_audio_review(actor,project_id,audio_id)


@router.post('/runs/{run_id}/speech')
async def create_speech(run_id:str,body:SpeechInput,actor=Depends(get_actor),idempotency_key:str|None=Header(None)):
    return await generate_speech(actor,run_id,body.model_dump(),idempotency_key)


@router.post('/runs/{run_id}/prepare-reference')
async def create_reference(run_id:str,body:ReferencePreparation,actor=Depends(get_actor),idempotency_key:str|None=Header(None)):
    return await prepare_reference(actor,run_id,body.model_dump(),idempotency_key)


@router.post('/runs/{run_id}/translate')
async def translate_segment(run_id:str,body:GenerateTranslationInput,actor=Depends(get_actor),idempotency_key:str|None=Header(None)):
    return await generate_translation(actor,run_id,body.model_dump(),idempotency_key)


@router.post('/runs/{run_id}/waveform')
async def create_waveform(run_id:str,body:WaveformInput,actor=Depends(get_actor),idempotency_key:str|None=Header(None)):
    return await prepare_waveform(actor,run_id,body.model_dump(),idempotency_key)


@router.post('/projects/{project_id}/segments/{segment_id}/translations',status_code=201)
async def edit_translation(project_id:str,segment_id:str,body:TranslationEdit,
        actor=Depends(get_actor),idempotency_key:str|None=Header(None)):
    return await editor.edit_translation(actor,project_id,segment_id,body.model_dump(),idempotency_key)


@router.get('/tasks')
async def list_tasks(actor=Depends(get_actor)):
    return await workbench.tasks(actor)


@router.get('/tasks/summary')
async def task_summary(actor=Depends(get_actor)):
    return await workbench.summary(actor)


@router.get('/tasks/{task_id}')
async def get_task(task_id:str,actor=Depends(get_actor)):
    return await workbench.task(actor,task_id)


@router.get('/runs/{run_id}')
async def run_detail(run_id:str,actor=Depends(get_actor)):
    return await workbench.run(actor,run_id)


@router.get('/projects/{project_id}/records/{kind}')
async def project_records(project_id:str,kind:RecordKind,offset:int=Query(0,ge=0),
                          limit:int=Query(100,ge=1,le=200),actor=Depends(get_actor)):
    return await workbench.records(actor,project_id,kind,offset=offset,limit=limit)


@router.get('/projects/{project_id}/studio')
async def studio_snapshot(project_id:str,source_id:str|None=None,segment_set_id:str|None=None,
        profile_id:str|None=None,timeline_id:str|None=None,actor=Depends(get_actor)):
    return await workbench.studio(actor,project_id,source_id=source_id,segment_set_id=segment_set_id,profile_id=profile_id,timeline_id=timeline_id)


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


@router.post('/runs/{run_id}/decode-job',status_code=201)
async def authorize_decode_job(run_id:str,body:DecodeInput,actor=Depends(get_actor),idempotency_key:str|None=Header(None)):
    return await issue_decode_job(actor,run_id,body.model_dump(),idempotency_key)
