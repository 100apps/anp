import asyncio
import time
import httpx
from anp.sdk.http import async_client
from anp.config import value,secret
from anp.sdk.objects import Objects
from anp.errors import BusinessError

DEFAULT_WORKSPACE='00000000-0000-4000-8000-000000000001'


async def create_user(email,password,role='developer',workspace_id=DEFAULT_WORKSPACE):
    headers={'apikey':secret('SERVICE_ROLE_KEY'),'Authorization':'Bearer '+secret('SERVICE_ROLE_KEY')}
    async with async_client(timeout=30,trust_env=False) as client:
        response=await client.get(value('supabase','url')+'/auth/v1/admin/users',headers=headers,params={'per_page':1000})
        response.raise_for_status()
        user=next((u for u in response.json()['users'] if u['email']==email),None)
        if user is None:
            response=await client.post(value('supabase','url')+'/auth/v1/admin/users',headers=headers,
                json={'email':email,'password':password,'email_confirm':True})
            response.raise_for_status()
            user=response.json()
    objects=Objects(secret('SERVICE_ROLE_KEY'))
    await objects.upsert('profiles',{'user_id':user['id'],'email':email,'display_name':email.split('@')[0]},'user_id')
    await objects.upsert('members',{'workspace_id':workspace_id,'user_id':user['id'],'role':role},'workspace_id,user_id')
    return {'id':user['id'],'email':email,'role':role,'workspace_id':workspace_id}


async def seed():
    user=await create_user(secret('ADMIN_EMAIL'),secret('ADMIN_PASSWORD'),'admin')
    await Objects(secret('SERVICE_ROLE_KEY')).upsert('admins',{'user_id':user['id']},'user_id')
    headers={'apikey':secret('SERVICE_ROLE_KEY'),'Authorization':'Bearer '+secret('SERVICE_ROLE_KEY')}
    async with async_client(timeout=30,trust_env=False) as client:
        exists=await client.get(value('supabase','url')+'/storage/v1/bucket/artifacts',headers=headers)
        if exists.status_code==404 or exists.status_code==400:
            r=await client.post(value('supabase','url')+'/storage/v1/bucket',headers=headers,
                                json={'id':'artifacts','name':'artifacts','public':False})
            r.raise_for_status()
        else: exists.raise_for_status()
    from anp.sdk.tracing import ensure_score_configs
    await wait_url(value('langfuse','url')+'/api/public/health')
    await asyncio.to_thread(ensure_score_configs)
    return {'admin_email':user['email'],'workspace_id':DEFAULT_WORKSPACE}


async def wait_url(url,timeout=180,headers=None):
    until=time.monotonic()+timeout
    async with async_client(timeout=3,trust_env=False) as client:
        while time.monotonic()<until:
            try:
                r=await client.get(url,headers=headers)
                if r.is_success: return
            except httpx.HTTPError: pass
            await asyncio.sleep(1)
    raise BusinessError('服务启动超时：'+url,503,'STARTUP_TIMEOUT')
