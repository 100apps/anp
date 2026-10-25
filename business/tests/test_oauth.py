"""OIDC 授权边界与 Supabase 身份桥；独立于真实公司 OAuth 联调。"""
import json
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from anp_business.server import oauth


@pytest.fixture
def client(monkeypatch,tmp_path):
    monkeypatch.setenv('ANP_BUSINESS_OAUTH_FILE',str(tmp_path/'oauth.json'))
    monkeypatch.setenv('ANP_BUSINESS_DATA_ROOT',str(tmp_path/'data'))
    app=FastAPI()
    app.include_router(oauth.router)
    app.add_middleware(oauth.OAuthSession)
    with TestClient(app) as client:
        yield client


def test_unconfigured_oauth_is_disabled(client):
    assert client.get('/api/auth/oauth/providers').json()==[]
    assert client.get('/api/auth/oauth/start').status_code==404


def test_oauth_rejects_unsafe_configuration():
    for extra in ({'issuer':'http://example.com'}, {'issuer':'https://idp.test','client_id':'app','redirect_uri':'http://evil.test/api/auth/oauth/callback'}, {'admin_subjects':['*']}):
        with pytest.raises(ValueError):
            oauth.validate_settings(extra)
    values=oauth.validate_settings({'issuer':'https://idp.test','client_id':'app','redirect_uri':'http://127.0.0.1:58010/api/auth/oauth/callback'})
    assert values['admin_subjects']==[] and values['client_secret_file']==''


def test_callback_without_state_is_rejected_without_session(client):
    result=client.get('/api/auth/oauth/callback?code=fixture')
    assert result.status_code==400 and 'anp_session' not in result.cookies
    assert 'fixture' not in result.text and result.headers['cache-control']=='no-store'


def test_start_pins_callback_origin(client):
    oauth.config_path().write_text(json.dumps({'issuer':'https://idp.test','client_id':'app','redirect_uri':'http://127.0.0.1:58010/api/auth/oauth/callback'}))
    response=client.get('/api/auth/oauth/start',follow_redirects=False)
    assert response.status_code==400


@pytest.mark.asyncio
async def test_bridge_subject_identity_does_not_auto_link_email(monkeypatch,tmp_path):
    import httpx
    calls=[]
    def handler(request):
        values=json.loads(request.content)
        calls.append((str(request.url),values))
        if request.url.path.endswith('generate_link'):
            assert values['email'].endswith('@oauth.anp.local')
            assert values['email']!='admin@anp.local'
            return httpx.Response(200,json={'user':{'id':'user-id'},'hashed_token':'fixture-token','verification_type':'signup'})
        if '/admin/users/' in request.url.path:
            return httpx.Response(200,json={})
        assert values['type']=='signup'
        return httpx.Response(200,json={'access_token':'fixture-session','expires_in':3600})
    key=tmp_path/'key';key.write_text('fixture-service-key')
    monkeypatch.setattr(oauth,'http_client',lambda **kwargs:httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    config={**oauth.DEFAULT,'issuer':'https://idp.test','service_key_file':str(key)}
    claims={'sub':'subject-1','email':'admin@anp.local'}
    assert (await oauth.business_session(claims,config))['access_token']=='fixture-session'
    assert calls[1][1]['app_metadata']['anp_role']=='user'
    config['admin_subjects']=['subject-1']
    await oauth.business_session(claims,config)
    assert calls[4][1]['app_metadata']['anp_role']=='admin'


def test_oauth_flow_cookie_is_secure_for_each_request_origin(monkeypatch,tmp_path):
    from fastapi import Request
    monkeypatch.setenv('ANP_BUSINESS_DATA_ROOT',str(tmp_path/'data'))
    app=FastAPI()
    @app.get('/api/auth/oauth/cookie-test')
    def cookie_test(request:Request):
        request.session['state']='fixture-state'
        return {'ok':True}
    app.add_middleware(oauth.OAuthSession)
    with TestClient(app,base_url='http://testserver') as browser:
        assert '; secure' not in browser.get('/api/auth/oauth/cookie-test').headers['set-cookie'].lower()
    # 同一个 ASGI 应用已初始化过 HTTP；后续 HTTPS 请求仍必须设置 Secure。
    with TestClient(app,base_url='https://testserver') as browser:
        result=browser.get('/api/auth/oauth/cookie-test')
        assert '; secure' in result.headers['set-cookie'].lower()
        assert 'httponly' in result.headers['set-cookie'].lower()
