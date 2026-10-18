"""对完整来源ASR候选做逐块强制对齐，复用一次受控模型；不裁剪或改正词时间。"""
from hashlib import sha256
import json
from pathlib import Path
import time

from anp.errors import BusinessError
from apps.deep_dub.nodes.alignment import summarize_words
from apps.deep_dub.nodes.source_asr import pcm_info,extract_window,checked_coverage
from apps.deep_dub.nodes.transcribe import AudioModelSession,align_words,ALIGN_RULE
from apps.deep_dub.services.blocking import await_blocking,file_identity

RULE='source-asr-word-times-unclamped-v1'


def summarize(proof,recognition):
    try:
        checked_coverage(recognition)
        assert proof['rule_revision']==RULE
        assert proof['transcript_policy']=='unreviewed-asr-candidate'
        assert proof['semantic_review']==proof['subtitle_timing_review']=='not_measured'
        assert proof['time_origin']=='source-audio-pcm-sample-zero-only'
        assert proof['pcm']==recognition['pcm']
        assert proof['input']==recognition['normalized_input'] and proof['reported_source_streams']==recognition['media_probe']['streams']
        assert proof['asr_transcripts_sha256']==sha256(json.dumps([c['recognition']['text'] for c in recognition['chunks']],ensure_ascii=False,separators=(',',':')).encode()).hexdigest()
        session=proof['session'];chunks=proof['chunks']
        assert len(chunks)==len(recognition['chunks'])
        words=[];skipped=0;requests=0;invalid=0;uncovered=0;zeros=0;overlap=0
        for source,chunk in zip(recognition['chunks'],chunks,strict=True):
            assert all(type(chunk[k]) is int for k in ('index','start_sample','end_sample'))
            assert all(chunk[k]==source[k] for k in ('index','start_sample','end_sample','pcm_sha256'))
            text=source['recognition']['text']
            if not text.strip():
                assert chunk['status']=='no-asr-text' and chunk['model'] is None and chunk['alignment'] is None
                skipped+=1
                continue
            requests+=1
            assert chunk['status']=='measured'
            model=chunk['model'];part=chunk['end_sample']-chunk['start_sample']
            assert model['family']=='qwen3_forced_aligner' and model['rule_revision']==ALIGN_RULE
            assert model['supplied_transcript']==text and model['clamp_timestamps_to_audio'] is False
            assert model['normalized_samples']==part and model['operation_stopped'] is False
            assert model['language_hint']==recognition['language_hint']
            assert model['model_session_id']==session['session_id']
            assert all(model[k]==proof['model'][k] for k in ('model_id','weights_sha256','runtime_sha256','source_revision'))
            assert chunk['normalized_pcm_sha256']==source['pcm_sha256']
            measured=summarize_words(model['words'],part,text)
            assert chunk['alignment']==measured
            invalid+=not measured['valid_timestamps'];uncovered+=not measured['transcript_token_coverage']
            zeros+=measured['zero_length_content_words'];overlap+=measured['internal_overlap_samples']
            for word in measured['words']:
                words.append({**word,'chunk_index':chunk['index'],
                    'source_start_sample':chunk['start_sample']+word['start_sample'],
                    'source_end_sample':chunk['start_sample']+word['end_sample']})
        assert type(session['requests']) is int and session['requests']==requests
        assert requests>0 and session['warm_cache_checks']==requests-1 and session['initially_unloaded'] is True
        assert session['operation_stopped'] is True
        assert proof['words']==words
        order_issues=sum(b['source_start_sample']<a['source_start_sample'] for a,b in zip(words,words[1:]))
        cross_overlap=sum(max(0,a['source_end_sample']-b['source_start_sample']) for a,b in zip(words,words[1:]) if a['chunk_index']!=b['chunk_index'])
    except (KeyError,TypeError,AssertionError,ValueError):
        raise BusinessError('来源词时间与PCM、ASR候选、模型或完整分块不一致',409,'SOURCE_ALIGNMENT_EVIDENCE_INVALID') from None
    return {'chunk_count':len(chunks),'model_requests':requests,'empty_asr_chunks':skipped,'word_count':len(words),
        'invalid_timestamp_chunks':invalid,'uncovered_transcript_chunks':uncovered,'zero_length_content_words':zeros,
        'internal_overlap_samples':overlap,'global_start_order_issues':order_issues,'cross_chunk_overlap_samples':cross_overlap,
        'scope':'仅给定ASR候选的词边界测量，不证明正文正确、说话人、语义边界或字幕落点通过'}


