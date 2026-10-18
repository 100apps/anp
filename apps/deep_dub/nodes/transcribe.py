"""独立ASR，不以期待译稿作提示；保留真实识别文本与过程证据。"""
import asyncio
import json
from pathlib import Path
import time
import wave
import math
import unicodedata
from uuid import uuid4

import httpx

from anp.errors import BusinessError
from anp.sdk.http import async_client
from apps.deep_dub.nodes.media import _run,executable,probe_media
from apps.deep_dub.services.blocking import await_blocking,file_identity
from apps.deep_dub.services.model_pool import OwnedModelServer

RULE='qwen3-asr-owned-unprompted-v1'
ALIGN_RULE='qwen3-forced-word-time-v1'
NEMOTRON_RULE='nemotron-asr-owned-unprompted-v1'
PARAKEET_RULE='parakeet-tdt-owned-unprompted-words-v1'


def checked_native_words(words,text,samples):
    try:
        assert isinstance(words,list) and len(words)<=4096 and (bool(words) or not text.strip())
        previous=0
        for word in words:
            assert isinstance(word,dict) and isinstance(word['word'],str) and 0<len(word['word'])<=1000
            assert type(word['start_sample']) is int and type(word['end_sample']) is int
            assert previous<=word['start_sample']<=word['end_sample']<=samples
            assert type(word['confidence']) in (int,float) and math.isfinite(word['confidence'])
            previous=word['end_sample']
        clean=lambda value:''.join(unicodedata.normalize('NFC',value).split())
        assert clean(''.join(w['word'] for w in words))==clean(text)
    except (KeyError,TypeError,AssertionError,ValueError):
        raise BusinessError('原生词时间、文本覆盖或样本边界无效',502,'ASR_NATIVE_WORDS_INVALID') from None
    return words


async def _ready(server,config,client):
    deadline=time.monotonic()+30
    while True:
        if server.process.poll() is not None:raise BusinessError('ASR模型进程启动后退出',503,'MODEL_START_FAILED')
        try:
            if (await client.get(server.endpoint+'/health',timeout=2)).status_code==200:break
        except httpx.TransportError:pass
        if time.monotonic()>deadline:raise BusinessError('ASR模型服务启动超时',504,'MODEL_START_TIMEOUT')
        await asyncio.sleep(.2)
    if not await await_blocking(server.owns_listener):raise BusinessError('ASR端口不属于本次进程',409,'MODEL_LISTENER_MISMATCH')
    models=await client.get(server.endpoint+'/v1/models');models.raise_for_status();entries=models.json().get('data')
    if not isinstance(entries,list) or len(entries)!=1 or entries[0].get('id')!=config.model_id or entries[0].get('loaded') is not False:
        raise BusinessError('ASR模型身份或惰性状态不符',409,'MODEL_IDENTITY_MISMATCH')


class AudioModelSession:
    """只在同一次阶段/物理GPU租约内复用；每个HTTP客户端仍独立创建和关闭。"""
    def __init__(self,config,pool,folder):
        self.config=config;self.pool=pool;self.server=OwnedModelServer(config,pool,folder)
        self.id=uuid4().hex;self.active=False;self.closed=False;self.requests=0;self.warm_checks=0
        self.started=None;self.ready=None;self.finished=None

    async def __aenter__(self):
        self.started=time.monotonic()
        try:
            self.server.start()
            async with async_client(timeout=httpx.Timeout(30,connect=5),trust_env=False) as client:
                await _ready(self.server,self.config,client)
            self.ready=time.monotonic();self.active=True;return self
        except BaseException:
            await await_blocking(self.server.stop);self.closed=True;raise

    async def __aexit__(self,*args):
        self.active=False
        await await_blocking(self.server.stop)
        self.finished=time.monotonic();self.closed=True

    def receipt(self):
        if not self.closed or self.ready is None or self.finished is None or not self.pool.stopped():
            raise BusinessError('批量模型进程尚未确认退出',409,'OPERATION_NOT_STOPPED')
        return {'session_id':self.id,'requests':self.requests,'warm_cache_checks':self.warm_checks,'initially_unloaded':True,'startup_seconds':self.ready-self.started,
            'process_lifecycle_seconds':self.finished-self.started,'owned_server_exit_code':self.server.exit_code,
            'operation_stopped':True,'scope':'同一阶段租约内的批量模型进程；不是跨作业后台常驻服务'}


