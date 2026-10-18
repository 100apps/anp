"""运行与阶段契约；本地执行和 HTTP 均保留当前用户身份。"""
from typing import Literal
from uuid import UUID

from pydantic import Field, SecretStr, model_validator

from apps.deep_dub.services.versions import Contract, identifier, operation_key


class RunInput(Contract):
    quality_profile_id: UUID
    mode: Literal['local', 'http'] = 'local'
    purpose: Literal['production', 'acceptance'] = 'production'
    flow_revision: str = Field(min_length=1, max_length=200)
    expected_version: int = Field(ge=1, strict=True)
    reuse_policy: Literal['none', 'approved_versions'] = 'none'


class StageInput(Contract):
    node: str = Field(min_length=1, max_length=100)
    partition: str = Field(default='main', min_length=1, max_length=200)
    input_hash: str = Field(pattern=r'^[0-9a-f]{64}$')
    device_kind: Literal['cpu', 'gpu'] | None = None


class StageReceipt(Contract):
    status: Literal['succeeded', 'failed', 'waiting_review']
    outputs: list[UUID] = Field(default_factory=list, max_length=10000)
    elapsed_seconds: float = Field(ge=0, allow_inf_nan=False)
    error: dict | None = None
    lease_id: UUID | None = None
    fence: int | None = Field(default=None, ge=1, strict=True)
    token: SecretStr | None = None
    operation_stopped: bool = Field(default=False, strict=True)

    @model_validator(mode='after')
    def consistent_receipt(self):
        if len(set(self.outputs)) != len(self.outputs):
            raise ValueError('产物不能重复')
        if (self.status == 'failed' and self.error is None
                or self.status == 'succeeded' and self.error is not None):
            raise ValueError('失败须提供错误，成功不能携带错误')
        if any(x is not None for x in (self.lease_id, self.fence, self.token)) and not all(
                x is not None for x in (self.lease_id, self.fence, self.token)):
            raise ValueError('租约 ID、fence 与凭据必须同时提供')
        return self


class ScoreInput(Contract):
    metric: str = Field(min_length=1, max_length=100)
    rule_revision: str = Field(min_length=1, max_length=200)
    measurement_status: Literal['measured', 'not_measured']
    value: float | None = Field(default=None, allow_inf_nan=False)
    evidence_artifact_id: UUID | None = None
    details: dict = Field(default_factory=dict)

    @model_validator(mode='after')
    def evidence_matches_measurement(self):
        if self.measurement_status == 'measured':
            if self.value is None or self.evidence_artifact_id is None:
                raise ValueError('已测评分需要数值及证据')
        elif self.value is not None:
            raise ValueError('未测评分必须为空值')
        return self


class LostAttemptInput(Contract):
    lease_ids:list[UUID]=Field(min_length=1,max_length=100)


class ExecutionService:
    async def close_lost_attempt(self,actor,attempt_id,lease_ids):
        await actor.require('deep_dub.device.manage');await actor.require('deep_dub.task.run')
        body=LostAttemptInput.model_validate({'lease_ids':lease_ids})
        return await actor.objects.rpc('deep_dub_close_lost_attempt',p_workspace=actor.workspace_id,
            p_attempt=identifier(attempt_id),p_lease_ids=[str(lease) for lease in body.lease_ids])

    async def queue(self, actor, project_id, payload, request_key=None):
        await actor.require('deep_dub.task.create')
        await actor.require('deep_dub.task.run')
        body = RunInput.model_validate(payload)
        return await actor.objects.rpc('deep_dub_queue_run', p_workspace=actor.workspace_id,
            p_project=identifier(project_id), p_profile=str(body.quality_profile_id), p_mode=body.mode,
            p_purpose=body.purpose, p_flow_revision=body.flow_revision,
            p_expected_version=body.expected_version, p_reuse_policy=body.reuse_policy,
            p_request_key=operation_key(request_key))

    async def list(self, actor, project_id):
        await actor.require('deep_dub.task.read')
        return await actor.objects.select('deep_dub_runs', workspace_id='eq.'+actor.workspace_id,
            project_id='eq.'+identifier(project_id), order='queued_at.desc', limit='100')

    async def start(self, actor, run_id):
        await actor.require('deep_dub.task.run')
        return await actor.objects.rpc('deep_dub_start_run', p_workspace=actor.workspace_id,
                                       p_run=identifier(run_id))

    async def cancel(self, actor, run_id):
        await actor.require('deep_dub.task.cancel')
        return await actor.objects.rpc('deep_dub_cancel_run', p_workspace=actor.workspace_id,
                                       p_run=identifier(run_id))

    async def begin_stage(self, actor, run_id, payload, request_key=None):
        await actor.require('deep_dub.task.run')
        body = StageInput.model_validate(payload)
        return await actor.objects.rpc('deep_dub_begin_stage', p_workspace=actor.workspace_id,
            p_run=identifier(run_id), p_node=body.node, p_partition=body.partition,
            p_input_hash=body.input_hash, p_device_kind=body.device_kind,
            p_request_key=operation_key(request_key))

    async def mark_stage(self, actor, attempt_id, phase: Literal['started', 'loaded']):
        await actor.require('deep_dub.task.run')
        return await actor.objects.rpc('deep_dub_mark_stage', p_workspace=actor.workspace_id,
            p_attempt=identifier(attempt_id), p_phase=phase)

    async def finish_stage(self, actor, attempt_id, payload):
        await actor.require('deep_dub.task.run')
        body = StageReceipt.model_validate(payload)
        return await actor.objects.rpc('deep_dub_finish_stage', p_workspace=actor.workspace_id,
            p_attempt=identifier(attempt_id), p_status=body.status,
            p_outputs=[str(x) for x in body.outputs], p_elapsed=body.elapsed_seconds, p_error=body.error,
            p_lease=str(body.lease_id) if body.lease_id else None, p_fence=body.fence,
            p_token=body.token.get_secret_value() if body.token else None,
            p_operation_stopped=body.operation_stopped)

    async def record_score(self, actor, attempt_id, payload, request_key=None):
        await actor.require('deep_dub.audit')
        body = ScoreInput.model_validate(payload)
        return await actor.objects.rpc('deep_dub_record_score', p_workspace=actor.workspace_id,
            p_attempt=identifier(attempt_id), p_metric=body.metric, p_rule_revision=body.rule_revision,
            p_measurement_status=body.measurement_status, p_value=body.value,
            p_evidence=str(body.evidence_artifact_id) if body.evidence_artifact_id else None,
            p_details=body.details, p_request_key=operation_key(request_key))


service = ExecutionService()
