"""只对anp执行平台真实操作；凭据仅在内存，不写入验收回执。"""
import json
import subprocess
import uuid

import httpx
from manage import ROOT, PROJECT, ensure_owned_project, read_env


def check(base='http://127.0.0.1:51760'):
    # 固定项目与固定容器，不读取相邻环境。
    ensure_owned_project(read_env(ROOT/'.env')['ANP_WORKSPACE_ID'])
    raw=subprocess.check_output(['docker','exec',PROJECT+'-toolbox-1','cat','/data/config/credentials.json'])
    secret=json.loads(raw)
    result={}
    with httpx.Client(base_url=base,timeout=60,trust_env=False,follow_redirects=True) as client:
        response=client.post('/dbx/api/auth/login',json={'password':secret['DBX_PASSWORD']})
        assert response.status_code==200, 'DBX登录失败'
        assert client.get('/dbx/api/auth/check').json()['authenticated']
        result['dbx_login_cookie']=True
        response=client.get('/litellm/v1/models',headers={'Authorization':'Bearer '+secret['LITELLM_MASTER_KEY']})
        assert response.status_code==200 and isinstance(response.json()['data'],list), 'LiteLLM模型列表接口失败'
        result['litellm_authenticated_models']=True
        # Studio表编辑器实际调用；首页健康不能代替数据库连接验收。
        response=client.post('/supabase/api/platform/pg-meta/default/query',json={'query':'SELECT nspname AS name FROM pg_namespace'})
        assert response.status_code==200 and isinstance(response.json(),list), 'Studio schema加载失败'
        assert any(item.get('name')=='public' for item in response.json())
        result['studio_public_schema']=True
        response=client.post('/supabase/api/platform/pg-meta/default/query',json={'query':'SELECT 1 AS value'})
        assert response.status_code==200 and response.json()==[{'value':1}], 'Studio真实查询失败'
        result['studio_query']=True
        response=client.get('/supabase/api/platform/pg-meta/default/tables',params={'included_schemas':'public'})
        assert response.status_code==200 and isinstance(response.json(),list), 'Studio表加载失败'
        result['studio_public_tables_loaded']=True
        headers={'apikey':secret['SERVICE_KEY'],'Authorization':'Bearer '+secret['SERVICE_KEY']}
        bucket='anp-acceptance'
        response=client.get('/supabase/storage/v1/bucket/'+bucket,headers=headers)
        if response.status_code!=200:
            created=client.post('/supabase/storage/v1/bucket',headers=headers,json={'id':bucket,'name':bucket,'public':False})
            assert created.status_code in (200,201), 'Supabase私有桶创建失败'
        payload=('anp-storage-api-'+uuid.uuid4().hex).encode()
        path='/supabase/storage/v1/object/'+bucket+'/probe.txt'
        response=client.post(path,headers={**headers,'Content-Type':'text/plain','x-upsert':'true'},content=payload)
        assert response.status_code in (200,201), 'Supabase Storage实际上传失败'
        response=client.get(path,headers=headers)
        assert response.status_code==200 and response.content==payload, 'Supabase Storage下载内容不一致'
        result['supabase_storage_upload_download']=True
        denied=client.get(path,headers={'apikey':secret['ANON_KEY'],'Authorization':'Bearer '+secret['ANON_KEY']})
        assert denied.status_code in (400,401,403,404), '匿名用户不应读取私有桶'
        result['supabase_storage_private_rls']=True
        signed=client.post('/supabase/storage/v1/object/sign/'+bucket+'/probe.txt',headers=headers,json={'expiresIn':60})
        assert signed.status_code==200, 'Supabase签名下载生成失败'
        signed_path=signed.json()['signedURL']
        # 上游Storage返回相对/object/sign，沿同源Storage API目录访问。
        if signed_path.startswith('/object/'):
            signed_path='/supabase/storage/v1'+signed_path
        response=client.get(signed_path)
        assert response.status_code==200 and response.content==payload, 'Supabase签名下载失败'
        result['supabase_storage_signed_download']=True
    return result


if __name__=='__main__':
    result=check()
    (ROOT/'var/evidence').mkdir(parents=True,exist_ok=True)
    (ROOT/'var/evidence/platform-operations.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print('DBX登录、LiteLLM鉴权、Studio数据库查询和Supabase Storage读写通过')
