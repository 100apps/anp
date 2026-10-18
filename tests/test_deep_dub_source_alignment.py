"""来源词时间的偏移、坏时间保留与受控进程复用；夹具不是模型质量证据。"""
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
from apps.deep_dub.nodes.source_asr import RULE as ASR_BATCH_RULE,windows,pcm_info,extract_window
from apps.deep_dub.nodes.transcribe import RULE as ASR_RULE
from apps.deep_dub.nodes.source_alignment import align_source,summarize
from apps.deep_dub.services.blocking import file_identity
from apps.deep_dub.services.local_pool import LocalCpuPool
from apps.deep_dub.services.model_pool import ModelConfig
import apps.deep_dub.services.model_pool as models


def recognition(source,tmp_path,empty=False):
    pcm=pcm_info(source);chunks=[]
    identity={'model_id':'fixture-asr','weights_sha256':'b'*64,'runtime_sha256':'a'*64,'source_revision':'c'*40}
    for index,(start,end) in enumerate(windows(pcm['sample_count'],30)):
        raw_hash=extract_window(source,tmp_path/f'asr-chunk-{index}.wav',start,end)
        chunks.append({'index':index,'start_sample':start,'end_sample':end,'pcm_sha256':raw_hash,'normalized_pcm_sha256':raw_hash,
            'recognition':{**identity,'rule_revision':ASR_RULE,'family':'qwen3_asr','context_prompt':'','text':'' if empty and index==2 else 'hello world',
                'model_session_id':'asr-session','operation_stopped':False,'normalized_samples':end-start,'language_hint':'English'}})
    return {'rule_revision':ASR_BATCH_RULE,'pcm':pcm,'normalized_input':file_identity(source),'chunk_seconds':30,'chunks':chunks,
        'model':identity,'session':{'session_id':'asr-session','requests':len(chunks),'warm_cache_checks':len(chunks)-1,'operation_stopped':True,'initially_unloaded':True},
        'boundary_review':'not_measured','semantic_review':'not_measured','subtitle_timing_review':'not_measured','language_hint':'English',
        'media_probe':{'streams':[{'codec_type':'audio','start_time':'0.000000'}]}}


@pytest.mark.parametrize('mode',['success','bad_words','empty_last','cancel','second_error','cache_reset'])
@pytest.mark.skipif(sys.platform!='win32',reason='此夹具真实校验Windows监听PID；本机受控模型运行器尚不支持其他系统')
def test_whole_source_alignment_reuses_server_preserves_measurements_and_releases_after_stop(tmp_path,monkeypatch,mode):
    with socket.socket() as sock:sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
    source=tmp_path/'source.wav'
    with wave.open(str(source),'wb') as target:
        target.setnchannels(1);target.setsampwidth(2);target.setframerate(16000)
        target.writeframes(array('h',[1000,-1000]*16000*31).tobytes())
    asr=recognition(source,tmp_path,empty=mode=='empty_last')
    config=ModelConfig(executable=sys.executable,executable_sha256='a'*64,working_directory=str(tmp_path),weights=str(tmp_path/'weights'),
        weights_sha256='d'*64,source_revision='e'*40,gpu_uuid='GPU-fixture',gpu_index=0,family='qwen3_forced_aligner',model_id='fixture-aligner',port=port,coordination_locks=[str(tmp_path/'lock')])
    marker=tmp_path/'requests.json';script=tmp_path/'fixture.py'
    script.write_text('''from http.server import HTTPServer,BaseHTTPRequestHandler
import json,sys,time
from pathlib import Path
requests=[]
class Handler(BaseHTTPRequestHandler):
 def log_message(self,*args):pass
 def send(self,status,body):
  self.send_response(status);self.send_header('Content-Type','application/json');self.end_headers();self.wfile.write(json.dumps(body).encode())
 def do_GET(self):self.send(200,{'status':'ok'} if self.path=='/health' else {'data':[{'id':'fixture-aligner','loaded':bool(requests) and sys.argv[2]!='cache_reset'}]})
 def do_POST(self):
  body=json.loads(self.rfile.read(int(self.headers['Content-Length'])))
  assert body['request']['text']=='hello world' and body['request']['options']['clamp_timestamps_to_audio'] is False
  requests.append(body['request']['audio']);target=Path(sys.argv[3]);target.with_suffix('.tmp').write_text(json.dumps(requests));target.with_suffix('.tmp').replace(target)
  if len(requests)==2 and sys.argv[2]=='cancel':time.sleep(60)
  words=[{'word':'hello','start_sample':-10 if sys.argv[2]=='bad_words' else 100,'end_sample':500,'confidence':None},{'word':'world','start_sample':500,'end_sample':1000,'confidence':None}]
  self.send(500 if len(requests)==2 and sys.argv[2]=='second_error' else 200,{'words':words})
HTTPServer(('127.0.0.1',int(sys.argv[1])),Handler).serve_forever()
''',encoding='utf-8')
    original=subprocess.Popen;started=[]
    def launch(command,**kwargs):
        if len(command)>2 and command[0]==sys.executable and command[1]=='--config':
            # 直接运行真实解释器，避免Windows venv启动器派生监听子进程，使PID归属检查仍真实执行。
            process=original([sys._base_executable,str(script),str(port),mode,str(marker)],**kwargs);started.append(process);return process
        return original(command,**kwargs)
    monkeypatch.setattr(models.subprocess,'Popen',launch)
    async def run():
        with LocalCpuPool('fixture-source-alignment',directory=tmp_path/'pool') as pool:
            task=asyncio.create_task(align_source(config,pool,tmp_path,source,asr,timeout=10))
            if mode=='cancel':
                # 此夹具保留真实Windows监听PID核对，不能套用跳过该检查的旧夹具短等待。
                for _ in range(900):
                    if task.done():await task
                    try:
                        if len(json.loads(marker.read_bytes()))==2:break
                    except (FileNotFoundError,ValueError):pass
                    await asyncio.sleep(.05)
                else:pytest.fail('第二个真实HTTP请求未开始')
                task.cancel()
                with pytest.raises(asyncio.CancelledError):await task
            elif mode in ('second_error','cache_reset'):
                with pytest.raises(BusinessError):await task
            else:
                proof=await task;summary=proof['summary'];assert summarize(proof,asr)==summary
                assert proof['session']['requests']==(2 if mode=='empty_last' else 3)
                assert summary['invalid_timestamp_chunks']==(3 if mode=='bad_words' else 0)
                assert summary['empty_asr_chunks']==(1 if mode=='empty_last' else 0)
                assert proof['words'][2]['source_start_sample']==480000+(-10 if mode=='bad_words' else 100)
                assert proof['semantic_review']==proof['subtitle_timing_review']=='not_measured'
                for bad in ('word_offset','source_text','claimed_review','model','clamp','pcm'):
                    changed=deepcopy(proof);altered=deepcopy(asr)
                    if bad=='word_offset':changed['words'][0]['source_start_sample']+=1
                    if bad=='source_text':altered['chunks'][0]['recognition']['text']='different words'
                    if bad=='claimed_review':changed['subtitle_timing_review']='passed'
                    if bad=='model':changed['chunks'][0]['model']['model_id']='another-model'
                    if bad=='clamp':changed['chunks'][0]['model']['clamp_timestamps_to_audio']=True
                    if bad=='pcm':changed['input']['sha256']='f'*64
                    with pytest.raises(BusinessError):summarize(changed,altered)
            assert len(started)==1 and started[0].poll() is not None and pool.stopped()
    asyncio.run(run())
