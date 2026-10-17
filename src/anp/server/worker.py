import os
import secrets
from fastapi import FastAPI,Header
from fastapi.responses import JSONResponse
from pydantic import BaseModel,Field
from anp.config import secret,value
import httpx
from anp.sdk.tracing import task_attributes
from anp.errors import BusinessError
from anp.sdk.objects import Objects
from anp.execution.nodes import execute_node

app=FastAPI(title='ANP 节点 Worker',version='0.2.0')
SOURCE_REVISION='worker-v2'


class NodeRequest(BaseModel):
    state: dict
    parent_span_id: str | None=Field(default=None,pattern=r'^[0-9a-f]{16}$')


class StudioTaskRequest(BaseModel):
    payload: dict
    request_key: str=Field(min_length=1,max_length=200)


@app.post('/internal/apps/{app_id}/studio/tasks')
async def studio_task(app_id:str,body:StudioTaskRequest,authorization: str | None=Header(None)):
    if not secrets.compare_digest(authorization or '', 'Bearer '+secret('WORKER_TOKEN')):
        raise BusinessError('Worker 访问凭据不正确',401,'UNAUTHENTICATED')
    from anp.auth.identity import actor_from_token
    from anp.applications.registry import application,entrypoint
    # Studio 是持有引擎令牌的本机管理员开发入口；身份由 Worker 决定。
    async with httpx.AsyncClient(timeout=15,trust_env=False) as client:
        response=await client.post(value('supabase','url')+'/auth/v1/token?grant_type=password',
            headers={'apikey':secret('ANON_KEY')},
            json={'email':secret('ADMIN_EMAIL'),'password':secret('ADMIN_PASSWORD')})
        response.raise_for_status()
    actor=await actor_from_token(response.json()['access_token'])
    service=entrypoint(application(app_id)['services']['entry'])
    task=await service.create(actor,body.payload,body.request_key)
    claim=await actor.objects.rpc('begin_task',p_task=task['id'],p_mode='studio')
    task=claim['task']
    return {'task_id':task['id'],'attempt':task['attempt'],'input_data':task['input'],
            'trace_attributes':task_attributes(task)}


@app.exception_handler(BusinessError)
async def business_error(request,error):
    return JSONResponse(status_code=error.status,content={'error':{'code':error.code,'message':error.message}})


@app.get('/health')
async def health():
    return {'status':'ok','role':'worker','pid':os.getpid(),'revision':SOURCE_REVISION}


@app.post('/internal/apps/{app_id}/nodes/{node}')
async def execute(app_id:str,node:str,body:NodeRequest,authorization: str | None=Header(None)):
    if not secrets.compare_digest(authorization or '', 'Bearer '+secret('WORKER_TOKEN')):
        raise BusinessError('Worker 访问凭据不正确',401,'UNAUTHENTICATED')
    objects=Objects(secret('SERVICE_ROLE_KEY'))
    task_id=body.state.get('task_id','')
    rows=await objects.select('tasks',id='eq.'+task_id,app_id='eq.'+app_id)
    if not rows: raise BusinessError('任务不存在',404,'NOT_FOUND')
    task=rows[0]
    try:
        output=await execute_node(objects,task,node,body.state,body.parent_span_id)
        if task['mode']=='studio' and node=='save':
            await objects.patch('tasks',{'status':'succeeded','result':output},id='eq.'+task_id)
    except Exception as error:
        if task['mode']=='studio' and task['status']!='cancelled':
            await objects.patch('tasks',{'status':'failed','error':str(error)[:1000]},id='eq.'+task_id)
        raise
    return {'output':output,'trace_attributes':task_attributes(task)}
