"""完整既有音轨的真实源词时间测量；不算新下载、整片译配或质量通过。"""
import asyncio,json,os,subprocess,sys
from pathlib import Path
from uuid import uuid4

import pytest

from anp import ROOT
from anp.auth.identity import actor_from_token
from anp.auth.session import login,session_path
from anp.sdk.http import async_client
from apps.deep_dub.services.execution import service as execution
from apps.deep_dub.services.source_alignment import align,read_latest,record_scores
from tests.test_deep_dub_runtime_live import flush
from tests.test_deep_dub_waveform_live import waveform_api

pytestmark=pytest.mark.skipif(os.environ.get('ANP_DEEP_DUB_SOURCE_ALIGNMENT_LIVE')!='1',reason='需明确启用整轨强制对齐真实GPU及既有ASR证据')


def test_whole_source_word_timing_owned_process_persisted_and_three_entries(identities,waveform_api,monkeypatch):
    file=ROOT/'.local/deep-dub-source-asr-live.json'
    if not file.is_file():pytest.skip('需要已有完整音轨识别证据')
    original=json.loads(file.read_bytes())
    monkeypatch.setenv('ANP_PROFILE','source-word-timing-'+uuid4().hex)
    monkeypatch.setenv('PYTHONIOENCODING','utf-8')
    async def run():
        actor=await actor_from_token(identities['admin']['token'])
        project=(await actor.objects.select('deep_dub_projects',id='eq.'+original['project_id']))[0]
        old=(await actor.objects.select('deep_dub_runs',id='eq.'+original['run_id']))[0]
        queued=await execution.queue(actor,project['id'],{'quality_profile_id':old['quality_profile_id'],'purpose':'acceptance',
            'reuse_policy':'approved_versions','flow_revision':'source-word-timing-acceptance-v1','expected_version':project['row_version']})
        await execution.start(actor,queued['id']);success=False
        try:
            body={'source_id':original['source_id'],'timeout_seconds':3600};key=uuid4().hex
            endpoint=waveform_api+f'/api/apps/deep_dub/runs/{queued["id"]}/align-source'
            async with async_client(timeout=3650,trust_env=False,headers={'Authorization':'Bearer '+identities['admin']['token']}) as client:
                assert (await client.post(endpoint,json=body,headers={'Authorization':'Bearer '+identities['viewer']['token']})).status_code==403
                assert (await client.post(endpoint,json=body,headers={'Authorization':''})).status_code==401
                assert (await client.post(endpoint,json=body,headers={'Authorization':'Bearer '+identities['outside']['token']})).status_code in (403,404)
                response=await client.post(endpoint,json=body,headers={'Idempotency-Key':key})
                if response.status_code!=200:(ROOT/'.local/deep-dub-source-alignment-last-failure.json').write_bytes(response.content)
                assert response.status_code==200,'来源对齐失败；原始错误留私有日志'
                result=response.json();assert result['status']=='succeeded'
                view=waveform_api+f'/api/apps/deep_dub/projects/{project["id"]}/sources/{original["source_id"]}/word-timing'
                visible=await client.get(view,headers={'Authorization':'Bearer '+identities['viewer']['token']});assert visible.status_code==200
                readback=visible.json();measured=readback['measurement']
                assert (await client.get(view,headers={'Authorization':''})).status_code==401
                assert (await client.get(view,headers={'Authorization':'Bearer '+identities['outside']['token']})).status_code in (403,404)
            assert measured['session']['requests']==len(original['readback']['recognition']['chunks'])
            assert measured['session']['warm_cache_checks']==measured['session']['requests']-1
            assert measured['session']['operation_stopped'] and measured['summary']['word_count']>0
            assert measured['semantic_review']==measured['subtitle_timing_review']=='not_measured'
            assert result==await align(actor,queued['id'],body,key)
            assert readback==await read_latest(actor,project['id'],original['source_id'])
            await record_scores(actor,result['attempt'])
            scores=await actor.objects.select('deep_dub_scores',stage_attempt_id='eq.'+result['attempt']['id'])
            assert len(scores)==7 and sum(s['measurement_status']=='not_measured' for s in scores)==2
            leases=await actor.objects.select('deep_dub_device_leases',select='id,status,operation_stopped',stage_attempt_id='eq.'+result['attempt']['id'])
            assert len(leases)==1 and leases[0]['status']=='released' and leases[0]['operation_stopped']
            for table in ('deep_dub_audits','deep_dub_segment_sets','deep_dub_translations','deep_dub_timeline_revisions'):
                assert not await actor.objects.select(table,project_id='eq.'+project['id'])
            await login(identities['admin']['email'],identities['admin']['password'])
            for options in [('--endpoint','http://127.0.0.1:1'),('--transport','http','--endpoint',waveform_api)]:
                def cli():
                    completed=subprocess.run([sys.executable,'-m','anp.cli.entry',*options,'deep_dub','runs','align-source',queued['id'],
                        original['source_id'],'--request-key',key],cwd=ROOT,capture_output=True,text=True,encoding='utf-8',timeout=90)
                    assert completed.returncode==0,'来源词时间CLI重放失败'
                    return json.loads(completed.stdout)
                assert await asyncio.to_thread(cli)==result
            await flush(actor)
            record={'project_id':project['id'],'run_id':queued['id'],'source_id':original['source_id'],'source_artifact_id':original['source_artifact_id'],
                'result':result,'readback':readback,'score_ids':[s['id'] for s in scores],'actor_user_id':actor.user['id'],
                'three_entry_replay':True,'viewer_read_only':True,'lease_released':True,'new_video_download':False,
                'new_alignment_model_execution':True,'new_source_asr_execution':False,'kept_for_browser':os.environ.get('ANP_DEEP_DUB_SOURCE_ALIGNMENT_KEEP')=='1'}
            (ROOT/'.local/deep-dub-source-alignment-live.json').write_bytes(json.dumps(record,ensure_ascii=False,indent=2).encode())
            success=True
        finally:
            if not (success and os.environ.get('ANP_DEEP_DUB_SOURCE_ALIGNMENT_KEEP')=='1'):
                await execution.cancel(actor,queued['id']);await flush(actor)
    try:asyncio.run(run())
    finally:session_path().unlink(missing_ok=True)
