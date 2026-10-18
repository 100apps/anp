"""审核、选版和时间线的共用契约；服务端 RPC 负责最终权限与并发校验。"""
from typing import Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator

from anp.errors import BusinessError


class Contract(BaseModel):
    model_config = ConfigDict(extra='forbid')


class QualityRules(Contract):
    translation_required: list[str] = Field(default_factory=lambda: ['meaning','facts','numbers','negation','names','complete'])
    audio_required: list[str] = Field(default_factory=lambda: ['content','decode','nonsilent','timing','collision','bounds'])
    max_advance_ms: int = Field(default=500, ge=0, le=500, strict=True)
    collision_tolerance_ms: int = Field(default=50, ge=0, le=50, strict=True)
    background_mode: Literal['preserve','omit-by-quality-choice'] = 'preserve'
    voice_preference: Literal['episode-reference','stable-chinese-voice'] = 'episode-reference'


class QualityProfileInput(Contract):
    profile_key: Literal['high','medium','low'] = 'high'
    revision: int = Field(ge=1, strict=True)
    rules: QualityRules = Field(default_factory=QualityRules)
    provenance: str = Field(min_length=1, max_length=1000)


class AuditInput(Contract):
    kind: Literal['translation','audio','voice_reference','emotion_reference','background','timeline','render','source']
    subject_id: UUID
    quality_profile_id: UUID
    rule_key: str = Field(min_length=1, max_length=100)
    rule_revision: str = Field(min_length=1, max_length=200)
    status: Literal['passed','failed','not_measured']
    method: Literal['human','independent_model','technical']
    evidence_artifact_id: UUID | None = None
    details: dict = Field(default_factory=dict)

    @model_validator(mode='after')
    def measured_requires_evidence(self):
        if self.status != 'not_measured' and self.evidence_artifact_id is None:
            raise ValueError('已测审核必须关联证据产物')
        if self.status == 'passed' and self.method == 'technical' and (self.kind == 'translation'
            or (self.kind == 'voice_reference' and self.rule_key == 'speaker_identity')
            or (self.kind == 'background' and self.rule_key == 'event_preservation')):
            raise ValueError('语义、身份或事件保留通过需要人工或独立模型复核')
        return self


class SelectionInput(Contract):
    segment_id: UUID
    translation_id: UUID
    audio_attempt_id: UUID
    quality_profile_id: UUID
    audit_ids: list[UUID] = Field(min_length=1, max_length=64)
    expected_version: int = Field(ge=1, strict=True)

    @model_validator(mode='after')
    def unique_audits(self):
        if len(set(self.audit_ids)) != len(self.audit_ids):
            raise ValueError('审核集合不能重复')
        return self


class TimelineClip(Contract):
    segment_id: UUID
    selection_id: UUID
    start_ms: int = Field(ge=0, strict=True)
    gain: float = Field(default=1, gt=0, le=4, allow_inf_nan=False, strict=True)


class TimelineInput(Contract):
    segment_set_id: UUID
    quality_profile_id: UUID
    clips: list[TimelineClip] = Field(min_length=1, max_length=10000)
    expected_version: int = Field(ge=1, strict=True)


def identifier(value):
    try:
        return str(UUID(str(value)))
    except (ValueError, TypeError, AttributeError):
        raise BusinessError('项目 ID 格式不正确', 422, 'VALIDATION_ERROR') from None


def operation_key(value):
    key = value if value is not None else str(uuid4())
    if not isinstance(key, str) or not 1 <= len(key) <= 200:
        raise BusinessError('幂等键长度应为 1–200', 422, 'VALIDATION_ERROR')
    return key


class VersionService:
    async def create_quality_profile(self, actor, payload):
        await actor.require('deep_dub.project.edit')
        body = QualityProfileInput.model_validate(payload)
        return await actor.objects.rpc('deep_dub_create_quality_profile', p_workspace=actor.workspace_id,
            p_key=body.profile_key, p_revision=body.revision, p_rules=body.rules.model_dump(), p_provenance=body.provenance)

    async def record_audit(self, actor, project_id, payload, request_key=None):
        await actor.require('deep_dub.audit')
        body = AuditInput.model_validate(payload).model_dump(mode='json')
        return await actor.objects.rpc('deep_dub_record_audit', p_workspace=actor.workspace_id,
            p_project=identifier(project_id), p_kind=body['kind'], p_subject=body['subject_id'],
            p_profile=body['quality_profile_id'], p_rule=body['rule_key'], p_rule_revision=body['rule_revision'],
            p_status=body['status'], p_method=body['method'], p_evidence=body['evidence_artifact_id'],
            p_details=body['details'], p_request_key=operation_key(request_key))

    async def select_version(self, actor, project_id, payload, request_key=None):
        await actor.require('deep_dub.project.edit')
        body = SelectionInput.model_validate(payload).model_dump(mode='json')
        return await actor.objects.rpc('deep_dub_select_version', p_workspace=actor.workspace_id,
            p_project=identifier(project_id), p_segment=body['segment_id'], p_translation=body['translation_id'],
            p_audio=body['audio_attempt_id'], p_profile=body['quality_profile_id'], p_audits=body['audit_ids'],
            p_expected_version=body['expected_version'], p_request_key=operation_key(request_key))

    async def commit_timeline(self, actor, project_id, payload, request_key=None):
        await actor.require('deep_dub.project.edit')
        body = TimelineInput.model_validate(payload).model_dump(mode='json')
        return await actor.objects.rpc('deep_dub_commit_timeline', p_workspace=actor.workspace_id,
            p_project=identifier(project_id), p_segment_set=body['segment_set_id'], p_profile=body['quality_profile_id'],
            p_clips=body['clips'], p_expected_version=body['expected_version'], p_request_key=operation_key(request_key))


service = VersionService()
