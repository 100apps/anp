"""真实API→Supabase/RustFS Range与短期Cookie；验收夹具，不开始整片生产。"""
import asyncio
from datetime import datetime,timezone
from hashlib import sha256
import json
from pathlib import Path
import subprocess
import sys
import time
from uuid import uuid4

from anp.auth.identity import actor_from_token
from anp.config import value
from anp.config import secret
from anp.auth.session import login,session_path
from anp.management.bootstrap import create_user
from anp.sdk.objects import Objects
from anp.sdk.http import async_client
from apps.deep_dub.nodes.media import executable
from apps.deep_dub.services.artifacts import service as artifacts
from apps.deep_dub.services.execution import service as execution
from apps.deep_dub.services.media import COOKIE_NAME,COOKIE_PATH,sessions
from tests.test_deep_dub_runtime_live import setup,flush
from tests.test_deep_dub_contracts_live import context,pytestmark


def test_real_private_media_full_range_cookie_scope_expiry_and_identity(context,tmp_path,monkeypatch):
    _,tokens=context
    monkeypatch.setenv('ANP_PROFILE','deep-dub-media-'+uuid4().hex)
    monkeypatch.setenv('PYTHONIOENCODING','utf-8')
    audio=tmp_path/'media.wav'
    subprocess.run([executable('ffmpeg'),'-v','error','-f','lavfi','-i','sine=frequency=440:duration=2',
        '-ar','16000','-ac','1',str(audio)],check=True,capture_output=True,timeout=20)
    content=audio.read_bytes()
    video=tmp_path/'media.mp4'
    subprocess.run([executable('ffmpeg'),'-v','error','-f','lavfi','-i','testsrc2=s=160x90:r=20:d=3',
        '-f','lavfi','-i','sine=frequency=440:duration=3','-c:v','libx264','-pix_fmt','yuv420p',
        '-c:a','aac','-shortest','-movflags','+faststart',str(video)],check=True,capture_output=True,timeout=20)
    async def run():
        actor=await actor_from_token(tokens['admin']);_,_,queued=await setup(actor)
        try:
            attempt=(await execution.begin_stage(actor,queued['id'],
                {'node':'ingest_media_transport_fixture','input_hash':'f'*64}))['attempt']['id']
            await execution.mark_stage(actor,attempt,'started');started=time.monotonic()
            saved=await artifacts.upload(actor,attempt,audio,'audio/wav',2000,16000)
            movie=await artifacts.upload(actor,attempt,video,'video/mp4',3000)
            html=tmp_path/'unsafe.html';html.write_text('<script>notExecutableFixture()</script>',encoding='utf8')
            unsafe=await artifacts.upload(actor,attempt,html,'text/html')
            await execution.finish_stage(actor,attempt,{'status':'succeeded','outputs':[saved['id'],movie['id'],unsafe['id']],
                'elapsed_seconds':time.monotonic()-started})
            endpoint=value('workbench','api_url').rstrip('/')+'/api/apps/deep_dub/media'
            url=endpoint+'/artifacts/'+saved['id']
            async with async_client(timeout=20,trust_env=False) as client:
                assert (await client.get(url)).status_code==401
                bearer={'Authorization':'Bearer '+tokens['admin']}
                full=await client.get(url,headers=bearer)
                assert full.status_code==200 and full.content==content
                assert full.headers['etag']=='"'+sha256(content).hexdigest()+'"'
                assert full.headers['cache-control']=='private, no-store' and full.headers['x-content-type-options']=='nosniff'
                info=await client.get(url+'/info',headers=bearer)
                assert info.status_code==200 and set(info.json())=={'id','project_id','media_type','bytes','sha256','duration_ms','sample_rate'}
                video_range=await client.get(endpoint+'/artifacts/'+movie['id'],headers={**bearer,'Range':'bytes=0-31'})
                assert video_range.status_code==206 and video_range.content==video.read_bytes()[:32] and video_range.headers['content-type']=='video/mp4'
                for header,expected in [('bytes=0-19',content[:20]),('bytes=20-39',content[20:40]),
                    ('bytes=-20',content[-20:]),('bytes=64000-',content[64000:])]:
                    response=await client.get(url,headers={**bearer,'Range':header})
                    assert response.status_code==206 and response.content==expected
                invalid=await client.get(url,headers={**bearer,'Range':'bytes=99999999-'})
                assert invalid.status_code==416 and invalid.headers['content-range']==f'bytes */{len(content)}'
                multi=await client.get(url,headers={**bearer,'Range':'bytes=0-1,4-5'})
                assert multi.status_code==416
                fallback=await client.get(url,headers={**bearer,'Range':'bytes=0-9','If-Range':'"wrong-version"'})
                assert fallback.status_code==200 and fallback.content==content
                blocked=await client.get(endpoint+'/artifacts/'+unsafe['id'],headers=bearer)
                assert blocked.status_code==415
                missing=await client.get(endpoint+'/artifacts/'+str(uuid4()),headers=bearer)
                assert missing.status_code==404
                foreign=await client.get(url,headers={'Authorization':'Bearer '+tokens['viewer'],'X-Workspace-ID':str(uuid4())})
                assert foreign.status_code==403
                session=await client.post(endpoint+'/session',headers=bearer)
                assert session.status_code==200 and set(session.json())=={'expires_at'}
                cookie_header=session.headers['set-cookie'].lower()
                assert 'httponly' in cookie_header and 'samesite=strict' in cookie_header and ('path='+COOKIE_PATH.lower()) in cookie_header
                native=await client.get(url,headers={'Range':'bytes=0-15'})
                assert native.status_code==206 and native.content==content[:16]
                # 显式的错误身份不能借现有Cookie自动降级成管理员。
                assert (await client.get(url,headers={'Authorization':'Bearer fixture-invalid'})).status_code==401
                cleared=await client.delete(endpoint+'/session');assert cleared.status_code==204
                assert (await client.get(url)).status_code==401
                cookie,expires=sessions.issue(actor,ttl=2)
                client.cookies.set(COOKIE_NAME,cookie,path=COOKIE_PATH)
                assert (await client.get(url,headers={'Range':'bytes=0-1'})).status_code==206
                await asyncio.sleep(2.1)
                expired=await client.get(url)
                assert expired.status_code==401 and expired.json()['error']['code']=='MEDIA_SESSION_EXPIRED'
                altered=cookie[:-1]+('0' if cookie[-1]!='0' else '1')
                client.cookies.set(COOKIE_NAME,altered,path=COOKIE_PATH)
                assert (await client.get(url)).status_code==401
            # 单独测试身份，撤销其成员资格；不改默认角色的共享权限或其他账号。
            password='ANP-Media-Test-'+secret('ADMIN_PASSWORD')[:24]
            user=await create_user('anp-test-media-reader@anp.local',password,'viewer',actor.workspace_id)
            try:
                async with async_client(timeout=20,trust_env=False) as reader:
                    logged=await reader.post(value('supabase','url')+'/auth/v1/token?grant_type=password',
                        headers={'apikey':secret('ANON_KEY')},json={'email':user['email'],'password':password})
                    assert logged.is_success,'专用媒体测试账号登录失败'
                    issued=await reader.post(endpoint+'/session',headers={'Authorization':'Bearer '+logged.json()['access_token']})
                    assert issued.status_code==200
                    assert (await reader.get(url,headers={'Range':'bytes=0-9'})).status_code==206
                    await Objects(secret('SERVICE_ROLE_KEY')).request('DELETE','members',params={
                        'workspace_id':'eq.'+actor.workspace_id,'user_id':'eq.'+user['id']})
                    revoked=await reader.get(url,headers={'Range':'bytes=10-19'})
                    assert revoked.status_code==403 and revoked.json()['error']['code']=='NO_WORKSPACE'
            finally:
                await create_user(user['email'],password,'viewer',actor.workspace_id)
            await flush(actor)
            Path('.local/deep-dub-media-transport-live.json').write_bytes((json.dumps({
                'checked_at_utc':datetime.now(timezone.utc).isoformat(),
                'scope':'真实8181 API→Supabase Storage→RustFS；2秒正弦验收WAV，非TTS或整片生产',
                'run_id':queued['id'],'artifact_id':saved['id'],'sha256':saved['sha256'],'bytes':len(content),
                'video_artifact_id':movie['id'],'video_sha256':movie['sha256'],'video_bytes':movie['bytes'],
                'video_scope':'3秒H264/AAC测试图与正弦夹具，非实际来源或配音产物',
                'full_bytes_equal':True,'prefix_suffix_resume_ranges_equal':True,'invalid_and_multi_range_416':True,
                'if_range_mismatch_full_response':True,'html_inline_rejected':True,
                'unjoined_workspace_denied':True,'anonymous_and_bad_bearer_denied':True,
                'httponly_samesite_strict_path_cookie':True,'server_verified_expiry_and_tamper':True,
                'cookie_clear_verified':True,'credential_in_media_url':False,
                'unexpired_cookie_rechecks_membership_on_next_range':True,'dedicated_reader_restored_to_viewer':True,
            },ensure_ascii=False,indent=2)+'\n').encode())
        finally:
            await execution.cancel(actor,queued['id']);await flush(actor)
    asyncio.run(run())
    report_path=Path('.local/deep-dub-media-transport-live.json')
    report=json.loads(report_path.read_text())
    def cli(*args):
        result=subprocess.run([sys.executable,'-c','from anp.cli.main import app;app()',*args],
            capture_output=True,text=True,encoding='utf8',timeout=30)
        assert result.returncode==0,'媒体CLI调用失败，输出不在异常中公开'
        return json.loads(result.stdout)
    try:
        asyncio.run(login(secret('ADMIN_EMAIL'),secret('ADMIN_PASSWORD')))
        local=cli('--endpoint','http://127.0.0.1:1','deep_dub','media','info',report['artifact_id'])
        remote=cli('--transport','http','deep_dub','media','info',report['artifact_id'])
        assert local==remote and 'object_path' not in local
        report['local_cli_info_equals_http']=True
        report_path.write_bytes((json.dumps(report,ensure_ascii=False,indent=2)+'\n').encode())
    finally:session_path().unlink(missing_ok=True)
