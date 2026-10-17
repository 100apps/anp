import json
import socket
from tests.conftest import cli,listening


def test_local_cli_without_business_web(identities):
    # 此测试在首次验收中必须在 API/Worker 启动前执行。
    assert not listening(8180) and not listening(8281), '请先停止业务服务，再验证默认本地 CLI'
    created=cli(['demo','tasks','create','--title','CLI / 本地独立执行','--text','Hello AI. 本地执行。'],identities['admin'])
    assert created.returncode==0,created.stdout
    task=json.loads(created.stdout)
    run=cli(['demo','tasks','run',task['id']],identities['admin'])
    assert run.returncode==0,run.stdout
    assert json.loads(run.stdout)['status']=='succeeded'
    result=cli(['demo','tasks','result',task['id']],identities['admin'])
    assert result.returncode==0,result.stdout
    assert json.loads(result.stdout)['report']['normalized']['text']=='Hello AI. 本地执行。'


def test_local_cli_enforces_viewer_acl(identities):
    response=cli(['demo','tasks','create','--text','无权限创建'],identities['viewer'],'viewer-test')
    assert response.returncode!=0
    assert json.loads(response.stdout)['error']['code']=='FORBIDDEN'
