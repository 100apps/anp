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
from apps.deep_dub.services.execution import service as execution
from apps.deep_dub.services.observations import deliver_one

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


def test_execution_same_identity_and_real_langfuse_unknown_score(context):
    """行政验收阶段，无视频/设备操作；未测质量必须在 Langfuse 保持未测。"""
    import time
    import json
    from pathlib import Path
    from datetime import datetime, timezone
    app, tokens = context

    async def run():
        actor = await actor_from_token(tokens['admin'])
        async with async_client(transport=httpx.ASGITransport(app=app), base_url='http://acceptance',
                headers={'Authorization':'Bearer '+tokens['admin']}) as client:
            projects = (await client.get('/api/apps/deep_dub/projects')).json()
            project = projects[0]
            profile = await service.create_quality_profile(actor, PROFILE)
            body = {'quality_profile_id':profile['id'], 'purpose':'acceptance',
                    'flow_revision':'observation-contract-v1', 'expected_version':project['row_version']}
            key = 'observation-acceptance-'+str(uuid4())
            response = await client.post('/api/apps/deep_dub/projects/'+project['id']+'/runs',
                                         json=body, headers={'Idempotency-Key':key})
            assert response.status_code == 201, response.text
            queued = response.json()
            assert queued == await execution.queue(actor, project['id'], body, key)
            try:
                assert (await client.post('/api/apps/deep_dub/runs/'+queued['id']+'/start')).status_code == 200
                denied = await client.post('/api/apps/deep_dub/runs/'+queued['id']+'/stages',
                    json={'node':'acceptance_observation','input_hash':'a'*64},
                    headers={'Authorization':'Bearer '+tokens['viewer']})
                assert denied.status_code == 403
                staged = await client.post('/api/apps/deep_dub/runs/'+queued['id']+'/stages',
                    json={'node':'acceptance_observation','input_hash':'a'*64})
                assert staged.status_code == 201, staged.text
                attempt = staged.json()['attempt']['id']
                assert (await client.post('/api/apps/deep_dub/attempts/'+attempt+'/mark/started')).status_code == 200
                started = time.monotonic()
                receipt = {'status':'succeeded','outputs':[], 'elapsed_seconds':time.monotonic()-started}
                finished = await client.post('/api/apps/deep_dub/attempts/'+attempt+'/receipt', json=receipt)
                assert finished.status_code == 200 and finished.json()['committed'], finished.text
                measured = {'metric':'speaker_identity','rule_revision':'acceptance-unmeasured-v1',
                            'measurement_status':'not_measured'}
                scored = await client.post('/api/apps/deep_dub/attempts/'+attempt+'/scores',json=measured)
                assert scored.status_code == 201 and scored.json()['value'] is None, scored.text
                results = []
                for _ in range(100):
                    delivered = await deliver_one(actor)
                    if not delivered['claimed']:
                        break
                    assert delivered['sent'], delivered
                    results.append(delivered)
                else:
                    raise AssertionError('观测投递未在限定批数内排空')
                trace_id = queued['id'].replace('-','')
                async with async_client(timeout=15,trust_env=False,
                        auth=httpx.BasicAuth(secret('LANGFUSE_PUBLIC_KEY'),secret('LANGFUSE_SECRET_KEY'))) as lf:
                    for _ in range(20):
                        trace = await lf.get(value('langfuse','url')+'/api/public/v2/observations',
                                             params={'traceId':trace_id,'limit':100})
                        scores = await lf.get(value('langfuse','url')+'/api/public/v3/scores',
                                              params={'traceId':trace_id,'limit':100})
                        if (trace.is_success and trace.json().get('data') and scores.is_success
                                and any(s['id']==scored.json()['id'] for s in scores.json().get('data', []))):
                            break
                        await asyncio.sleep(1)
                    assert trace.is_success, 'Langfuse 追踪尚未可读'
                    data = trace.json()['data'][0]
                    assert data['userId']==actor.user['id'] and data['sessionId']==queued['id']
                    score = next(s for s in scores.json()['data'] if s['id']==scored.json()['id'])
                    assert score['dataType']=='TEXT' and score['value']=='not_measured'
                evidence = {'checked_at_utc':datetime.now(timezone.utc).isoformat(),
                    'scope':'真实 V3 Supabase + ASGI 业务契约 + 本机 Langfuse；没有媒体生产或真实设备操作',
                    'run_id':queued['id'],'purpose':'acceptance','trace_id':trace_id,
                    'user_id':data['userId'],'session_id':data['sessionId'],
                    'attempt_id':attempt,'score_id':score['id'],'score_type':score['dataType'],
                    'local_numeric_value':scored.json()['value'],'langfuse_polymorphic_value':score['value'],
                    'delivery_count':len(results),'viewer_forbidden':True}
                Path('.local/deep-dub-observation-live.json').write_bytes((json.dumps(evidence,ensure_ascii=False,indent=2)+'\n').encode())
            finally:
                await execution.cancel(actor, queued['id'])
    asyncio.run(run())
