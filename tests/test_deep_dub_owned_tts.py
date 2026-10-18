"""实际子进程/HTTP生命周期夹具；不宣称模型推理或GPU性能通过。"""
from array import array
import asyncio
import base64
from io import BytesIO
import json
from pathlib import Path
import socket
import subprocess
import sys
import wave

import pytest

from anp.errors import BusinessError
from apps.deep_dub.nodes.tts import synthesize,validate_audio
from apps.deep_dub.services.local_pool import LocalCpuPool
from apps.deep_dub.services.model_pool import ModelConfig,FileLock
import apps.deep_dub.services.model_pool as models


def wav():
    target=BytesIO()
    with wave.open(target,'wb') as stream:
        stream.setnchannels(1);stream.setsampwidth(2);stream.setframerate(24000)
        stream.writeframes(array('h',[1000,-1000]*1200).tobytes())
    return target.getvalue()


def config(tmp_path):
    with socket.socket() as probe:probe.bind(('127.0.0.1',0));port=probe.getsockname()[1]
    return ModelConfig(executable=sys.executable,executable_sha256='a'*64,working_directory=str(tmp_path),
        weights=str(tmp_path/'weights'),weights_sha256='b'*64,source_revision='c'*40,
        gpu_uuid='GPU-fixture',gpu_index=0,port=port,coordination_locks=[str(tmp_path/'legacy.lock')])


def test_wav_metadata_truncation_and_silence_rejected():
    raw=wav();assert validate_audio(raw,24000,1)['sample_count']==2400
    for data,rate,channels in [(raw,16000,1),(raw,24000,2),(raw[:-2],24000,1),(b'not wav',24000,1)]:
        with pytest.raises(BusinessError):validate_audio(data,rate,channels)
    empty=BytesIO()
    with wave.open(empty,'wb') as stream:
        stream.setnchannels(1);stream.setsampwidth(2);stream.setframerate(24000);stream.writeframes(b'\0'*4800)
    with pytest.raises(BusinessError):validate_audio(empty.getvalue(),24000,1)
    actual=BytesIO()
    with wave.open(actual,'wb') as stream:
        stream.setnchannels(1);stream.setsampwidth(2);stream.setframerate(22050);stream.writeframes(array('h',[1000,-1000]*2205).tobytes())
    assert validate_audio(actual.getvalue(),22050,1)['duration_ms']==200


@pytest.mark.skipif(sys.platform!='win32',reason='Windows NVIDIA探测')
def test_gpu_inventory_rejects_unknown_work_and_only_allows_configured_desktop_path(tmp_path,monkeypatch):
    c=config(tmp_path);c.allowed_graphics_executables=[str(tmp_path/'desktop.exe')]
    def output(command):
        return '0, GPU-fixture, 12000\n' if 'index,uuid,memory.free' in command[1] else f'GPU-fixture, 123, {tmp_path / "desktop.exe"}\n'
    monkeypatch.setattr(models,'command_output',output)
    assert models.inventory(c)['allowed_desktop_processes']==1
    c.allowed_graphics_executables=[]
    with pytest.raises(BusinessError) as occupied:models.inventory(c)
    assert occupied.value.code=='MODEL_GPU_OCCUPIED'


def test_wrong_model_hash_cannot_be_silently_accepted(tmp_path):
    with pytest.raises(BusinessError) as result:config(tmp_path).verify_files()
    assert result.value.code=='MODEL_IDENTITY_MISMATCH'


def test_legacy_router_lock_is_exclusive_without_deleting_file(tmp_path):
    path=tmp_path/'legacy.lock'
    with FileLock(path):
        with pytest.raises(BusinessError) as result:
            with FileLock(path):pass
        assert result.value.code=='MODEL_POOL_BUSY'
    assert path.exists()
    with FileLock(path):pass


@pytest.mark.parametrize('mode',['success','upstream_error','cancel'])
def test_actual_http_process_exit_and_cancel_keep_unrelated_process(tmp_path,monkeypatch,mode):
    c=config(tmp_path);raw=wav();voice=tmp_path/'voice.wav';voice.write_bytes(raw)
    emotion=tmp_path/'emotion.wav';emotion.write_bytes(raw)
    script=tmp_path/'fixture.py';marker=tmp_path/'request-started'
    script.write_text('''from http.server import HTTPServer,BaseHTTPRequestHandler
import base64,json,sys,time
from pathlib import Path
class Handler(BaseHTTPRequestHandler):
 def log_message(self,*args):pass
 def send(self,status,data):
  self.send_response(status);self.send_header('Content-Type','application/json');self.end_headers();self.wfile.write(json.dumps(data).encode())
 def do_GET(self):
  self.send(200,{'status':'ok'} if self.path=='/health' else {'data':[{'id':'anp-index-tts2-5-q8','loaded':False}]})
 def do_POST(self):
  body=json.loads(self.rfile.read(int(self.headers['Content-Length'])))
  assert body['request']['audio']!=body['request']['voice_ref']
  Path(sys.argv[3]).write_text('started')
  if sys.argv[2]=='cancel':time.sleep(60)
  self.send(500 if sys.argv[2]=='upstream_error' else 200,{'audio':sys.argv[4],'sample_rate':24000,'channels':1,'timing':{'wall_ms':1}})
HTTPServer(('127.0.0.1',int(sys.argv[1])),Handler).serve_forever()
''',encoding='utf-8')
    original=subprocess.Popen
    unrelated=original([sys.executable,'-c','import time;time.sleep(60)'],creationflags=subprocess.CREATE_NO_WINDOW if sys.platform=='win32' else 0)
    def launch(command,**kwargs):
        return original([sys.executable,str(script),str(c.port),mode,str(marker),base64.b64encode(raw).decode()],**kwargs)
    monkeypatch.setattr(models.subprocess,'Popen',launch)
    # 此夹具独立验证生命周期，端口归属由真实GPU集成再验；不伪装成真实模型检查。
    monkeypatch.setattr(models.OwnedModelServer,'owns_listener',lambda self:self.process.poll() is None)
    async def run():
        with LocalCpuPool('fixture-'+mode,tmp_path/'pool') as pool:
            operation=asyncio.create_task(synthesize(c,pool,tmp_path,'测试文本',voice,emotion,timeout=10))
            if mode=='cancel':
                for _ in range(300):
                    if marker.exists():break
                    if operation.done():await operation
                    await asyncio.sleep(.02)
                assert marker.exists();operation.cancel()
                with pytest.raises(asyncio.CancelledError):await operation
            elif mode=='upstream_error':
                with pytest.raises(BusinessError) as result:await operation
                assert result.value.code=='TTS_UPSTREAM_FAILED'
            else:
                output,proof=await operation
                assert output.read_bytes()==raw and proof['audio']['sample_count']==2400
                assert proof['naturalness']=='not_measured' and proof['operation_stopped'] is True
            assert pool.stopped() and len(pool.data['jobs'])==1 and pool.data['jobs'][0]['exit_code'] is not None
            assert unrelated.poll() is None
    try:asyncio.run(run())
    finally:
        unrelated.terminate();unrelated.wait(timeout=10)
