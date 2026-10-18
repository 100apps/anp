"""真实用户、独立业务端口、两种 CLI 正向改稿；只生成 acceptance 夹具。"""
import asyncio
from datetime import datetime,timezone
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time
from uuid import uuid4

import pytest

from anp import ROOT
from anp.auth.identity import actor_from_token
from anp.auth.session import login,session_path
from anp.config import secret
from anp.sdk.http import async_client
from apps.deep_dub.nodes.media import executable
from apps.deep_dub.services.projects import service as projects
from apps.deep_dub.services.execution import service as execution
from apps.deep_dub.services.versions import service as versions
from apps.deep_dub.services.artifacts import service as artifacts
from apps.deep_dub.services.candidates import service as candidates
from apps.deep_dub.services.editor import service as editor
from apps.deep_dub.services.workbench import service as workbench
from tests.test_deep_dub_contracts_live import context,PROFILE,pytestmark
from tests.test_deep_dub_runtime_live import flush


@pytest.fixture
def isolated_editor_api():
    from anp.dev.make import process_options,stop
    # 不重启其他会话正在使用的 8181/F5/API，使用相同宿主入口的独立回环端口。
    with socket.socket() as probe:
        probe.bind(('127.0.0.1',0))
        port=probe.getsockname()[1]
    endpoint=f'http://127.0.0.1:{port}'
    path=ROOT/'.local/logs/deep-dub-editor-api.log';path.parent.mkdir(parents=True,exist_ok=True)
    with path.open('w',encoding='utf-8') as log:
        command=[sys.executable,'-m','uvicorn','anp.server.api:app','--host','127.0.0.1','--port',str(port)]
        process=subprocess.Popen(command,cwd=ROOT,stdout=log,stderr=log,**process_options(command))
        try:
            async def ready():
                async with async_client(timeout=2,trust_env=False) as client:
                    for _ in range(80):
                        if process.poll() is not None:pytest.fail('独立验收 API 提前退出，诊断留在私有日志')
                        try:
                            response=await client.get(endpoint+'/openapi.json')
                            if response.status_code==200 and '/api/apps/deep_dub/projects/{project_id}/segments/{segment_id}/translations' in response.json()['paths']:return
                        except Exception:pass
                        await asyncio.sleep(.1)
                pytest.fail('独立验收 API 未就绪')
            asyncio.run(ready());yield endpoint
        finally:
            stop(process)


