import asyncio
import json
import subprocess
from datetime import datetime,timezone,timedelta
from pathlib import Path
import time
import boto3
from botocore.config import Config
import httpx
from anp import ROOT
from anp.config import secret,value
from anp.management.dbx import configure_dbx
from tests.conftest import cli


def test_dbx_preconfigured_connections_and_login():
    assert httpx.get(value('dbx','url')+'/api/connection/list',trust_env=False).status_code in (401,403)
    result=asyncio.run(configure_dbx())
    assert len(result['connections'])==6
    assert all(c['status']=='ok' for c in result['connections'])


def test_one_instance_per_infrastructure():
    raw=subprocess.check_output(['docker','ps','--filter','label=com.docker.compose.project=anp2','--format','{{.Label "com.docker.compose.service"}}'],text=True)
    services=raw.splitlines()
    for name in ('postgres','redis','clickhouse','rustfs'):
        assert services.count(name)==1
    assert 'platform-db' not in services


def test_rustfs_private_artifact_and_tracing(identities):
    created=cli(['demo','tasks','create','--title','存储与追踪验收','--text','报告文件保存在 RustFS。'],identities['admin'],'storage-test')
    task=json.loads(created.stdout)
    run=cli(['demo','tasks','run',task['id']],identities['admin'],'storage-test')
    assert run.returncode==0,run.stdout
    result=json.loads(cli(['demo','tasks','result',task['id']],identities['admin'],'storage-test').stdout)
    s3=boto3.client('s3',endpoint_url=value('storage','endpoint'),region_name='us-east-1',
        aws_access_key_id=secret('S3_ACCESS_KEY'),aws_secret_access_key=secret('S3_SECRET_KEY'),
        config=Config(s3={'addressing_style':'path'}))
    objects=s3.list_objects_v2(Bucket='supabase-storage').get('Contents',[])
    match=next(o for o in objects if task['id'] in o['Key'])
    saved=json.loads(s3.get_object(Bucket='supabase-storage',Key=match['Key'])['Body'].read())
    assert saved==result['report']
    response=httpx.get(value('supabase','url')+'/storage/v1/object/authenticated/'+result['object_path'],
        headers={'apikey':secret('ANON_KEY'),'Authorization':'Bearer '+identities['outside']['token']},trust_env=False)
    assert response.status_code!=200
    deadline=time.monotonic()+30
    while time.monotonic()<deadline:
        response=httpx.get(value('langfuse','url')+'/api/public/v2/observations',trust_env=False,
            auth=(secret('LANGFUSE_PUBLIC_KEY'),secret('LANGFUSE_SECRET_KEY')),
            params={'traceId':task['id'].replace('-',''),'fromStartTime':task['created_at'],
                    'toStartTime':(datetime.now(timezone.utc)+timedelta(seconds=2)).isoformat()})
        assert response.status_code==200,response.text
        if {'业务节点/normalize','业务节点/analyze','业务节点/save'} <= {r['name'] for r in response.json()['data']}:
            return
        time.sleep(1)
    raise AssertionError('Langfuse 未收到三个本地节点观测')


def test_gateway_mock_model():
    response=httpx.post(value('litellm','url')+'/chat/completions',trust_env=False,timeout=30,
        headers={'Authorization':'Bearer '+secret('LITELLM_KEY')},
        json={'model':'demo-mock','messages':[{'role':'user','content':'验收模型网关'}]})
    assert response.status_code==200,response.text
    assert '模拟响应' in response.json()['choices'][0]['message']['content']
