"""来源整段音频的连续样本分块识别；不把块边界当作语义分段或字幕时间。"""
from hashlib import sha256
from pathlib import Path
import time
import wave

from anp.errors import BusinessError
from apps.deep_dub.nodes.media import _run,executable,probe_media
from apps.deep_dub.nodes.transcribe import AudioModelSession,transcribe,RULE as ASR_RULE
from apps.deep_dub.services.blocking import await_blocking,file_identity

RULE='source-pcm-contiguous-asr-v1'
MAX_SAMPLES=16000*14400


def pcm_info(path):
    digest=sha256();count=0
    with wave.open(str(path),'rb') as source:
        if (source.getnchannels(),source.getsampwidth(),source.getframerate(),source.getcomptype())!=(1,2,16000,'NONE'):
            raise BusinessError('来源识别需要16kHz单声道PCM16',422,'SOURCE_PCM_INVALID')
        expected=source.getnframes()
        if not 0<expected<=MAX_SAMPLES:raise BusinessError('来源音频必须在四小时内',422,'SOURCE_PCM_INVALID')
        while block:=source.readframes(16384):
            digest.update(block);count+=len(block)//2
    if count!=expected:raise BusinessError('来源PCM音频截断',422,'SOURCE_PCM_INVALID')
    return {'sample_rate':16000,'sample_count':count,'pcm_sha256':digest.hexdigest()}


def windows(samples,seconds):
    if (isinstance(samples,bool) or not isinstance(samples,int) or not 0<samples<=MAX_SAMPLES
        or isinstance(seconds,bool) or not isinstance(seconds,int) or not 30<=seconds<=90):
        raise BusinessError('来源分块大小或样本数无效',422,'SOURCE_ASR_INPUT_INVALID')
    size=seconds*16000
    result=[(start,min(samples,start+size)) for start in range(0,samples,size)]
    if len(result)>1 and result[-1][1]-result[-1][0]<1600:
        result[-2:]=[(result[-2][0],result[-1][1])]
    return result


def extract_window(source,target,start,end):
    if Path(target).exists():raise BusinessError('拒绝覆盖来源识别分块',409,'SOURCE_ASR_TARGET_EXISTS')
    with wave.open(str(source),'rb') as original:
        original.setpos(start);block=original.readframes(end-start)
        if len(block)!=(end-start)*2:raise BusinessError('来源识别分块未读完整',422,'SOURCE_PCM_INVALID')
    with wave.open(str(target),'wb') as output:
        output.setnchannels(1);output.setsampwidth(2);output.setframerate(16000);output.writeframes(block)
    return sha256(block).hexdigest()


def checked_coverage(proof):
    try:
        pcm=proof['pcm'];chunks=proof['chunks'];session=proof['session']
        assert proof['rule_revision']==RULE and pcm['sample_rate']==16000
        assert type(pcm['sample_count']) is int and 0<pcm['sample_count']<=MAX_SAMPLES
        expected=windows(pcm['sample_count'],proof['chunk_seconds'])
        assert isinstance(chunks,list) and len(chunks)==len(expected)
        assert session['operation_stopped'] is True and type(session['requests']) is int and session['requests']==len(chunks)
        assert session['initially_unloaded'] is True and session['warm_cache_checks']==len(chunks)-1
        assert all(proof[k]=='not_measured' for k in ('boundary_review','semantic_review','subtitle_timing_review'))
        text_size=0
        for i,((start,end),chunk) in enumerate(zip(expected,chunks,strict=True)):
            model=chunk['recognition']
            assert all(type(chunk[k]) is int for k in ('index','start_sample','end_sample'))
            assert chunk['index']==i and (chunk['start_sample'],chunk['end_sample'])==(start,end)
            assert chunk['pcm_sha256']==chunk['normalized_pcm_sha256'] and len(chunk['pcm_sha256'])==64
            assert model['rule_revision']==ASR_RULE and model['family']=='qwen3_asr' and model['context_prompt']==''
            assert model['model_session_id']==session['session_id'] and model['operation_stopped'] is False
            assert model['normalized_samples']==end-start and model['language_hint']==proof['language_hint']
            assert all(model[k]==proof['model'][k] for k in ('model_id','weights_sha256','runtime_sha256','source_revision'))
            assert isinstance(model['text'],str) and len(model['text'])<=64000
            text_size+=len(model['text'])
        assert text_size<=1000000
    except (AssertionError,KeyError,TypeError,ValueError,ZeroDivisionError):
        raise BusinessError('来源识别分块、模型或覆盖证据无效',409,'SOURCE_ASR_EVIDENCE_INVALID') from None
    return {'sample_coverage':1.,'chunk_count':len(chunks),'empty_chunks':sum(not c['recognition']['text'].strip() for c in chunks),
        'model_sessions':1,'scope':'仅证明连续PCM样本被提交到识别器，非词边界、语义、事实或字幕通过'}


