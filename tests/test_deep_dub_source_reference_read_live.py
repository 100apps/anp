"""冻结生产API和两种CLI读取既有官方参考，不重复下载或执行模型。"""
import asyncio,json,os,subprocess,sys
from uuid import uuid4
import pytest
from anp import ROOT
from anp.auth.session import login,session_path

pytestmark=pytest.mark.skipif(os.environ.get('ANP_DEEP_DUB_REFERENCE_READ_LIVE')!='1',reason='需本轮参考比对证据及生产API')


def test_frozen_production_api_and_cli_read_same_reference(identities,monkeypatch):
    record=json.loads((ROOT/'.local/deep-dub-source-reference-live.json').read_bytes())
    monkeypatch.setenv('ANP_PROFILE','reference-result-read-'+uuid4().hex)
    try:
        asyncio.run(login(identities['admin']['email'],identities['admin']['password']))
        for options in [('--endpoint','http://127.0.0.1:1'),('--transport','http','--endpoint','http://127.0.0.1:8181')]:
            completed=subprocess.run([sys.executable,'-m','anp.cli.entry',*options,'deep_dub','source-reference',record['result']['attempt']['id']],
                cwd=ROOT,capture_output=True,text=True,encoding='utf-8',timeout=90)
            assert completed.returncode==0,'冻结生产API/CLI参考证据读取失败'
            assert json.loads(completed.stdout)==record['readback']
    finally:session_path().unlink(missing_ok=True)
