"""真实HTTP子服务/文件回执，验证断连后不重发；不是V100或模型质量验收。"""
import base64
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
import io,json,threading,wave
from uuid import uuid4
import pytest
from apps.deep_dub.nodes import resident_job as job


def sound():
    target=io.BytesIO()
    with wave.open(target,'wb') as wav:
        wav.setnchannels(1);wav.setsampwidth(2);wav.setframerate(24000);wav.writeframes(b'\x01\x00'*2400)
    return target.getvalue()


@contextmanager
def server(mode='success',entered=None,release=None):
    calls=[]
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*a):pass
        def do_POST(self):
            calls.append(json.loads(self.rfile.read(int(self.headers['Content-Length']))))
            if entered is not None:entered.set()
            if release is not None:release.wait(3)
            if mode=='disconnect':self.close_connection=True;return
            audio=sound() if mode=='success' else b'not a wave file'
            body=json.dumps({'audio':base64.b64encode(audio).decode(),'sample_rate':24000,'channels':1}).encode()
            self.send_response(200);self.send_header('Content-Length',str(len(body)));self.end_headers();self.wfile.write(body)
    http=ThreadingHTTPServer(('127.0.0.1',0),Handler)
    thread=threading.Thread(target=http.serve_forever,daemon=True);thread.start()
    try:yield http.server_port,calls
    finally:http.shutdown();http.server_close();thread.join(timeout=5)


@pytest.fixture
def prepared(tmp_path):
    (tmp_path/'.workspace-marker').write_text(job.MARKER)
    directory=tmp_path/str(uuid4());directory.mkdir()
    for name in ('voice.wav','emotion.wav'):(directory/name).write_bytes(sound())
    spec={'rule_revision':job.RULE,'job_id':directory.name,'service_host':'127.0.0.1','port':18123,'service_pid':123,
        'model_id':'fixture','service_identity':{'fixture':True},'timeout_seconds':5,
        'input_files':{name:job.digest(directory/name) for name in ('voice.wav','emotion.wav')},
        'request':{'text':'这是未经质量验收的候选。','language':'zh','voice_ref':str(directory/'voice.wav'),'audio':str(directory/'emotion.wav'),
            'seed':42,'max_tokens':1500,'do_sample':False,'num_beams':3,'options':{'duration_factor':1.,'emotion_alpha':1}}}
    def save(): (directory/'request.json').write_bytes(job.canonical(spec))
    save();return directory,spec,save


def execute(directory,**kwargs):return job.execute(directory,identity_probe=lambda s:s['service_identity'],**kwargs)


def test_actual_http_receipt_and_replay_only_post_once(prepared):
    directory,spec,save=prepared
    with server() as (port,calls):
        spec['port']=port;save();first=execute(directory)
        assert first['status']=='succeeded' and first['operation_stopped'] and not first['resident_service_stopped']
        assert first['quality']=='not_measured' and (directory/'result.wav').read_bytes()==sound()
        assert execute(directory)==first and len(calls)==1
        assert calls[0]['request']['text']==spec['request']['text']
        (directory/'result.wav').write_bytes(b'tampered')
        with pytest.raises(ValueError,match='产物摘要'):execute(directory)
        assert len(calls)==1


def test_disconnect_is_uncertain_and_never_auto_retried(prepared):
    directory,spec,save=prepared
    with server('disconnect') as (port,calls):
        spec['port']=port;save();result=execute(directory)
        assert result['status']=='uncertain' and not result['operation_stopped'] and not (directory/'result.wav').exists()
        assert execute(directory)==result and len(calls)==1


def test_complete_bad_audio_is_failed_but_request_has_returned(prepared):
    directory,spec,save=prepared
    with server('invalid') as (port,calls):
        spec['port']=port;save();result=execute(directory)
        assert result['status']=='failed' and result['operation_stopped'] and not (directory/'result.wav').exists()
        assert execute(directory)==result and len(calls)==1


def test_exit_after_durable_intent_never_reissues_post(prepared,monkeypatch):
    directory,spec,save=prepared;original=job.save
    def stop(path,value):original(path,value);raise SystemExit('模拟发送前owner退出，不能猜测尚未发送')
    with server() as (port,calls):
        spec['port']=port;save();monkeypatch.setattr(job,'save',stop)
        with pytest.raises(SystemExit):execute(directory)
        monkeypatch.setattr(job,'save',original)
        result=execute(directory);assert result['status']=='submit_intent' and not result['operation_stopped'] and not calls


@pytest.mark.parametrize('change',[
    lambda s:s.update(service_host='8.8.8.8'),
    lambda s:s.update(rule_revision='unknown'),
    lambda s:s['request'].update(voice_ref='/some/other/job.wav'),
    lambda s:s['request']['options'].update(duration_factor=.1),
    lambda s:s['request']['options'].update(emotion_alpha=True),
    lambda s:s.update(actor_token='must-not-be-delegated'),
])
def test_invalid_scope_stops_before_intent_or_http(prepared,change):
    directory,spec,save=prepared;change(spec);save()
    with pytest.raises(ValueError):execute(directory)
    assert not (directory/'state.json').exists()


def test_reference_changed_during_identity_probe_stops_before_intent(prepared):
    directory,spec,save=prepared
    def change(_): (directory/'voice.wav').write_bytes(b'changed');return {}
    with pytest.raises(ValueError,match='摘要变化'):job.execute(directory,identity_probe=change)
    assert not (directory/'state.json').exists()


def test_same_job_id_cannot_switch_frozen_input_after_submission(prepared):
    directory,spec,save=prepared
    with server() as (port,calls):
        spec['port']=port;save();execute(directory)
        spec['request']['text']='另一份稿件';save()
        with pytest.raises(ValueError,match='另一份输入'):execute(directory)
        assert len(calls)==1


def test_two_competing_workers_cannot_submit_same_job_twice(prepared):
    directory,spec,save=prepared;entered=threading.Event();release=threading.Event();results=[]
    with server(entered=entered,release=release) as (port,calls):
        spec['port']=port;save()
        thread=threading.Thread(target=lambda:results.append(execute(directory)));thread.start()
        try:
            assert entered.wait(3)
            with pytest.raises(OSError):execute(directory)
            assert len(calls)==1
        finally:release.set();thread.join(timeout=5)
        assert not thread.is_alive() and results[0]['status']=='succeeded' and len(calls)==1
