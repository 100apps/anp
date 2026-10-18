"""将已校验的第二套识别差异定位到原音；不裁决正文、不产生字幕时间。"""
from collections import Counter
from hashlib import sha256

from anp.errors import BusinessError
from apps.deep_dub.nodes.source_reference import tokens
from apps.deep_dub.nodes.source_review import canonical

RULE = 'source-cross-model-review-v1'


def extend_queue(packet, primary, secondary, evidence_id):
    proof = secondary['measurement']
    inputs = secondary['input']
    binding = packet['binding']
    if (inputs['source_id'] != binding['source_id']
        or inputs['asr_evidence_artifact_id'] != binding['asr_evidence_artifact_id']
        or inputs['asr_evidence_sha256'] != binding['asr_evidence_sha256']
        or inputs['pcm_artifact_id'] != binding['pcm_artifact_id']
        or inputs['reference_evidence_artifact_id'] != binding['reference_evidence_artifact_id']
        or proof['pcm'] != primary['pcm']):
        raise BusinessError('跨模型复核证据绑定了不同来源或识别版本', 409, 'SOURCE_REVIEW_INPUT_CHANGED')
    # 阅读服务已重新验证模型/全轨/比较值；再次绑定整个证据，不能只绑定一个可变摘要。
    fingerprint = sha256(canonical({'rule': RULE, 'base': packet['packet_sha256'],
        'secondary_evidence_artifact_id': evidence_id, 'proof_sha256': sha256(canonical(secondary)).hexdigest()})).hexdigest()

    def indexed(chunks):
        return [dict(word, chunk_index=i) for i, chunk in enumerate(chunks)
                for word in tokens(chunk['recognition']['text'])]

    left_words, right_words = indexed(primary['chunks']), indexed(proof['chunks'])
    count, rate = primary['pcm']['sample_count'], primary['pcm']['sample_rate']
    added = []
    for number, diff in enumerate(proof['primary_comparison']['differences']):
        def nearby(words, bounds):
            start, end = bounds
            # 插入/删除的空侧必须保留前后锚点，尤其是整轨起止点。
            selected = words[start:end] if start < end else words[max(0, start-1):min(len(words), start+1)]
            return sorted({word['chunk_index'] for word in selected})
        left = nearby(left_words, diff['reference_tokens'])
        right = nearby(right_words, diff['asr_tokens'])
        chunks = [primary['chunks'][i] for i in left] + [proof['chunks'][i] for i in right]
        if not chunks:
            raise BusinessError('跨模型差异缺少原音定位锚点', 409, 'SOURCE_REVIEW_INPUT_CHANGED')
        start = max(0, min(c['start_sample'] for c in chunks)-rate*2)
        end = min(count, max(c['end_sample'] for c in chunks)+rate*2)
        added.append({'id': sha256((fingerprint+':'+str(number)).encode()).hexdigest(),
            'kind': 'cross_model_difference', 'review_status': 'not_measured',
            'difference_index': number, 'difference_operation': diff['kind'],
            'contains_number': diff['contains_number'], 'primary_text': diff['reference_text'],
            'secondary_text': diff['asr_text'], 'primary_chunk_indexes': left, 'secondary_chunk_indexes': right,
            'primary_context': '\n'.join(primary['chunks'][i]['recognition']['text'] for i in left),
            'secondary_context': '\n'.join(proof['chunks'][i]['recognition']['text'] for i in right),
            'audition': {'start_sample': start, 'end_sample': end, 'sample_rate': rate,
                'start_ms': start*1000/rate, 'end_ms': end*1000/rate, 'precision': 'cross-model-chunk-fallback',
                'scope': '两侧识别块的并集粗定位，非独立词时间或字幕时间'},
            'reason': '不同声学模型的正文候选有差异；请查听原音，模型一致或多数一致均不证明正确'})
    items = packet['items'] + added
    items.sort(key=lambda row: (0 if row['kind'] == 'invalid_word_time' else 1 if row.get('contains_number') else 2,
                               row['audition']['start_sample'], row['id']))
    result = {**packet, 'rule_revision': RULE, 'base_packet_sha256': packet['packet_sha256'],
        'binding': {**binding, 'secondary_evidence_artifact_id': evidence_id,
                    'secondary_proof_sha256': sha256(canonical(secondary)).hexdigest()},
        'items': items, 'total': len(items), 'counts': dict(Counter(i['kind'] for i in items)),
        'numeric_differences': sum(bool(i.get('contains_number')) for i in items)}
    result.pop('packet_sha256')
    return {**result, 'packet_sha256': sha256(canonical(result)).hexdigest()}
