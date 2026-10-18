"""默认本地/HTTP CLI读取冻结平台的待审图；不重新执行来源阶段。"""
import asyncio,json,os,subprocess,sys
from uuid import uuid4
import pytest
from anp import ROOT
from anp.auth.session import login,session_path

pytestmark=pytest.mark.skipif(os.environ.get('ANP_DEEP_DUB_SOURCE_PREPARATION_READ_LIVE')!='1',reason='需已完成来源准备与保留的待审平台线程')


def test_prepared_source_thread_read_local_and_http_cli(identities,monkeypatch):
    record=json.loads((ROOT/'.local/deep-dub-source-preparation-live.json').read_bytes())
    monkeypatch.setenv('ANP_PROFILE','source-preparation-read-'+uuid4().hex)
    try:
        asyncio.run(login(identities['admin']['email'],identities['admin']['password']))
        for options in [('--endpoint','http://127.0.0.1:1'),('--transport','http','--endpoint','http://127.0.0.1:8089')]:
            result=subprocess.run([sys.executable,'-m','anp.cli.entry',*options,'deep_dub','runs','source-preparation',record['run_id'],record['grant']['procedure_id']],
                cwd=ROOT,capture_output=True,text=True,encoding='utf-8',timeout=90)
            assert result.returncode==0,'待审流程读取失败，不公开原始输出'
            assert json.loads(result.stdout)==record['state']
    finally:session_path().unlink(missing_ok=True)
