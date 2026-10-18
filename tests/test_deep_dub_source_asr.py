"""连续样本覆盖及同一受控HTTP子进程；夹具文本不作为真实ASR证据。"""
from array import array
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
from apps.deep_dub.nodes.source_asr import windows,pcm_info,checked_coverage,recognize_source
from apps.deep_dub.services.local_pool import LocalCpuPool
from apps.deep_dub.services.model_pool import ModelConfig
import apps.deep_dub.services.model_pool as models


def test_contiguous_windows_cover_exact_samples_and_merge_sub_100ms_tail():
    for count in [1600,16000*60,16000*60+1,16000*60+1600,16000*14400]:
        parts=windows(count,60)
        assert parts[0][0]==0 and parts[-1][1]==count
        assert all(a<b for a,b in parts) and all(parts[i][1]==parts[i+1][0] for i in range(len(parts)-1))
        assert sum(b-a for a,b in parts)==count
    assert windows(960001,60)==[(0,960001)]
    for sample,seconds in [(True,60),(0,60),(16000*14400+1,60),(16000,True),(16000,29),(16000,91)]:
        with pytest.raises(BusinessError):windows(sample,seconds)


@pytest.mark.parametrize('mode',['success','cancel','second_error','cache_reset'])
def test_source_chunks_share_one_owned_server_and_stop_after_second_request(tmp_path,monkeypatch,mode):
    with socket.socket() as probe:probe.bind(('127.0.0.1',0));port=probe.getsockname()[1]
    config=ModelConfig(executable=sys.executable,executable_sha256='a'*64,working_directory=str(tmp_path),
        weights=str(tmp_path/'weights'),weights_sha256='b'*64,source_revision='c'*40,gpu_uuid='GPU-fixture',gpu_index=0,
        family='qwen3_asr',model_id='fixture-asr',port=port,coordination_locks=[str(tmp_path/'lock')])
    source=tmp_path/'source.wav'
    with wave.open(str(source),'wb') as output:
        output.setnchannels(1);output.setsampwidth(2);output.setframerate(16000)
        output.writeframes(array('h',[1000,-1000]*16000*31).tobytes())
    marker=tmp_path/'requests.json';script=tmp_path/'fixture.py'
    script.write_text('''from http.server import HTTPServer,BaseHTTPRequestHandler
import json,sys,time
from pathlib import Path
requests=[]
class Handler(BaseHTTPRequestHandler):
 def log_message(self,*args):pass
 def send(self,status,body):
  self.send_response(status);self.send_header('Content-Type','application/json');self.end_headers();self.wfile.write(json.dumps(body).encode())
 def do_GET(self):self.send(200,{'status':'ok'} if self.path=='/health' else {'data':[{'id':'fixture-asr','loaded':bool(requests) and sys.argv[2]!='cache_reset'}]})
 def do_POST(self):
  body=json.loads(self.rfile.read(int(self.headers['Content-Length'])))
  assert body['request']['text']=='' and body['request']['language']=='English'
  requests.append(body['request']['audio']);target=Path(sys.argv[3]);target.with_suffix('.tmp').write_text(json.dumps(requests));target.with_suffix('.tmp').replace(target)
  if len(requests)==2 and sys.argv[2]=='cancel':time.sleep(60)
  self.send(500 if len(requests)==2 and sys.argv[2]=='second_error' else 200,{'text':'fixture chunk '+str(len(requests))})
HTTPServer(('127.0.0.1',int(sys.argv[1])),Handler).serve_forever()
''',encoding='utf-8')
    original=subprocess.Popen;started=[]
    def launch(command,**kwargs):
        if len(command)>2 and command[0]==sys.executable and command[1]=='--config':
            process=original([sys.executable,str(script),str(port),mode,str(marker)],**kwargs);started.append(process);return process
        return original(command,**kwargs)
    monkeypatch.setattr(models.subprocess,'Popen',launch)
    monkeypatch.setattr(models.OwnedModelServer,'owns_listener',lambda self:self.process is not None and self.process.poll() is None)
    async def run():
        folder=tmp_path/'job';folder.mkdir()
        with LocalCpuPool('source-asr-'+mode,tmp_path/'pool') as pool:
            pending=asyncio.create_task(recognize_source(config,pool,folder,source,chunk_seconds=30,timeout=10))
            if mode=='cancel':
                for _ in range(400):
                    if marker.exists() and len(json.loads(marker.read_bytes()))==2:break
                    if pending.done():await pending
                    await asyncio.sleep(.02)
                assert marker.exists() and len(json.loads(marker.read_bytes()))==2
                pending.cancel()
                with pytest.raises(asyncio.CancelledError):await pending
            elif mode in ('second_error','cache_reset'):
                with pytest.raises(BusinessError) as failure:await pending
                assert failure.value.code==('ASR_UPSTREAM_FAILED' if mode=='second_error' else 'MODEL_SESSION_RESET')
            else:
                proof,pcm=await pending
                assert proof['session']['requests']==3 and proof['session']['operation_stopped'] is True
                assert pcm_info(pcm)['sample_count']==16000*62
                assert checked_coverage(proof)['sample_coverage']==1 and proof['semantic_review']=='not_measured'
                assert all(c['recognition']['operation_stopped'] is False for c in proof['chunks'])
                for bad in ['gap','language','session','claimed_review']:
                    changed=deepcopy(proof)
                    if bad=='gap':changed['chunks'][1]['start_sample']+=1
                    if bad=='language':changed['chunks'][0]['recognition']['language_hint']='Chinese'
                    if bad=='session':changed['chunks'][0]['recognition']['model_session_id']='other'
                    if bad=='claimed_review':changed['semantic_review']='passed'
                    with pytest.raises(BusinessError):checked_coverage(changed)
            assert len(started)==1 and started[0].poll() is not None and pool.stopped()
            assert all(j['exit_code'] is not None for j in pool.data['jobs'])
    asyncio.run(run())
