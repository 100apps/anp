"""真实 CLI 子进程与临时 Uvicorn 契约验收；不启动媒体生产。"""
import asyncio
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
from uuid import uuid4

import pytest

from anp import ROOT
from anp.auth.session import login,session_path
from anp.config import secret
from anp.sdk.http import async_client
from anp.auth.session import current_actor
from apps.deep_dub.services.execution import service as execution
from apps.deep_dub.services.observations import deliver_one
from apps.deep_dub.services.versions import service as versions
from tests.test_deep_dub_contracts_live import PROFILE
from tests.test_deep_dub_contracts_live import context

pytestmark=pytest.mark.skipif(os.environ.get('ANP_DEEP_DUB_LIVE')!='1',reason='需显式开启真实 V3 CLI 验收')


def test_default_local_cli_without_business_web_matches_http_cli(context,monkeypatch):
    profile='deep-dub-cli-'+uuid4().hex
    monkeypatch.setenv('ANP_PROFILE',profile)
    monkeypatch.setenv('PYTHONIOENCODING','utf-8')
    asyncio.run(login(secret('ADMIN_EMAIL'),secret('ADMIN_PASSWORD')))
    server=None
    output=ROOT/'.local/deep-dub-cli-http-server.log'

    def cli(*args,success=True):
        result=subprocess.run([sys.executable,'-m','apps.deep_dub.cli',*args],cwd=ROOT,
            capture_output=True,text=True,encoding='utf8',timeout=30)
        assert (result.returncode==0)==success, 'CLI 状态与预期不符；原始输出仅留本机'
        try:return json.loads(result.stdout)
        except ValueError:raise AssertionError('CLI 未返回结构化 JSON') from None

    try:
        # 此时下面的专用 HTTP 进程尚未创建，local 直接执行服务契约。
        local=cli('--endpoint','http://127.0.0.1:1','projects','list');assert local
        project=local[0]['id']
        with socket.socket() as listener:
            listener.bind(('127.0.0.1',0));port=listener.getsockname()[1]
        endpoint=f'http://127.0.0.1:{port}'
        with output.open('wb') as log:
            server=subprocess.Popen([sys.executable,'-m','uvicorn','apps.deep_dub.api.application:app',
                '--host','127.0.0.1','--port',str(port),'--no-access-log'],cwd=ROOT,stdout=log,stderr=log,
                creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
            async def ready():
                async with async_client(timeout=1,trust_env=False) as c:
                    for _ in range(50):
                        assert server.poll() is None,'临时业务 API 已退出'
                        try:
                            if (await c.get(endpoint+'/openapi.json')).is_success:return
                        except Exception:
                            pass
                        await asyncio.sleep(.1)
                raise AssertionError('临时业务 API 未就绪')
            asyncio.run(ready())
            remote=cli('--transport','http','--endpoint',endpoint,'projects','list')
            assert local==remote
            assert cli('runs','list',project)==cli('--transport','http','--endpoint',endpoint,'runs','list',project)
            async def prepare_upload():
                actor=await current_actor()
                quality=await versions.create_quality_profile(actor,PROFILE)
                queued=await execution.queue(actor,project,{'quality_profile_id':quality['id'],
                    'purpose':'acceptance','flow_revision':'cli-artifact-contract-v1','expected_version':local[0]['row_version']})
                await execution.start(actor,queued['id'])
                attempt=(await execution.begin_stage(actor,queued['id'],{'node':'acceptance_cli_upload','input_hash':'c'*64}))['attempt']['id']
                await execution.mark_stage(actor,attempt,'started')
                return actor,queued['id'],attempt
            actor,run_id,attempt=asyncio.run(prepare_upload());started=time.monotonic()
            try:
                with tempfile.TemporaryDirectory(prefix='anp-cli-upload-test-') as folder:
                    file=Path(folder)/'payload.bin';file.write_bytes(b'acceptance-only-\n'*131072)
                    uploaded=cli('upload-artifact',attempt,str(file))
                    replayed=cli('--transport','http','--endpoint',endpoint,'upload-artifact',attempt,str(file))
                    assert uploaded==replayed
                async def finish_upload():
                    await execution.finish_stage(actor,attempt,{'status':'succeeded','outputs':[uploaded['id']],
                        'elapsed_seconds':time.monotonic()-started,'operation_stopped':True})
                    for _ in range(20):
                        item=await deliver_one(actor)
                        if not item['claimed']:break
                        assert item['sent']
                asyncio.run(finish_upload())
            finally:
                asyncio.run(execution.cancel(actor,run_id))
            # 真实账号降为 viewer 会话，确认两入口均由服务端拒绝，不靠 UI 隐藏按钮。
            asyncio.run(login('test-viewer@anp.local','ANP-Test-'+secret('ADMIN_PASSWORD')[:24]))
            local_denied=cli('projects','create','禁止写入','https://example.com/forbidden',success=False)
            remote_denied=cli('--transport','http','--endpoint',endpoint,'projects','create','禁止写入','https://example.com/forbidden',success=False)
            assert local_denied['error']['code']==remote_denied['error']['code']=='FORBIDDEN'
            Path('.local/deep-dub-cli-live.json').write_bytes((json.dumps({
                'checked_at_utc':datetime.now(timezone.utc).isoformat(),
                'local_default_without_test_http_process':True,'projects_equal':True,'runs_equal':True,
                'local_and_http_upload_same_artifact':True,'artifact_bytes':uploaded['bytes'],
                'viewer_forbidden_in_both_transports':True,
                'scope':'真实 CLI 子进程 + 临时业务 HTTP + V3 Supabase；尚未注册工作台或生产图'},
                ensure_ascii=False,indent=2)+'\n').encode())
    finally:
        if server is not None and server.poll() is None:
            server.terminate()
            try:server.wait(timeout=10)
            except subprocess.TimeoutExpired:
                server.kill();server.wait(timeout=10)
        session_path().unlink(missing_ok=True)
