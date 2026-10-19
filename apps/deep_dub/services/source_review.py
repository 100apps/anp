"""同一用户权限下读取并关联ASR、词时间与官方参考；不缓存授权判断。"""
from typing import Literal

from anp.errors import BusinessError
from apps.deep_dub.services import source_recognition, source_alignment, source_reference
from apps.deep_dub.services.blocking import await_blocking
from apps.deep_dub.services.versions import identifier
from apps.deep_dub.nodes.source_review import build_queue

ReviewKind = Literal['all', 'reference_difference', 'invalid_word_time', 'word_coverage', 'empty_asr', 'chunk_boundary', 'numbers', 'cross_model_difference','parakeet_difference']


async def read(actor, project_id, source_id, *, offset=0, limit=30, kind: ReviewKind='all', expected_hash=None, include_secondary=False,include_parakeet=False):
    await actor.require('deep_dub.project.read')
    project_id, source_id = identifier(project_id), identifier(source_id)
    if type(offset) is not int or offset < 0 or type(limit) is not int or not 1 <= limit <= 100 or kind not in ReviewKind.__args__ or type(include_secondary) is not bool or type(include_parakeet) is not bool:
        raise BusinessError('复核分页或分类参数无效', 422, 'VALIDATION_ERROR')
    if kind == 'cross_model_difference' and not include_secondary:
        raise BusinessError('跨模型差异需要启用第二套识别复核', 422, 'VALIDATION_ERROR')
    if kind=='parakeet_difference' and not include_parakeet:
        raise BusinessError('Parakeet差异需要显式加入对应证据',422,'VALIDATION_ERROR')
    recognized = await source_recognition.read_latest(actor, project_id, source_id)
    aligned = await source_alignment.read_latest(actor, project_id, source_id)
    reference = await source_reference.read_latest_reference(actor, project_id, source_id)
    if not all((recognized, aligned, reference)):
        raise BusinessError('源稿复核需要同一来源的ASR、词时间和参考证据', 409, 'SOURCE_REVIEW_INPUT_MISSING')
    inputs = aligned['input']
    compared = reference['proof']['input']
    if (inputs['asr_evidence_artifact_id'] != recognized['evidence_artifact_id']
        or compared['asr_evidence_artifact_id'] != recognized['evidence_artifact_id']
        or inputs['pcm_artifact_id'] != recognized['pcm_artifact_id']
        or inputs['asr_evidence_sha256'] != compared['asr_evidence_sha256']
        or inputs['source_id'] != source_id or compared['source_id'] != source_id):
        raise BusinessError('最新证据关联了不同识别版本，不能混用旧审核输入', 409, 'SOURCE_REVIEW_INPUT_CHANGED')
    binding = {'project_id': project_id, 'source_id': source_id, 'pcm_artifact_id': recognized['pcm_artifact_id'],
               'asr_evidence_artifact_id': recognized['evidence_artifact_id'],
               'asr_evidence_sha256': inputs['asr_evidence_sha256'],
               'word_evidence_artifact_id': aligned['evidence_artifact_id'],
               'reference_evidence_artifact_id': reference['evidence_artifact_id']}
    packet = await await_blocking(build_queue, recognized['recognition'], aligned['measurement'], reference['proof']['reference'], binding)
    if include_secondary:
        # 局部导入避免第二套识别服务复用 canonical 时形成模块循环。
        from apps.deep_dub.services.source_independent_asr import read_latest
        from apps.deep_dub.nodes.source_cross_review import extend_queue
        secondary = await read_latest(actor, project_id, source_id)
        if not secondary:
            raise BusinessError('请先完成第二套整轨识别，再打开跨模型复核', 409, 'SOURCE_REVIEW_SECONDARY_MISSING')
        packet = await await_blocking(extend_queue, packet, recognized['recognition'], secondary['proof'], secondary['evidence_artifact_id'])
    if include_parakeet:
        from apps.deep_dub.services.source_independent_asr import read_latest
        from apps.deep_dub.nodes.source_native_review import extend_native_queue
        native=await read_latest(actor,project_id,source_id,'parakeet')
        if not native:raise BusinessError('请先完成Parakeet整轨盲转录',409,'SOURCE_REVIEW_PARAKEET_MISSING')
        packet=await await_blocking(extend_native_queue,packet,recognized['recognition'],native['proof'],native['evidence_artifact_id'])
    if expected_hash is not None and expected_hash != packet['packet_sha256']:
        raise BusinessError('复核输入已变化，请从第一页重新读取', 409, 'SOURCE_REVIEW_INPUT_CHANGED')
    selected = [row for row in packet['items'] if kind == 'all' or row['kind'] == kind or (kind == 'numbers' and row.get('contains_number'))]
    return {**packet, 'items': selected[offset:offset + limit], 'matched': len(selected), 'offset': offset,
            'limit': limit, 'kind': kind, 'has_more': offset + limit < len(selected)}
