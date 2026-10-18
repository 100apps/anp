"""真实官方页、已有整轨ASR、同身份三入口；不重跑模型或声称整片生产。"""
import asyncio,json,os,subprocess,sys
from pathlib import Path
from uuid import uuid4

import pytest

from anp import ROOT
from anp.auth.identity import actor_from_token
from anp.auth.session import login,session_path
from anp.sdk.http import async_client
from apps.deep_dub.services.execution import service as execution
from apps.deep_dub.services.source_reference import compare,read
from tests.test_deep_dub_runtime_live import flush
from tests.test_deep_dub_waveform_live import waveform_api

pytestmark=pytest.mark.skipif(os.environ.get('ANP_DEEP_DUB_SOURCE_REFERENCE_LIVE')!='1',reason='需明确启用真实官方参考网络及已有ASR证据')


def test_official_reference_persisted_scored_and_same_cli_contract(identities,waveform_api,monkeypatch):
    file=ROOT/'.local/deep-dub-source-asr-live.json'
    if not file.is_file():pytest.skip('需本轮既有整轨ASR')
    original=json.loads(file.read_bytes())
    monkeypatch.setenv('ANP_PROFILE','source-reference-'+uuid4().hex)
    monkeypatch.setenv('PYTHONIOENCODING','utf-8')
    async def run():
        actor=await actor_from_token(identities['admin']['token'])
        project=(await actor.objects.select('deep_dub_projects',id='eq.'+original['project_id']))[0]
        old=(await actor.objects.select('deep_dub_runs',id='eq.'+original['run_id']))[0]
        queued=await execution.queue(actor,project['id'],{'quality_profile_id':old['quality_profile_id'],
            'purpose':'acceptance','reuse_policy':'approved_versions','flow_revision':'source-reference-acceptance-v1','expected_version':project['row_version']})
        await execution.start(actor,queued['id'])
        success=False
        try:
            body={'source_id':original['source_id'],'reference_url':'https://oyc.yale.edu/history/hist-119/lecture-1'}
            key=uuid4().hex
            endpoint=waveform_api+f'/api/apps/deep_dub/runs/{queued["id"]}/compare-source-reference'
            async with async_client(timeout=180,trust_env=False,headers={'Authorization':'Bearer '+identities['admin']['token']}) as client:
                for role,expected in [('viewer',(403,)),('outside',(403,404))]:
                    assert (await client.post(endpoint,json=body,headers={'Authorization':'Bearer '+identities[role]['token']})).status_code in expected
                assert (await client.post(endpoint,json=body,headers={'Authorization':''})).status_code==401
                response=await client.post(endpoint,json=body,headers={'Idempotency-Key':key})
                if response.status_code!=200:
                    (ROOT/'.local/deep-dub-source-reference-last-failure.json').write_bytes(response.content)
                assert response.status_code==200,'参考比对失败；原始错误留在私有回执'
                result=response.json();assert result['status']=='succeeded'
                view=waveform_api+f'/api/apps/deep_dub/attempts/{result["attempt"]["id"]}/source-reference'
                visible=await client.get(view,headers={'Authorization':'Bearer '+identities['viewer']['token']})
                assert visible.status_code==200
                assert (await client.get(view,headers={'Authorization':'Bearer '+identities['outside']['token']})).status_code in (403,404)
                assert (await client.get(view,headers={'Authorization':''})).status_code==401
                proof=visible.json()
                latest=waveform_api+f'/api/apps/deep_dub/projects/{project["id"]}/sources/{original["source_id"]}/reference'
                assert (await client.get(latest)).json()==proof
                assert (await client.get(latest,headers={'Authorization':'Bearer '+identities['outside']['token']})).status_code in (403,404)
            assert proof==await read(actor,result['attempt']['id'])
            assert result==await compare(actor,queued['id'],body,key)
            assert proof['proof']['reference']['video_id']=='QXXp1bHd6gI'
            assert len(proof['proof']['reference']['chapters'])==6
            assert proof['proof']['comparison']['difference_count']>0
            assert proof['proof']['comparison']['semantic_review']=='not_measured'
            assert not proof['proof']['local_media_online_identity_verified']
            scores=await actor.objects.select('deep_dub_scores',stage_attempt_id='eq.'+result['attempt']['id'])
            assert len(scores)==4 and sum(s['measurement_status']=='not_measured' for s in scores)==2
            lease=await actor.objects.select('deep_dub_device_leases',select='id,status,operation_stopped',stage_attempt_id='eq.'+result['attempt']['id'])
            assert len(lease)==1 and lease[0]['status']=='released' and lease[0]['operation_stopped']
            await login(identities['admin']['email'],identities['admin']['password'])
            for options in [('--endpoint','http://127.0.0.1:1'),('--transport','http','--endpoint',waveform_api)]:
                def cli():
                    completed=subprocess.run([sys.executable,'-m','anp.cli.entry',*options,'deep_dub','runs','compare-source-reference',
                        queued['id'],original['source_id'],body['reference_url'],'--request-key',key],cwd=ROOT,capture_output=True,text=True,encoding='utf-8',timeout=90)
                    assert completed.returncode==0,'来源参考CLI失败；不公开敏感原始输出'
                    return json.loads(completed.stdout)
                assert await asyncio.to_thread(cli)==result
                def get_cli():
                    completed=subprocess.run([sys.executable,'-m','anp.cli.entry',*options,'deep_dub','source-reference',result['attempt']['id']],cwd=ROOT,capture_output=True,text=True,encoding='utf-8',timeout=90)
                    assert completed.returncode==0,'来源参考读取CLI失败'
                    return json.loads(completed.stdout)
                assert await asyncio.to_thread(get_cli)==proof
            await flush(actor)
            (ROOT/'.local/deep-dub-source-reference-live.json').write_bytes(json.dumps({'project_id':project['id'],
                'run_id':queued['id'],'result':result,'readback':proof,'score_ids':[s['id'] for s in scores],
                'three_entry_replay':True,'viewer_read_only':True,'lease_released':True,'new_video_download':False,
                'new_asr_model_execution':False,'actor_user_id':actor.user['id'],
                'kept_for_browser':os.environ.get('ANP_DEEP_DUB_REFERENCE_KEEP')=='1'},ensure_ascii=False,indent=2).encode())
            success=True
        finally:
            if not (success and os.environ.get('ANP_DEEP_DUB_REFERENCE_KEEP')=='1'):
                await execution.cancel(actor,queued['id']);await flush(actor)
    try:asyncio.run(run())
    finally:session_path().unlink(missing_ok=True)
