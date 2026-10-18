"""复用已生成的整轨证据验证读取CLI，不重复执行模型。"""
import asyncio,json,os,subprocess,sys
from pathlib import Path
from uuid import uuid4
import pytest

from anp import ROOT
from anp.auth.session import login,session_path

pytestmark=pytest.mark.skipif(os.environ.get('ANP_DEEP_DUB_SOURCE_READ_LIVE')!='1',reason='需已有本轮私有ASR验收回执')


def test_source_result_read_cli_local_without_business_web_and_http(identities,monkeypatch):
    file=ROOT/'.local/deep-dub-source-asr-live.json'
    if not file.is_file():pytest.skip('先运行整轨来源ASR验收')
    record=json.loads(file.read_bytes());monkeypatch.setenv('ANP_PROFILE','source-result-read-'+uuid4().hex)
    try:
        asyncio.run(login(identities['admin']['email'],identities['admin']['password']))
        for arguments in [('--endpoint','http://127.0.0.1:1'),('--transport','http','--endpoint','http://127.0.0.1:8181')]:
            result=subprocess.run([sys.executable,'-m','anp.cli.entry',*arguments,'deep_dub','projects','source-recognition',
                record['project_id'],record['source_id']],cwd=ROOT,capture_output=True,text=True,encoding='utf-8',timeout=45)
            assert result.returncode==0,'来源识别结果CLI读取失败；不公开原始输出'
            assert json.loads(result.stdout)==record['readback']
    finally:session_path().unlink(missing_ok=True)
