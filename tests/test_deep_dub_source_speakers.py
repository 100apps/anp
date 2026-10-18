import asyncio
from copy import deepcopy
import json
from pathlib import Path
import socket
import subprocess
import sys
import wave

import pytest

from anp.errors import BusinessError
from apps.deep_dub.nodes.source_speakers import RULE, OPTIONS, SESSION_OPTIONS, analyze, summarize, verify
from apps.deep_dub.services.local_pool import LocalCpuPool
from apps.deep_dub.services.model_pool import ModelConfig
import apps.deep_dub.services.model_pool as models
from tests.test_deep_dub_source_alignment import recognition


def turn(start,end,speaker='speaker_0'):
    return {'start_sample':start,'end_sample':end,'speaker_id':speaker,'confidence':1.0}


def test_union_overlap_preserves_raw_bad_timestamps_and_does_not_count_same_speaker_twice():
    raw=[turn(0,1000),turn(500,1500),turn(700,1200,'speaker_1'),turn(-10,100,'speaker_2'),turn(2000,4001)]
    before=deepcopy(raw);measured=summarize(raw,{'sample_count':4000,'sample_rate':16000})
    assert measured['active_samples']==1500 and measured['multi_speaker_overlap_samples']==500
    assert measured['invalid_turn_indexes']==[3,4] and not measured['valid_timestamps']
    assert measured['speaker_count']==3 and raw==before
    empty=summarize([],{'sample_count':4000,'sample_rate':16000})
    assert empty['active_samples']==empty['speaker_count']==0


@pytest.mark.parametrize('change',[{'start_sample':True},{'end_sample':1.2},{'speaker_id':'speaker_8'},
    {'confidence':float('nan')},{'confidence':True},{'confidence':1.1},{'text':'unexpected'}])
def test_raw_schema_and_confidence_contract(change):
    with pytest.raises(BusinessError):summarize([{**turn(0,1),**change}],{'sample_rate':16000,'sample_count':1000})


@pytest.mark.parametrize('mode',['success','empty','bad_time','invalid','upstream_error','cancel','changed'])
@pytest.mark.skipif(sys.platform!='win32',reason='受控执行器当前核验Windows监听PID')
def test_actual_http_one_whole_pcm_request_and_owned_exit(tmp_path,monkeypatch,mode):
    with socket.socket() as sock:sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
    source=tmp_path/'source.wav'
    with wave.open(str(source),'wb') as target:
        target.setnchannels(1);target.setsampwidth(2);target.setframerate(16000);target.writeframes(b'\0\0'*32000)
    primary=recognition(source,tmp_path)
    config=ModelConfig(executable=sys.executable,executable_sha256='a'*64,working_directory=str(tmp_path),weights=str(tmp_path/'weights'),
        weights_sha256='d'*64,source_revision='e'*40,gpu_uuid='GPU-fixture',gpu_index=0,family='nemotron_3_diar',model_id='fixture-diar',port=port,coordination_locks=[str(tmp_path/'lock')])
    marker=tmp_path/'request.json';script=tmp_path/'fixture.py'
    script.write_text('''from http.server import HTTPServer,BaseHTTPRequestHandler
import json,sys,time
from pathlib import Path
class Handler(BaseHTTPRequestHandler):
 def log_message(self,*args):pass
 def send(self,status,data):
  self.send_response(status);self.send_header('Content-Type','application/json');self.end_headers();self.wfile.write(json.dumps(data).encode())
 def do_GET(self):self.send(200,{'status':'ok'} if self.path=='/health' else {'data':[{'id':'fixture-diar','loaded':False}]})
 def do_POST(self):
  body=json.loads(self.rfile.read(int(self.headers['Content-Length'])))
  Path(sys.argv[3]).write_text(json.dumps(body))
  mode=sys.argv[2]
  if mode=='cancel':time.sleep(60)
  if mode=='changed':
   with open(body['request']['audio'],'ab') as target:target.write(b'x')
  data={'timing':{},'speaker_turns':[{'start_sample':-10 if mode=='bad_time' else 0,'end_sample':16000,'speaker_id':'speaker_0','confidence':1.0}]}
  if mode=='empty':data.pop('speaker_turns')
  if mode=='invalid':data['speaker_turns'][0]['speaker_id']='someone-else'
  self.send(500 if mode=='upstream_error' else 200,data)
HTTPServer(('127.0.0.1',int(sys.argv[1])),Handler).serve_forever()
''',encoding='utf-8')
    def start(server):
        settings=Path(server.folder)/'server.json'
        # 使用真实配置生成器，然后仅将Popen替换为受本测试管理的HTTP进程。
        original_popen=models.subprocess.Popen
        def launch(*args,**kwargs):
            generated=json.loads(settings.read_bytes())
            assert generated['models'][0]['task']=='diar'
            assert generated['models'][0]['session_options']==SESSION_OPTIONS
            return original_popen([sys._base_executable,str(script),str(port),mode,str(marker)],**kwargs)
        with monkeypatch.context() as scoped:
            scoped.setattr(models.subprocess,'Popen',launch)
            original_start(server)
    original_start=models.OwnedModelServer.start
    monkeypatch.setattr(models.OwnedModelServer,'start',start)
    async def scenario():
        with LocalCpuPool('fixture-'+mode,tmp_path/'pool') as pool:
            task=asyncio.create_task(analyze(config,pool,tmp_path,source,primary,10))
            if mode=='cancel':
                for _ in range(300):
                    if marker.exists():break
                    if task.done():await task
                    await asyncio.sleep(.02)
                assert marker.exists();task.cancel()
                with pytest.raises(asyncio.CancelledError):await task
            elif mode in ('invalid','upstream_error','changed'):
                with pytest.raises(BusinessError):await task
            else:
                result=await task
                assert result['summary']['turn_count']==(0 if mode=='empty' else 1)
                assert result['summary']['invalid_turn_indexes']==([0] if mode=='bad_time' else [])
                assert result['submitted_samples']==32000 and result['session']['requests']==1
                assert result['speaker_identity']==result['subtitle_timing_review']=='not_measured'
                assert verify(result,primary)==result['summary']
                corrupted=deepcopy(result);corrupted['speaker_identity']='passed'
                with pytest.raises(BusinessError):verify(corrupted,primary)
            body=json.loads(marker.read_bytes())
            assert body['request']=={'audio':str(source.resolve()),'options':OPTIONS}
            assert pool.stopped() and len(pool.data['jobs'])==1 and pool.data['jobs'][0]['exit_code'] is not None
    asyncio.run(scenario())
