"""验证 V3 存储拓扑契约，不使用或打印本机密钥。"""
import json
import os
import subprocess
from pathlib import Path
import yaml

ROOT=Path(__file__).resolve().parents[1]
COMPOSE=ROOT/'deploy/storage/compose.yaml'


def test_exactly_four_official_storage_images():
    document=yaml.safe_load(COMPOSE.read_text(encoding='utf8'))
    assert set(document['services'])=={'postgres','redis','clickhouse','rustfs'}
    prefixes={'postgres':'supabase/postgres:','redis':'redis:',
        'clickhouse':'clickhouse/clickhouse-server:','rustfs':'rustfs/rustfs@sha256:'}
    for name,service in document['services'].items():
        assert service['image'].startswith(prefixes[name])
        assert service['profiles']==['local',name]
        assert 'build' not in service
        assert all(port.startswith('127.0.0.1:') for port in service['ports'])
    assert all('anp_v3' in value['name'] for value in document['volumes'].values())


def test_storage_compose_is_valid_and_profiles_can_select_one_service():
    environment={**os.environ,**{key:'anp-v3-contract-only' for key in (
        'POSTGRES_PASSWORD','JWT_SECRET','REDIS_PASSWORD','CH_PASSWORD','S3_ACCESS_KEY','S3_SECRET_KEY')}}
    result=subprocess.run(['docker','compose','-f',str(COMPOSE),'--profile','redis','config','--format','json'],
        cwd=ROOT,env=environment,capture_output=True,text=True,encoding='utf8')
    assert result.returncode==0,result.stderr
    services=json.loads(result.stdout)['services']
    assert set(services)=={'redis'}
    assert services['redis']['command'][-1]=='anp-v3-contract-only'


def test_clickhouse_keeper_is_embedded_in_same_server():
    from xml.etree import ElementTree
    config=ElementTree.parse(ROOT/'deploy/storage/clickhouse.xml').getroot()
    assert config.findtext('keeper_server/tcp_port')=='9181'
    assert config.findtext('keeper_map_path_prefix')=='/anp/keeper_map'
