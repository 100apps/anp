"""来源、原文分段、译文候选；登记与独立审核分开。"""
from uuid import UUID
from typing import Annotated,Literal
from pydantic import Field, model_validator

from apps.deep_dub.services.versions import Contract,identifier,operation_key


class SourceInput(Contract):
    artifact_id: UUID
    duration_ms: int=Field(gt=0,le=2**63-1,strict=True)
    identity_evidence: dict=Field(min_length=1)


class Cue(Contract):
    cue_no: int=Field(ge=0,le=2**31-1,strict=True)
    speaker_key: str=Field(min_length=1,max_length=200)
    source_text: str=Field(min_length=1,max_length=100000)
    start_ms: int=Field(ge=0,le=2**63-1,strict=True)
    end_ms: int=Field(gt=0,le=2**63-1,strict=True)

    @model_validator(mode='after')
    def meaningful(self):
        if self.end_ms<=self.start_ms or not self.speaker_key.strip() or not self.source_text.strip():
            raise ValueError('cue必须有原文、角色及有效时间窗')
        return self


class Segment(Contract):
    cue_numbers: list[Annotated[int,Field(ge=0,le=2**31-1,strict=True)]]=Field(min_length=1,max_length=100000)


class TranscriptInput(Contract):
    source_id: UUID
    strategy_revision: str=Field(min_length=1,max_length=200)
    cues: list[Cue]=Field(min_length=1,max_length=100000)
    segments: list[Segment]=Field(min_length=1,max_length=10000)

    @model_validator(mode='after')
    def complete_partition(self):
        cues={c.cue_no:c for c in self.cues}
        if len(cues)!=len(self.cues):raise ValueError('cue序号不能重复')
        flattened=[n for s in self.segments for n in s.cue_numbers]
        if len(flattened)!=len(cues) or set(flattened)!=set(cues):
            raise ValueError('分段必须恰好覆盖全部cue一次')
        for segment in self.segments:
            if segment.cue_numbers!=sorted(segment.cue_numbers):raise ValueError('cue顺序必须递增')
            if len({cues[n].speaker_key for n in segment.cue_numbers})!=1:raise ValueError('不能合并不同角色')
        return self


class TranslationInput(Contract):
    segment_id: UUID
    context_artifact_id: UUID
    chinese_text: str=Field(min_length=1,max_length=100000)
    model_id: str=Field(min_length=1,max_length=200)
    prompt_revision: str=Field(min_length=1,max_length=200)


class ReferenceInput(Contract):
    kind: Literal['voice','emotion']
    source_id: UUID
    subject_id: UUID
    start_ms: int=Field(ge=0,le=2**63-1,strict=True)
    end_ms: int=Field(gt=0,le=2**63-1,strict=True)
    original_artifact_id: UUID
    processed_artifact_id: UUID
    processing: dict=Field(default_factory=dict)

    @model_validator(mode='after')
    def valid_window(self):
        if self.end_ms<=self.start_ms:raise ValueError('参考窗口结束必须晚于开始')
        return self


class AudioInput(Contract):
    segment_id: UUID
    translation_id: UUID
    voice_reference_id: UUID
    emotion_reference_id: UUID
    artifact_id: UUID
    evidence_artifact_id: UUID
    kind: Literal['generation','pcm_tempo']='generation'
    parent_id: UUID|None=None
    generation_factor: float=Field(default=1,ge=.90,le=1.35,allow_inf_nan=False,strict=True)
    pcm_tempo_factor: float=Field(default=1,ge=.90,le=1.10,allow_inf_nan=False,strict=True)
    parameters: dict=Field(min_length=1)

    @model_validator(mode='after')
    def generation_and_pcm_are_distinct(self):
        if self.kind=='generation':
            if self.parent_id is not None or self.pcm_tempo_factor!=1:
                raise ValueError('模型生成不能伪装为PCM派生')
            model=self.parameters.get('model_id')
            if not isinstance(model,str) or not 1<=len(model.strip())<=200:
                raise ValueError('必须记录实际模型身份')
        elif self.parent_id is None:
            raise ValueError('PCM派生必须保留父音频版本')
        return self


class BackgroundInput(Contract):
    original_artifact_id: UUID
    processed_artifact_id: UUID
    removed_artifact_id: UUID|None=None
    evidence_artifact_id: UUID
    model_id: str=Field(min_length=1,max_length=200)
    parameters: dict=Field(default_factory=dict)