async def recognize_source(config,pool,folder,source,*,language='English',chunk_seconds=60,timeout=300):
    if config.family!='qwen3_asr' or language not in ('English','Chinese'):
        raise BusinessError('来源识别模型或语言无效',422,'SOURCE_ASR_INPUT_INVALID')
    windows(1,chunk_seconds)
    source=Path(source).resolve(strict=True);folder=Path(folder);normalized=folder/'source-16k.wav'
    if normalized.exists():raise BusinessError('拒绝覆盖来源PCM',409,'SOURCE_ASR_TARGET_EXISTS')
    original=await await_blocking(file_identity,source);probe=await probe_media(source)
    if not 0<probe['duration_ms']<=14400000 or not any(s['codec_type']=='audio' for s in probe['streams']):
        raise BusinessError('来源需要四小时以内的音轨',422,'SOURCE_ASR_INPUT_INVALID')
    started=time.monotonic()
    await _run([executable('ffmpeg'),'-nostdin','-v','error','-xerror','-err_detect','explode','-threads','1','-i',str(source),
        '-map','0:a:0','-vn','-ac','1','-ar','16000','-c:a','pcm_s16le',str(normalized)],600)
    pcm=await await_blocking(pcm_info,normalized);prepared=await await_blocking(file_identity,normalized)
    ranges=windows(pcm['sample_count'],chunk_seconds);chunks=[]
    async with AudioModelSession(config,pool,folder) as session:
        for index,(start,end) in enumerate(ranges):
            part=folder/f'chunk-{index:04d}';part.mkdir();wavefile=part/'window.wav'
            raw_hash=await await_blocking(extract_window,normalized,wavefile,start,end)
            result=await transcribe(config,pool,part,wavefile,language,timeout,session=session)
            actual=await await_blocking(pcm_info,part/'asr-16k.wav')
            if actual['pcm_sha256']!=raw_hash:raise BusinessError('来源分块归一化改变了PCM',409,'SOURCE_ASR_INPUT_CHANGED')
            chunks.append({'index':index,'start_sample':start,'end_sample':end,'pcm_sha256':raw_hash,
                'normalized_pcm_sha256':actual['pcm_sha256'],'recognition':result})
            pool.data['source_asr_progress']={'completed_chunks':len(chunks),'total_chunks':len(ranges)};pool.save()
            if sum(len(c['recognition']['text']) for c in chunks)>1000000:
                raise BusinessError('来源识别文本超限',502,'SOURCE_ASR_RESPONSE_LIMIT')
    if original!=await await_blocking(file_identity,source) or prepared!=await await_blocking(file_identity,normalized):
        raise BusinessError('来源识别期间完整输入变化',409,'SOURCE_ASR_INPUT_CHANGED')
    proof={'rule_revision':RULE,'input':original,'media_probe':probe,'pcm':pcm,'normalized_input':prepared,
        'language_hint':language,'chunk_seconds':chunk_seconds,'chunks':chunks,'session':session.receipt(),
        'model':{'model_id':config.model_id,'weights_sha256':config.weights_sha256,'runtime_sha256':config.executable_sha256,'source_revision':config.source_revision},
        'elapsed_with_normalization_seconds':time.monotonic()-started,'audio_duration_minus_media_ms':pcm['sample_count']/16-probe['duration_ms'],
        'boundary_review':'not_measured','semantic_review':'not_measured','subtitle_timing_review':'not_measured',
        'time_origin':'仅来源音轨PCM的样本0，不自动映射为全片字幕落点；分块可能跨越词边界'}
    proof['coverage']=checked_coverage(proof)
    return proof,normalized
