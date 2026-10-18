from copy import deepcopy
import asyncio
from types import SimpleNamespace

import pytest

from anp.errors import BusinessError
from apps.deep_dub.nodes.source_review import build_queue
from apps.deep_dub.services import source_review


def fixture():
    chunks = [{'index': 0, 'start_sample': 0, 'end_sample': 960000, 'recognition': {'text': 'Doctorow does not claim 4.2 million.'}},
              {'index': 1, 'start_sample': 960000, 'end_sample': 1920000, 'recognition': {'text': 'He will not return.'}}]
    def words(text):
        return [{'word': word, 'start_sample': i*32000, 'end_sample': (i+1)*32000} for i, word in enumerate(text.split())]
    alignment = [{'alignment': {'valid_timestamps': True, 'transcript_token_coverage': True, 'words': words(c['recognition']['text'])}} for c in chunks]
    return {'pcm': {'sample_count': 1920000, 'sample_rate': 16000}, 'chunks': chunks}, {'chunks': alignment}, {'text': 'Doctorow does claim 4.3 million. He will not return.'}


def test_numeric_and_negation_differences_keep_both_sides_and_stable_bound_ids():
    recognition, measurement, reference = fixture()
    before = deepcopy((recognition, measurement, reference))
    packet = build_queue(recognition, measurement, reference, {'source_id': 'first'})
    assert packet['counts'] == {'reference_difference': 2, 'chunk_boundary': 1}
    assert packet['numeric_differences'] == 1
    numeric = packet['items'][0]
    assert (numeric['reference_text'], numeric['asr_text']) == ('4.3', '4.2')
    assert numeric['kind'] == 'reference_difference' and numeric['difference_operation'] == 'replace'
    assert numeric['audition']['precision'] == 'word-candidate'
    negation = next(x for x in packet['items'] if x.get('asr_text') == 'not')
    assert negation['reference_text'] == ''
    assert all(i['review_status'] == 'not_measured' for i in packet['items'])
    assert packet['source_review'] == packet['speaker_review'] == packet['subtitle_timing_review'] == 'not_measured'
    assert build_queue(recognition, measurement, reference, {'source_id': 'first'}) == packet
    changed = build_queue(recognition, measurement, reference, {'source_id': 'second'})
    assert {i['id'] for i in packet['items']}.isdisjoint(i['id'] for i in changed['items'])
    assert (recognition, measurement, reference) == before


def test_invalid_raw_word_is_preserved_and_only_audition_is_bounded():
    recognition, measurement, reference = fixture()
    bad = measurement['chunks'][0]['alignment']
    bad['valid_timestamps'] = False
    bad['words'][-1]['end_sample'] = 962560
    bad['transcript_token_coverage'] = False
    packet = build_queue(recognition, measurement, reference, {})
    issue = packet['items'][0]
    assert issue['kind'] == 'invalid_word_time'
    assert issue['raw_outliers'][0]['end_sample'] == 962560
    assert all(i['audition']['precision'] == 'chunk-fallback' for i in packet['items'] if i['kind'] == 'reference_difference')
    assert packet['counts']['word_coverage'] == 1
    for item in packet['items']:
        assert 0 <= item['audition']['start_sample'] < item['audition']['end_sample'] <= 1920000
    assert bad['words'][-1]['end_sample'] == 962560


def test_numeric_word_token_mismatch_does_not_gain_false_precision():
    recognition, measurement, reference = fixture()
    measurement['chunks'][0]['alignment']['words'][4]['word'] = '42'
    result = build_queue(recognition, measurement, reference, {})
    assert all(i['audition']['precision'] == 'chunk-fallback' for i in result['items'] if i['kind'] == 'reference_difference')


def test_empty_asr_and_all_chunk_boundaries_remain_in_review_queue():
    recognition, measurement, reference = fixture()
    recognition['chunks'][1]['recognition']['text'] = ''
    measurement['chunks'][1]['alignment'] = None
    result = build_queue(recognition, measurement, reference, {})
    assert result['counts']['empty_asr'] == result['counts']['chunk_boundary'] == 1
    boundary = next(i for i in result['items'] if i['kind'] == 'chunk_boundary')
    assert boundary['boundary_sample'] == 960000
    assert boundary['audition']['start_sample'] == 864000


def test_service_pagination_snapshot_binding_and_each_call_permission(monkeypatch):
    recognition, measurement, reference = fixture()
    project = '00000000-0000-4000-8000-000000000001'
    source = '00000000-0000-4000-8000-000000000002'
    original = {'asr_evidence_artifact_id': 'asr', 'asr_evidence_sha256': 'a'*64, 'pcm_artifact_id': 'pcm', 'source_id': source}
    recognized = {'recognition': recognition, 'evidence_artifact_id': 'asr', 'pcm_artifact_id': 'pcm'}
    aligned = {'measurement': measurement, 'input': deepcopy(original), 'evidence_artifact_id': 'words'}
    compared = {'proof': {'input': deepcopy(original), 'reference': reference}, 'evidence_artifact_id': 'reference'}
    async def asr(*args): return recognized
    async def alignment(*args): return aligned
    async def comparison(*args): return compared
    monkeypatch.setattr(source_review.source_recognition, 'read_latest', asr)
    monkeypatch.setattr(source_review.source_alignment, 'read_latest', alignment)
    monkeypatch.setattr(source_review.source_reference, 'read_latest_reference', comparison)
    calls = []
    revoked = False
    async def require(permission):
        calls.append(permission)
        if revoked: raise BusinessError('权限已撤回', 403, 'FORBIDDEN')
    actor = SimpleNamespace(require=require)
    async def scenario():
        nonlocal revoked
        first = await source_review.read(actor, project, source, limit=1)
        second = await source_review.read(actor, project, source, limit=1, offset=1, expected_hash=first['packet_sha256'])
        assert first['total'] == second['matched'] == 3 and first['has_more']
        assert first['items'][0]['id'] != second['items'][0]['id']
        numeric = await source_review.read(actor, project, source, kind='numbers', expected_hash=first['packet_sha256'])
        assert numeric['matched'] == 1 and numeric['items'][0]['contains_number']
        aligned['evidence_artifact_id'] = 'new-words'
        with pytest.raises(BusinessError, match='复核输入已变化'):
            await source_review.read(actor, project, source, expected_hash=first['packet_sha256'])
        compared['proof']['input']['asr_evidence_artifact_id'] = 'old-asr'
        with pytest.raises(BusinessError, match='不同识别版本'):
            await source_review.read(actor, project, source)
        revoked = True
        with pytest.raises(BusinessError, match='权限已撤回'):
            await source_review.read(actor, project, source)
    asyncio.run(scenario())
    assert calls == ['deep_dub.project.read']*6