class RenderInput(Contract):
    timeline_id: UUID
    background_id: UUID|None=None
    strategy_key: str=Field(min_length=1,max_length=200)
    parameters: dict=Field(default_factory=dict)
    dialogue_artifact_id: UUID
    mix_artifact_id: UUID
    subtitle_artifact_id: UUID
    video_artifact_id: UUID
    evidence_artifact_id: UUID


class CandidateService:
    async def source(self,actor,attempt_id,payload,request_key=None):
        await actor.require('deep_dub.task.run')
        body=SourceInput.model_validate(payload)
        return await actor.objects.rpc('deep_dub_register_source',p_workspace=actor.workspace_id,
            p_attempt=identifier(attempt_id),p_artifact=str(body.artifact_id),p_duration_ms=body.duration_ms,
            p_identity=body.identity_evidence,p_request_key=operation_key(request_key))

    async def transcript(self,actor,attempt_id,payload,request_key=None):
        await actor.require('deep_dub.task.run')
        body=TranscriptInput.model_validate(payload)
        return await actor.objects.rpc('deep_dub_register_transcript',p_workspace=actor.workspace_id,
            p_attempt=identifier(attempt_id),p_source=str(body.source_id),p_strategy=body.strategy_revision,
            p_cues=[c.model_dump() for c in body.cues],p_segments=[s.model_dump() for s in body.segments],
            p_request_key=operation_key(request_key))

    async def translation(self,actor,attempt_id,payload,request_key=None):
        await actor.require('deep_dub.task.run')
        body=TranslationInput.model_validate(payload)
        return await actor.objects.rpc('deep_dub_register_translation',p_workspace=actor.workspace_id,
            p_attempt=identifier(attempt_id),p_segment=str(body.segment_id),p_context=str(body.context_artifact_id),
            p_chinese=body.chinese_text,p_model=body.model_id,p_prompt=body.prompt_revision,
            p_request_key=operation_key(request_key))

    async def reference(self,actor,attempt_id,payload,request_key=None):
        await actor.require('deep_dub.task.run')
        body=ReferenceInput.model_validate(payload)
        return await actor.objects.rpc('deep_dub_register_reference',p_workspace=actor.workspace_id,
            p_attempt=identifier(attempt_id),p_kind=body.kind,p_source=str(body.source_id),p_subject=str(body.subject_id),
            p_start_ms=body.start_ms,p_end_ms=body.end_ms,p_original=str(body.original_artifact_id),
            p_processed=str(body.processed_artifact_id),p_processing=body.processing,p_request_key=operation_key(request_key))

    async def audio(self,actor,attempt_id,payload,request_key=None):
        await actor.require('deep_dub.task.run')
        body=AudioInput.model_validate(payload)
        return await actor.objects.rpc('deep_dub_register_audio',p_workspace=actor.workspace_id,
            p_attempt=identifier(attempt_id),p_segment=str(body.segment_id),p_translation=str(body.translation_id),
            p_voice=str(body.voice_reference_id),p_emotion=str(body.emotion_reference_id),p_artifact=str(body.artifact_id),
            p_evidence=str(body.evidence_artifact_id),p_kind=body.kind,p_parent=str(body.parent_id) if body.parent_id else None,
            p_generation_factor=body.generation_factor,p_pcm_tempo_factor=body.pcm_tempo_factor,
            p_parameters=body.parameters,p_request_key=operation_key(request_key))

    async def background(self,actor,attempt_id,payload,request_key=None):
        await actor.require('deep_dub.task.run')
        body=BackgroundInput.model_validate(payload)
        return await actor.objects.rpc('deep_dub_register_background',p_workspace=actor.workspace_id,
            p_attempt=identifier(attempt_id),p_original=str(body.original_artifact_id),p_processed=str(body.processed_artifact_id),
            p_removed=str(body.removed_artifact_id) if body.removed_artifact_id else None,
            p_evidence=str(body.evidence_artifact_id),p_model=body.model_id,p_parameters=body.parameters,
            p_request_key=operation_key(request_key))

    async def render(self,actor,attempt_id,payload,request_key=None):
        await actor.require('deep_dub.task.run')
        body=RenderInput.model_validate(payload)
        return await actor.objects.rpc('deep_dub_register_render',p_workspace=actor.workspace_id,
            p_attempt=identifier(attempt_id),p_timeline=str(body.timeline_id),
            p_background=str(body.background_id) if body.background_id else None,p_strategy=body.strategy_key,p_parameters=body.parameters,
            p_dialogue=str(body.dialogue_artifact_id),p_mix=str(body.mix_artifact_id),p_subtitle=str(body.subtitle_artifact_id),
            p_video=str(body.video_artifact_id),p_evidence=str(body.evidence_artifact_id),p_request_key=operation_key(request_key))


service=CandidateService()