async def _run_audio_model(config,pool,folder,source,language='Chinese',timeout=300,exact_text=None,session=None):
    aligning=exact_text is not None
    if config.family not in (('qwen3_forced_aligner',) if aligning else ('qwen3_asr','nemotron_asr','parakeet_tdt')) or language not in ('Chinese','English') or not 5<=timeout<=900:
        raise BusinessError('ASR模型用途、语言或超时不合法',422,'ASR_INPUT_INVALID')
    if aligning and (not isinstance(exact_text,str) or not exact_text.strip() or len(exact_text)>2000):
        raise BusinessError('强制对齐需要有界且明确的给定文本',422,'ALIGN_INPUT_INVALID')
    source=Path(source).resolve(strict=True);folder=Path(folder)
    before=await await_blocking(file_identity,source);probe=await probe_media(source)
    if not 0<probe['duration_ms']<=120000 or not any(s['codec_type']=='audio' for s in probe['streams']):
        raise BusinessError('ASR需要不超过120秒的实际音频',422,'ASR_INPUT_INVALID')
    normalized=folder/'asr-16k.wav'
    if normalized.exists():raise BusinessError('ASR输入副本已存在，拒绝覆盖',409,'ASR_TARGET_EXISTS')
    await _run([executable('ffmpeg'),'-v','error','-xerror','-err_detect','explode','-threads','1','-i',str(source),
        '-map','0:a:0','-vn','-ac','1','-ar','16000','-c:a','pcm_s16le',str(normalized)],60)
    with wave.open(str(normalized),'rb') as audio:
        frames=audio.getnframes()
        if audio.getframerate()!=16000 or audio.getnchannels()!=1 or audio.getsampwidth()!=2 or abs(frames/16-probe['duration_ms'])>3:
            raise BusinessError('ASR格式转换改变了输入时长',422,'ASR_DURATION_MISMATCH')
    prepared=await await_blocking(file_identity,normalized)
    if session is not None and (not isinstance(session,AudioModelSession) or not session.active or session.closed
        or session.pool is not pool or session.config!=config):
        raise BusinessError('ASR批量会话不属于当前模型和租约',409,'MODEL_SESSION_MISMATCH')
    server=session.server if session else OwnedModelServer(config,pool,folder)
    started=time.monotonic();ready=None
    try:
        if session is None:server.start()
        async with async_client(timeout=httpx.Timeout(timeout,connect=5),trust_env=False) as client:
            if session is None:await _ready(server,config,client)
            elif not await await_blocking(server.owns_listener):raise BusinessError('ASR端口不属于本次进程',409,'MODEL_LISTENER_MISMATCH')
            if session is not None and session.requests:
                models=await client.get(server.endpoint+'/v1/models');models.raise_for_status();entries=models.json().get('data')
                if not isinstance(entries,list) or len(entries)!=1 or entries[0].get('id')!=config.model_id or entries[0].get('loaded') is not True:
                    raise BusinessError('批量识别的模型已被卸载或替换',409,'MODEL_SESSION_RESET')
                session.warm_checks+=1
            ready=time.monotonic()
            body={'model':config.model_id,'busy_timeout_ms':1000,'request':{'audio':str(normalized),'text':'',
                'language':language,'max_tokens':512}}
            if config.family=='nemotron_asr':body['request'].update(language={'English':'en-US','Chinese':'zh-CN'}[language],max_tokens=0)
            if config.family=='parakeet_tdt':
                if language!='English':raise BusinessError('当前Parakeet仅验收英语来源',422,'ASR_INPUT_INVALID')
                body['request'].update(language='en',max_tokens=0)
            if aligning:body['request']={'audio':str(normalized),'text':exact_text,'language':language,'options':{'clamp_timestamps_to_audio':False}}
            async with asyncio.timeout(timeout), client.stream('POST',server.endpoint+'/v1/tasks/run',json=body) as reply:
                data=bytearray()
                async for block in reply.aiter_bytes():
                    data.extend(block)
                    if len(data)>1024**2:raise BusinessError('ASR文本响应超限',502,'ASR_RESPONSE_INVALID')
                if reply.status_code!=200:
                    (pool.directory/'model-logs'/(pool.history_name+'.upstream-error')).write_bytes(data[:16384])
                    raise BusinessError('ASR模型未成功完成识别',502,'ASR_UPSTREAM_FAILED')
            try:
                response=json.loads(data)
                if aligning:
                    words=response['words']
                    if not isinstance(words,list) or len(words)>4096:raise ValueError()
                else:
                    text=response['text']
                    if not isinstance(text,str) or len(text)>64000:raise ValueError()
                    if config.family=='parakeet_tdt':words=checked_native_words(response.get('words',[]),text,frames)
            except (ValueError,KeyError,TypeError):
                raise BusinessError('ASR响应不是有界文本',502,'ASR_RESPONSE_INVALID') from None
            completed=time.monotonic()
            if before!=await await_blocking(file_identity,source) or prepared!=await await_blocking(file_identity,normalized):
                raise BusinessError('识别期间输入音频变化',409,'ASR_INPUT_CHANGED')
            if session is not None:session.requests+=1
    finally:
        if session is None:await await_blocking(server.stop)
    result={'rule_revision':ALIGN_RULE if aligning else PARAKEET_RULE if config.family=='parakeet_tdt' else NEMOTRON_RULE if config.family=='nemotron_asr' else RULE,'language_hint':language,
        'model_id':config.model_id,'family':config.family,'weights_sha256':config.weights_sha256,
        'runtime_sha256':config.executable_sha256,'source_revision':config.source_revision,
        'input':before,'normalized_input':prepared,'normalized_samples':frames,
        'startup_seconds':ready-started,'request_with_cold_load_seconds':completed-ready,
        'process_lifecycle_seconds':time.monotonic()-started if session is None else None,'reported_timing':response.get('timing'),
        'owned_server_exit_code':server.exit_code,'operation_stopped':pool.stopped(),
        'semantic_review':'not_measured','speaker_identity':'not_measured','naturalness':'not_measured'}
    if session is not None:result.update(model_session_id=session.id,request_seconds=completed-ready,
        scope='批量中的一个请求；进程退出证明以整批session回执为准，不能单独释放租约')
    if aligning:result.update(words=words,supplied_transcript=exact_text,clamp_timestamps_to_audio=False)
    else:result.update(text=text.strip(),context_prompt='')
    if config.family=='parakeet_tdt':result.update(words=words,word_timing_origin='native-tdt-emission-samples',word_timing_review='not_measured')
    return result


async def transcribe(config,pool,folder,source,language='Chinese',timeout=300,*,session=None):
    return await _run_audio_model(config,pool,folder,source,language,timeout,session=session)


async def align_words(config,pool,folder,source,text,language='Chinese',timeout=300,*,session=None):
    try:return await _run_audio_model(config,pool,folder,source,language,timeout,exact_text=text,session=session)
    except BusinessError as error:
        raise BusinessError(error.message.replace('ASR','词对齐'),error.status,error.code.replace('ASR_','ALIGN_')) from None
