import json
import socket
from tests.conftest import cli


def test_local_cli_without_business_web(identities):
    # 所有业务与图执行地址都不可访问；任何误用 Web/Worker/Agent 都会令命令失败。
    disabled={'ANP_WORKBENCH_API_URL':'http://127.0.0.1:1','ANP_WORKBENCH_WORKER_URL':'http://127.0.0.1:1',
        'ANP_AGENT_URL':'http://127.0.0.1:1'}
    created=cli(['demo','tasks','create','--title','CLI / 本地独立执行','--text','Hello AI. 本地执行。'],identities['admin'],environment=disabled)
    assert created.returncode==0,created.stdout
    task=json.loads(created.stdout)
    run=cli(['demo','tasks','run',task['id']],identities['admin'],environment=disabled)
    assert run.returncode==0,run.stdout
    assert json.loads(run.stdout)['status']=='succeeded'
    result=cli(['demo','tasks','result',task['id']],identities['admin'],environment=disabled)
    assert result.returncode==0,result.stdout
    assert json.loads(result.stdout)['report']['normalized']['text']=='Hello AI. 本地执行。'


def test_local_cli_enforces_viewer_acl(identities):
    response=cli(['demo','tasks','create','--text','无权限创建'],identities['viewer'],'viewer-test')
    assert response.returncode!=0
    assert json.loads(response.stdout)['error']['code']=='FORBIDDEN'
