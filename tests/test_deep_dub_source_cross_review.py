from copy import deepcopy
import asyncio

import pytest

from anp.errors import BusinessError
from apps.deep_dub.nodes.source_cross_review import extend_queue
from apps.deep_dub.nodes.source_reference import compare_reference
from apps.deep_dub.nodes.source_review import build_queue
from tests.test_deep_dub_source_review import fixture


def sample():
    primary, measured, reference = fixture()
    binding = {'source_id': 'source', 'pcm_artifact_id': 'pcm', 'asr_evidence_artifact_id': 'asr',
               'asr_evidence_sha256': 'a'*64, 'reference_evidence_artifact_id': 'reference'}
    chunks = [{'index': i, 'start_sample': i*480000, 'end_sample': (i+1)*480000,
               'recognition': {'text': text}} for i, text in enumerate([
                   'Doctorow does claim 4.3 million.', '', 'He will not', 'return. extra'])]
    secondary = {'input': binding.copy(), 'measurement': {'pcm': primary['pcm'], 'chunks': chunks,
        'primary_comparison': compare_reference('\n'.join(c['recognition']['text'] for c in primary['chunks']),
                                                [{'text': c['recognition']['text']} for c in chunks])}}
    return build_queue(primary, measured, reference, binding), primary, secondary


def test_cross_model_insert_delete_number_and_chunk_union_preserve_original_inputs():
    packet, primary, secondary = sample()
    before = deepcopy((packet, primary, secondary))
    result = extend_queue(packet, primary, secondary, 'second')
    assert result['base_packet_sha256'] == packet['packet_sha256']
    assert result['total'] == packet['total']+3
    assert result['counts']['cross_model_difference'] == 3
    added = [i for i in result['items'] if i['kind'] == 'cross_model_difference']
    assert {i['difference_operation'] for i in added} == {'delete', 'insert', 'replace'}
    numeric = next(i for i in added if i['contains_number'])
    assert (numeric['primary_text'], numeric['secondary_text']) == ('4.2', '4.3')
    assert (numeric['audition']['start_sample'], numeric['audition']['end_sample']) == (0, 992000)
    tail = next(i for i in added if i['difference_operation'] == 'insert')
    assert tail['primary_text'] == '' and tail['secondary_text'] == 'extra'
    assert tail['audition']['end_sample'] == primary['pcm']['sample_count']
    assert all(i['review_status'] == 'not_measured' for i in result['items'])
    assert result['source_review'] == result['subtitle_timing_review'] == 'not_measured'
    assert {i['id'] for i in packet['items']} <= {i['id'] for i in result['items']}
    assert (packet, primary, secondary) == before
    assert extend_queue(packet, primary, secondary, 'second') == result
    changed = extend_queue(packet, primary, secondary, 'new-evidence')
    assert changed['packet_sha256'] != result['packet_sha256']
    assert {i['id'] for i in changed['items'] if i['kind'] == 'cross_model_difference'}.isdisjoint(i['id'] for i in added)


@pytest.mark.parametrize('field', ['source_id', 'pcm_artifact_id', 'asr_evidence_artifact_id',
                                  'asr_evidence_sha256', 'reference_evidence_artifact_id'])
def test_cannot_mix_old_or_different_source_evidence(field):
    packet, primary, secondary = sample()
    secondary['input'][field] = 'other'
    with pytest.raises(BusinessError, match='不同来源或识别版本'):
        extend_queue(packet, primary, secondary, 'second')


def test_changed_pcm_rejected_even_when_source_ids_match():
    packet, primary, secondary = sample()
    secondary['measurement']['pcm'] = {**primary['pcm'], 'sample_count': 1}
    with pytest.raises(BusinessError):
        extend_queue(packet, primary, secondary, 'second')


def test_service_secondary_is_opt_in_and_requires_explicit_kind_flag():
    from types import SimpleNamespace
    from apps.deep_dub.services.source_review import read
    async def require(permission): assert permission == 'deep_dub.project.read'
    async def scenario():
        actor = SimpleNamespace(require=require)
        for params in ({'kind': 'cross_model_difference'}, {'include_secondary': 'true'}):
            with pytest.raises(BusinessError, match='跨模型差异需要|参数无效'):
                await read(actor, '00000000-0000-4000-8000-000000000001',
                           '00000000-0000-4000-8000-000000000002', **params)
    asyncio.run(scenario())


def test_service_optional_secondary_snapshot_and_missing_evidence(monkeypatch):
    from types import SimpleNamespace
    from apps.deep_dub.services import source_review, source_independent_asr
    packet, primary, secondary = sample()
    source = '00000000-0000-4000-8000-000000000002'
    project = '00000000-0000-4000-8000-000000000001'
    _, measured, reference = fixture()
    binding = {**packet['binding'], 'source_id': source}
    secondary['input'] = binding.copy()
    async def asr(*args):
        return {'recognition': primary, 'evidence_artifact_id': 'asr', 'pcm_artifact_id': 'pcm'}
    async def aligned(*args):
        return {'measurement': measured, 'input': binding, 'evidence_artifact_id': 'words'}
    async def compared(*args):
        return {'proof': {'input': binding, 'reference': reference}, 'evidence_artifact_id': 'reference'}
    result = {'proof': secondary, 'evidence_artifact_id': 'second'}
    calls = []
    async def independent(*args):
        calls.append('secondary')
        return result
    async def require(permission): calls.append(permission)
    monkeypatch.setattr(source_review.source_recognition, 'read_latest', asr)
    monkeypatch.setattr(source_review.source_alignment, 'read_latest', aligned)
    monkeypatch.setattr(source_review.source_reference, 'read_latest_reference', compared)
    monkeypatch.setattr(source_independent_asr, 'read_latest', independent)
    async def scenario():
        nonlocal result
        actor = SimpleNamespace(require=require)
        base = await source_review.read(actor, project, source)
        assert calls == ['deep_dub.project.read']
        merged = await source_review.read(actor, project, source, include_secondary=True, limit=1)
        assert merged['matched'] == 6 and merged['base_packet_sha256'] == base['packet_sha256']
        page = await source_review.read(actor, project, source, include_secondary=True, limit=1,
                                       offset=1, expected_hash=merged['packet_sha256'])
        assert page['items'][0]['id'] != merged['items'][0]['id']
        result['evidence_artifact_id'] = 'new-second'
        with pytest.raises(BusinessError, match='复核输入已变化'):
            await source_review.read(actor, project, source, include_secondary=True, expected_hash=merged['packet_sha256'])
        result = None
        with pytest.raises(BusinessError, match='请先完成第二套整轨识别'):
            await source_review.read(actor, project, source, include_secondary=True)
        assert await source_review.read(actor, project, source) == base
    asyncio.run(scenario())
