import asyncio
from hashlib import sha256
import json
import socket
import subprocess
import sys
import wave

import pytest

from anp.errors import BusinessError
from apps.deep_dub.nodes.auditory_review import ask
from apps.deep_dub.services.audio_judge_runtime import AudioJudgeConfig,AudioJudgeSession
import apps.deep_dub.services.audio_judge_runtime as runtime
from apps.deep_dub.services.local_pool import LocalCpuPool


@pytest.mark.parametrize('mode',['success','missing_audio','cancel'])
@pytest.mark.parametrize('family',['voxtral','qwen2_5_omni'])
@pytest.mark.skipif(sys.platform!='win32',reason='本机GPU执行器当前核验Windows监听PID')
def test_owned_listener_authentication_capability_and_real_process_exit(tmp_path,monkeypatch,mode,family):
    with socket.socket() as sock:sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
    wav=tmp_path/'audio.wav'
    with wave.open(str(wav),'wb') as f:
        f.setnchannels(1);f.setsampwidth(2);f.setframerate(16000);f.writeframes(b'\0\0'*32000)
    script=tmp_path/'fake.py';marker=tmp_path/'requested'
    script.write_text('''from http.server import HTTPServer,BaseHTTPRequestHandler
import json,sys,time
from pathlib import Path
key=Path(sys.argv[3]).read_text()
class Handler(BaseHTTPRequestHandler):
 def log_message(self,*args):pass
 def send(self,status,data):
  self.send_response(status);self.send_header('Content-Type','application/json');self.end_headers();self.wfile.write(json.dumps(data).encode())
 def allowed(self):return self.headers.get('Authorization')=='Bearer '+key
 def do_GET(self):
  if not self.allowed():self.send(401,{});return
  self.send(200,{'status':'ok'} if self.path=='/health' else {'model_path':sys.argv[2],'total_slots':1,'modalities':{'audio':sys.argv[4]!='missing_audio'},'build_info':'fixture'})
 def do_POST(self):
  if not self.allowed():self.send(401,{});return
  body=json.loads(self.rfile.read(int(self.headers['Content-Length'])))
  assert body['messages'][0]['content'][0]['type']=='input_audio'
  schema=body['response_format'];assert schema['type']=='json_object'
  assert 'issues' in schema['schema']['required'] and schema['schema']['additionalProperties'] is False
  data=json.loads(body['messages'][0]['content'][1]['text'].rsplit('\\n',1)[-1]);Path(sys.argv[5]).write_text('requested')
  if sys.argv[4]=='cancel':time.sleep(60)
  self.send(200,{'model':'fixture-judge','choices':[{'finish_reason':'stop','message':{'content':json.dumps({'window_id':data['window_id'],'status':'matched','reason':'fixture only','issues':[]})}}]})
HTTPServer(('127.0.0.1',int(sys.argv[1])),Handler).serve_forever()
''',encoding='utf-8')
    config=AudioJudgeConfig(executable=sys.executable,executable_sha256='a'*64,working_directory=str(tmp_path),weights=str(tmp_path/'weights'),weights_sha256='b'*64,
        source_revision='c'*40,gpu_uuid='GPU-fixture',gpu_index=0,port=port,model_id='fixture-judge',coordination_locks=[str(tmp_path/'lock')],
        mmproj=str(tmp_path/'mmproj'),mmproj_sha256='d'*64,runtime_files={'fixture.dll':'e'*64},family=family)
    original=subprocess.Popen;processes=[]
    def launch(command,**kwargs):
        assert command[command.index('--host')+1]=='127.0.0.1'
        keyfile=command[command.index('--api-key-file')+1]
        assert '--api-key' not in command and kwargs['env']['CUDA_VISIBLE_DEVICES']=='GPU-fixture'
        assert 'LLAMA_API_KEY' not in kwargs['env']
        process=original([sys._base_executable,str(script),str(port),config.weights,keyfile,mode,str(marker)],**kwargs);processes.append(process);return process
    monkeypatch.setenv('LLAMA_API_KEY','should-not-inherit')
    monkeypatch.setattr(runtime.subprocess,'Popen',launch)
    async def scenario():
        with LocalCpuPool('audio-judge-fixture',tmp_path/'pool') as pool:
            async def work():
                async with AudioJudgeSession(config,pool,tmp_path) as session:
                    answer=await ask(session,wav,'fixture only','window')
                    assert answer['verdict']['status']=='matched'
                assert session.receipt()['operation_stopped']
                assert session.server.api_key not in json.dumps(session.receipt())
            task=asyncio.create_task(work())
            if mode=='cancel':
                for _ in range(500):
                    if marker.exists():break
                    if task.done():await task
                    await asyncio.sleep(.02)
                assert marker.exists();task.cancel()
                with pytest.raises(asyncio.CancelledError):await task
            elif mode=='missing_audio':
                with pytest.raises(BusinessError,match='音频能力'):await task
            else:await task
            assert pool.stopped() and len(pool.data['jobs'])==1
            assert all(p.poll() is not None for p in processes)
    asyncio.run(scenario())
