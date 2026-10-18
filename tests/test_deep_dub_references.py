"""实际PCM窗口/信号统计与处理；不替代说话人身份、参考纯净度或听审。"""
from array import array
import asyncio
import math
import subprocess
import wave

import pytest

from anp.errors import BusinessError
from apps.deep_dub.nodes.references import prepare_reference,stats,RATE
from apps.deep_dub.nodes.media import executable


def tone(path,seconds=3):
    values=array('h',(int(8192*math.sin(2*math.pi*440*i/RATE)) for i in range(RATE*seconds)))
    with wave.open(str(path),'wb') as stream:
        stream.setnchannels(1);stream.setsampwidth(2);stream.setframerate(RATE);stream.writeframes(values.tobytes())


def test_raw_exact_window_and_no_fake_identity_claim(tmp_path):
    source=tmp_path/'source.wav';tone(source)
    raw,processed,proof=asyncio.run(prepare_reference(source,tmp_path,375,2375))
    assert raw==processed and proof['original']['sample_count']==2*RATE
    assert proof['original']['duration_ms']==2000 and proof['original']['channels']==1
    assert .17<proof['original']['rms_full_scale']<.18
    assert proof['original']['speaker_identity']=='not_measured' and proof['processed']['listening_quality']=='not_measured'


def test_band_filter_preserves_sample_count_and_original(tmp_path):
    source=tmp_path/'source.wav';tone(source)
    raw,processed,proof=asyncio.run(prepare_reference(source,tmp_path,0,3000,'speech-band'))
    assert raw!=processed and raw.read_bytes()!=processed.read_bytes()
    assert proof['original']['sample_count']==proof['processed']['sample_count']==3*RATE
    assert proof['original']['speaker_identity']=='not_measured'


def test_source_clock_offset_is_padded_before_trim(tmp_path):
    source=tmp_path/'offset.mkv'
    subprocess.run([executable('ffmpeg'),'-v','error','-f','lavfi','-i','color=s=64x64:r=10:d=3','-itsoffset','0.5',
        '-f','lavfi','-i','sine=frequency=440:sample_rate=24000:duration=2','-c:v','libx264','-pix_fmt','yuv420p','-c:a','pcm_s16le',str(source)],check=True,capture_output=True,timeout=20)
    raw,_,proof=asyncio.run(prepare_reference(source,tmp_path,0,2000))
    with wave.open(str(raw),'rb') as data:
        values=array('h');values.frombytes(data.readframes(12000))
    assert max(abs(n) for n in values)==0 and proof['original']['sample_count']==2*RATE


@pytest.mark.parametrize('start,end,mode',[(True,1000,'raw'),(0,0,'raw'),(-1,1000,'raw'),(0,121000,'raw'),(0,1000,'volume=99')])
def test_invalid_windows_and_arbitrary_filters_rejected(tmp_path,start,end,mode):
    source=tmp_path/'source.wav';tone(source)
    with pytest.raises(BusinessError):asyncio.run(prepare_reference(source,tmp_path,start,end,mode))


def test_silence_cannot_be_registered_as_usable_reference(tmp_path):
    source=tmp_path/'silence.wav'
    with wave.open(str(source),'wb') as data:
        data.setnchannels(1);data.setsampwidth(2);data.setframerate(RATE);data.writeframes(b'\0'*(RATE*2))
    with pytest.raises(BusinessError) as error:asyncio.run(prepare_reference(source,tmp_path,0,1000))
    assert error.value.code=='REFERENCE_SILENT'
