"""工作台 API 宿主，Web、HTTP CLI 使用相同的业务服务契约。"""
import asyncio
from contextlib import asynccontextmanager,suppress
import logging
import os
from pathlib import Path
from fastapi import FastAPI,Depends
from fastapi.responses import JSONResponse,RedirectResponse
from pydantic import BaseModel,Field
import httpx
from anp.config import value,secret
from anp.applications.registry import applications,entrypoint
from anp.auth.dependencies import get_actor
from anp.errors import BusinessError
from anp.sdk.objects import Objects
from anp.execution.agent_server import reconcile_remote

logger=logging.getLogger('anp.api')
SOURCE_REVISION='api-v2'


async def coordinator():
    objects=Objects(secret('SERVICE_ROLE_KEY'))
    while True:
        try:
            tasks=await objects.select('tasks',mode='eq.http',status='eq.running',limit=50)
            for task in tasks:
                try: await reconcile_remote(objects,task)
                except Exception as error: logger.warning('任务 %s 稍后重试：%s',task['id'],type(error).__name__)
        except Exception as error:
            logger.warning('等待对象服务：%s',type(error).__name__)
        await asyncio.sleep(0.7)


@asynccontextmanager
async def lifespan(app):
    job=asyncio.create_task(coordinator())
    yield
    job.cancel()
    with suppress(asyncio.CancelledError): await job


app=FastAPI(title='ANP 工作台 API',version='0.2.0',lifespan=lifespan)


@app.exception_handler(BusinessError)
async def business_error(request,error):
    return JSONResponse(status_code=error.status,content={'error':{'code':error.code,'message':error.message}})


@app.exception_handler(httpx.HTTPError)
async def upstream_error(request,error):
    return JSONResponse(status_code=503,content={'error':{'code':'UPSTREAM_UNAVAILABLE','message':'平台服务暂不可用'}})


@app.get('/')
async def home():
    return RedirectResponse(value('workbench','web_url'))


@app.get('/health')
async def health():
    return {'status':'ok','role':'api','pid':os.getpid(),'revision':SOURCE_REVISION}


@app.get('/api/public/config')
async def public_config():
    return {'title':value('workbench','title'),'supabase_url':value('supabase','url'),
            'supabase_anon_key':secret('ANON_KEY')}


@app.get('/api/me')
async def me(actor=Depends(get_actor)):
    objects=actor.objects
    memberships=await objects.select('members',user_id='eq.'+actor.user['id'])
    current=next(m for m in memberships if m['workspace_id']==actor.workspace_id)
    permissions=await objects.select('role_permissions',role='eq.'+current['role'])
    return {'id':actor.user['id'],'email':actor.user['email'],'workspace_id':actor.workspace_id,
        'role':current['role'],'permissions':[p['permission'] for p in permissions],
        'workspaces':await objects.select('workspaces'),
        'is_platform_admin':await objects.rpc('is_admin')}


@app.get('/api/applications')
async def list_apps(actor=Depends(get_actor)):
    result=[]
    for app_id,manifest in applications().items():
        if await actor.objects.rpc('has_permission',p_workspace=actor.workspace_id,p_permission=app_id+'.task.read'):
            result.append({'id':app_id,'name':manifest['name'],'description':manifest['description'],
                           'version':manifest['version'],'menu':manifest['menu']})
    return result


@app.get('/api/members')
async def members(actor=Depends(get_actor)):
    await actor.require('workspace.members.manage')
    rows=await actor.objects.select('members',workspace_id='eq.'+actor.workspace_id)
    profiles={p['user_id']:p for p in await actor.objects.select('profiles')}
    return [{**row,'email':profiles.get(row['user_id'],{}).get('email','')} for row in rows]


class MemberRole(BaseModel):
    role: str=Field(pattern='^(admin|developer|viewer)$')


@app.patch('/api/members/{user_id}')
async def change_role(user_id:str,body:MemberRole,actor=Depends(get_actor)):
    await actor.objects.rpc('set_member_role',p_workspace=actor.workspace_id,p_user=user_id,p_role=body.role)
    return {'updated':True}


@app.get('/api/connections')
async def connections(actor=Depends(get_actor)):
    if not await actor.objects.rpc('is_admin'):
        raise BusinessError('仅平台管理员可以管理基础设施连接',403,'FORBIDDEN')
    items=[
        ('supabase','Supabase',value('supabase','url'),'/auth/v1/health','认证、对象与文件'),
        ('litellm','LiteLLM',value('litellm','url').removesuffix('/v1'),'/health/liveliness','模型网关'),
        ('langfuse','Langfuse',value('langfuse','url'),'/api/public/health','运行追踪'),
        ('rustfs','RustFS',value('storage','endpoint'),'/health/ready','对象存储'),
        ('dbx','DBX',value('dbx','url'),'/api/auth/check','数据库管理'),
        ('agent','Agent Server',value('agent','url'),'/ok','图执行引擎'),
    ]
    async def check(item):
        name,label,url,probe,description=item
        try:
            async with httpx.AsyncClient(timeout=4,trust_env=False) as client:
                headers={'apikey':secret('ANON_KEY')} if name=='supabase' else {}
                if name=='agent': headers={'Authorization':'Bearer '+secret('ENGINE_TOKEN')}
                r=await client.get(url+probe,headers=headers)
            status='ok' if r.is_success else 'unavailable'
        except httpx.HTTPError: status='unavailable'
        public=url.replace('127.0.0.1','localhost')
        if name=='rustfs': public='http://localhost:9001'
        return {'id':name,'name':label,'url':public,'status':status,'description':description}
    result=await asyncio.gather(*(check(i) for i in items))
    from urllib.parse import quote
    result.append({'id':'studio','name':'LangGraph Studio',
        'url':'https://smith.langchain.com/studio/?baseUrl='+quote(value('agent','url'),safe=':/'),
        'status':'external','description':'使用 LangSmith 账号；首次连接需配置 Authorization 请求头，见本机登录信息'})
    return result


for manifest in applications().values():
    app.include_router(entrypoint(manifest['api']['entry']))
