"""本地媒体原语；完整解码与元数据探测分开，不产生主观质量结论。"""
import asyncio
from decimal import Decimal,ROUND_HALF_UP
from fractions import Fraction
from math import ceil
import json
from pathlib import Path
import shutil
import subprocess

from anp.errors import BusinessError
from apps.deep_dub.services.local_pool import process_observer
from apps.deep_dub.services.blocking import file_identity,await_blocking


def executable(name):
    result=shutil.which(name)
    if not result:raise BusinessError('缺少媒体工具：'+name,503,'MEDIA_TOOL_UNAVAILABLE')
    return result


async def _run(command,timeout):
    # Windows 宿主为 psycopg 使用 SelectorEventLoop；通过独立通信线程管理本次子进程，
    # 不切换全局事件循环策略，也不把阻塞 communicate 放到业务事件循环。
    observer=process_observer.get()
    if observer is not None:observer.starting(command)
    try:
        process=subprocess.Popen(command,stdout=subprocess.PIPE,stderr=subprocess.PIPE,
            creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    except OSError:
        if observer is not None:observer.spawn_failed()
        raise BusinessError('媒体子进程无法启动',503,'MEDIA_TOOL_UNAVAILABLE') from None
    if observer is not None:
        try:observer.started(process.pid)
        except BaseException:
            if process.poll() is None:process.kill()
            await asyncio.to_thread(process.communicate)
            raise
    communicating=asyncio.create_task(asyncio.to_thread(process.communicate))
    try:
        stdout,_=await asyncio.wait_for(asyncio.shield(communicating),timeout)
    except (asyncio.TimeoutError,asyncio.CancelledError) as error:
        # 只结束本函数创建的子进程，不触碰其他模型/FFmpeg作业。
        if process.poll() is None:
            try:process.kill()
            except ProcessLookupError:pass
        await asyncio.shield(communicating)
        if isinstance(error,asyncio.CancelledError):raise
        raise BusinessError('媒体处理超时，当前子进程已结束',408,'MEDIA_TIMEOUT') from None
    finally:
        if process.poll() is None:
            process.kill()
            await asyncio.to_thread(process.wait)
        if observer is not None and process.poll() is not None:observer.ended(process.pid,process.returncode)
    if process.returncode:
        # 原始错误可能带本机路径或用户元数据，不直接进入公开日志/产物。
        raise BusinessError('媒体处理失败',422,'MEDIA_PROCESS_FAILED')
    return stdout


async def probe_media(path):
    file=Path(path).resolve(strict=True)
    raw=await _run([executable('ffprobe'),'-v','error','-show_format','-show_streams','-of','json',str(file)],30)
    try:
        data=json.loads(raw)
        streams=[]
        for stream in data['streams']:
            kind=stream.get('codec_type')
            if kind not in ('audio','video'):continue
            streams.append({key:stream[key] for key in ['index','codec_type','codec_name','sample_rate','channels',
                'width','height','duration','time_base','start_time','avg_frame_rate'] if key in stream})
        duration=Decimal(data['format']['duration'])
        if not duration.is_finite() or duration<=0 or not streams:raise ValueError()
        duration_ms=int((duration*1000).quantize(Decimal(1),rounding=ROUND_HALF_UP))
    except (KeyError,ValueError,TypeError,ArithmeticError):
        raise BusinessError('媒体缺少有效时长或音视频流',422,'MEDIA_PROBE_INVALID') from None
    # 不保存 format.filename、任意 tags、命令行或宿主机路径。
    return {'duration_ms':duration_ms,'streams':streams}


async def full_decode(path,timeout=600):
    file=Path(path).resolve(strict=True)
    before=await await_blocking(file_identity,file)
    probe=await probe_media(file)
    ffmpeg=executable('ffmpeg')
    version=(await _run([ffmpeg,'-version'],10)).decode('utf8',errors='replace').splitlines()[0]
    output=await _run([ffmpeg,'-v','error','-threads','1','-xerror','-err_detect','explode','-i',str(file),
        '-map','0:v?','-map','0:a?','-threads','1','-progress','pipe:1','-nostats','-f','null','-'],timeout)
    progress={}
    for line in output.decode('utf8',errors='replace').splitlines():
        if '=' in line:
            key,value=line.split('=',1);progress[key]=value
    try:
        decoded_us=int(progress['out_time_us'])
        if progress.get('progress')!='end' or decoded_us<=0:raise ValueError()
    except (KeyError,ValueError):
        raise BusinessError('未取得完整解码结束证据',422,'MEDIA_DECODE_INCOMPLETE') from None
    tail_tolerance_us=100000
    for stream in probe['streams']:
        if stream['codec_type']=='video':
            try:
                rate=Fraction(stream.get('avg_frame_rate','0/0'))
                if rate>0:tail_tolerance_us=max(tail_tolerance_us,ceil(1000000/rate))
            except (ValueError,ZeroDivisionError):pass
    if decoded_us+tail_tolerance_us<probe['duration_ms']*1000:
        raise BusinessError('解码未覆盖媒体尾部',422,'MEDIA_DECODE_INCOMPLETE')
    after=await await_blocking(file_identity,file)
    if before!=after:raise BusinessError('解码期间媒体内容发生变化',409,'MEDIA_INPUT_CHANGED')
    return {**before,**probe,'rule_key':'full_decode','method':'technical','measured':True,
            'decoded_duration_us':decoded_us,'tail_tolerance_us':tail_tolerance_us,'decoder_exit_code':0,'tool_version':version,
            'scope':'全部音视频流；字幕结构、语义、声音身份、自然度与混音质量需要另行验收'}
