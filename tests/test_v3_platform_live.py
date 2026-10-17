"""显式开启的 V3 单容器集成验收；不会把部分集成当作完整平台通过。"""
from html.parser import HTMLParser
import os
import time
from urllib.parse import urljoin
import httpx
import pytest
from anp.config import secret

pytestmark=pytest.mark.skipif(os.environ.get('ANP_V3_PLATFORM_SMOKE')!='1',reason='需显式开启隔离平台验收')
BASE='http://127.0.0.1:8089'


class Assets(HTMLParser):
    def __init__(self):super().__init__();self.urls=[]
    def handle_starttag(self,tag,attrs):
        attributes=dict(attrs)
        if tag=='script' and attributes.get('src'):self.urls.append(attributes['src'])
        if tag=='link' and attributes.get('rel')=='stylesheet':self.urls.append(attributes['href'])


def test_source_components_and_gateway_assets():
    expected={'auth','storage','meta','studio','dbx','langfuse-web','langfuse-worker','litellm','lens'}
    with httpx.Client(base_url=BASE,trust_env=False,timeout=20) as client:
        deadline=time.monotonic()+60
        while True:
            states=client.get('/_platform/services').json()['services']
            if all(states[name]['status']=='ready' for name in expected):break
            assert time.monotonic()<deadline,{name:spec['status'] for name,spec in states.items()}
            time.sleep(2)
        # 当前版本的部分集成必须诚实地保持全平台未就绪。
        if any(spec['status']=='missing_artifact' for spec in states.values()):
            assert client.get('/_platform/health').status_code==503
        for prefix,path in [('/supabase','/supabase/project/default'),('/dbx','/dbx/'),
            ('/langfuse','/langfuse/auth/sign-in'),('/litellm','/litellm/ui/')]:
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
