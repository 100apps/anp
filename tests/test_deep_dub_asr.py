"""字符代理和真实HTTP子进程夹具；不将夹具文本冒充模型识别。"""
from array import array
import asyncio
import json
from pathlib import Path
import socket
import subprocess
import sys
import wave

import pytest

from anp.errors import BusinessError
from apps.deep_dub.nodes.text_compare import compare,normalize
from apps.deep_dub.nodes.transcribe import transcribe
from apps.deep_dub.services.local_pool import LocalCpuPool
from apps.deep_dub.services.model_pool import ModelConfig
import apps.deep_dub.services.model_pool as models


def test_comparison_normalizes_punctuation_without_claiming_semantic_pass():
    result=compare('我这就开始。','我这就开始！')
    assert result['edit_distance']==0 and result['normalized_exact_match'] is True
    assert '不能自动通过' in result['scope']
    assert 'passed' not in result


@pytest.mark.parametrize('expected,recognized',[('-3','3'),('1.2','12'),('10%','10'),('不需要','需要'),('C++','C'),('3/4','34'),('١٫٥','١٥'),('10‰','10'),('10٪','10')])
def test_numbers_signs_negation_and_code_names_are_not_silently_erased(expected,recognized):
    result=compare(expected,recognized)
    assert result['edit_distance']>0 and not result['normalized_exact_match']


def test_edit_distance_empty_transcript_and_unicode_width():
    assert normalize('１００％')=='100%'
    assert compare('一二三','')['character_error_rate']==1
    assert compare('a','aaaa')['character_error_rate']==3
    assert compare('abc','axc')['edit_distance']==1
    for expected,observed in [('', '文字'),('。。。','文字'),('字'*2001,'字'),('字','字'*4001)]:
        with pytest.raises(BusinessError):compare(expected,observed)


@pytest.mark.parametrize('mode',['success','empty','upstream_error','invalid','cancel','changed'])
@pytest.mark.parametrize('family',['qwen3_asr','nemotron_asr'])
def test_actual_asr_http_lifecycle_and_no_expected_text_prompt(tmp_path,monkeypatch,mode,family):
    with socket.socket() as port:port.bind(('127.0.0.1',0));number=port.getsockname()[1]
    config=ModelConfig(executable=sys.executable,executable_sha256='a'*64,working_directory=str(tmp_path),
        weights=str(tmp_path/'weights'),weights_sha256='b'*64,source_revision='c'*40,gpu_uuid='GPU-fixture',gpu_index=0,
        family=family,model_id='fixture-asr',port=number,coordination_locks=[str(tmp_path/'lock')])
    source=tmp_path/'source.wav'
    with wave.open(str(source),'wb') as stream:
        stream.setnchannels(1);stream.setsampwidth(2);stream.setframerate(24000);stream.writeframes(array('h',[1000,-1000]*1200).tobytes())
    marker=tmp_path/'requested';script=tmp_path/'fixture.py'
    script.write_text('''from http.server import HTTPServer,BaseHTTPRequestHandler
import json,sys,time,wave
from pathlib import Path
class Handler(BaseHTTPRequestHandler):
 def log_message(self,*args):pass
 def send(self,status,data):
  self.send_response(status);self.send_header('Content-Type','application/json');self.end_headers();self.wfile.write(json.dumps(data).encode())
 def do_GET(self):self.send(200,{'status':'ok'} if self.path=='/health' else {'data':[{'id':'fixture-asr','loaded':False}]})
 def do_POST(self):
  body=json.loads(self.rfile.read(int(self.headers['Content-Length'])))
  assert set(body['request'])=={'audio','text','language','max_tokens'} and body['request']['text']==''
  assert body['request']['language']==('zh-CN' if sys.argv[5]=='nemotron_asr' else 'Chinese')
  assert body['request']['max_tokens']==(0 if sys.argv[5]=='nemotron_asr' else 512)
  with wave.open(body['request']['audio'],'rb') as f:assert f.getframerate()==16000 and f.getnchannels()==1
  Path(sys.argv[3]).write_text('requested')
  if sys.argv[2]=='cancel':time.sleep(60)
  if sys.argv[2]=='changed':Path(sys.argv[4]).write_bytes(b'changed')
  self.send(500 if sys.argv[2]=='upstream_error' else 200,{'text':123 if sys.argv[2]=='invalid' else '' if sys.argv[2]=='empty' else '识别文本','timing':{'wall_ms':1}})
HTTPServer(('127.0.0.1',int(sys.argv[1])),Handler).serve_forever()
''',encoding='utf-8')
    original=subprocess.Popen
    def launch(command,**kwargs):
        if len(command)>2 and command[0]==sys.executable and command[1]=='--config':
            return original([sys.executable,str(script),str(number),mode,str(marker),str(source),family],**kwargs)
        return original(command,**kwargs)
    monkeypatch.setattr(models.subprocess,'Popen',launch)
    monkeypatch.setattr(models.OwnedModelServer,'owns_listener',lambda self:self.process.poll() is None)
    async def run():
        with LocalCpuPool('fixture-'+mode,tmp_path/'pool') as pool:
            pending=asyncio.create_task(transcribe(config,pool,tmp_path,source,timeout=10))
            if mode=='cancel':
                for _ in range(300):
                    if marker.exists():break
                    if pending.done():await pending
                    await asyncio.sleep(.02)
                assert marker.exists();pending.cancel()
                with pytest.raises(asyncio.CancelledError):await pending
            elif mode in ('upstream_error','invalid','changed'):
                with pytest.raises(BusinessError) as error:await pending
                assert error.value.code=={'upstream_error':'ASR_UPSTREAM_FAILED','invalid':'ASR_RESPONSE_INVALID','changed':'ASR_INPUT_CHANGED'}[mode]
            else:
                data=await pending
                assert data['text']==('' if mode=='empty' else '识别文本')
                assert data['context_prompt']=='' and data['normalized_samples']==1600 and data['operation_stopped'] is True
                assert data['semantic_review']=='not_measured'
                assert data['family']==family
                assert data['rule_revision']==('nemotron-asr-owned-unprompted-v1' if family=='nemotron_asr' else 'qwen3-asr-owned-unprompted-v1')
            assert pool.stopped() and len(pool.data['jobs'])>=3 and all(j['exit_code'] is not None for j in pool.data['jobs'])
    asyncio.run(run())
