"""独立ASR，不以期待译稿作提示；保留真实识别文本与过程证据。"""
import asyncio
import json
from pathlib import Path
import time
import wave

import httpx

from anp.errors import BusinessError
from anp.sdk.http import async_client
from apps.deep_dub.nodes.media import _run,executable,probe_media
from apps.deep_dub.services.blocking import await_blocking,file_identity
from apps.deep_dub.services.model_pool import OwnedModelServer

RULE='qwen3-asr-owned-unprompted-v1'


async def transcribe(config,pool,folder,source,language='Chinese',timeout=300):
    if config.family!='qwen3_asr' or language not in ('Chinese','English') or not 5<=timeout<=900:
        raise BusinessError('ASR模型用途、语言或超时不合法',422,'ASR_INPUT_INVALID')
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
    server=OwnedModelServer(config,pool,folder);started=time.monotonic();ready=None
    try:
        server.start()
        async with async_client(timeout=httpx.Timeout(timeout,connect=5),trust_env=False) as client:
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
            ready=time.monotonic()
            body={'model':config.model_id,'busy_timeout_ms':1000,'request':{'audio':str(normalized),'text':'',
                'language':language,'max_tokens':512}}
            async with asyncio.timeout(timeout), client.stream('POST',server.endpoint+'/v1/tasks/run',json=body) as reply:
                data=bytearray()
                async for block in reply.aiter_bytes():
                    data.extend(block)
                    if len(data)>1024**2:raise BusinessError('ASR文本响应超限',502,'ASR_RESPONSE_INVALID')
                if reply.status_code!=200:
                    (pool.directory/'model-logs'/(pool.history_name+'.upstream-error')).write_bytes(data[:16384])
                    raise BusinessError('ASR模型未成功完成识别',502,'ASR_UPSTREAM_FAILED')
            try:
                response=json.loads(data);text=response['text']
                if not isinstance(text,str) or len(text)>64000:raise ValueError()
            except (ValueError,KeyError,TypeError):
                raise BusinessError('ASR响应不是有界文本',502,'ASR_RESPONSE_INVALID') from None
            completed=time.monotonic()
            if before!=await await_blocking(file_identity,source) or prepared!=await await_blocking(file_identity,normalized):
                raise BusinessError('识别期间输入音频变化',409,'ASR_INPUT_CHANGED')
    finally:await await_blocking(server.stop)
    return {'rule_revision':RULE,'text':text.strip(),'language_hint':language,'context_prompt':'',
        'model_id':config.model_id,'family':config.family,'weights_sha256':config.weights_sha256,
        'runtime_sha256':config.executable_sha256,'source_revision':config.source_revision,
        'input':before,'normalized_input':prepared,'normalized_samples':frames,
        'startup_seconds':ready-started,'request_with_cold_load_seconds':completed-ready,
        'process_lifecycle_seconds':time.monotonic()-started,'reported_timing':response.get('timing'),
        'owned_server_exit_code':server.exit_code,'operation_stopped':pool.stopped(),
        'semantic_review':'not_measured','speaker_identity':'not_measured','naturalness':'not_measured'}
