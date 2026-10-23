"""私有配置可重复初始化；不能重置操作者的存储路径或已有密码。"""
from pathlib import Path
import json
import sys
import pytest

sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'scripts'))
import manage


def test_init_preserves_explicit_root_and_credentials(monkeypatch,tmp_path):
    monkeypatch.setattr(manage,'ROOT',tmp_path)
    (tmp_path/'business').mkdir()
    (tmp_path/'.env').write_text('ANP_DATA_ROOT="/srv/anp/owned-data"\nANP_SERVICES=redis\nPOSTGRES_PASSWORD=existing-secret-for-test\n',encoding='utf-8')
    manage.init()
    first=manage.read_env(tmp_path/'.env')
    manage.init()
    second=manage.read_env(tmp_path/'.env')
    assert first==second
    assert not (tmp_path/'var/dubbing').exists()
    assert not any(key.startswith('ANP_DUB_') for key in manage.read_env(tmp_path/'business/.env'))
    assert second['ANP_DATA_ROOT']=='/srv/anp/owned-data'
    assert second['POSTGRES_PASSWORD']=='existing-secret-for-test'
    assert second['ANP_SERVICES']=='redis'


def test_foreign_compose_instance_is_rejected(monkeypatch):
    def output(command,**kwargs):
        if command[1]=='ps':return 'foreign-container\n'
        return json.dumps([{'Config':{'Labels':{'dev.anp.workspace':'another-workspace'}}}])
    monkeypatch.setattr(manage.subprocess,'check_output',output)
    with pytest.raises(RuntimeError,match='不属于本工作区'):
        manage.ensure_owned_project('this-workspace')


def test_owned_compose_instance_can_be_managed(monkeypatch):
    def output(command,**kwargs):
        if command[1]=='ps':return 'owned-container\n'
        return json.dumps([{'Config':{'Labels':{'dev.anp.workspace':'this-workspace'}}}])
    monkeypatch.setattr(manage.subprocess,'check_output',output)
    manage.ensure_owned_project('this-workspace')


def test_realtime_upgrade_preserves_existing_credentials(monkeypatch,tmp_path):
    sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'docker/toolbox/runtime'))
    import settings
    monkeypatch.setattr(settings,'DATA',tmp_path)
    monkeypatch.setattr(settings.os,'chown',lambda *args: None)
    keys=('POSTGRES_PASSWORD','REDIS_PASSWORD','S3_SECRET_KEY','JWT_SECRET','CLICKHOUSE_PASSWORD')
    original={key:'existing-'+key for key in keys}
    for key in keys: monkeypatch.delenv(key,raising=False)
    path=tmp_path/'config/credentials.json'
    path.parent.mkdir(); path.write_text(json.dumps(original))
    upgraded=settings.credentials()
    assert all(upgraded[key]==value for key,value in original.items())
    assert len(upgraded['REALTIME_DB_ENC_KEY'])==16
    assert len(upgraded['REALTIME_DB_ENC_KEY_GCM'])==32
    assert len(upgraded['REALTIME_SECRET_KEY_BASE'])>=64
    assert settings.credentials()==upgraded
    assert path.stat().st_mode & 0o777 == 0o600


def test_realtime_cookie_fits_postgres_notification_channel(monkeypatch,tmp_path):
    sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'docker/toolbox/runtime'))
    import settings
    monkeypatch.setattr(settings,'DATA',tmp_path)
    monkeypatch.setattr(settings.os,'chown',lambda *args: None)
    for key in ('POSTGRES_PASSWORD','REDIS_PASSWORD','S3_SECRET_KEY','JWT_SECRET','CLICKHOUSE_PASSWORD'):
        monkeypatch.delenv(key,raising=False)
    secret=settings.credentials()
    limits={'node_studio_mb':512,'node_web_mb':1024,'node_worker_mb':512}
    first=settings.definitions(secret,limits)['supabase-realtime']['env']['RELEASE_COOKIE']
    assert 32<=len(first.encode())<=63
    assert first!=secret['REALTIME_SECRET_KEY_BASE']
    assert settings.definitions(secret,limits)['supabase-realtime']['env']['RELEASE_COOKIE']==first
