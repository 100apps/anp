"""由已校验的来源证据构造复核队列；定位是试听辅助，不是字幕或通过结论。"""
from collections import Counter
from hashlib import sha256
import json

from apps.deep_dub.nodes.source_reference import compare_reference, tokens
from apps.deep_dub.nodes.text_compare import normalize

RULE = 'source-review-queue-v1'


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()


def build_queue(recognition, measurement, reference, binding):
    chunks = recognition['chunks']
    sample_count = recognition['pcm']['sample_count']
    rate = recognition['pcm']['sample_rate']
    comparison = compare_reference(reference['text'], [{'text': c['recognition']['text']} for c in chunks])
    observed = []
    mapped = {}
    for chunk, aligned in zip(chunks, measurement['chunks'], strict=True):
        text = chunk['recognition']['text']
        words = tokens(text)
        offset = len(observed)
        observed.extend({**w, 'chunk_index': chunk['index']} for w in words)
        alignment = aligned['alignment']
        if not alignment or not alignment['valid_timestamps']:
            continue
        # 只在整块词序列完全一致时关联词时间。不能用模糊对齐掩盖缺词或数字变化。
        timed = []
        for word in alignment['words']:
            timed.extend({**t, 'start_sample': chunk['start_sample'] + word['start_sample'],
                          'end_sample': chunk['start_sample'] + word['end_sample']}
                         for t in tokens(word['word']))
        if [w['key'] for w in words] == [w['key'] for w in timed]:
            for index, word in enumerate(timed, offset):
                mapped[index] = word

    expected = tokens(reference['text'])
    issues = []
    fingerprint = sha256(canonical({'rule': RULE, 'binding': binding})).hexdigest()

    def audition(start, end, precision):
        # 仅将试听区间限定在真实PCM内；从不裁改词边界原始值。
        left = max(0, min(sample_count, start - rate * 2))
        right = min(sample_count, max(0, end + rate * 2))
        if right <= left:
            left, right = 0, min(sample_count, rate * 6)
            precision = 'unavailable-fallback'
        return {'start_sample': left, 'end_sample': right, 'sample_rate': rate,
                'start_ms': left * 1000 / rate, 'end_ms': right * 1000 / rate,
                'precision': precision, 'scope': 'PCM音轨试听候选窗口，非字幕或已审核词时间'}

    def add(kind, key, row):
        issues.append({'id': sha256((fingerprint + ':' + kind + ':' + str(key)).encode()).hexdigest(),
                       'kind': kind, 'review_status': 'not_measured', **row})

    def context(text, sequence, start, end):
        left, right = max(0, start - 12), min(len(sequence), end + 12)
        return text[sequence[left]['start_char']:sequence[right - 1]['end_char']] if left < right else ''

    for index, diff in enumerate(comparison['differences']):
        a, b = diff['reference_tokens']
        c, d = diff['asr_tokens']
        indexes = list(range(c, d)) if c < d else list(range(max(0, c - 1), min(len(observed), c + 1)))
        nearby = sorted({observed[i]['chunk_index'] for i in indexes})
        # 缺失词使用两侧锚点；块内不完整或词时间相互倒置时降为块定位。
        timed = [mapped[i] for i in indexes if i in mapped]
        precise = bool(indexes) and len(timed) == len(indexes)
        precise = precise and all(x['start_sample'] <= x['end_sample'] for x in timed)
        precise = precise and all(x['end_sample'] <= y['start_sample'] for x, y in zip(timed, timed[1:]))
        if precise:
            start, end, precision = timed[0]['start_sample'], timed[-1]['end_sample'], 'word-candidate'
        else:
            start = min(chunks[i]['start_sample'] for i in nearby)
            end = max(chunks[i]['end_sample'] for i in nearby)
            precision = 'chunk-fallback'
        asr_context = '\n'.join(chunks[i]['recognition']['text'] for i in nearby)
        add('reference_difference', index, {**{k: v for k, v in diff.items() if k != 'kind'},
            'difference_operation': diff['kind'], 'difference_index': index,
            'reference_context': context(reference['text'], expected, a, b),
            'asr_context': asr_context, 'audition': audition(start, end, precision),
            'reason': '逐词文本差异，双方均为待核对资料；不能直接采信官方文本或ASR'})

    for chunk, aligned in zip(chunks, measurement['chunks'], strict=True):
        alignment = aligned['alignment']
        if not alignment:
            add('empty_asr', chunk['index'], {'chunk_index': chunk['index'],
                'asr_context': chunk['recognition']['text'],
                'audition': audition(chunk['start_sample'], chunk['end_sample'], 'chunk-fallback'),
                'reason': '识别正文为空，需核对是否静音、背景或遗漏'})
            continue
        if not alignment['valid_timestamps']:
            outliers = [w for w in alignment['words'] if not 0 <= w['start_sample'] <= w['end_sample'] <= chunk['end_sample'] - chunk['start_sample']]
            add('invalid_word_time', chunk['index'], {'chunk_index': chunk['index'], 'raw_outliers': outliers,
                'asr_context': chunk['recognition']['text'],
                'audition': audition(chunk['start_sample'], chunk['end_sample'], 'chunk-fallback'),
                'reason': '原始词时间越界或倒置；不裁剪后冒充通过'})
        if not alignment['transcript_token_coverage']:
            add('word_coverage', chunk['index'], {'chunk_index': chunk['index'],
                'asr_context': chunk['recognition']['text'],
                'supplied_normalized': normalize(chunk['recognition']['text']),
                'aligned_normalized': normalize(''.join(w['word'] for w in alignment['words'])),
                'audition': audition(chunk['start_sample'], chunk['end_sample'], 'chunk-fallback'),
                'reason': '严格字符覆盖差异；标点和数字分隔符仍保留，不自动归为正文漏词'})
    for left, right in zip(chunks, chunks[1:]):
        edge = right['start_sample']
        add('chunk_boundary', right['index'], {'chunk_index': right['index'],
            'preceding_text': left['recognition']['text'][-500:], 'following_text': right['recognition']['text'][:500],
            'boundary_sample': edge, 'audition': audition(edge - rate * 4, edge + rate * 4, 'chunk-boundary'),
            'reason': '固定分块可能切词、重复或遗漏；每个接缝仍需复核'})
    # 数字差异和异常优先，但稳定ID与原差异编号均不受排序或筛选影响。
    priority = {'invalid_word_time': 0, 'empty_asr': 1, 'word_coverage': 3, 'chunk_boundary': 4, 'reference_difference': 5}
    issues.sort(key=lambda row: (2 if row.get('contains_number') else priority[row['kind']],
                                 row['audition']['start_sample'], row['id']))
    counts = dict(Counter(row['kind'] for row in issues))
    result = {'rule_revision': RULE, 'binding': binding, 'pcm': recognition['pcm'],
              'items': issues, 'total': len(issues), 'counts': counts,
              'numeric_differences': sum(bool(i.get('contains_number')) for i in issues),
              'source_review': 'not_measured', 'speaker_review': 'not_measured', 'subtitle_timing_review': 'not_measured',
              'scope': '复核定位队列，不更改来源、转录、原始词时间、运行状态或审核记录'}
    return {**result, 'packet_sha256': sha256(canonical(result)).hexdigest()}
