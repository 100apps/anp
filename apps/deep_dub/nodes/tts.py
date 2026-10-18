"""audio.cpp HTTP配音请求；本机受控进程的退出才是取消/成功释放设备的边界。"""
import asyncio
import base64
import binascii
from io import BytesIO
import json
from pathlib import Path
import time
import wave

import httpx

from anp.errors import BusinessError
from anp.sdk.http import async_client
from apps.deep_dub.services.blocking import await_blocking,file_identity
from apps.deep_dub.services.model_pool import OwnedModelServer

RULE='index-tts2-5-owned-http-v1'


def validate_audio(data,sample_rate,channels):
    if not isinstance(data,bytes) or not 44<len(data)<=32*1024**2:
        raise BusinessError('模型音频响应为空或超限',502,'TTS_RESPONSE_INVALID')
    try:
        with wave.open(BytesIO(data),'rb') as audio:
            frames=audio.getnframes();rate=audio.getframerate();count=audio.getnchannels()
            if rate!=sample_rate or count!=channels or rate not in (22050,24000) or count!=1 or audio.getsampwidth()!=2 or not 0<frames<=rate*120:
                raise ValueError()
            samples=audio.readframes(frames)
            if len(samples)!=frames*2 or not any(samples):raise ValueError()
    except (wave.Error,EOFError,ValueError):
        raise BusinessError('模型WAV格式、时长或实际样本不合法',502,'TTS_RESPONSE_INVALID') from None
    return {'sample_rate':rate,'channels':count,'sample_count':frames,'duration_ms':round(frames*1000/rate)}


async def synthesize(config,pool,folder,text,voice,emotion,generation_factor=1.,timeout=300,seed=42):
    if not isinstance(text,str) or not text.strip() or len(text)>2000:
        raise BusinessError('配音文本为空或超过2000字，应先合理分段',422,'TTS_INPUT_INVALID')
    if not .90<=generation_factor<=1.35 or not 5<=timeout<=900:
        raise BusinessError('配音倍率或超时不合法',422,'TTS_INPUT_INVALID')
    folder=Path(folder);voice=Path(voice).resolve(strict=True);emotion=Path(emotion).resolve(strict=True)
    initial=await await_blocking(lambda:[file_identity(voice),file_identity(emotion)])
    output=folder/'generated.wav'
    if output.exists():raise BusinessError('配音输出已存在，拒绝覆盖',409,'TTS_TARGET_EXISTS')
    parameters={'model_id':config.model_id,'model_family':'index_tts2','model_variant':'2.5',
        'weights_sha256':config.weights_sha256,'runtime_sha256':config.executable_sha256,'source_revision':config.source_revision,
        'rule_revision':RULE,'backend':'cuda','generation_factor':generation_factor,'do_sample':False,'num_beams':3,
        'max_tokens':1500,'seed':seed,'emotion_alpha':1,'duration_factor':1/generation_factor,
        'quality_scope':'候选生成，非独审/音色身份/自然度通过'}
    body={'model':config.model_id,'busy_timeout_ms':1000,'request':{'text':text,'language':'zh','voice_ref':str(voice),
        'audio':str(emotion),'seed':seed,'max_tokens':1500,'do_sample':False,'num_beams':3,
        'options':{'duration_factor':1/generation_factor,'emotion_alpha':1}}}
    server=OwnedModelServer(config,pool,folder);started=time.monotonic();ready=None;request_started=None;response=None
    try:
        server.start()
        async with async_client(timeout=httpx.Timeout(timeout,connect=5),trust_env=False) as client:
            deadline=time.monotonic()+30
            while True:
                if server.process.poll() is not None:raise BusinessError('本次模型进程启动后退出',503,'MODEL_START_FAILED')
                try:
                    health=await client.get(server.endpoint+'/health',timeout=2)
                    if health.status_code==200:break
                except httpx.TransportError:pass
                if time.monotonic()>deadline:raise BusinessError('模型服务启动超时',504,'MODEL_START_TIMEOUT')
                await asyncio.sleep(.2)
            if not await await_blocking(server.owns_listener):raise BusinessError('端口不是本次模型进程持有',409,'MODEL_LISTENER_MISMATCH')
            models=await client.get(server.endpoint+'/v1/models');models.raise_for_status()
            entries=models.json().get('data')
            if not isinstance(entries,list) or len(entries)!=1 or entries[0].get('id')!=config.model_id or entries[0].get('loaded') is not False:
                raise BusinessError('新进程模型清单或惰性状态不符',409,'MODEL_IDENTITY_MISMATCH')
            ready=time.monotonic();request_started=ready
            async with asyncio.timeout(timeout), client.stream('POST',server.endpoint+'/v1/tasks/run',json=body) as reply:
                if reply.status_code!=200:
                    diagnostic=bytearray()
                    async for chunk in reply.aiter_bytes():
                        diagnostic.extend(chunk[:max(0,16384-len(diagnostic))])
                        if len(diagnostic)>=16384:break
                    (pool.directory/'model-logs'/(pool.history_name+'.upstream-error')).write_bytes(diagnostic)
                    raise BusinessError('模型服务未成功完成配音请求',502,'TTS_UPSTREAM_FAILED')
                data=bytearray()
                async for chunk in reply.aiter_bytes():
                    data.extend(chunk)
                    if len(data)>48*1024**2:raise BusinessError('模型JSON响应超限',502,'TTS_RESPONSE_INVALID')
                try:
                    response=json.loads(data);encoded=response['audio']
                    if not isinstance(encoded,str):raise ValueError()
                    raw=base64.b64decode(encoded,validate=True)
                    (pool.directory/'model-logs'/(pool.history_name+'.metadata.json')).write_bytes(json.dumps({
                        'sample_rate':response.get('sample_rate'),'channels':response.get('channels'),
                        'timing':response.get('timing'),'wav_bytes':len(raw)}).encode())
                    stats=validate_audio(raw,response['sample_rate'],response['channels'])
                except (ValueError,KeyError,TypeError,binascii.Error):
                    raise BusinessError('模型响应缺少合法WAV或元数据',502,'TTS_RESPONSE_INVALID') from None
            generated_at=time.monotonic()
            if initial!=await await_blocking(lambda:[file_identity(voice),file_identity(emotion)]):
                raise BusinessError('模型生成期间参考发生变化',409,'TTS_REFERENCE_CHANGED')
            output.write_bytes(raw)
    finally:
        # 即使客户端断开/租约失效，也持有句柄并等待真正退出后才允许上层释放GPU。
        await await_blocking(server.stop)
    proof={'rule_revision':RULE,'parameters':parameters,'reference_identities':initial,'audio':stats,
        'owned_server_exit_code':server.exit_code,'operation_stopped':pool.stopped(),
        'startup_seconds':ready-started,'request_with_cold_load_seconds':generated_at-request_started,
        'process_lifecycle_seconds':time.monotonic()-started,'reported_timing':response.get('timing'),
        'semantic_consistency':'not_measured','speaker_identity':'not_measured','naturalness':'not_measured'}
    return output,proof
