"""词边界、能量和指定落点代理；不模拟内容通过或实际模型质量。"""
from array import array
import asyncio
import json
import math
import socket
import subprocess
import sys
import wave

import pytest

from anp.errors import BusinessError
from apps.deep_dub.nodes.alignment import summarize_words,signal_profile,placement_metrics
from apps.deep_dub.nodes.transcribe import align_words
from apps.deep_dub.services.local_pool import LocalCpuPool
from apps.deep_dub.services.model_pool import ModelConfig
import apps.deep_dub.services.model_pool as models


def words():return [{'word':'给','start_sample':100,'end_sample':500,'confidence':1.},{'word':'定','start_sample':500,'end_sample':1000,'confidence':1.}]


def test_valid_words_are_not_treated_as_content_or_quality_pass():
    result=summarize_words(words(),1600,'给定。')
    assert result['valid_timestamps'] and result['transcript_token_coverage'] and result['token_onset_ms']==6.25
    assert result['token_offset_ms']==62.5 and '未校准' in result['scope']
    assert 'passed' not in result


@pytest.mark.parametrize('case',['outside','reversed','zero','overlap','order','missing'])
def test_bad_boundaries_and_coverage_are_retained_as_failed_measurements(case):
    raw=words()
    if case=='outside':raw[1]['end_sample']=1800
    if case=='reversed':raw[1]['end_sample']=400
    if case=='zero':raw[1]['end_sample']=500
    if case=='overlap':raw[1]['start_sample']=400
    if case=='order':raw=list(reversed(raw))
    if case=='missing':raw=raw[:1]
    data=summarize_words(raw,1600,'给定')
    assert not (data['valid_timestamps'] and data['transcript_token_coverage'])
    assert data['words'][0]['word']==raw[0]['word']


def test_invalid_timestamp_contract_does_not_become_zero():
    for invalid in [True,1.5,float('nan'),None,'100']:
        raw=words();raw[0]['start_sample']=invalid
        with pytest.raises(BusinessError):summarize_words(raw,1600,'给定')


def test_actual_pcm_energy_boundaries_and_truncation(tmp_path):
    source=tmp_path/'signal.wav'
    samples=array('h',[0]*1600+[int(8192*math.sin(2*math.pi*440*i/16000)) for i in range(3200)]+[0]*1600)
    with wave.open(str(source),'wb') as audio:
        audio.setnchannels(1);audio.setsampwidth(2);audio.setframerate(16000);audio.writeframes(samples.tobytes())
    signal=signal_profile(source)
    assert signal['sample_count']==6400 and signal['onset_ms']==100 and signal['offset_ms']==300
    assert signal['active_frame_count']==20 and signal['clipped_sample_ratio']==0
    source.write_bytes(source.read_bytes()[:-2])
    with pytest.raises(BusinessError):signal_profile(source)


def test_energy_unknown_and_proposed_placement_are_not_saved_timeline():
    alignment=summarize_words(words(),1600,'给定')
    signal={'sample_count':1600,'onset_ms':None,'offset_ms':None}
    result=placement_metrics(alignment,signal,start_ms=0,source_start_ms=1000,source_end_ms=2000,video_duration_ms=3000,max_advance_ms=500)
    assert result['energy_onset_delta_ms'] is None and not result['start_within_advance_bound']
    assert result['placement_kind']=='analysis_only' and result['source_window_duration_ratio']==.1
    assert '碰撞' in result['scope']
    empty=summarize_words([],1600,'给定')
    assert empty['token_onset_ms'] is None and not empty['valid_timestamps']


@pytest.mark.parametrize('mode',['success','bad','cancel'])
def test_actual_aligner_http_fixture_preserves_supplied_transcript_and_owned_exit(tmp_path,monkeypatch,mode):
    with socket.socket() as probe:probe.bind(('127.0.0.1',0));port=probe.getsockname()[1]
    config=ModelConfig(executable=sys.executable,executable_sha256='a'*64,working_directory=str(tmp_path),weights=str(tmp_path/'weights'),
        weights_sha256='b'*64,source_revision='c'*40,gpu_uuid='GPU-fixture',gpu_index=0,family='qwen3_forced_aligner',model_id='fixture-aligner',port=port,coordination_locks=[str(tmp_path/'lock')])
    source=tmp_path/'source.wav'
    with wave.open(str(source),'wb') as audio:
        audio.setnchannels(1);audio.setsampwidth(2);audio.setframerate(16000);audio.writeframes(array('h',[1000,-1000]*800).tobytes())
    script=tmp_path/'fixture.py';marker=tmp_path/'requested'
    script.write_text('''from http.server import HTTPServer,BaseHTTPRequestHandler
import json,sys,time
from pathlib import Path
class Handler(BaseHTTPRequestHandler):
 def log_message(self,*args):pass
 def send(self,data):
  self.send_response(200);self.send_header('Content-Type','application/json');self.end_headers();self.wfile.write(json.dumps(data).encode())
 def do_GET(self):self.send({'status':'ok'} if self.path=='/health' else {'data':[{'id':'fixture-aligner','loaded':False}]})
 def do_POST(self):
  body=json.loads(self.rfile.read(int(self.headers['Content-Length'])))
  assert body['request']['text']=='给定' and body['request']['options']['clamp_timestamps_to_audio'] is False and 'max_tokens' not in body['request']
  Path(sys.argv[3]).write_text('requested')
  if sys.argv[2]=='cancel':time.sleep(60)
  self.send({'words':'bad' if sys.argv[2]=='bad' else [{'word':'给','start_sample':100,'end_sample':500,'confidence':1},{'word':'定','start_sample':500,'end_sample':1000,'confidence':1}]})
HTTPServer(('127.0.0.1',int(sys.argv[1])),Handler).serve_forever()
''',encoding='utf-8')
    original=subprocess.Popen
    def launch(command,**kwargs):
        if len(command)>2 and command[0]==sys.executable and command[1]=='--config':
            settings=json.loads((tmp_path/'server.json').read_bytes())
            assert settings['models'][0]['task']=='align' and settings['models'][0]['session_options']=={}
            return original([sys.executable,str(script),str(port),mode,str(marker)],**kwargs)
        return original(command,**kwargs)
    monkeypatch.setattr(models.subprocess,'Popen',launch);monkeypatch.setattr(models.OwnedModelServer,'owns_listener',lambda self:self.process.poll() is None)
    async def run():
        with LocalCpuPool('fixture-'+mode,tmp_path/'pool') as pool:
            operation=asyncio.create_task(align_words(config,pool,tmp_path,source,'给定',timeout=10))
            if mode=='cancel':
                for _ in range(300):
                    if marker.exists():break
                    if operation.done():await operation
                    await asyncio.sleep(.02)
                assert marker.exists();operation.cancel()
                with pytest.raises(asyncio.CancelledError):await operation
            elif mode=='bad':
                with pytest.raises(BusinessError) as error:await operation
                assert error.value.code=='ALIGN_RESPONSE_INVALID'
            else:
                result=await operation
                assert result['supplied_transcript']=='给定' and 'context_prompt' not in result and 'text' not in result
                assert summarize_words(result['words'],result['normalized_samples'],'给定')['valid_timestamps']
            assert pool.stopped() and all(j['exit_code'] is not None for j in pool.data['jobs'])
    asyncio.run(run())
