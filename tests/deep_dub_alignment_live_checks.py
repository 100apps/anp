"""由显式 ALIGN_LIVE 开关调用的真实词对齐检查，复用新WAV/ASR生产前置。"""
import asyncio
import json
from pathlib import Path
import subprocess
import sys
from uuid import uuid4

from anp import ROOT
from anp.sdk.http import async_client
from apps.deep_dub.services.audio_timing import analyze,read_latest,record_scores


async def verify_alignment(actor,run,audio,endpoint,identities,tmp_path):
    url=endpoint+f'/api/apps/deep_dub/runs/{run["id"]}/align-audio'
    before=(await actor.objects.select('deep_dub_projects',id='eq.'+run['project_id']))[0]['row_version']
    assert await read_latest(actor,run['project_id'],audio['id']) is None
    body={'audio_attempt_id':audio['id'],'timeout_seconds':300};key=uuid4().hex
    async with async_client(timeout=360,trust_env=False,headers={'Authorization':'Bearer '+identities['admin']['token']}) as client:
        assert (await client.post(url,json=body,headers={'Authorization':'Bearer '+identities['viewer']['token']})).status_code==403
        assert (await client.post(url,json=body,headers={'Authorization':''})).status_code==401
        assert (await client.post(url,json=body,headers={'Authorization':'Bearer '+identities['outside']['token']})).status_code in (403,404)
        response=await client.post(url,json=body,headers={'Idempotency-Key':key})
        if response.status_code!=200:Path('.local/deep-dub-alignment-last-failure.json').write_bytes(response.content)
        assert response.status_code==200,'实际对齐失败；私有模型日志保留'
        stage=response.json();assert stage['status']=='succeeded'
        get=endpoint+f'/api/apps/deep_dub/projects/{run["project_id"]}/audio/{audio["id"]}/timing'
        view=await client.get(get,headers={'Authorization':'Bearer '+identities['viewer']['token']});assert view.status_code==200
        result=view.json();assert result['audio_attempt_id']==audio['id'] and result['alignment']['word_count']>0
        assert result['semantic_review']==result['timing_review']=='not_measured'
        assert result['placement']['placement_kind']=='analysis_only'
        assert (await client.get(get,headers={'Authorization':''})).status_code==401
        assert (await client.get(get,headers={'Authorization':'Bearer '+identities['outside']['token']})).status_code in (403,404)
    assert stage==await analyze(actor,run['id'],body,key)
    await record_scores(actor,stage['attempt'])
    scores=await actor.objects.select('deep_dub_scores',stage_attempt_id='eq.'+stage['attempt']['id']);assert len(scores)==9
    assert not await actor.objects.select('deep_dub_audits',project_id='eq.'+run['project_id'])
    assert not await actor.objects.select('deep_dub_selections',project_id='eq.'+run['project_id'])
    assert not await actor.objects.select('deep_dub_timeline_revisions',project_id='eq.'+run['project_id'])
    assert (await actor.objects.select('deep_dub_projects',id='eq.'+run['project_id']))[0]['row_version']==before
    leases=await actor.objects.select('deep_dub_device_leases',select='id,status,operation_stopped',stage_attempt_id='eq.'+stage['attempt']['id'])
    assert len(leases)==1 and leases[0]['status']=='released' and leases[0]['operation_stopped'] is True
    def cli(*options):
        p=subprocess.run([sys.executable,'-m','anp.cli.entry',*options,'deep_dub','runs','align-audio',run['id'],audio['id'],'--request-key',key],
            cwd=ROOT,capture_output=True,text=True,encoding='utf-8',timeout=60)
        assert p.returncode==0,'词对齐CLI重放失败，响应不公开';return json.loads(p.stdout)
    assert await asyncio.to_thread(cli,'--endpoint','http://127.0.0.1:1')==stage
    assert await asyncio.to_thread(cli,'--transport','http','--endpoint',endpoint)==stage
    return {'stage_attempt_id':stage['attempt']['id'],'readback':result,'score_ids':[s['id'] for s in scores],
        'three_entry_replay':True,'viewer_read_only':True,'no_quality_audit_or_timeline':True,'lease_released':True}
