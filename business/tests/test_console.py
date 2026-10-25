"""管理边界、配置冲突和凭据泄露回归；不是上游服务验收。"""
import json
from pathlib import Path
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from langgraph_sdk import Auth
from starlette.requests import Request
from anp_business.server import console, security
from anp_business.server.platforms import PLATFORMS, forwarded_headers


@pytest.fixture
def client(monkeypatch, tmp_path):
    async def user(headers):
        token = headers.get('authorization', '')
        if not token:
            raise Auth.exceptions.HTTPException(status_code=401, detail='请先登录')
        return {'id':'test', 'role':'admin' if token == 'Bearer admin' else 'user'}
    monkeypatch.setattr(security, 'current_user', user)
    monkeypatch.setenv('ANP_BUSINESS_OVERRIDES', str(tmp_path / 'overrides.json'))
    monkeypatch.delenv('ANP_NATIVE_ROOT', raising=False)
    app = FastAPI()
    app.include_router(console.router)
    app.add_middleware(security.PlatformAccess)
    with TestClient(app) as value:
        yield value


ADMIN = {'authorization':'Bearer admin'}


def test_console_requires_admin_and_same_origin(client):
    assert client.get('/api/console/state').status_code == 401
    assert client.get('/api/console/state', headers={'authorization':'Bearer user'}).status_code == 403
    assert client.post('/api/console/credentials/langfuse', headers={**ADMIN,'origin':'https://evil.invalid'}).status_code == 403
    result = client.get('/api/console/state', headers=ADMIN)
    assert result.status_code == 200 and result.headers['cache-control'] == 'no-store'
    assert result.json()['managed'] is False


def test_mapping_persists_and_rejects_stale_or_secret_urls(client):
    state = client.get('/api/console/state', headers=ADMIN).json()
    values = {'platforms':state['platforms'], 'revision':state['mapping_revision'], 'host':'127.0.0.1'}
    values['platforms']['dbx'] = 'http://127.0.0.1:44444/dbx/'
    assert client.put('/api/console/mappings', headers=ADMIN, json=values).status_code == 200
    assert client.get('/api/console/state', headers=ADMIN).json()['platforms']['dbx'] == values['platforms']['dbx']
    assert client.put('/api/console/mappings', headers=ADMIN, json=values).status_code == 409
    values['revision'] = client.get('/api/console/state', headers=ADMIN).json()['mapping_revision']
    for bad in ('file:///etc/passwd', 'http://admin:secret@localhost/', 'http://localhost/?secret=foo', 'http://localhost:bad/'):
        values['platforms']['dbx'] = bad
        assert client.put('/api/console/mappings', headers=ADMIN, json=values).status_code == 400
    assert Path(console.override_path()).stat().st_mode & 0o777 == 0o600


def test_native_credentials_do_not_appear_in_inventory(client, monkeypatch, tmp_path):
    root = tmp_path / 'bundle'
    for name in ('runtime','config','var/secrets'):
        (root / name).mkdir(parents=True)
    (root / 'anp').touch()
    (root / 'runtime/console_control.py').touch()
    (root / 'config/anp.json').write_text(json.dumps({'ports':{'langfuse-web':53100,'litellm':54000}}))
    (root / 'var/secrets/runtime.json').write_text(json.dumps({'langfuse-user-password':'fixture-not-a-real-secret','litellm':'fixture-key'}))
    monkeypatch.setenv('ANP_NATIVE_ROOT', str(root))
    response = client.post('/api/console/credentials/langfuse', headers=ADMIN)
    assert response.status_code == 200 and response.json()['password'] == 'fixture-not-a-real-secret'
    assert response.headers['cache-control'] == 'no-store'
    assert client.post('/api/console/credentials/dbx', headers=ADMIN).status_code == 404
    override = console.override_path()
    override.write_text(json.dumps({'platforms':{'langfuse':'https://external.invalid/'}}))
    assert client.post('/api/console/credentials/langfuse', headers=ADMIN).status_code == 409


def test_grafana_trusted_identity_cannot_be_spoofed(monkeypatch):
    monkeypatch.setenv('ANP_NATIVE_ROOT', '/fixture')
    scope = {'type':'http','method':'GET','path':'/grafana/','scheme':'http','server':('localhost',80), 'headers':[(b'x-anp-user',b'evil')], 'anp_user':{'role':'admin'}}
    assert dict(forwarded_headers(Request(scope), PLATFORMS['grafana']))['x-anp-user'] == 'admin'
    scope.pop('anp_user')
    assert 'x-anp-user' not in dict(forwarded_headers(Request(scope), PLATFORMS['grafana']))



def test_rustfs_credentials_use_own_access_key_and_console_port(client, monkeypatch, tmp_path):
    root = tmp_path / 'bundle'
    for name in ('runtime', 'defaults', 'var/secrets'):
        (root / name).mkdir(parents=True)
    (root / 'anp').touch()
    (root / 'runtime/console_control.py').touch()
    (root / 'defaults/anp.json').write_text(json.dumps({'ports':{'rustfs':59002}}))
    (root / 'var/secrets/runtime.json').write_text(json.dumps({'s3-access':'fixture-access','s3':'fixture-secret'}))
    monkeypatch.setenv('ANP_NATIVE_ROOT', str(root))
    response = client.post('/api/console/credentials/rustfs', headers=ADMIN)
    assert response.status_code == 200
    assert response.json()['username'] == 'fixture-access' and response.json()['password'] == 'fixture-secret'
    assert response.headers['cache-control'] == 'no-store'
