"""Parakeet差异的原生词候选定位；不裁决哪套正文正确。"""
from collections import Counter
from hashlib import sha256
from apps.deep_dub.nodes.source_cross_review import extend_queue
from apps.deep_dub.nodes.source_reference import tokens
from apps.deep_dub.nodes.source_review import canonical
from apps.deep_dub.nodes.transcribe import checked_native_words
from anp.errors import BusinessError

RULE='source-native-word-review-queue-v1'


def extend_native_queue(packet,primary,secondary,evidence_id):
    proof=secondary['measurement']
    if proof['model']['family']!='parakeet_tdt':
        raise BusinessError('原生词定位需要已核对的Parakeet证据',409,'SOURCE_REVIEW_INPUT_CHANGED')
    # 复用完整来源/原稿/参考绑定检查；原有Nemotron扩展与默认队列不改。
    coarse=extend_queue(packet,primary,secondary,evidence_id)
    fingerprint=sha256(canonical({'rule':RULE,'base':packet['packet_sha256'],
        'evidence_artifact_id':evidence_id,'proof_sha256':sha256(canonical(secondary)).hexdigest()})).hexdigest()
    native=[]
    for chunk in proof['chunks']:
        model=chunk['recognition'];size=chunk['end_sample']-chunk['start_sample']
        checked_native_words(model['words'],model['text'],size)
        timed=[{**token,'start_sample':chunk['start_sample']+word['start_sample'],
            'end_sample':chunk['start_sample']+word['end_sample'],'chunk_index':chunk['index']}
            for word in model['words'] for token in tokens(word['word'])]
        source=tokens(model['text'])
        # 字符串一致不保证分词边界一致，不能强行关联错位时间。
        if [w['key'] for w in timed]==[w['key'] for w in source]:native.extend(timed)
        else:native.extend([None]*len(source))
    originals={i['id'] for i in packet['items']};added=[]
    count,rate=primary['pcm']['sample_count'],primary['pcm']['sample_rate']
    for row in coarse['items']:
        if row['id'] in originals:continue
        diff=proof['primary_comparison']['differences'][row['difference_index']]
        start,end=diff['asr_tokens']
        indexes=list(range(start,end)) if start<end else list(range(max(0,start-1),min(len(native),start+1)))
        selected=[native[i] for i in indexes]
        available=bool(selected) and all(w is not None for w in selected)
        available=available and all(a['end_sample']<=b['start_sample'] or
            (a['start_sample'],a['end_sample'])==(b['start_sample'],b['end_sample']) for a,b in zip(selected,selected[1:]))
        audition=row['audition']
        if available:
            first,last=selected[0]['start_sample'],selected[-1]['end_sample']
            left,right=max(0,first-rate*2),min(count,last+rate*2)
            audition={'start_sample':left,'end_sample':right,'sample_rate':rate,
                'start_ms':left*1000/rate,'end_ms':right*1000/rate,'precision':'native-word-candidate',
                'scope':'仅Parakeet原生词样本定位，非人工或独立边界通过'}
        added.append({**row,'id':sha256((fingerprint+':'+str(row['difference_index'])).encode()).hexdigest(),
            'kind':'parakeet_difference','secondary_model':'Parakeet-TDT','audition':audition,
            'fallback_audition':row['audition'],'native_token_indexes':indexes,
            'native_anchor_mode':'adjacent-insertion-deletion' if start==end else 'candidate-token-range',
            'reason':'第一套与Parakeet盲转录差异；原生词时间仅辅助原音定位，模型或多数一致都不能判内容通过'})
    items=packet['items']+added
    items.sort(key=lambda row:(0 if row['kind']=='invalid_word_time' else 1 if row.get('contains_number') else 2,
        row['audition']['start_sample'],row['id']))
    result={**packet,'rule_revision':RULE,'base_packet_sha256':packet['packet_sha256'],
        'binding':{**packet['binding'],'parakeet_evidence_artifact_id':evidence_id,
            'parakeet_proof_sha256':sha256(canonical(secondary)).hexdigest()},
        'items':items,'total':len(items),'counts':dict(Counter(i['kind'] for i in items)),
        'numeric_differences':sum(bool(i.get('contains_number')) for i in items)}
    result.pop('packet_sha256')
    return {**result,'packet_sha256':sha256(canonical(result)).hexdigest()}