async def align_source(config,pool,folder,pcm_file,recognition,timeout=600):
    checked_coverage(recognition)
    if config.family!='qwen3_forced_aligner':
        raise BusinessError('来源词对齐必须使用已配置强制对齐模型',422,'SOURCE_ALIGNMENT_INPUT_INVALID')
    if any(len(c['recognition']['text'])>2000 for c in recognition['chunks']):
        raise BusinessError('ASR块文本超过对齐器2000字限制，不能截断正文',422,'SOURCE_ALIGNMENT_INPUT_INVALID')
    if not any(c['recognition']['text'].strip() for c in recognition['chunks']):
        raise BusinessError('完整ASR没有可对齐文本，不伪造词时间',422,'SOURCE_ALIGNMENT_INPUT_INVALID')
    folder=Path(folder);pcm_file=Path(pcm_file)
    before=await await_blocking(file_identity,pcm_file)
    pcm=await await_blocking(pcm_info,pcm_file)
    if before!=recognition['normalized_input'] or pcm!=recognition['pcm']:
        raise BusinessError('来源PCM不是识别阶段的完整输入',409,'SOURCE_ALIGNMENT_INPUT_MISMATCH')
    started=time.monotonic();chunks=[];words=[]
    async with AudioModelSession(config,pool,folder) as session:
        for chunk in recognition['chunks']:
            row={k:chunk[k] for k in ('index','start_sample','end_sample','pcm_sha256')}
            text=chunk['recognition']['text']
            if not text.strip():
                chunks.append({**row,'status':'no-asr-text','model':None,'alignment':None})
                pool.data['source_alignment_progress']={'completed_chunks':len(chunks),'total_chunks':len(recognition['chunks'])};pool.save()
                continue
            part=folder/f'word-chunk-{chunk["index"]:04d}';part.mkdir()
            source=part/'input.wav'
            identity=await await_blocking(extract_window,pcm_file,source,chunk['start_sample'],chunk['end_sample'])
            if identity!=chunk['pcm_sha256']:
                raise BusinessError('词对齐分块PCM与原识别窗口不同',409,'SOURCE_ALIGNMENT_INPUT_MISMATCH')
            model=await align_words(config,pool,part,source,text,recognition['language_hint'],timeout,session=session)
            normalized=await await_blocking(pcm_info,part/'asr-16k.wav')
            if normalized['pcm_sha256']!=identity:
                raise BusinessError('对齐器预处理改变了识别PCM样本',409,'SOURCE_ALIGNMENT_INPUT_MISMATCH')
            measured=summarize_words(model['words'],model['normalized_samples'],text)
            chunks.append({**row,'status':'measured','normalized_pcm_sha256':identity,'model':model,'alignment':measured})
            words.extend({**word,'chunk_index':chunk['index'],'source_start_sample':chunk['start_sample']+word['start_sample'],
                'source_end_sample':chunk['start_sample']+word['end_sample']} for word in measured['words'])
            pool.data['source_alignment_progress']={'completed_chunks':len(chunks),'total_chunks':len(recognition['chunks'])};pool.save()
    if before!=await await_blocking(file_identity,pcm_file):
        raise BusinessError('来源词对齐期间完整PCM发生变化',409,'SOURCE_ALIGNMENT_INPUT_MISMATCH')
    proof={'rule_revision':RULE,'transcript_policy':'unreviewed-asr-candidate','pcm':pcm,'input':before,
        'asr_transcripts_sha256':sha256(json.dumps([c['recognition']['text'] for c in recognition['chunks']],ensure_ascii=False,separators=(',',':')).encode()).hexdigest(),
        'model':{k:getattr(config,name) for k,name in [('model_id','model_id'),('weights_sha256','weights_sha256'),('runtime_sha256','executable_sha256'),('source_revision','source_revision')]},
        'chunks':chunks,'words':words,'session':session.receipt(),'elapsed_seconds':time.monotonic()-started,
        'time_origin':'source-audio-pcm-sample-zero-only','reported_source_streams':recognition['media_probe']['streams'],
        'semantic_review':'not_measured','subtitle_timing_review':'not_measured'}
    proof['summary']=summarize(proof,recognition)
    return proof
