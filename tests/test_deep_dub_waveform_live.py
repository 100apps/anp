"""独立业务端口与真实RustFS/租约波形；明确acceptance，无实际译配或质量通过。"""
import asyncio
from datetime import datetime,timezone
import json
from pathlib import Path
import socket
import subprocess
import sys
import time
import tomllib
from string import Template
from uuid import uuid4

import pytest
import psycopg

from anp import ROOT
from anp.auth.identity import actor_from_token
from anp.auth.session import login,session_path
from anp.config import secret
from anp.sdk.http import async_client
from apps.deep_dub.nodes.media import executable
from apps.deep_dub.services.artifacts import service as artifacts
from apps.deep_dub.services.candidates import service as candidates
from apps.deep_dub.services.execution import service as execution
from apps.deep_dub.services.projects import service as projects
from apps.deep_dub.services.versions import service as versions
from apps.deep_dub.services import waveforms
from tests.test_deep_dub_contracts_live import context,PROFILE,pytestmark
from tests.test_deep_dub_runtime_live import flush


@pytest.fixture
def waveform_api():
    with socket.socket() as probe:assert probe.connect_ex(('127.0.0.1',8184))!=0,'独立8184端口已占用，不能接管'
    path=ROOT/'.local/logs/deep-dub-waveform-api.log';path.parent.mkdir(parents=True,exist_ok=True)
    with path.open('w',encoding='utf-8') as log:
        process=subprocess.Popen([sys.executable,'-m','uvicorn','anp.server.api:app','--host','127.0.0.1','--port','8184'],
            cwd=ROOT,stdout=log,stderr=log,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        try:
            async def ready():
                async with async_client(timeout=2,trust_env=False) as client:
                    for _ in range(80):
                        assert process.poll() is None,'独立API提前退出，原始诊断留私有日志'
                        try:
                            result=await client.get('http://127.0.0.1:8184/openapi.json')
                            if result.status_code==200 and '/api/apps/deep_dub/runs/{run_id}/waveform' in result.json()['paths']:return
                        except Exception:pass
                        await asyncio.sleep(.1)
                pytest.fail('独立API未就绪')
            asyncio.run(ready());yield 'http://127.0.0.1:8184'
        finally:
            process.terminate()
            try:process.wait(timeout=10)
            except subprocess.TimeoutExpired:process.kill();process.wait(timeout=5)


def test_real_waveform_stage_http_cli_and_viewer(context,identities,waveform_api,tmp_path,monkeypatch):
    _,tokens=context;endpoint=waveform_api
    monkeypatch.setenv('ANP_PROFILE','waveform-'+uuid4().hex);monkeypatch.setenv('PYTHONIOENCODING','utf-8')
    file=tmp_path/'fixture.mp4'
    subprocess.run([executable('ffmpeg'),'-v','error','-f','lavfi','-i','color=s=64x64:r=10:d=3',
        '-f','lavfi','-i','sine=frequency=440:sample_rate=16000:duration=3',
        '-c:v','libx264','-pix_fmt','yuv420p','-c:a','aac','-shortest',str(file)],check=True,capture_output=True,timeout=20)
    async def run():
        actor=await actor_from_token(tokens['admin'])
        project=await projects.create(actor,{'name':'[验收] 波形与监听时间尺','source_url':'https://example.invalid/waveform-acceptance'})
        quality=await versions.create_quality_profile(actor,PROFILE)
        queued=await execution.queue(actor,project['id'],{'purpose':'acceptance','quality_profile_id':quality['id'],
            'flow_revision':'waveform-contract-v1','expected_version':project['row_version']})
        await execution.start(actor,queued['id'])
        try:
            attempt=(await execution.begin_stage(actor,queued['id'],{'node':'acceptance_wave_input','input_hash':'c'*64}))['attempt']['id']
            await execution.mark_stage(actor,attempt,'started');started=time.monotonic()
            movie=await artifacts.upload(actor,attempt,file,'video/mp4',3000)
            source=await candidates.source(actor,attempt,{'artifact_id':movie['id'],'duration_ms':3000,'identity_evidence':{'acceptance_only':True,'generated_tone':True}})
            await candidates.transcript(actor,attempt,{'source_id':source['id'],'strategy_revision':'manual-waveform-fixture',
                'cues':[{'cue_no':0,'speaker_key':'fixture','source_text':'[人工夹具] 正弦音，不含语音。','start_ms':500,'end_ms':2500}],
                'segments':[{'cue_numbers':[0]}]})
            await execution.finish_stage(actor,attempt,{'status':'succeeded','outputs':[movie['id']],
                'elapsed_seconds':time.monotonic()-started,'operation_stopped':True})
            path='/api/apps/deep_dub/media/artifacts/'+movie['id']+'/waveform';job='/api/apps/deep_dub/runs/'+queued['id']+'/waveform'
            key=str(uuid4());body={'artifact_id':movie['id']}
            async with async_client(timeout=60,trust_env=False,headers={'Authorization':'Bearer '+tokens['admin']}) as client:
                assert (await client.get(endpoint+path)).status_code==404
                assert (await client.post(endpoint+job,json=body,headers={'Authorization':'Bearer '+tokens['viewer']})).status_code==403
                result=await client.post(endpoint+job,json=body,headers={'Idempotency-Key':key});assert result.status_code==200
                stage=result.json();assert stage['status']=='succeeded' and len(stage['attempt']['outputs'])==1
                assert (await client.post(endpoint+job,json=body,headers={'Idempotency-Key':key})).json()==stage
                wave=await client.get(endpoint+path,params={'start':.5,'end':2.5,'points':64});assert wave.status_code==200
                data=wave.json();assert data['durationSec']==2 and len(data['peaks'])==64 and max(data['peaks'])>.1
                assert data['qualityEvaluated'] is False
                view=await client.get(endpoint+path,params={'start':.5,'end':2.5,'points':64},headers={'Authorization':'Bearer '+tokens['viewer']})
                assert view.status_code==200 and view.json()==data
                assert (await client.get(endpoint+path,headers={'Authorization':''})).status_code==401
                assert (await client.get(endpoint+path,headers={'Authorization':'Bearer '+identities['outside']['token']})).status_code in (403,404)
                assert (await client.get(endpoint+path,params={'end':99})).status_code==422
                assert data==await waveforms.read(actor,movie['id'],start=.5,end=2.5,points=64)
            # 租约表含令牌摘要，普通业务身份无直接SELECT；验收器只读核对停止字段，不放宽业务权限。
            storage=tomllib.loads((ROOT/'config/deployment.local.toml').read_text())['storage']
            dsn=Template(storage['postgres_host_url']).substitute(POSTGRES_PASSWORD=secret('POSTGRES_PASSWORD'))
            with psycopg.connect(dsn) as check:
                check.execute('SET TRANSACTION READ ONLY')
                leases=check.execute('SELECT status,operation_stopped FROM platform.deep_dub_device_leases WHERE stage_attempt_id=%s',
                    (stage['attempt']['id'],)).fetchall()
            assert leases==[('released',True)]
            assert not await actor.objects.select('deep_dub_audits',project_id='eq.'+project['id'])
            assert not await actor.objects.select('deep_dub_audio_attempts',project_id='eq.'+project['id'])
            return {'project':project['id'],'run':queued['id'],'artifact':movie['id'],'key':key,'stage':stage,'wave':data,'identity':actor.user['id']}
        finally:await execution.cancel(actor,queued['id']);await flush(actor)
    try:
        record=asyncio.run(run())
        asyncio.run(login(identities['admin']['email'],identities['admin']['password']))
        def cli(*options):
            result=subprocess.run([sys.executable,'-m','anp.cli.entry',*options,'deep_dub','media','waveform',record['artifact'],
                '--start','0.5','--end','2.5','--points','64'],cwd=ROOT,capture_output=True,text=True,encoding='utf-8',timeout=60)
            assert result.returncode==0,'CLI失败，响应不公开';return json.loads(result.stdout)
        assert cli('--endpoint','http://127.0.0.1:1')==record['wave']==cli('--transport','http','--endpoint',endpoint)
        Path('.local/deep-dub-waveform-live.json').write_bytes((json.dumps({'checked_at_utc':datetime.now(timezone.utc).isoformat(),
            'purpose':'acceptance','project_id':record['project'],'run_id':record['run'],'artifact_id':record['artifact'],
            'stage_attempt_id':record['stage']['attempt']['id'],'waveform_artifact_id':record['stage']['attempt']['outputs'][0],
            'rule_revision':waveforms.RULE,'points':64,'window_seconds':2,'max_peak':max(record['wave']['peaks']),
            'http_equals_local_and_http_cli':True,'same_key_replay':True,'viewer_read_allowed_write_denied':True,
            'anonymous_foreign_denied':True,'cpu_lease_released':True,'run_cancelled':True,'generated_tone_only':True,
            'no_tts_or_audit':True},ensure_ascii=False,indent=2)+'\n').encode())
    finally:session_path().unlink(missing_ok=True)
