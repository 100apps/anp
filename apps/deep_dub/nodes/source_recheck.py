"""固定ASR块的接缝改窗重识别；保留原文，不将同模型复测当作独立内容通过。"""
from pathlib import Path
import time

from anp.errors import BusinessError
from apps.deep_dub.nodes.source_asr import checked_coverage, pcm_info, extract_window
from apps.deep_dub.nodes.transcribe import AudioModelSession, transcribe, RULE as ASR_RULE
from apps.deep_dub.services.blocking import await_blocking, file_identity

RULE = 'source-boundary-unprompted-recheck-v1'
RADIUS_SAMPLES = 6 * 16000


def plan(recognition, boundaries=None):
    checked_coverage(recognition)
    chunks = recognition['chunks']
    selected = list(range(1, len(chunks))) if boundaries is None else boundaries
    if (not isinstance(selected, list) or len(selected) > 499 or
        any(type(i) is not int or not 1 <= i < len(chunks) for i in selected) or len(set(selected)) != len(selected)):
        raise BusinessError('接缝编号必须唯一且属于当前识别分块', 422, 'SOURCE_RECHECK_INPUT_INVALID')
    if not selected:
        raise BusinessError('当前来源没有可复测的块接缝', 422, 'SOURCE_RECHECK_INPUT_INVALID')
    samples = recognition['pcm']['sample_count']
    return [{'boundary_index': i, 'boundary_sample': chunks[i]['start_sample'],
             'start_sample': max(0, chunks[i]['start_sample'] - RADIUS_SAMPLES),
             'end_sample': min(samples, chunks[i]['start_sample'] + RADIUS_SAMPLES),
             'preceding_text': chunks[i-1]['recognition']['text'],
             'following_text': chunks[i]['recognition']['text']} for i in sorted(selected)]


def check(proof, recognition):
    try:
        expected = plan(recognition, proof['boundaries'])
        assert proof['rule_revision'] == RULE and proof['pcm'] == recognition['pcm']
        assert proof['source_text_modified'] is False and proof['independent_review'] is False
        assert proof['semantic_review'] == proof['subtitle_timing_review'] == 'not_measured'
        assert len(proof['windows']) == len(expected)
        session = proof['session']
        assert session['operation_stopped'] is True and session['initially_unloaded'] is True
        assert session['requests'] == len(expected) and session['warm_cache_checks'] == len(expected)-1
        for row, target in zip(proof['windows'], expected, strict=True):
            assert all(row[k] == v for k, v in target.items())
            model = row['recognition']
            assert row['pcm_sha256'] == row['normalized_pcm_sha256']
            assert model['family'] == 'qwen3_asr' and model['rule_revision'] == ASR_RULE and model['context_prompt'] == ''
            assert model['normalized_samples'] == target['end_sample']-target['start_sample']
            assert model['language_hint'] == recognition['language_hint'] and model['model_session_id'] == session['session_id']
            assert model['operation_stopped'] is False
            assert all(model[k] == proof['model'][k] for k in ('model_id','weights_sha256','runtime_sha256','source_revision'))
            assert isinstance(model['text'], str) and len(model['text']) <= 64000
    except (KeyError, TypeError, ValueError, AssertionError):
        raise BusinessError('接缝重识别证据与原始PCM、分块或模型不匹配', 409, 'SOURCE_RECHECK_EVIDENCE_INVALID') from None
    return {'window_count': len(expected), 'submitted_samples': sum(w['end_sample']-w['start_sample'] for w in expected),
            'empty_windows': sum(not w['recognition']['text'].strip() for w in proof['windows']),
            'scope': '改窗且无文本提示的同模型重识别，仅提供额外声学候选，不自动修改原稿或通过独立审核'}


async def recheck(config, pool, folder, pcm_file, recognition, boundaries=None, timeout=300):
    ranges = plan(recognition, boundaries)
    folder, pcm_file = Path(folder), Path(pcm_file)
    original = await await_blocking(file_identity, pcm_file)
    if original != recognition['normalized_input'] or await await_blocking(pcm_info, pcm_file) != recognition['pcm']:
        raise BusinessError('接缝输入不是识别阶段的完整PCM', 409, 'SOURCE_RECHECK_INPUT_CHANGED')
    if config.family != 'qwen3_asr':
        raise BusinessError('接缝重识别需要ASR模型', 422, 'SOURCE_RECHECK_INPUT_INVALID')
    started = time.monotonic()
    rows = []
    async with AudioModelSession(config, pool, folder) as session:
        for target in ranges:
            part = folder / ('boundary-%04d' % target['boundary_index'])
            part.mkdir()
            wavefile = part / 'window.wav'
            digest = await await_blocking(extract_window, pcm_file, wavefile, target['start_sample'], target['end_sample'])
            recognized = await transcribe(config, pool, part, wavefile, recognition['language_hint'], timeout, session=session)
            actual = await await_blocking(pcm_info, part/'asr-16k.wav')
            if actual['pcm_sha256'] != digest:
                raise BusinessError('重识别预处理改变了PCM窗口', 409, 'SOURCE_RECHECK_INPUT_CHANGED')
            rows.append({**target, 'pcm_sha256': digest, 'normalized_pcm_sha256': actual['pcm_sha256'], 'recognition': recognized})
            pool.data['source_recheck_progress'] = {'completed_windows': len(rows), 'total_windows': len(ranges)}
            pool.save()
    if original != await await_blocking(file_identity, pcm_file):
        raise BusinessError('接缝重识别期间完整PCM变化', 409, 'SOURCE_RECHECK_INPUT_CHANGED')
    proof = {'rule_revision': RULE, 'pcm': recognition['pcm'], 'boundaries': [r['boundary_index'] for r in ranges],
             'windows': rows, 'session': session.receipt(), 'elapsed_seconds': time.monotonic()-started,
             'model': {k:getattr(config,v) for k,v in [('model_id','model_id'),('weights_sha256','weights_sha256'),('runtime_sha256','executable_sha256'),('source_revision','source_revision')]},
             'source_text_modified': False, 'independent_review': False, 'semantic_review': 'not_measured', 'subtitle_timing_review': 'not_measured'}
    proof['summary'] = check(proof, recognition)
    return proof
