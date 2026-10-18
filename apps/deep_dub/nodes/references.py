"""原声参考窗口和保守频带处理；技术PCM统计不冒充角色身份或听感审核。"""
from array import array
from math import sqrt
from pathlib import Path
import sys
import wave

from anp.errors import BusinessError
from apps.deep_dub.nodes.media import _run,executable,probe_media
from apps.deep_dub.services.blocking import await_blocking,file_identity

RULE='reference-mono24k-window-v1'
RATE=24000


def stats(path):
    with wave.open(str(path),'rb') as source:
        if source.getnchannels()!=1 or source.getsampwidth()!=2 or source.getframerate()!=RATE:
            raise BusinessError('参考音频格式不是24kHz单声道PCM16',422,'REFERENCE_PCM_INVALID')
        count=0;total=0;peak=0;clipped=0
        while block:=source.readframes(RATE):
            values=array('h');values.frombytes(block)
            if sys.byteorder!='little':values.byteswap()
            count+=len(values)
            for n in values:
                total+=n*n;peak=max(peak,abs(n));clipped+=abs(n)>=32760
    if not count or count>RATE*120:raise BusinessError('参考样本数为空或超过120秒',422,'REFERENCE_PCM_INVALID')
    rms=sqrt(total/count)/32768
    if rms<1e-5:raise BusinessError('参考窗口未取得有效非静音样本',422,'REFERENCE_SILENT')
    return {'sample_rate':RATE,'channels':1,'sample_count':count,'duration_ms':round(count*1000/RATE),
        'peak_full_scale':peak/32768,'rms_full_scale':rms,'clipped_sample_ratio':clipped/count,
        'speaker_identity':'not_measured','listening_quality':'not_measured'}


async def prepare_reference(source,folder,start_ms,end_ms,filter_mode='raw',timeout=300):
    if not isinstance(start_ms,int) or isinstance(start_ms,bool) or not isinstance(end_ms,int) or isinstance(end_ms,bool) or not 0<=start_ms<end_ms or end_ms-start_ms>120000:
        raise BusinessError('原声参考窗口不合法或超过120秒',422,'REFERENCE_WINDOW_INVALID')
    if filter_mode not in ('raw','speech-band'):
        raise BusinessError('只允许已定义的参考处理策略',422,'REFERENCE_FILTER_INVALID')
    source=Path(source).resolve(strict=True);folder=Path(folder).resolve(strict=True)
    before=await await_blocking(file_identity,source);probe=await probe_media(source)
    if end_ms>probe['duration_ms'] or not any(s['codec_type']=='audio' for s in probe['streams']):
        raise BusinessError('窗口超出来源时长或来源无音频',422,'REFERENCE_WINDOW_INVALID')
    raw=folder/'original.wav';processed=folder/'processed.wav'
    if raw.exists() or processed.exists():raise BusinessError('参考目标已存在，拒绝覆盖',409,'REFERENCE_TARGET_EXISTS')
    trim=f'aresample=async=1:first_pts=0,atrim=start={start_ms/1000:.3f}:end={end_ms/1000:.3f},asetpts=PTS-STARTPTS'
    await _run([executable('ffmpeg'),'-v','error','-xerror','-err_detect','explode','-threads','1','-copyts','-start_at_zero',
        '-i',str(source),'-map','0:a:0','-vn','-af',trim,'-ac','1','-ar',str(RATE),'-c:a','pcm_s16le',str(raw)],timeout)
    original=await await_blocking(stats,raw)
    if abs(original['duration_ms']-(end_ms-start_ms))>2:
        raise BusinessError('参考PCM未覆盖准确原声窗口',422,'REFERENCE_DURATION_MISMATCH')
    if filter_mode=='speech-band':
        await _run([executable('ffmpeg'),'-v','error','-xerror','-i',str(raw),'-af','highpass=f=70,lowpass=f=10000',
            '-ac','1','-ar',str(RATE),'-c:a','pcm_s16le',str(processed)],timeout)
        cleaned=await await_blocking(stats,processed)
        if cleaned['sample_count']!=original['sample_count']:
            raise BusinessError('参考处理改变了样本数',422,'REFERENCE_DURATION_MISMATCH')
    else:processed=raw;cleaned=original
    if before!=await await_blocking(file_identity,source):
        raise BusinessError('提取期间原片发生变化',409,'MEDIA_INPUT_CHANGED')
    version=(await _run([executable('ffmpeg'),'-version'],10)).decode(errors='replace').splitlines()[0]
    return raw,processed,{'rule_revision':RULE,'source_sha256':before['sha256'],'source_bytes':before['bytes'],
        'start_ms':start_ms,'end_ms':end_ms,'filter_mode':filter_mode,'original':original,'processed':cleaned,
        'tool_version':version,'scope':'准确窗口、PCM格式和信号统计；非分离/身份/自然度/情绪有效性通过'}
