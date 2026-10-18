"""第二种声学模型对同一完整PCM盲识别；独立识别器不等于独立内容验收通过。"""
from pathlib import Path
import time

from anp.errors import BusinessError
from apps.deep_dub.nodes.source_asr import windows,extract_window,pcm_info,checked_coverage
from apps.deep_dub.nodes.transcribe import AudioModelSession,transcribe,NEMOTRON_RULE
from apps.deep_dub.nodes.source_reference import compare_reference
from apps.deep_dub.services.blocking import await_blocking,file_identity

RULE='source-secondary-nemotron-contiguous-v1'


def verify(proof,primary):
    try:
        checked_coverage(primary)
        assert proof['rule_revision']==RULE and proof['pcm']==primary['pcm']
        assert proof['independent_recognizer'] is True and proof['independent_review'] is False and proof['source_text_modified'] is False
        assert proof['semantic_review']==proof['speaker_review']==proof['subtitle_timing_review']=='not_measured'
        assert proof['model']['family']=='nemotron_asr' and primary['chunks'][0]['recognition']['family']=='qwen3_asr'
        assert proof['model']['weights_sha256']!=primary['model']['weights_sha256']
        ranges=windows(primary['pcm']['sample_count'],30)
        assert len(proof['chunks'])==len(ranges)
        session=proof['session']
        assert session['operation_stopped'] is True and session['initially_unloaded'] is True
        assert session['requests']==len(ranges) and session['warm_cache_checks']==len(ranges)-1
        for i,((start,end),chunk) in enumerate(zip(ranges,proof['chunks'],strict=True)):
            assert (chunk['index'],chunk['start_sample'],chunk['end_sample'])==(i,start,end)
            assert chunk['pcm_sha256']==chunk['normalized_pcm_sha256']
            model=chunk['recognition']
            assert model['family']=='nemotron_asr' and model['rule_revision']==NEMOTRON_RULE
            assert model['context_prompt']=='' and model['language_hint']==primary['language_hint']
            assert model['normalized_samples']==end-start and model['model_session_id']==session['session_id'] and model['operation_stopped'] is False
            assert all(model[k]==proof['model'][k] for k in ('model_id','weights_sha256','runtime_sha256','source_revision'))
            assert isinstance(model['text'],str) and len(model['text'])<=64000
        comparison=compare_reference('\n'.join(c['recognition']['text'] for c in primary['chunks']),
                                     [{'text':c['recognition']['text']} for c in proof['chunks']])
        assert comparison==proof['primary_comparison']
    except (KeyError,AssertionError,TypeError,ValueError):
        raise BusinessError('第二套识别证据与完整PCM、模型身份或原始识别不一致',409,'SOURCE_SECONDARY_EVIDENCE_INVALID') from None
    return {'sample_coverage':1.,'chunk_count':len(ranges),'empty_chunks':sum(not c['recognition']['text'].strip() for c in proof['chunks']),
            'primary_difference_count':comparison['difference_count'],
            'scope':'不同声学模型在相同连续PCM上的无正文提示识别与差异候选，不证明正文、角色或字幕通过'}


async def recognize(config,pool,folder,pcm_file,primary):
    checked_coverage(primary)
    if config.family!='nemotron_asr' or config.weights_sha256==primary['model']['weights_sha256']:
        raise BusinessError('第二套识别必须使用不同家族与权重',422,'SOURCE_SECONDARY_MODEL_INVALID')
    folder,pcm_file=Path(folder),Path(pcm_file)
    original=await await_blocking(file_identity,pcm_file)
    if original!=primary['normalized_input'] or await await_blocking(pcm_info,pcm_file)!=primary['pcm']:
        raise BusinessError('第二套识别输入不是原始完整PCM',409,'SOURCE_SECONDARY_INPUT_CHANGED')
    started=time.monotonic();chunks=[];ranges=windows(primary['pcm']['sample_count'],30)
    async with AudioModelSession(config,pool,folder) as session:
        for index,(start,end) in enumerate(ranges):
            part=folder/('secondary-%04d'%index);part.mkdir();wave=part/'input.wav'
            digest=await await_blocking(extract_window,pcm_file,wave,start,end)
            result=await transcribe(config,pool,part,wave,primary['language_hint'],300,session=session)
            normalized=await await_blocking(pcm_info,part/'asr-16k.wav')
            if normalized['pcm_sha256']!=digest:raise BusinessError('第二套识别预处理改变了PCM',409,'SOURCE_SECONDARY_INPUT_CHANGED')
            chunks.append({'index':index,'start_sample':start,'end_sample':end,'pcm_sha256':digest,'normalized_pcm_sha256':normalized['pcm_sha256'],'recognition':result})
            pool.data['source_secondary_progress']={'completed_chunks':len(chunks),'total_chunks':len(ranges)};pool.save()
    if original!=await await_blocking(file_identity,pcm_file):raise BusinessError('第二套识别期间完整PCM变化',409,'SOURCE_SECONDARY_INPUT_CHANGED')
    proof={'rule_revision':RULE,'pcm':primary['pcm'],'model':{'family':config.family,**{k:getattr(config,v) for k,v in [('model_id','model_id'),('weights_sha256','weights_sha256'),('runtime_sha256','executable_sha256'),('source_revision','source_revision')]}},
        'chunks':chunks,'session':session.receipt(),'elapsed_seconds':time.monotonic()-started,'independent_recognizer':True,'independent_review':False,'source_text_modified':False,
        'semantic_review':'not_measured','speaker_review':'not_measured','subtitle_timing_review':'not_measured'}
    proof['primary_comparison']=await await_blocking(compare_reference,'\n'.join(c['recognition']['text'] for c in primary['chunks']),[{'text':c['recognition']['text']} for c in chunks])
    proof['summary']=await await_blocking(verify,proof,primary)
    return proof
