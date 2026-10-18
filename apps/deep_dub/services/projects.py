"""项目契约；Web、HTTP CLI 和本地 CLI 共同使用，保留调用者身份。"""
from uuid import UUID, uuid4

from pydantic import BaseModel, Field, field_validator

from anp.errors import BusinessError


class ProjectInput(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    source_url: str = Field(min_length=1, max_length=4096)

    @field_validator('name')
    @classmethod
    def name_not_blank(cls, value):
        if not value.strip():
            raise ValueError('项目名称不能为空')
        return value


class ProjectService:
    async def create(self, actor, payload, request_key=None):
        await actor.require('deep_dub.project.create')
        body = ProjectInput.model_validate(payload)
        key = request_key if request_key is not None else str(uuid4())
        if not 1 <= len(key) <= 200:
            raise BusinessError('幂等键长度应为 1–200', 422, 'VALIDATION_ERROR')
        return await actor.objects.rpc('deep_dub_create_project', p_workspace=actor.workspace_id,
            p_name=body.name, p_source_url=body.source_url, p_request_key=key)

    async def list(self, actor):
        await actor.require('deep_dub.project.read')
        return await actor.objects.select('deep_dub_projects', workspace_id='eq.'+actor.workspace_id,
                                          order='created_at.desc', limit='100')

    async def get(self, actor, project_id):
        await actor.require('deep_dub.project.read')
        try:
            identifier = str(UUID(project_id))
        except (ValueError, TypeError, AttributeError):
            raise BusinessError('项目 ID 格式不正确', 422, 'VALIDATION_ERROR') from None
        rows = await actor.objects.select('deep_dub_projects', id='eq.'+identifier,
                                          workspace_id='eq.'+actor.workspace_id)
        if not rows:
            raise BusinessError('项目不存在或无权访问', 404, 'NOT_FOUND')
        return rows[0]


service = ProjectService()
