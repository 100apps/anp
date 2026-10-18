"""新获取公开完整原片、三入口重放和私有证据；acceptance，不是整片译配RTF。"""
import asyncio,json,os,subprocess,sys
from pathlib import Path
from uuid import uuid4

import pytest
from anp import ROOT
from anp.auth.identity import actor_from_token
from anp.auth.session import login,session_path
from anp.sdk.http import async_client
from apps.deep_dub.services.projects import service as projects
from apps.deep_dub.services.execution import service as execution
from apps.deep_dub.services.versions import service as versions
from apps.deep_dub.services.source_acquisition import acquire,read,describe
from apps.deep_dub.nodes.source_fetch import command
from apps.deep_dub.services.processing import decode_artifact
from apps.deep_dub.services.runtime import StageResult
from tests.test_deep_dub_contracts_live import PROFILE
from tests.test_deep_dub_runtime_live import flush
from tests.test_deep_dub_waveform_live import waveform_api

pytestmark=pytest.mark.skipif(os.environ.get('ANP_DEEP_DUB_SOURCE_ACQUISITION_LIVE')!='1',reason='需明确允许新下载整片和真实V3存储')


def test_new_original_download_persisted_and_replayed_three_entries(identities,waveform_api,monkeypatch):
    monkeypatch.setenv('ANP_PROFILE','source-fetch-'+uuid4().hex)
    monkeypatch.setenv('PYTHONIOENCODING','utf-8')
    async def run():
        actor=await actor_from_token(identities['admin']['token'])
        project=await projects.create(actor,{'name':'[新来源获取验收] Yale HIST119 完整原片','source_url':'https://www.youtube.com/watch?v=QXXp1bHd6gI'})
        profile=await versions.create_quality_profile(actor,PROFILE)
        queued=await execution.queue(actor,project['id'],{'quality_profile_id':profile['id'],'purpose':'acceptance',
            'flow_revision':'source-acquisition-acceptance-v1','expected_version':project['row_version']})
        await execution.start(actor,queued['id']);success=False
        try:
            body={'timeout_seconds':3600};key=uuid4().hex
            _,config,inputs,digest=await describe(actor,queued['id'],body)
            attempt=(await execution.begin_stage(actor,queued['id'],{'node':'source_acquisition','input_hash':digest,'device_kind':'cpu'},key))['attempt']
            folder=ROOT/'.local/deep_dub/source-fetch'/attempt['id']
            plan={'project_id':project['id'],'run_id':queued['id'],'attempt_id':attempt['id'],
                'command':command(config,inputs['download_url'],folder),'scope':'acceptance新下载整片；等待命令展示计入本次阶段验收，不是正式生产计时'}
            (ROOT/'.local/deep-dub-source-acquisition-plan.json').write_bytes(json.dumps(plan,ensure_ascii=False,indent=2).encode())
            if os.environ.get('ANP_DEEP_DUB_SOURCE_ACQUISITION_SHOW_COMMAND')=='1':
                marker=ROOT/'.local/deep-dub-source-acquisition-go.json'
                for _ in range(600):
                    if marker.is_file() and json.loads(marker.read_bytes()).get('attempt_id')==attempt['id']:break
                    await asyncio.sleep(1)
                else:pytest.fail('下载命令尚未展示，未启动网络获取')
            endpoint=waveform_api+f'/api/apps/deep_dub/runs/{queued["id"]}/acquire-source'
            async with async_client(timeout=3650,trust_env=False,headers={'Authorization':'Bearer '+identities['admin']['token']}) as client:
                assert (await client.post(endpoint,json=body,headers={'Authorization':'Bearer '+identities['viewer']['token']})).status_code==403
                assert (await client.post(endpoint,json=body,headers={'Authorization':''})).status_code==401
                assert (await client.post(endpoint,json=body,headers={'Authorization':'Bearer '+identities['outside']['token']})).status_code in (403,404)
                response=await client.post(endpoint,json=body,headers={'Idempotency-Key':key})
                if response.status_code!=200:(ROOT/'.local/deep-dub-source-acquisition-last-failure.json').write_bytes(response.content)
                assert response.status_code==200,'来源下载失败；错误分类保存在私有回执'
                result=response.json();assert result['status']=='succeeded' and result['attempt']['id']==attempt['id']
                url=waveform_api+f'/api/apps/deep_dub/runs/{queued["id"]}/source-acquisition'
                visible=await client.get(url,headers={'Authorization':'Bearer '+identities['viewer']['token']});assert visible.status_code==200
                readback=visible.json();assert readback==await read(actor,queued['id'])
                assert (await client.get(url,headers={'Authorization':''})).status_code==401
                assert (await client.get(url,headers={'Authorization':'Bearer '+identities['outside']['token']})).status_code in (403,404)
            assert result==await acquire(actor,queued['id'],body,key)
            scores=await actor.objects.select('deep_dub_scores',stage_attempt_id='eq.'+attempt['id']);assert len(scores)==4
            assert sum(x['measurement_status']=='not_measured' for x in scores)==2
            leases=await actor.objects.select('deep_dub_device_leases',select='id,status,operation_stopped',stage_attempt_id='eq.'+attempt['id'])
            assert len(leases)==1 and leases[0]['status']=='released' and leases[0]['operation_stopped']
            await login(identities['admin']['email'],identities['admin']['password'])
            for options in [('--endpoint','http://127.0.0.1:1'),('--transport','http','--endpoint',waveform_api)]:
                def cli():
                    completed=subprocess.run([sys.executable,'-m','anp.cli.entry',*options,'deep_dub','runs','acquire-source',queued['id'],'--request-key',key],
                        cwd=ROOT,capture_output=True,text=True,encoding='utf-8',timeout=120)
                    assert completed.returncode==0,'来源下载CLI重放失败；不公开原始输出'
                    return json.loads(completed.stdout)
                assert await asyncio.to_thread(cli)==result
            decoded=await decode_artifact(actor,queued['id'],{'artifact_id':readback['proof']['video_artifact_id'],'timeout_seconds':1800})
            assert decoded['status']=='succeeded'
            await flush(actor)
            record={'project_id':project['id'],'run_id':queued['id'],'result':result,'readback':readback,'decoded':decoded,
                'three_entry_replay':True,'viewer_read_only':True,'lease_released':True,'new_download':True,
                'actor_user_id':actor.user['id'],'score_ids':[s['id'] for s in scores],
                'scope':'新下载完整来源及真实完整解码；acceptance，非完整译配/RTF/内容听审通过'}
            (ROOT/'.local/deep-dub-source-acquisition-live.json').write_bytes(json.dumps(record,ensure_ascii=False,indent=2).encode())
            success=True
        finally:
            await execution.cancel(actor,queued['id']);await flush(actor)
    try:asyncio.run(run())
    finally:session_path().unlink(missing_ok=True)