def test_real_editor_versions_http_local_cli_and_permissions(context,identities,isolated_editor_api,tmp_path,monkeypatch):
    _,tokens=context;endpoint=isolated_editor_api
    monkeypatch.setenv('ANP_PROFILE','deep-editor-'+uuid4().hex)
    monkeypatch.setenv('PYTHONIOENCODING','utf-8')
    video=tmp_path/'editor-fixture.mp4'
    subprocess.run([executable('ffmpeg'),'-v','error','-f','lavfi','-i','color=c=gray:s=64x64:r=10:d=2',
        '-an','-c:v','libx264','-pix_fmt','yuv420p',str(video)],check=True,capture_output=True,timeout=20)

    async def run():
        actor=await actor_from_token(tokens['admin'])
        project=await projects.create(actor,{'name':'[验收] 编辑器不可变译稿','source_url':'https://example.invalid/deep-editor-acceptance'})
        quality=await versions.create_quality_profile(actor,PROFILE)
        queued=await execution.queue(actor,project['id'],{'purpose':'acceptance','quality_profile_id':quality['id'],
            'flow_revision':'editor-contract-v1','expected_version':project['row_version']})
        try:
            await execution.start(actor,queued['id'])
            attempt=(await execution.begin_stage(actor,queued['id'],{'node':'acceptance_editor_inputs','input_hash':'e'*64}))['attempt']['id']
            await execution.mark_stage(actor,attempt,'started');started=time.monotonic()
            movie=await artifacts.upload(actor,attempt,video,'video/mp4',2000)
            source=await candidates.source(actor,attempt,{'artifact_id':movie['id'],'duration_ms':2000,
                'identity_evidence':{'fixture_only':True,'no_speech':True,'purpose':'人工编辑契约，不是翻译质量验证'}})
            parts=await candidates.transcript(actor,attempt,{'source_id':source['id'],'strategy_revision':'acceptance-editor-v1',
                'cues':[{'cue_no':0,'speaker_key':'fixture','source_text':'Twelve samples remain.','start_ms':0,'end_ms':1000}],
                'segments':[{'cue_numbers':[0]}]})
            segments=await actor.objects.select('deep_dub_segments',workspace_id='eq.'+actor.workspace_id,segment_set_id='eq.'+parts['id'])
            segment=segments[0]
            file=tmp_path/'context.json';file.write_text(json.dumps({'acceptance_only':True,'not_transcription':True}),encoding='utf-8')
            context_object=await artifacts.upload(actor,attempt,file,'application/json')
            original=await candidates.translation(actor,attempt,{'segment_id':segment['id'],'context_artifact_id':context_object['id'],
                'chinese_text':'还剩十二份样本。','model_id':'acceptance-manual-fixture','prompt_revision':'editor-seed-v1'})
            await execution.finish_stage(actor,attempt,{'status':'succeeded','outputs':[movie['id'],context_object['id']],
                'elapsed_seconds':time.monotonic()-started,'operation_stopped':True})
            current=await projects.get(actor,project['id'])
            body={'base_translation_id':original['id'],'expected_revision':original['revision'],
                'expected_project_version':current['row_version'],'chinese_text':'还有十二份样本，等待核对。'}
            key=str(uuid4());path=f'/api/apps/deep_dub/projects/{project["id"]}/segments/{segment["id"]}/translations'
            async with async_client(trust_env=False,timeout=30,headers={'Authorization':'Bearer '+tokens['admin']}) as client:
                response=await client.post(endpoint+path,json=body,headers={'Idempotency-Key':key})
                assert response.status_code==201,'真实端口改稿失败，原始响应不公开'
                receipt=response.json()
                assert receipt==await editor.edit_translation(actor,project['id'],segment['id'],body,key)
                assert (await client.post(endpoint+path,json=body)).status_code==409
                assert (await client.post(endpoint+path,json={**body,'chinese_text':'冲突重试。'},headers={'Idempotency-Key':key})).status_code==409
                denied=await client.post(endpoint+path,json=body,headers={'Authorization':'Bearer '+tokens['viewer']})
                assert denied.status_code==403
                anonymous=await client.post(endpoint+path,json=body,headers={'Authorization':''})
                assert anonymous.status_code==401
                foreign=await client.post(endpoint+path,json=body,headers={'Authorization':'Bearer '+identities['outside']['token']})
                assert foreign.status_code in (403,404)
                snapshot_path=endpoint+f'/api/apps/deep_dub/projects/{project["id"]}/studio'
                read=await client.get(snapshot_path)
                assert read.status_code==200 and read.json()==await workbench.studio(actor,project['id'])
                view=read.json()
                assert len(view['translations'])==2 and view['translation_edits'][0]['translation_id']==receipt['translation_id']
                assert all('object_path' not in item for item in view['artifacts'])
                assert (await client.get(snapshot_path,headers={'Authorization':'Bearer '+tokens['viewer']})).status_code==200
                assert (await client.get(snapshot_path,headers={'Authorization':''})).status_code==401
                assert (await client.get(snapshot_path,headers={'Authorization':'Bearer '+identities['outside']['token']})).status_code in (403,404)
            rows=await actor.objects.select('deep_dub_translations',workspace_id='eq.'+actor.workspace_id,segment_id='eq.'+segment['id'],order='revision.asc')
            assert len(rows)==2 and rows[0]==original and rows[1]['id']==receipt['translation_id'] and rows[1]['model_id']=='human:editor'
            assert await actor.objects.select('deep_dub_audits',translation_id='eq.'+receipt['translation_id'])==[]
            return project,segment,queued,body,key,receipt
        finally:
            await execution.cancel(actor,queued['id']);await flush(actor)
    try:
        project,segment,queued,body,key,receipt=asyncio.run(run())
        body_path=tmp_path/'edit.json';body_path.write_text(json.dumps(body,ensure_ascii=False),encoding='utf-8')
        asyncio.run(login(secret('ADMIN_EMAIL'),secret('ADMIN_PASSWORD')))
        def cli(*arguments):
            result=subprocess.run([sys.executable,'-m','anp.cli.entry',*arguments,'deep_dub','edit-translation',project['id'],segment['id'],
                '--input',str(body_path),'--request-key',key],cwd=ROOT,capture_output=True,text=True,encoding='utf-8',timeout=60)
            assert result.returncode==0,'CLI 改稿失败，原始响应不公开'
            return json.loads(result.stdout)
        # 本地 CLI 指向不可用 API 地址，仍须通过原身份直接调用业务服务。
        assert cli('--endpoint','http://127.0.0.1:1')==receipt
        assert cli('--transport','http','--endpoint',endpoint)==receipt
        def studio_cli(*arguments):
            result=subprocess.run([sys.executable,'-m','anp.cli.entry',*arguments,'deep_dub','projects','studio',project['id']],
                cwd=ROOT,capture_output=True,text=True,encoding='utf-8',timeout=60)
            assert result.returncode==0,'CLI 工作台快照失败，原始响应不公开'
            return json.loads(result.stdout)
        assert studio_cli('--endpoint','http://127.0.0.1:1')==studio_cli('--transport','http','--endpoint',endpoint)
        Path('.local/deep-dub-editor-live.json').write_text(json.dumps({'checked_at_utc':datetime.now(timezone.utc).isoformat(),
            'purpose':'acceptance','project_id':project['id'],'segment_id':segment['id'],'run_id':queued['id'],
            'receipt':receipt,'real_http_port':8183,'default_local_cli_without_business_api':True,'http_cli_equal':True,
            'studio_local_cli_equals_http_cli':True,'studio_read_service_equals_real_http':True,'studio_viewer_read_allowed':True,
            'old_translation_unchanged':True,'no_new_audit_passed':True,'viewer_and_anonymous_and_foreign_denied':True,
            'run_cancelled':True,'production_tts_started':False},ensure_ascii=False,indent=2),encoding='utf-8')
    finally:session_path().unlink(missing_ok=True)
