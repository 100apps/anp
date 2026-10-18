"""实际FFmpeg与有界PCM峰值：验证时间窗口、振幅、静音和无声失败，不代表TTS质量。"""
from array import array
import asyncio
import json
import subprocess
import wave

import pytest

from anp.errors import BusinessError
from apps.deep_dub.nodes.media import executable
from apps.deep_dub.nodes.waveform import waveform,reduce_pcm,RATE,BIN,RULE
from apps.deep_dub.services.waveforms import window,validate


def test_real_waveform_absolute_scale_and_silence(tmp_path):
    path=tmp_path/'fixture.wav'
    values=array('h',[0]*RATE+[16384,-16384]*(RATE//2))
    with wave.open(str(path),'wb') as target:
        target.setnchannels(1);target.setsampwidth(2);target.setframerate(RATE);target.writeframes(values.tobytes())
    data=asyncio.run(waveform(path))
    assert data['sample_count']==RATE*2 and data['duration_seconds']==2
    assert len(data['peaks'])==200 and data['peaks'][:100]==[0]*100 and data['peaks'][100:]==[.5]*100
    assert data['quality_evaluated'] is False and data['amplitude_scale']=='absolute-full-scale'
    result=window(data,1,2,16)
    assert result['durationSec']==1 and result['peaks']==[.5]*16
    item={'id':'fixture','sha256':data['input_sha256'],'bytes':data['input_bytes']}
    data['artifact_id']='fixture';validate(data,item)
    for field,value in [('input_sha256','0'*64),('sample_count',True),('peaks',[float('nan')]*200),('quality_evaluated',True)]:
        with pytest.raises(BusinessError):validate({**data,field:value},item)


def test_waveform_keeps_audio_start_offset(tmp_path):
    path=tmp_path/'offset.mkv'
    subprocess.run([executable('ffmpeg'),'-v','error','-f','lavfi','-i','color=s=64x64:r=10:d=2',
        '-itsoffset','0.5','-f','lavfi','-i','sine=frequency=440:sample_rate=16000:duration=1',
        '-c:v','libx264','-pix_fmt','yuv420p','-c:a','pcm_s16le',str(path)],check=True,capture_output=True,timeout=20)
    data=asyncio.run(waveform(path))
    assert data['peaks'][:49]==[0]*49
    assert max(data['peaks'][51:140])>.1 and 1.49<=data['duration_seconds']<=1.51


def test_no_audio_does_not_create_fake_waveform(tmp_path):
    path=tmp_path/'silent.mp4'
    subprocess.run([executable('ffmpeg'),'-v','error','-f','lavfi','-i','color=s=64x64:r=10:d=1',
        '-an','-c:v','libx264','-pix_fmt','yuv420p',str(path)],check=True,capture_output=True,timeout=20)
    with pytest.raises(BusinessError) as error:asyncio.run(waveform(path))
    assert error.value.code=='WAVEFORM_NO_AUDIO'


@pytest.mark.parametrize('start,end,points',[(0,3,16),(1,1,16),(-1,1,16),(0,2,2),(0,2,True),(float('nan'),2,16)])
def test_waveform_window_rejects_out_of_bounds(start,end,points):
    with pytest.raises(BusinessError):window({'duration_seconds':2,'peaks':[.25]*200},start,end,points)


def test_incomplete_pcm_and_partial_last_bin(tmp_path):
    path=tmp_path/'raw.pcm';path.write_bytes(array('h',[-32768]*161).tobytes())
    data=reduce_pcm(path);assert data['peaks']==[1,1] and data['sample_count']==161
    path.write_bytes(b'\0')
    with pytest.raises(BusinessError):reduce_pcm(path)
