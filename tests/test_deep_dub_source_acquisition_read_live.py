"""冻结生产读取新下载回执；不再次获取视频。"""
import asyncio,json,os,subprocess,sys
from uuid import uuid4
import pytest
from anp import ROOT
from anp.auth.session import login,session_path

pytestmark=pytest.mark.skipif(os.environ.get('ANP_DEEP_DUB_SOURCE_ACQUISITION_READ_LIVE')!='1',reason='需本轮真实下载回执与冻结生产API')


def test_new_source_read_local_cli_and_frozen_http(identities,monkeypatch):
    record=json.loads((ROOT/'.local/deep-dub-source-acquisition-live.json').read_bytes())
    monkeypatch.setenv('ANP_PROFILE','source-fetch-read-'+uuid4().hex)
    try:
        asyncio.run(login(identities['admin']['email'],identities['admin']['password']))
        for options in [('--endpoint','http://127.0.0.1:1'),('--transport','http','--endpoint','http://127.0.0.1:8181')]:
            result=subprocess.run([sys.executable,'-m','anp.cli.entry',*options,'deep_dub','runs','source-acquisition',record['run_id']],
                cwd=ROOT,capture_output=True,text=True,encoding='utf-8',timeout=90)
            assert result.returncode==0,'新来源获取回执读取失败，不公开原始输出'
            assert json.loads(result.stdout)==record['readback']
    finally:session_path().unlink(missing_ok=True)
