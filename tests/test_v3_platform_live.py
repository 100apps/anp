"""显式开启的 V3 单容器集成验收；不会把部分集成当作完整平台通过。"""
from html.parser import HTMLParser
import hashlib
import json
import os
import time
from urllib.parse import urljoin
import httpx
import pytest
from anp.config import secret
from anp import ROOT

pytestmark=pytest.mark.skipif(os.environ.get('ANP_V3_PLATFORM_SMOKE')!='1',reason='需显式开启隔离平台验收')
BASE='http://127.0.0.1:8089'


@pytest.fixture(scope='module',autouse=True)
def wait_for_integrated_components():
    expected={'auth','storage','postgrest','meta','studio','dbx','langfuse-web','langfuse-worker','litellm','lens','agent'}
    deadline=time.monotonic()+120
    with httpx.Client(base_url=BASE,trust_env=False,timeout=5) as client:
        while True:
            try:
                response=client.get('/_platform/services');response.raise_for_status()
                states={name:spec['status'] for name,spec in response.json()['services'].items()}
                if all(states.get(name)=='ready' for name in expected):return
            except (httpx.HTTPError,ValueError):states={'gateway':'不可访问'}
            assert time.monotonic()<deadline,states
            time.sleep(2)


class Assets(HTMLParser):
    def __init__(self):super().__init__();self.urls=[]
    def handle_starttag(self,tag,attrs):
        attributes=dict(attrs)
        if tag=='script' and attributes.get('src'):self.urls.append(attributes['src'])
        if tag=='link' and attributes.get('rel')=='stylesheet':self.urls.append(attributes['href'])


def test_source_components_and_gateway_assets():
    with httpx.Client(base_url=BASE,trust_env=False,timeout=20) as client:
        states=client.get('/_platform/services').json()['services']
        assert all(spec['status']=='ready' for spec in states.values())
        assert client.get('/_platform/health').status_code==200
        for prefix,path in [('/supabase','/supabase/project/default'),('/dbx','/dbx/'),
            ('/langfuse','/langfuse/auth/sign-in'),('/litellm','/litellm/ui/'),('/lens','/lens/ui/')]:
            auth=('supabase',secret('DASHBOARD_PASSWORD')) if prefix=='/supabase' else None
            response=client.get(path,auth=auth,follow_redirects=True)
            assert response.status_code==200,(prefix,response.status_code)
            parser=Assets();parser.feed(response.text)
            assert parser.urls,(prefix,'缺少前端静态资源')
            for url in dict.fromkeys(parser.urls):
                if url.startswith('http'):continue
                resolved=urljoin(str(response.url),url)
                assert httpx.URL(resolved).path.startswith(prefix+'/'),(prefix,url)
                asset=client.get(resolved,auth=auth)
                assert asset.status_code==200,(prefix,url,asset.status_code)
        assert client.get('/supabase/project/default').status_code==401
        redirect=client.get('/lens/')
        assert redirect.status_code in (302,307,308)
        assert redirect.headers['location'].startswith('/lens/ui/')
        assert client.get('/dbx').headers['location']=='/dbx/'


def test_dbx_saved_connections_really_connect():
    with httpx.Client(base_url=BASE,trust_env=False,timeout=30) as client:
        client.post('/dbx/api/auth/login',json={'password':secret('DBX_PASSWORD')}).raise_for_status()
        response=client.get('/dbx/api/connection/list');response.raise_for_status()
        wanted={'anp-postgres','anp-agent_local','anp-litellm','anp-langfuse','anp-redis','anp-clickhouse'}
        configurations={entry['id']:entry for entry in response.json()}
        assert wanted<=configurations.keys()
        for name in wanted:
            config=configurations[name]
            config['driver_profile']=config.get('driver_profile') or config['db_type']
            for action in ['test','connect']:
                response=client.post('/dbx/api/connection/'+action,json={'config':config})
                assert response.status_code==200,(name,action,response.status_code)


def test_seeded_supabase_login_and_platform_acl():
    with httpx.Client(base_url=BASE,trust_env=False,timeout=20) as client:
        response=client.post('/supabase/auth/v1/token',params={'grant_type':'password'},
            json={'email':secret('ADMIN_EMAIL'),'password':secret('ADMIN_PASSWORD')})
        assert response.status_code==200
        session=response.json();token=session['access_token'];user_id=session['user']['id']
        headers={'Authorization':'Bearer '+token,'Accept-Profile':'platform','Content-Profile':'platform'}
        response=client.post('/supabase/rest/v1/rpc/is_admin',headers=headers,json={})
        assert response.status_code==200 and response.json() is True
        response=client.get('/supabase/rest/v1/members',headers=headers,params={'user_id':'eq.'+user_id})
        assert response.status_code==200 and len(response.json())==1
        assert response.json()[0]['role']=='admin'
        assert client.get('/supabase/rest/v1/members',headers={'Accept-Profile':'platform'}).status_code in (401,403)


def test_lens_session_auto_catalog_and_restricted_analysis_key():
    configuration=json.loads((ROOT/'.local/generated/platform-v3/config.json').read_text(encoding='utf8'))
    lens=configuration['services']['lens']['environment']
    with httpx.Client(base_url=BASE,trust_env=False,timeout=90) as client:
        assert client.get('/lens/auth/session').status_code==401
        response=client.post('/lens/auth/session',json={'token':lens['LENS_ADMIN_TOKEN']})
        assert response.status_code==200
        assert client.get('/lens/auth/session').json()['user_role']=='proxy_admin'
        models=client.get('/lens/lens/models');assert models.status_code==200
        assert 'auto' in [model['id'] for model in models.json()['data']]
        key=lens['LENS_ANALYSIS_API_KEY']
        response=client.get('/litellm/key/info',params={'key':hashlib.sha256(key.encode()).hexdigest()},
            headers={'Authorization':'Bearer '+secret('LITELLM_KEY')})
        assert response.status_code==200 and response.json()['info']['models']==['auto']
        for name,limit in configuration.get('lens_analysis_limits',{}).items():
            assert response.json()['info'][name]==limit
        body={'model':'auto:fast','messages':[{'role':'user','content':'只回复：连接成功'}],'max_tokens':32}
        headers={'Authorization':'Bearer '+key}
        assert client.post('/litellm/v1/chat/completions',headers=headers,json=body).status_code==403
        if os.environ.get('ANP_SKIP_LIVE_LLM')=='1':return  # CI 无私人上游密钥，仅跳过最后的真实付费模型调用。
        body['model']='auto'
        response=client.post('/litellm/v1/chat/completions',headers=headers,json=body)
        assert response.status_code==200
        assert response.json()['choices'][0]['message']['content'].strip()
