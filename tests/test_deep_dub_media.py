"""实际FFmpeg解码小型合成夹具；不算整片生产、设备租约或质量通过。"""
import asyncio
from pathlib import Path
import shutil
import subprocess
import sys
import json
from datetime import datetime,timezone

import pytest

from anp.errors import BusinessError
from apps.deep_dub.nodes.media import full_decode,probe_media,_run


@pytest.fixture
def movie(tmp_path):
    ffmpeg=shutil.which('ffmpeg')
    assert ffmpeg,'媒体原语验收需要真实ffmpeg'
    file=tmp_path/'fixture.mp4'
    subprocess.run([ffmpeg,'-v','error','-f','lavfi','-i','color=c=gray:s=64x64:r=10:d=2',
        '-f','lavfi','-i','anullsrc=r=24000:cl=mono','-t','2','-c:v','mpeg4','-c:a','aac','-shortest','-movflags','+faststart',str(file)],
        check=True,capture_output=True,timeout=20)
    return file


def test_full_decode_really_decodes_all_av_streams_without_exporting_paths(movie):
    result=asyncio.run(full_decode(movie))
    assert result['decoder_exit_code']==0 and result['measured']
    assert {s['codec_type'] for s in result['streams']}=={'audio','video'}
    assert 1900<=result['duration_ms']<=2100 and result['decoded_duration_us']>=1900000
    assert result['bytes']==movie.stat().st_size and len(result['sha256'])==64
    assert str(movie) not in str(result)
    Path('.local/deep-dub-full-decode-tool.json').write_bytes((json.dumps({
        'checked_at_utc':datetime.now(timezone.utc).isoformat(),
        'scope':'独立媒体原语实测：2秒合成视频+静音音轨，不是业务设备/整片生产或质量验收',
        'result':result},ensure_ascii=False,indent=2)+'\n').encode())


def test_truncated_file_cannot_pass_as_successful_decode(movie):
    movie.write_bytes(movie.read_bytes()[:100])
    with pytest.raises(BusinessError) as error:asyncio.run(full_decode(movie))
    assert error.value.code in ('MEDIA_PROCESS_FAILED','MEDIA_PROBE_INVALID','MEDIA_DECODE_INCOMPLETE')


def test_valid_duration_metadata_does_not_hide_truncated_packets(movie):
    data=movie.read_bytes();mdat=data.index(b'mdat')+4
    movie.write_bytes(data[:mdat+(len(data)-mdat)//2])
    # faststart 的 moov 元数据仍完整，ffprobe 能正常返回原时长；真实解码必须失败。
    assert asyncio.run(probe_media(movie))['duration_ms']==2000
    with pytest.raises(BusinessError) as error:asyncio.run(full_decode(movie))
    assert error.value.code in ('MEDIA_PROCESS_FAILED','MEDIA_DECODE_INCOMPLETE')


def test_timeout_ends_only_our_created_process(monkeypatch):
    original=subprocess.Popen;created=[]
    def capture(*args,**kwargs):
        process=original(*args,**kwargs);created.append(process);return process
    monkeypatch.setattr(subprocess,'Popen',capture)
    with pytest.raises(BusinessError) as error:
        asyncio.run(_run([sys.executable,'-c','import time;time.sleep(30)'],.1))
    assert error.value.code=='MEDIA_TIMEOUT' and len(created)==1 and created[0].returncode is not None


def test_cancellation_reaps_child_and_leaves_other_process_running(monkeypatch):
    original=subprocess.Popen;created=[]
    unrelated=original([sys.executable,'-c','import time;time.sleep(30)'],stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    def capture(*args,**kwargs):
        process=original(*args,**kwargs);created.append(process);return process
    monkeypatch.setattr(subprocess,'Popen',capture)
    async def run():
        task=asyncio.create_task(_run([sys.executable,'-c','import time;time.sleep(30)'],20))
        for _ in range(100):
            if created:break
            await asyncio.sleep(.01)
        assert created
        task.cancel()
        with pytest.raises(asyncio.CancelledError):await task
    try:
        asyncio.run(run())
        assert len(created)==1 and created[0].poll() is not None and unrelated.poll() is None
    finally:
        if unrelated.poll() is None:unrelated.kill()
        unrelated.wait(timeout=10)
