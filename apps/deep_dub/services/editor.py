"""人工编辑契约；不可变新稿、原身份、CAS 与幂等，不创建执行成功或审核通过。"""
from uuid import UUID

from pydantic import Field, model_validator

from apps.deep_dub.services.versions import Contract, identifier, operation_key


class TranslationEdit(Contract):
    base_translation_id: UUID | None = None
    expected_revision: int = Field(ge=0, strict=True)
    expected_project_version: int = Field(ge=1, strict=True)
    chinese_text: str = Field(min_length=1, max_length=100000)

    @model_validator(mode='after')
    def valid_base(self):
        if (self.expected_revision == 0) != (self.base_translation_id is None):
            raise ValueError('首次译稿必须无父版本，后续编辑必须指定父译稿')
        if not self.chinese_text.strip():
            raise ValueError('译稿不能为空白')
        return self


class EditorService:
    async def edit_translation(self, actor, project_id, segment_id, payload, request_key=None):
        await actor.require('deep_dub.project.read')
        await actor.require('deep_dub.project.edit')
        body = TranslationEdit.model_validate(payload)
        return await actor.objects.rpc('deep_dub_edit_translation',
            p_workspace=actor.workspace_id, p_project=identifier(project_id), p_segment=identifier(segment_id),
            p_base=str(body.base_translation_id) if body.base_translation_id else None,
            p_expected_revision=body.expected_revision, p_expected_project_version=body.expected_project_version,
            p_chinese=body.chinese_text, p_request_key=operation_key(request_key))


service = EditorService()
