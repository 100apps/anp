"""从冻结生产API与本地CLI读取既有整轨词时间，不重复启动GPU模型。"""
import asyncio,json,os,subprocess,sys
from uuid import uuid4
import pytest
from anp import ROOT
from anp.auth.session import login,session_path

pytestmark=pytest.mark.skipif(os.environ.get('ANP_DEEP_DUB_SOURCE_ALIGNMENT_READ_LIVE')!='1',reason='需本轮整轨词时间证据和冻结生产API')


def test_source_words_result_read_cli_local_and_frozen_http(identities,monkeypatch):
    record=json.loads((ROOT/'.local/deep-dub-source-alignment-live.json').read_bytes())
    monkeypatch.setenv('ANP_PROFILE','source-word-result-read-'+uuid4().hex)
    try:
        asyncio.run(login(identities['admin']['email'],identities['admin']['password']))
        for options in [('--endpoint','http://127.0.0.1:1'),('--transport','http','--endpoint','http://127.0.0.1:8181')]:
            completed=subprocess.run([sys.executable,'-m','anp.cli.entry',*options,'deep_dub','projects','source-word-timing',record['project_id'],record['source_id']],
                cwd=ROOT,capture_output=True,text=True,encoding='utf-8',timeout=90)
            assert completed.returncode==0,'完整来源词时间读取失败；不公开原始输出'
            assert json.loads(completed.stdout)==record['readback']
    finally:session_path().unlink(missing_ok=True)
