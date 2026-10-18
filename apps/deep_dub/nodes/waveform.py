"""真实 PCM 峰值；固定振幅标尺，不把响度归一化或波形当作质量评分。"""
from array import array
from pathlib import Path
from math import isfinite
import sys
from tempfile import TemporaryDirectory

from anp.errors import BusinessError
from apps.deep_dub.nodes.media import _run,executable,probe_media
from apps.deep_dub.services.blocking import await_blocking,file_identity

RULE='pcm-mono16k-100hz-max-v1'
RATE=16000
BIN=160
MAX_SECONDS=10800


def reduce_pcm(path):
    peaks=[];samples=0
    with Path(path).open('rb') as source:
        while block:=source.read(BIN*2):
            if len(block)%2:raise BusinessError('PCM 末尾不是完整样本',422,'WAVEFORM_DECODE_INVALID')
            values=array('h');values.frombytes(block)
            if sys.byteorder!='little':values.byteswap()
            samples+=len(values)
            if samples>MAX_SECONDS*RATE:raise BusinessError('波形超出三小时上限',413,'WAVEFORM_TOO_LARGE')
            peaks.append(round(max(abs(x) for x in values)/32768,6))
    if not samples:raise BusinessError('没有可解码的音频样本',422,'WAVEFORM_NO_AUDIO')
    return {'rule_revision':RULE,'sample_rate':RATE,'samples_per_peak':BIN,'sample_count':samples,
            'duration_seconds':samples/RATE,'peaks':peaks,'amplitude_scale':'absolute-full-scale',
            'quality_evaluated':False}


async def waveform(path,timeout=600):
    file=Path(path).resolve(strict=True)
    before=await await_blocking(file_identity,file)
    probe=await probe_media(file)
    if not any(s['codec_type']=='audio' for s in probe['streams']):
        raise BusinessError('媒体没有音频轨，不生成虚构波形',422,'WAVEFORM_NO_AUDIO')
    if probe['duration_ms']>MAX_SECONDS*1000:
        raise BusinessError('媒体超出三小时波形上限',413,'WAVEFORM_TOO_LARGE')
    with TemporaryDirectory(prefix='anp-waveform-pcm-') as folder:
        pcm=Path(folder)/'audio.pcm'
        limit=MAX_SECONDS*RATE*2+2
        progress=await _run([executable('ffmpeg'),'-v','error','-xerror','-err_detect','explode','-threads','1',
            '-copyts','-start_at_zero','-i',str(file),'-map','0:a:0','-vn','-threads','1',
            '-af','aresample=async=1:first_pts=0','-ac','1','-ar',str(RATE),'-c:a','pcm_s16le',
            '-fs',str(limit),'-progress','pipe:1','-nostats','-f','s16le',str(pcm)],timeout)
        if b'progress=end' not in progress or pcm.stat().st_size>=limit:
            raise BusinessError('波形解码未完整结束',422,'WAVEFORM_DECODE_INVALID')
        result=await await_blocking(reduce_pcm,pcm)
    audio=next(s for s in probe['streams'] if s['codec_type']=='audio')
    try:declared=float(audio.get('duration',0))
    except (TypeError,ValueError):declared=0
    if isfinite(declared) and declared>0 and result['duration_seconds']+.1<declared:
        raise BusinessError('波形样本未覆盖音频声明时长',422,'WAVEFORM_DECODE_INVALID')
    if before!=await await_blocking(file_identity,file):
        raise BusinessError('生成波形期间输入发生变化',409,'MEDIA_INPUT_CHANGED')
    return {**result,'input_sha256':before['sha256'],'input_bytes':before['bytes'],
            'stream_index':audio['index']}
