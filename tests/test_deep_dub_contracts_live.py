"""ASGI 适配调用真实 V3 Supabase；不算已注册运行端口或 Web 验收。"""
import asyncio
import os
from uuid import uuid4

from fastapi import FastAPI
import httpx
import pytest

from anp.config import secret, value
from anp.errors import BusinessError
from anp.sdk.http import async_client
from anp.auth.identity import actor_from_token
from anp.server.api import business_error
from apps.deep_dub.api.router import router
from apps.deep_dub.services.versions import service

pytestmark=pytest.mark.skipif(os.environ.get('ANP_DEEP_DUB_LIVE')!='1',reason='需显式开启真实 V3 契约验收')
PROFILE={'profile_key':'high','revision':1,'provenance':'deep_dub 本机规则契约 v1'}


@pytest.fixture(scope='module')
def context():
    assert value('supabase','url')=='http://127.0.0.1:8089/supabase'
    async def tokens():
        result={}
        for role,email,password in [('admin',secret('ADMIN_EMAIL'),secret('ADMIN_PASSWORD')),
            ('viewer','test-viewer@anp.local','ANP-Test-'+secret('ADMIN_PASSWORD')[:24])]:
            async with async_client(trust_env=False,timeout=20) as client:
                response=await client.post(value('supabase','url')+'/auth/v1/token?grant_type=password',
                    headers={'apikey':secret('ANON_KEY')},json={'email':email,'password':password})
                response.raise_for_status();result[role]=response.json()['access_token']
        return result
    app=FastAPI();app.add_exception_handler(BusinessError,business_error);app.include_router(router)
    return app,asyncio.run(tokens())


def test_quality_contract_uses_same_service_and_supabase_identity(context):
    app,tokens=context
    async def run():
        async with async_client(transport=httpx.ASGITransport(app=app),base_url='http://acceptance',headers={'Authorization':'Bearer '+tokens['admin']}) as client:
            response=await client.post('/api/apps/deep_dub/quality-profiles',json=PROFILE)
            assert response.status_code==201,response.text
            direct=await service.create_quality_profile(await actor_from_token(tokens['admin']),PROFILE)
            assert response.json()==direct
            assert direct['profile_key']=='high' and direct['rules']['background_mode']=='preserve'
    asyncio.run(run())


def test_asgi_enforces_login_viewer_and_body_contracts(context):
    app,tokens=context
    async def run():
        async with async_client(transport=httpx.ASGITransport(app=app),base_url='http://acceptance') as client:
            assert (await client.get('/api/apps/deep_dub/projects')).status_code==401
            assert (await client.post('/api/apps/deep_dub/quality-profiles',json=PROFILE,headers={'Authorization':'Bearer '+tokens['viewer']})).status_code==403
            bad={'segment_set_id':str(uuid4()),'quality_profile_id':str(uuid4()),'expected_version':1,
                'clips':[{'segment_id':str(uuid4()),'selection_id':str(uuid4()),'start_ms':-1,'gain':1}]}
            assert (await client.post('/api/apps/deep_dub/projects/'+str(uuid4())+'/timeline',json=bad,headers={'Authorization':'Bearer '+tokens['admin']})).status_code==422
    asyncio.run(run())


def test_postgrest_preserves_conflict_and_not_found_as_domain_http_status(context):
    app,tokens=context
    async def run():
        async with async_client(transport=httpx.ASGITransport(app=app),base_url='http://acceptance',headers={'Authorization':'Bearer '+tokens['admin']}) as client:
            profile=await client.post('/api/apps/deep_dub/quality-profiles',json=PROFILE);assert profile.status_code==201
            conflict=await client.post('/api/apps/deep_dub/quality-profiles',json={**PROFILE,'revision':999})
            assert conflict.status_code==409 and conflict.json()['error']['code']=='PT409'
            projects=await client.get('/api/apps/deep_dub/projects');assert projects.status_code==200 and projects.json()
            missing=await client.post('/api/apps/deep_dub/projects/'+projects.json()[0]['id']+'/audits',json={
                'kind':'translation','subject_id':str(uuid4()),'quality_profile_id':profile.json()['id'],
                'rule_key':'meaning','rule_revision':'contract-only','status':'not_measured','method':'independent_model'})
            assert missing.status_code==404 and missing.json()['error']['code']=='PT404'
    asyncio.run(run())
